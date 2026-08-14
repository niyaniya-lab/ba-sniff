r"""
Production capture + on-demand refresh.

Attaches to a running, logged-in BlueArchive.exe, captures every MX packet into
the native game structure (same storage as capture.py), and lets you trigger an
on-demand refresh — the tool asks the client to re-fetch a protocol so you get
current data WITHOUT navigating in-game (e.g. after a gacha pull).

  ..\.venv\Scripts\python.exe refresh.py
    [Enter]         -> refresh (default: full account sync)
    tasks           -> list available *NetworkTask names
    <TaskName ...>  -> refresh specific task(s), e.g. "CampaignListNetworkTask"
    q               -> quit

Output goes to ../captures/profile_latest.json (+ snapshots), same as capture.py.
"""
import os
import sys
import frida

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from capture import Capture  # reuse native-structure storage  # noqa: E402

PROC = "BlueArchive.exe"
AGENT = os.path.join(HERE, "agent", "_capture_refresh.js")

# Full account refresh in one call. AccountLoginSyncNoPart is the light login-sync
# variant that CAN be fired cold (unlike AccountLoginSync, which needs login
# context) and returns the entire Account_LoginSync payload: roster, currency,
# campaign clears, momotalk, equipment, gear, echelons, etc. Ideal "sync now".
DEFAULT_TASKS = ["AccountLoginSyncNoPartNetworkTask"]
TASKS_OUT = os.path.join(os.path.dirname(HERE), "captures", "tasks.txt")


def main():
    if not os.path.exists(AGENT):
        print(f"[-] {AGENT} not found. Build it:  cd agent && npm run build:refresh")
        sys.exit(1)
    try:
        session = frida.attach(PROC)
    except frida.ProcessNotFoundError:
        print(f"[-] '{PROC}' not running. Launch Blue Archive and log in first.")
        sys.exit(1)

    cap = Capture()

    def on_message(msg, data):
        t = msg.get("type")
        if t in ("log", "error"):
            cap.on_message(msg, data)
            return
        p = msg.get("payload") or {}
        pt = p.get("type")
        if pt in ("armed", "packet"):
            cap.on_message(msg, data)
        elif pt == "log_line":
            print(p.get("text", ""))
        elif pt == "dbg":
            print(f"[dbg] {p.get('task')} :: {p.get('step')}" + (f" ({p.get('extra')})" if p.get('extra') is not None else ""))
        elif pt == "fired":
            print(f"[refresh] fired {p.get('task')} — waiting for reply...")
        elif pt == "fire_error":
            print(f"[refresh] FAILED {p.get('task')}: {p.get('error')}")

    print(f"[+] attached to {PROC}; loading capture+refresh agent...")
    with open(AGENT, encoding="utf-8") as fh:
        code = fh.read()
    script = session.create_script(code)
    script.on("message", on_message)
    script.load()
    exports = script.exports_sync

    print("\nCommands:  [Enter]=refresh (full sync)   tasks=list   <TaskName...>=refresh specific   q=quit")
    try:
        while True:
            cmd = input("> ").strip()
            if cmd.lower() in ("q", "quit", "exit"):
                break
            if cmd.lower() in ("tasks", "list"):
                names = exports.list_tasks()
                with open(TASKS_OUT, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(names))
                print(f"[{len(names)} *NetworkTask classes] written to {TASKS_OUT}")
                continue
            tasks = cmd.split() if cmd else DEFAULT_TASKS
            exports.refresh(tasks)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        cap.snapshot()
        try: session.detach()
        except Exception: pass
        print("[+] detached.")


if __name__ == "__main__":
    main()
