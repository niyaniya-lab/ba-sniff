"""
ba_sniff.py — passive Blue Archive (Global, Steam) gateway packet sniffer.

Runs as a mitmproxy addon. It NEVER modifies or blocks traffic — pure observer.
It watches BlueArchive.exe's /api/gateway MX packets, decodes them via ba_codec,
writes decoded JSON to ./captures/, and merges the protocols we care about into
a consolidated profile_latest.json.

Usage:
    mitmdump --mode local:BlueArchive.exe --no-http2 -s ba_sniff.py
(see run.ps1 / README.md)
"""

import base64
import binascii
import json
import os
from datetime import datetime, timezone

from mitmproxy import ctx, http, tls

import ba_codec as codec


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


GATEWAY_MARKERS = ("/api/gateway", "/gateway")

# Only decrypt the game's MX gateway host. Everything else (Nexon Toy SDK, IAS
# login, gamescale, CDNs) is passed through untouched so cert-pinned auth flows
# succeed natively. Override with env BA_SNIFF_INTERCEPT (substring match on SNI).
INTERCEPT_SNI = os.environ.get("BA_SNIFF_INTERCEPT", "bagl.nexon.com")


def extract_mx(request: http.Request):
    """Pull the raw bytes of the multipart 'mx' file field, or None."""
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" not in ctype:
        return None
    try:
        mf = request.multipart_form
        if mf:
            for k, v in mf.items(multi=True):
                if k in (b"mx", b'"mx"'):
                    return v
    except Exception:
        pass
    if "boundary=" not in ctype:
        return None
    boundary = ctype.split("boundary=", 1)[1].strip().strip('"')
    data = request.content or b""
    delim = b"--" + boundary.encode("latin-1")
    for part in data.split(delim):
        if b'name="mx"' in part or b"name=mx" in part:
            idx = part.find(b"\r\n\r\n")
            if idx >= 0:
                body = part[idx + 4:]
                if body.endswith(b"\r\n"):
                    body = body[:-2]
                return body
    return None


class BASniff:
    def __init__(self):
        self.out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
        os.makedirs(self.out_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_path = os.path.join(self.out_dir, f"session_{stamp}.jsonl")
        self.hosts_path = os.path.join(self.out_dir, "hosts.log")
        self.pairs_path = os.path.join(self.out_dir, f"pairs_{stamp}.jsonl")
        self.profile_path = os.path.join(self.out_dir, "profile_latest.json")
        self.profile = {"captured_at": None, "protocols": {}}
        self.count_req = 0
        self.count_resp = 0
        self.count_decoded = 0
        self.count_undecoded = 0

    def load(self, loader):
        ctx.log.alert(f"[ba_sniff] passive sniffer active. writing -> {self.session_path}")
        ctx.log.alert(f"[ba_sniff] intercepting SNI containing '{INTERCEPT_SNI}'; all other hosts pass through untouched.")

    def tls_clienthello(self, data: tls.ClientHelloData):
        """Intercept only the game gateway; TLS-passthrough everything else so
        cert-pinned Nexon auth/SDK hosts (IAS login, Toy SDK) work natively."""
        sni = data.client_hello.sni or ""
        intercept = INTERCEPT_SNI in sni
        if not intercept:
            data.ignore_connection = True
        try:
            with open(self.hosts_path, "a", encoding="utf-8") as fh:
                fh.write(
                    f"{_now_iso()} TLS  sni={sni or '(none)'} -> "
                    f"{'INTERCEPT' if intercept else 'passthrough'}\n"
                )
        except Exception:
            pass

    def _write_line(self, record: dict):
        record["ts"] = _now_iso()
        try:
            with open(self.session_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            ctx.log.warn(f"[ba_sniff] write failed: {exc}")

    def _update_profile(self, protocol, inner):
        try:
            pid = int(protocol)
        except (TypeError, ValueError):
            return
        if pid not in codec.TARGET_PROTOCOLS or inner is None:
            return
        self.profile["captured_at"] = _now_iso()
        self.profile["protocols"][codec.proto_name(pid)] = inner
        try:
            with open(self.profile_path, "w", encoding="utf-8") as fh:
                json.dump(self.profile, fh, ensure_ascii=False, indent=2, default=str)
        except Exception as exc:
            ctx.log.warn(f"[ba_sniff] profile write failed: {exc}")

    def _log_host(self, flow: http.HTTPFlow, status=None):
        """Append every host+path BlueArchive hits (diagnostic; never alters flow)."""
        try:
            line = (
                f"{_now_iso()} {flow.request.method:4} "
                f"{flow.request.scheme}://{flow.request.pretty_host}:{flow.request.port}"
                f"{flow.request.path}"
            )
            if status is not None:
                line += f" -> {status}"
            with open(self.hosts_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    def _is_gateway(self, flow: http.HTTPFlow) -> bool:
        path = flow.request.path or ""
        if any(m in path for m in GATEWAY_MARKERS):
            return True
        ctype = flow.request.headers.get("content-type", "")
        return "multipart/form-data" in ctype and flow.request.method == "POST"

    def request(self, flow: http.HTTPFlow):
        self._log_host(flow)
        if not self._is_gateway(flow):
            return
        mx = extract_mx(flow.request)
        if mx is None:
            return
        decoded = codec.decode_request_mx(mx)
        if decoded is None:
            self._write_line({
                "dir": "req", "host": flow.request.pretty_host,
                "path": flow.request.path, "status": "mx-parse-failed",
                "mx_len": len(mx), "mx_preview": binascii.hexlify(mx[:24]).decode(),
            })
            return
        self.count_req += 1
        flow.metadata["ba_key"] = decoded["key"].hex()
        flow.metadata["ba_iv"] = decoded["iv"].hex()
        flow.metadata["ba_protocol"] = decoded["protocol"]
        # full raw request for offline crypto analysis
        flow.metadata["ba_mx_hex"] = mx.hex()
        body_off = 10 + len(decoded["key"]) + len(decoded["iv"])
        flow.metadata["ba_reqbody_hex"] = mx[body_off:].hex()
        flow.metadata["ba_reqjson"] = decoded["json"]
        pname = codec.proto_name(decoded["protocol"])
        ctx.log.info(
            f"[ba_sniff] REQ {pname} (proto={decoded['protocol']}) "
            f"keyLen={len(decoded['key'])} ivLen={len(decoded['iv'])}"
        )
        self._write_line({
            "dir": "req",
            "host": flow.request.pretty_host,
            "path": flow.request.path,
            "protocol": decoded["protocol"],
            "protocol_name": pname,
            "key_len": len(decoded["key"]),
            "iv_len": len(decoded["iv"]),
            "key_hex": decoded["key"].hex(),
            "iv_hex": decoded["iv"].hex(),
            "json": decoded["json"],
        })

    def response(self, flow: http.HTTPFlow):
        self._log_host(flow, status=flow.response.status_code if flow.response else "?")
        if "ba_protocol" not in flow.metadata:
            return
        self.count_resp += 1
        key = bytes.fromhex(flow.metadata.get("ba_key", ""))
        iv = bytes.fromhex(flow.metadata.get("ba_iv", ""))
        protocol = flow.metadata.get("ba_protocol")
        content = flow.response.content or b""

        # Dump the complete raw request/response pair for offline crypto analysis.
        try:
            with open(self.pairs_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": _now_iso(),
                    "path": flow.request.path,
                    "req_protocol": protocol,
                    "req_protocol_name": codec.proto_name(protocol),
                    "req_key_hex": flow.metadata.get("ba_key"),
                    "req_iv_hex": flow.metadata.get("ba_iv"),
                    "req_mx_hex": flow.metadata.get("ba_mx_hex"),
                    "req_body_hex": flow.metadata.get("ba_reqbody_hex"),
                    "req_json": flow.metadata.get("ba_reqjson"),
                    "resp_len": len(content),
                    "resp_b64": base64.b64encode(content).decode(),
                }, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            ctx.log.warn(f"[ba_sniff] pair dump failed: {exc}")

        result = codec.decode_response_envelope(content, key, iv)

        if result is None:
            self.count_undecoded += 1
            self._write_line({
                "dir": "resp", "protocol": protocol,
                "protocol_name": codec.proto_name(protocol),
                "status": "not-an-envelope",
                "resp_len": len(content),
                "resp_preview": content[:120].decode("utf-8", "replace"),
            })
            ctx.log.warn(f"[ba_sniff] RESP {codec.proto_name(protocol)}: not a JSON envelope")
            return

        method = result.get("method", "?")
        inner = result.get("inner")
        resp_proto = result.get("protocol", protocol)
        pname = codec.proto_name(resp_proto)

        if inner is not None:
            self.count_decoded += 1
            self._update_profile(resp_proto, inner)
            ctx.log.alert(f"[ba_sniff] RESP {pname} decoded via {method}")
        else:
            self.count_undecoded += 1
            ctx.log.warn(
                f"[ba_sniff] RESP {pname}: UNDECODED ({method}) "
                f"cipher_len={result.get('cipher_len')} — send this line to Claude"
            )

        self._write_line({
            "dir": "resp",
            "protocol": resp_proto,
            "protocol_name": pname,
            "decode_method": method,
            "decoded": inner,
            "undecoded_detail": None if inner is not None else {
                "cipher_len": result.get("cipher_len"),
                "cipher_preview": result.get("cipher_preview"),
                "resp_preview": content[:120].decode("utf-8", "replace"),
            },
        })

    def done(self):
        ctx.log.alert(
            f"[ba_sniff] session end: {self.count_req} reqs, {self.count_resp} resps, "
            f"{self.count_decoded} decoded, {self.count_undecoded} undecoded. "
            f"profile -> {self.profile_path}"
        )


addons = [BASniff()]
