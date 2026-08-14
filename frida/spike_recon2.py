"""
Recon runner that writes ALL agent output to captures/recon2.txt so it can be
analyzed offline (the console output is too long to read live).

Run with the game running + logged into the lobby:
    ..\.venv\Scripts\python.exe spike_recon2.py
Then tell Claude it's done; it reads captures/recon2.txt.
"""
import os
import sys
import time
import frida

PROC = "BlueArchive.exe"
AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent", "_agent.js")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "captures")
OUT = os.path.join(OUT_DIR, "recon2.txt")

os.makedirs(OUT_DIR, exist_ok=True)
done = {"flag": False}
fh = open(OUT, "w", encoding="utf-8")

def on_message(msg, data):
    t = msg.get("type")
    if t == "log":
        fh.write(str(msg.get("payload", "")) + "\n")
    elif t == "error":
        fh.write("[JS ERROR] " + str(msg.get("description")) + "\n")
        if msg.get("stack"):
            fh.write(str(msg["stack"]) + "\n")
    elif t == "send":
        p = msg.get("payload") or {}
        if isinstance(p, dict) and p.get("type") == "done":
            done["flag"] = True

def main():
    if not os.path.exists(AGENT):
        print(f"[-] {AGENT} not found. Build it: cd agent && npm run build:recon2")
        sys.exit(1)
    try:
        session = frida.attach(PROC)
    except frida.ProcessNotFoundError:
        print(f"[-] '{PROC}' not running. Launch Blue Archive and log in first.")
        sys.exit(1)
    print(f"[+] attached; running recon, writing -> {OUT}")
    with open(AGENT, encoding="utf-8") as f:
        code = f.read()
    script = session.create_script(code)
    script.on("message", on_message)
    # console.log from the agent goes to the LOG handler, not on('message')
    script.set_log_handler(lambda level, text: (fh.write(str(text) + "\n"), fh.flush()))
    script.load()
    deadline = time.time() + 180
    while not done["flag"] and time.time() < deadline:
        time.sleep(0.3)
    fh.flush(); fh.close()
    try: session.detach()
    except Exception: pass
    print(f"[+] done. {os.path.getsize(OUT)} bytes written to {OUT}")

if __name__ == "__main__":
    main()
