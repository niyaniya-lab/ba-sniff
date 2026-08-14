r"""
IFEO Debugger shim for BlueArchive JP.  NOT run directly — Windows invokes it when
the Yostar launcher starts the game (see jp_capture.py, which sets the IFEO key):

    python.exe jp_shim.py  <BlueArchive.exe path>  <launcher args...>

Why this exists: JP ships XIGNCODE3 (kernel anti-cheat). Attaching to a running
instance is denied even as admin; you can ONLY inject at process creation, before
XIGNCODE arms. A bare frida.spawn self-exits ~20s because it lacks the launcher's
environment. IFEO lets the real launcher drive the launch (env intact) while we
inject at creation. See docs/FINDINGS.md §11b.

Flow: (0) delete our own IFEO key (one-shot; normal launching restored no matter what),
(1) frida.spawn the game suspended with the launcher's args + cwd (inherits the launcher
env), (2) inject the HOOK-ONLY capture agent before resume, (3) feed packets into the
shared Capture pipeline -> captures/profile_jp_latest.json.

IMPORTANT: JP uses the lightweight hook-only agent (_capture.js) — NOT the full refresh
agent. The full agent's per-frame pump + a gc.choose name-resolution sweep during the
game's LOADING phase starves/crashes the client. So the shim does passive capture only;
JP (Japanese) name resolution is intentionally left out here (English names resolve from
the shared cross-region UniqueId cache; a safe in-lobby JP-name pass is a future add).
"""
import sys
import os
import json
import time
import subprocess
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))          # .../frida
BA_ROOT = os.path.dirname(HERE)
AGENT = os.path.join(HERE, "agent", "_capture.js")         # hook-only; lightweight (no pump/gc)
CAPTURES = os.path.join(BA_ROOT, "captures")
JP_PROFILE = os.path.join(CAPTURES, "profile_jp_latest.json")
STATUS = os.path.join(CAPTURES, "jp_capture_status.json")
KEY = r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\BlueArchive.exe"

status = {"argv": list(sys.argv), "phase": "start"}
def wstatus(**kw):
    status.update(kw)
    try:
        os.makedirs(CAPTURES, exist_ok=True)
        json.dump(status, open(STATUS, "w", encoding="utf-8"), indent=1)
    except Exception:
        pass
wstatus()

# one-shot: remove our IFEO key immediately so normal launching is restored no matter what
try:
    subprocess.run(["reg", "delete", KEY, "/f"], capture_output=True, text=True)
    status["ifeo_removed"] = True
except Exception as e:
    status["ifeo_removed"] = "err " + repr(e)

if len(sys.argv) < 2:
    wstatus(phase="error", error="no target exe in argv (not launched via IFEO?)")
    sys.exit(1)
exe = sys.argv[1]
args = sys.argv[2:]

import frida  # pip frida BEFORE BA_ROOT goes on the path (avoids the local frida/ dir clash)
sys.path.insert(0, BA_ROOT)
spec = importlib.util.spec_from_file_location("ba_capture", os.path.join(HERE, "capture.py"))
ba_capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ba_capture)
Capture = ba_capture.Capture

cap = Capture(out_dir=CAPTURES, profile_latest=JP_PROFILE, region="JP")
dev = frida.get_local_device()
detached = []
try:
    spid = dev.spawn([exe] + args, cwd=os.path.dirname(exe))
    wstatus(phase="spawned", pid=spid, target_args=args)
    session = dev.attach(spid)
    session.on("detached", lambda reason, *a: detached.append(reason))
    sc = session.create_script(open(AGENT, encoding="utf-8").read())
    sc.on("message", cap.on_message)
    sc.load()
    dev.resume(spid)
    wstatus(phase="capturing — log in and reach the lobby")
    for t in range(1800):  # up to 30 min of play; passive capture only
        time.sleep(1)
        if detached:
            break
        if t % 3 == 0:
            wstatus(phase="capturing", secs=t, packets=cap.packets_this_session,
                    protocols=sorted(cap.protocols.keys()),
                    has_loginsync=("Account_LoginSync" in cap.protocols))
    cap.snapshot()
    wstatus(phase="ended", detached=(detached[-1] if detached else "timeout"),
            packets=cap.packets_this_session, has_loginsync=("Account_LoginSync" in cap.protocols),
            profile=JP_PROFILE)
except Exception as e:
    wstatus(phase="error", error=repr(e))
