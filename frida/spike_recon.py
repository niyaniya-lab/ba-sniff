"""
Spike step 2 runner: load the compiled frida-il2cpp-bridge agent into the
running BlueArchive.exe and print its recon output (candidate crypto/packet
classes + methods).

Run with the game running (title or lobby):
    ..\.venv\Scripts\python.exe spike_recon.py
"""
import os
import sys
import time
import frida

PROC = "BlueArchive.exe"
AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent", "_agent.js")

done = {"flag": False}

def on_message(msg, data):
    t = msg.get("type")
    if t == "log":
        line = msg.get("payload", "")
        print(line)
        if "recon done" in str(line):
            done["flag"] = True
    elif t == "send":
        p = msg.get("payload")
        print("[send]", p)
        if isinstance(p, dict) and p.get("type") == "done":
            done["flag"] = True
    elif t == "error":
        print("[JS ERROR]", msg.get("description"))
        stack = msg.get("stack")
        if stack:
            print(stack)

def main():
    if not os.path.exists(AGENT):
        print(f"[-] {AGENT} not found. Build it: cd agent && npm run build")
        sys.exit(1)
    try:
        session = frida.attach(PROC)
    except frida.ProcessNotFoundError:
        print(f"[-] '{PROC}' not running. Launch Blue Archive first.")
        sys.exit(1)
    print(f"[+] attached to {PROC}; loading agent (IL2CPP enumeration can take ~10-40s)...")
    with open(AGENT, "r", encoding="utf-8") as fh:
        code = fh.read()
    script = session.create_script(code)
    script.on("message", on_message)
    script.load()

    # wait until the agent signals completion, or 120s cap
    deadline = time.time() + 120
    while not done["flag"] and time.time() < deadline:
        time.sleep(0.3)
    if not done["flag"]:
        print("[!] no 'recon done' within 120s - enumeration may still be running or errored above.")
    session.detach()
    print("[+] detached.")

if __name__ == "__main__":
    main()
