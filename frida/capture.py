r"""
Production capture runner for ba-sniff.

Attaches to BlueArchive.exe, loads the Newtonsoft-deserialize hook, and stores
every MX packet in the GAME'S OWN data structure (raw decrypted JSON), organised
by protocol name. No format conversion here -- the exporter layer (future) maps
this canonical dump to SchaleDB / other trackers.

Outputs (under ../captures/, git-ignored):
  profile_latest.json          consolidated: {captured_at, region, protocols:{Name:obj}}
  profile_<timestamp>.json     snapshot written on each session end (progress history)
  raw_<timestamp>.jsonl        every packet as received (audit/debug)

Usage:
  ..\.venv\Scripts\python.exe capture.py           # one session (attach now, run until game closes / Ctrl+C)
  ..\.venv\Scripts\python.exe capture.py --watch    # stay resident: capture every time you launch the game
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import frida

# reuse the protocol-id -> name map from the sibling package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ba_codec import proto_name, TARGET_PROTOCOLS  # noqa: E402

PROC = "BlueArchive.exe"
AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent", "_capture.js")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "captures")
PROFILE_LATEST = os.path.join(OUT_DIR, "profile_latest.json")


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Capture:
    def __init__(self, out_dir=OUT_DIR, profile_latest=PROFILE_LATEST, region=None):
        # paths are parameterized so alternate clients (e.g. JP via the IFEO launcher)
        # can capture to their own profile without clobbering the default one.
        # region ("Global"/"JP") is stamped into the profile so the exporter tags it.
        self.out_dir = out_dir
        self.profile_latest = profile_latest
        self.region = region
        os.makedirs(self.out_dir, exist_ok=True)
        # seed from an existing profile so we accumulate across sessions
        self.protocols = {}
        if os.path.exists(self.profile_latest):
            try:
                self.protocols = json.load(open(self.profile_latest, encoding="utf-8")).get("protocols", {})
            except Exception:
                self.protocols = {}
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.raw_path = os.path.join(self.out_dir, f"raw_{stamp}.jsonl")
        self.session_stamp = stamp
        self.packets_this_session = 0

    def on_message(self, msg, data):
        t = msg.get("type")
        if t == "log":
            print(msg.get("payload", ""))
            return
        if t == "error":
            print("[JS ERROR]", msg.get("description"))
            if msg.get("stack"):
                print(msg["stack"])
            return
        p = msg.get("payload") or {}
        pt = p.get("type")
        if pt == "armed":
            print(f"[+] hook armed on {p.get('count')} deserialize method(s). Play the game; packets will be captured.")
        elif pt == "fatal":
            print(f"[!] hook could not arm: {p.get('reason')} -- a game update may have changed the JSON library.")
        elif pt == "packet":
            self._store_packet(p)

    def _store_packet(self, p):
        raw = p.get("json", "")
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return
        proto = obj.get("Protocol", obj.get("protocol"))
        name = proto_name(proto)
        # store the game's native structure, keyed by protocol name (latest wins)
        self.protocols[name] = obj
        # Region/version-agnostic alias: the login bundle (roster + all sub-responses) is
        # carried under a protocol number that differs across clients (Global 1019 =
        # Account_LoginSync, JP 1017). Detect it by content and alias to the stable name so
        # the exporter finds it regardless of which client captured it.
        if name != "Account_LoginSync" and isinstance(obj, dict) and "CharacterListResponse" in obj:
            self.protocols["Account_LoginSync"] = obj
        self.packets_this_session += 1
        flag = " *target*" if _is_target(proto) else ""
        print(f"[packet] {name} ({proto}) {len(raw)}B{flag}")
        # append raw for audit
        try:
            with open(self.raw_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": _now_iso(), "protocol": proto,
                                     "protocol_name": name, "obj": obj}, ensure_ascii=False) + "\n")
        except Exception as e:
            print("[!] raw write failed:", e)
        self._write_profile()

    def _write_profile(self):
        doc = {"captured_at": _now_iso(), "protocols": self.protocols}
        if self.region:
            doc["region"] = self.region
        try:
            with open(self.profile_latest, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            print("[!] profile write failed:", e)

    def snapshot(self):
        if self.packets_this_session == 0:
            return
        snap = os.path.join(self.out_dir, f"profile_{self.session_stamp}.json")
        with open(snap, "w", encoding="utf-8") as fh:
            json.dump({"captured_at": _now_iso(), "protocols": self.protocols}, fh, ensure_ascii=False, indent=2)
        print(f"[+] snapshot -> {snap}  ({len(self.protocols)} protocols, {self.packets_this_session} packets this session)")


def _is_target(proto):
    try:
        return int(proto) in TARGET_PROTOCOLS
    except (TypeError, ValueError):
        return False


def run_session(cap: Capture) -> bool:
    """Attach + capture until the game closes or Ctrl+C. Returns False on Ctrl+C."""
    try:
        session = frida.attach(PROC)
    except frida.ProcessNotFoundError:
        return None  # not running
    print(f"[+] attached to {PROC}")
    with open(AGENT, encoding="utf-8") as fh:
        code = fh.read()
    script = session.create_script(code)
    script.on("message", cap.on_message)

    ended = {"flag": False}
    session.on("detached", lambda *a: ended.__setitem__("flag", True))
    script.load()
    try:
        while not ended["flag"]:
            time.sleep(0.4)
        print("[+] game closed.")
        return True
    except KeyboardInterrupt:
        print("\n[+] stopped by user.")
        return False
    finally:
        cap.snapshot()
        try: session.detach()
        except Exception: pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true",
                    help="stay resident and capture every time the game launches")
    args = ap.parse_args()

    if not os.path.exists(AGENT):
        print(f"[-] {AGENT} not found. Build it:  cd agent && npm run build:capture")
        sys.exit(1)

    cap = Capture()
    if not args.watch:
        r = run_session(cap)
        if r is None:
            print(f"[-] '{PROC}' not running. Launch Blue Archive first (or use --watch).")
            sys.exit(1)
        return

    print("[watch] resident mode. Waiting for the game; Ctrl+C to quit.")
    try:
        while True:
            r = run_session(cap)
            if r is False:      # user Ctrl+C during a session
                break
            if r is None:       # game not running yet
                time.sleep(3)
                continue
            print("[watch] waiting for the game to launch again...")
            time.sleep(3)
    except KeyboardInterrupt:
        print("\n[watch] bye.")


if __name__ == "__main__":
    main()
