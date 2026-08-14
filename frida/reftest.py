r"""
Headless refresh tester (for autonomous iteration): attaches, loads the
capture+refresh agent, fires the given task(s), waits, and writes a summary of
what fired / errored / which protocols replied to captures/reftest.txt.

    ..\.venv\Scripts\python.exe reftest.py [Wait] [TaskName ...]
"""
import os
import sys
import time
import json
import frida

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # ba-sniff root, for ba_codec
from ba_codec import proto_name  # noqa: E402

PROC = "BlueArchive.exe"
AGENT = os.path.join(HERE, "agent", "_capture_refresh.js")
OUT = os.path.join(os.path.dirname(HERE), "captures", "reftest.txt")

argv = sys.argv[1:]
wait = 9.0
if argv and argv[0].replace(".", "", 1).isdigit():
    wait = float(argv[0]); argv = argv[1:]
tasks = argv or ["CampaignListNetworkTask"]

lines = []
packets = []
fired = []
errors = []
misc = []

def log(s):
    lines.append(s)

def on_message(msg, data):
    t = msg.get("type")
    if t == "error":
        misc.append("JS ERROR " + str(msg.get("description")))
        return
    if t == "log":
        return
    p = msg.get("payload") or {}
    pt = p.get("type")
    if pt == "packet":
        try:
            obj = json.loads(p.get("json", ""))
            pr = obj.get("Protocol", obj.get("protocol"))
            packets.append((proto_name(pr), p.get("len")))
        except Exception:
            packets.append(("?", p.get("len")))
    elif pt == "fired":
        fired.append(p.get("task"))
    elif pt == "fire_error":
        errors.append(f"{p.get('task')}: {p.get('error')}")
    elif pt == "dbg":
        misc.append(f"dbg {p.get('task')} {p.get('step')} {p.get('extra')}")
    elif pt == "log_line":
        misc.append(p.get("text", ""))

def main():
    session = frida.attach(PROC)
    with open(AGENT, encoding="utf-8") as fh:
        code = fh.read()
    script = session.create_script(code)
    script.on("message", on_message)
    script.load()
    time.sleep(0.5)
    script.exports_sync.refresh(tasks)
    time.sleep(wait)
    session.detach()

    log("== reftest ==")
    log("requested tasks: " + ", ".join(tasks))
    log("fired: " + (", ".join(fired) if fired else "(none)"))
    log("errors: " + (", ".join(errors) if errors else "(none)"))
    log("misc: " + (" | ".join(misc) if misc else "(none)"))
    log(f"packets captured ({len(packets)}):")
    for name, ln in packets:
        log(f"  {name}  {ln}B")
    text = "\n".join(lines)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)

if __name__ == "__main__":
    main()
