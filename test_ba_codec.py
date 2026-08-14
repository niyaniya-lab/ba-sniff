"""
Round-trip tests for ba_codec.

These build synthetic gateway packets byte-for-byte the way the reverse-engineered
format says the real client does, then assert the decoder recovers the original
JSON. This verifies the format itself is internally consistent and that the
response-crypto sweep finds every AES mode the client might use. No mocks.

Run:  python -m pytest test_ba_codec.py -v
   or python test_ba_codec.py     (built-in runner, no pytest needed)
"""

import base64
import json
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import ba_codec as codec


# ---------------------------------------------------------------------------
# helpers to build synthetic packets (the "encoder" side)
# ---------------------------------------------------------------------------
def build_request_mx(protocol: int, extra: dict, key: bytes, iv: bytes,
                     crc: int = 0x11223344, type_conversion: int = 7) -> bytes:
    payload = {"Protocol": protocol}
    payload.update(extra)
    body = codec.encode_request_body(json.dumps(payload).encode("utf-8"))
    header = struct.pack("<Ii", crc, type_conversion) + bytes([len(key), len(iv)]) + key + iv
    return header + body


def _pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def encrypt_inner(plain: bytes, key: bytes, iv: bytes, mode: str) -> bytes:
    if mode == "ECB":
        enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        return enc.update(_pkcs7_pad(plain)) + enc.finalize()
    if mode == "CBC":
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        return enc.update(_pkcs7_pad(plain)) + enc.finalize()
    if mode == "CTR":
        enc = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
        return enc.update(plain) + enc.finalize()
    raise ValueError(mode)


def build_response(protocol, inner: dict, key: bytes, iv: bytes, mode: str) -> bytes:
    plain = json.dumps(inner).encode("utf-8")
    cipher = encrypt_inner(plain, key, iv, mode)
    envelope = {"protocol": protocol, "packet": base64.b64encode(cipher).decode()}
    return json.dumps(envelope).encode("utf-8")


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
KEY = bytes(range(16))
IV = bytes(range(16, 32))


def test_request_roundtrip_recovers_json_and_header_keys():
    mx = build_request_mx(1002, {"AccountId": 424242, "Nonce": "abc"}, KEY, IV)
    out = codec.decode_request_mx(mx)
    assert out is not None, "request failed to decode"
    assert out["protocol"] == 1002
    assert out["json"]["AccountId"] == 424242
    assert out["key"] == KEY, "response AES key not recovered from header"
    assert out["iv"] == IV, "response AES iv not recovered from header"
    assert out["crc"] == 0x11223344
    print("  request round-trip OK: proto=%s key=%s" % (out["protocol"], out["key"].hex()))


def test_request_handshake_zero_length_keys():
    # Handshake packets (GetCryptoKeys) send keyLen=0/ivLen=0.
    mx = build_request_mx(50001, {"ClientGeneratedKey": "x"}, b"", b"")
    out = codec.decode_request_mx(mx)
    assert out is not None
    assert out["protocol"] == 50001
    assert out["key"] == b"" and out["iv"] == b""
    print("  handshake (keyLen=0) round-trip OK")


def test_response_decodes_for_every_aes_mode():
    inner = {"AccountDB": {"Level": 78, "Exp": 12345},
             "CharacterList": [{"Id": 10000, "Level": 90}, {"Id": 20000, "Level": 55}]}
    for mode in ("ECB", "CBC", "CTR"):
        content = build_response("Account_Auth", inner, KEY, IV, mode)
        res = codec.decode_response_envelope(content, KEY, IV)
        assert res is not None, f"{mode}: not recognised as envelope"
        assert res["inner"] is not None, f"{mode}: failed to decrypt (method={res.get('method')})"
        assert res["inner"]["AccountDB"]["Level"] == 78, f"{mode}: wrong payload"
        assert res["inner"]["CharacterList"][0]["Level"] == 90
        print(f"  response {mode}: decoded via {res['method']}")


def test_response_plaintext_handshake_packet():
    # Handshake responses carry a plaintext JSON string in `packet`.
    inner = {"ServerSeed": 999, "SessionKey": 1}
    envelope = {"protocol": 50001, "packet": json.dumps(inner)}
    res = codec.decode_response_envelope(json.dumps(envelope).encode(), b"", b"")
    assert res is not None and res["inner"] is not None
    assert res["inner"]["ServerSeed"] == 999
    assert res["method"] == "plain-string"
    print("  plaintext handshake response OK")


def test_wrong_key_reports_undecoded_not_crash():
    inner = {"AccountDB": {"Level": 1}}
    content = build_response("Account_Auth", inner, KEY, IV, "CBC")
    wrong = bytes([0xFF] * 16)
    res = codec.decode_response_envelope(content, wrong, wrong)
    assert res is not None
    assert res["inner"] is None
    assert res["method"] == "UNDECODED"
    assert res["cipher_len"] > 0
    print("  wrong-key path reports UNDECODED cleanly")


def test_proto_name_mapping_and_fallback():
    assert codec.proto_name(2000) == "Character_List"
    assert codec.proto_name(999999) == "Protocol_999999"
    assert codec.proto_name("Account_Auth") == "Account_Auth"
    print("  proto_name mapping OK")


def test_target_protocols_cover_requested_data():
    # level/currency/roster/clears must all be reachable.
    for pid in (1002, 1003, 2000, 6000):
        assert pid in codec.TARGET_PROTOCOLS
    print("  target protocol set covers level/currency/roster/clears")


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            print(f"[RUN] {t.__name__}")
            t()
            print(f"[PASS] {t.__name__}\n")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {t.__name__}: {exc}\n")
        except Exception as exc:  # noqa
            failed += 1
            print(f"[ERROR] {t.__name__}: {type(exc).__name__}: {exc}\n")
    total = len(tests)
    print(f"==== {total - failed}/{total} passed ====")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if _run_all() else 0)
