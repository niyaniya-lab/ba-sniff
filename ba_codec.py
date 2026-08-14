"""
ba_codec.py — pure (mitmproxy-free) codec for Blue Archive gateway packets.

Kept independent of mitmproxy so it can be unit-tested in isolation. The addon
(ba_sniff.py) imports from here; only I/O and the mitmproxy hooks live there.

Wire format (reverse-engineered from Shittim-Server GatewayController.cs):

  request "mx" file field =
      [crc: u32 LE][typeConversion: i32 LE][keyLen: u8][ivLen: u8]
      [aesKey: keyLen bytes][aesIV: ivLen bytes]
      [body: XOR(0xD9) of ( [i32 LE plainLength] + gzip(json) )]

  response body = plaintext JSON envelope: {"protocol": <int|name>, "packet": <str>}
    * handshake (keyLen==0): `packet` is plaintext JSON (or base64 of plaintext)
    * in-session (keyLen>0): `packet` is base64( AES(headerKey, headerIV, innerJson) )
"""

import base64
import binascii
import gzip
import io
import json
import struct
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Protocol id -> name (from Shittim-Server ProtocolData.cs; not exhaustive).
PROTOCOL_NAMES = {
    1002: "Account_Auth",
    1003: "Account_CurrencySync",
    1012: "Account_Auth2",
    1014: "Account_CheckNexon",
    1019: "Account_LoginSync",
    2000: "Character_List",
    6000: "Campaign_List",
    8000: "Mission_List",
    8005: "Mission_Sync",
    19000: "Scenario_List",
    44000: "CharacterGear_List",
    50000: "Queuing_GetTicket",
    50001: "Queuing_GetCryptoKeys",
    50002: "Queuing_GetAuthTicket",
    50003: "Queuing_ProcessWaitingQueue",
}

# Protocols whose decoded response we merge into a consolidated profile.
TARGET_PROTOCOLS = {1002, 1003, 1019, 2000, 6000, 8000, 8005, 19000}

XOR_KEY = 0xD9


def proto_name(protocol) -> str:
    try:
        pid = int(protocol)
    except (TypeError, ValueError):
        return str(protocol)
    return PROTOCOL_NAMES.get(pid, f"Protocol_{pid}")


def xor(data: bytes, key: int = XOR_KEY) -> bytes:
    return bytes(b ^ key for b in data)


def try_gunzip(data: bytes):
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            return gz.read()
    except (OSError, EOFError, zlib.error):
        return None


def looks_like_json(raw: bytes) -> bool:
    s = raw.lstrip()
    return len(s) > 0 and s[:1] in (b"{", b"[")


def parse_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------
def decode_request_mx(mx: bytes):
    """Return dict(crc, type_conversion, key, iv, protocol, json, ...) or None."""
    if mx is None or len(mx) < 10:
        return None
    crc, type_conversion = struct.unpack_from("<Ii", mx, 0)
    key_len = mx[8]
    iv_len = mx[9]
    off = 10
    key = mx[off:off + key_len]; off += key_len
    iv = mx[off:off + iv_len]; off += iv_len
    body = mx[off:]

    payload = decode_request_body(body)
    obj = parse_json(payload) if payload is not None else None
    protocol = obj.get("Protocol", obj.get("protocol")) if isinstance(obj, dict) else None
    return {
        "crc": crc,
        "type_conversion": type_conversion,
        "key": key,
        "iv": iv,
        "protocol": protocol,
        "json": obj,
        "body_len": len(body),
        "raw_preview": binascii.hexlify(body[:16]).decode(),
    }


def decode_request_body(body: bytes):
    """Mirror GatewayController.DecodeGatewayPayloadBodies (3 variants)."""
    if not body:
        return None
    # variant A: XOR whole, then [i32 length][gzip]
    xored = xor(body)
    out = try_gunzip(xored[4:])
    if out is not None and looks_like_json(out):
        return out
    # variant B: clear length prefix, XOR the rest, then gzip
    out = try_gunzip(xor(body[4:]))
    if out is not None and looks_like_json(out):
        return out
    # variant C: XOR whole, gzip with no length prefix
    out = try_gunzip(xored)
    if out is not None and looks_like_json(out):
        return out
    # last resort: already plaintext?
    if looks_like_json(body):
        return body
    return None


def encode_request_body(json_bytes: bytes) -> bytes:
    """Inverse of variant A — used by tests to build synthetic packets."""
    gzipped = gzip.compress(json_bytes)
    inner = struct.pack("<i", len(json_bytes)) + gzipped
    return xor(inner)


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
def pkcs7_strip(data: bytes) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data[-pad:] == bytes([pad]) * pad:
        return data[:-pad]
    return data


def aes_decrypt_variants(cipher_bytes: bytes, key: bytes, iv: bytes):
    """Try ECB/CBC/CTR (+ zero-IV) and return (plaintext, method) or (None, None)."""
    if not key or len(key) not in (16, 24, 32):
        return None, None

    ivs = []
    if iv and len(iv) == 16:
        ivs.append(("iv", iv))
    ivs.append(("zeroiv", b"\x00" * 16))

    def _finish(pt, method):
        stripped = pkcs7_strip(pt)
        if looks_like_json(stripped) and parse_json(stripped) is not None:
            return stripped, method
        if looks_like_json(pt) and parse_json(pt) is not None:
            return pt, method + "+nopad"
        return None, None

    if len(cipher_bytes) % 16 == 0:
        try:
            dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
            pt = dec.update(cipher_bytes) + dec.finalize()
            res, m = _finish(pt, "AES-ECB")
            if res is not None:
                return res, m
        except Exception:
            pass
    for iv_name, iv_val in ivs:
        if len(cipher_bytes) % 16 != 0:
            continue
        try:
            dec = Cipher(algorithms.AES(key), modes.CBC(iv_val)).decryptor()
            pt = dec.update(cipher_bytes) + dec.finalize()
            res, m = _finish(pt, f"AES-CBC({iv_name})")
            if res is not None:
                return res, m
        except Exception:
            pass
    for iv_name, iv_val in ivs:
        try:
            dec = Cipher(algorithms.AES(key), modes.CTR(iv_val)).decryptor()
            pt = dec.update(cipher_bytes) + dec.finalize()
            res, m = _finish(pt, f"AES-CTR({iv_name})")
            if res is not None:
                return res, m
        except Exception:
            pass
    return None, None


def decode_response_envelope(content: bytes, key: bytes, iv: bytes):
    """Return dict(protocol, inner, method, ...) or None if not an envelope."""
    env = parse_json(content)
    if not isinstance(env, dict):
        return None
    protocol = env.get("protocol", env.get("Protocol"))
    packet = env.get("packet", env.get("Packet"))
    if packet is None:
        return {"protocol": protocol, "inner": env, "method": "envelope-only"}
    if isinstance(packet, (dict, list)):
        return {"protocol": protocol, "inner": packet, "method": "plain-object"}
    if isinstance(packet, str):
        obj = parse_json(packet.encode("utf-8"))
        if obj is not None:
            return {"protocol": protocol, "inner": obj, "method": "plain-string"}
        try:
            blob = base64.b64decode(packet, validate=False)
        except (binascii.Error, ValueError):
            blob = None
        if blob is not None:
            obj = parse_json(blob)
            if obj is not None:
                return {"protocol": protocol, "inner": obj, "method": "base64-plain"}
            pt, method = aes_decrypt_variants(blob, key, iv)
            if pt is not None:
                return {"protocol": protocol, "inner": parse_json(pt), "method": method}
            return {
                "protocol": protocol, "inner": None, "method": "UNDECODED",
                "cipher_len": len(blob),
                "cipher_preview": binascii.hexlify(blob[:16]).decode(),
            }
    return {"protocol": protocol, "inner": None, "method": "UNKNOWN"}
