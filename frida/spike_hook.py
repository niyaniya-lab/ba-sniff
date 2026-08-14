"""
Spike step 3 runner: load the JSON-deserialize hook, capture plaintext packet
JSON, and write any account-data hits to captures/frida_hits.jsonl.

Run with the game running (ideally start this AT the title screen, then log in /
navigate so the account packets fire while we're attached):
    ..\.venv\Scripts\python.exe spike_hook.py
"""
import os
import sys
import time
import json
import frida

PROC = "BlueArchive.exe"
HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent", "_hook.js")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "captures")
OUT = os.path.join(OUT_DIR, "frida_hits.jsonl")

os.makedirs(OUT_DIR, exist_ok=True)
state = {"armed": False, "hits": 0}

def on_message(msg, data):
    t = msg.get("type")
    if t == "log":
        print(msg.get("payload", ""))
    elif t == "error":
        print("[JS ERROR]", msg.get("description"))
        if msg.get("stack"):
            print(msg["stack"])
    elif t == "send":
        p = msg.get("payload") or {}
        pt = p.get("type")
        if pt == "armed":
            state["armed"] = True
            print(f"[+] hook armed on {p.get('count')} method(s). Navigate the game now (lobby -> students -> campaign).")
        elif pt == "hit":
            state["hits"] += 1
            head = p.get("head", "")
            with open(OUT, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(p, ensure_ascii=False) + "\n")
            print(f"\n[HIT #{p.get('n')}] {p.get('method')} len={p.get('len')}")
            print("   " + head[:600].replace("\n", " "))
        elif pt == "done":
            pass

def main():
    if not os.path.exists(HOOK):
        print(f"[-] {HOOK} not found. Build it: cd agent && npm run build:hook")
        sys.exit(1)
    try:
        session = frida.attach(PROC)
    except frida.ProcessNotFoundError:
        print(f"[-] '{PROC}' not running. Launch Blue Archive first.")
        sys.exit(1)
    print(f"[+] attached to {PROC}; loading hook...")
    with open(HOOK, "r", encoding="utf-8") as fh:
        code = fh.read()
    script = session.create_script(code)
    script.on("message", on_message)
    script.load()
    print(f"[+] Capturing. Writing hits -> {OUT}")
    print("[+] Log in / open student list / open a campaign, then press Ctrl+C here when done.")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n[+] stopping. total account hits: {state['hits']}")
    finally:
        try: session.detach()
        except Exception: pass

if __name__ == "__main__":
    main()
