r"""
Listen-mode client for the injected frida-gadget.

After `inject_gadget.py --mode listen` has loaded the gadget into BlueArchive.exe,
this connects to the gadget's in-process socket, loads the same _capture.js agent
the normal (attach-based) capture uses, and reuses capture.py's Capture pipeline
so packets land in ../captures exactly as usual.

Usage (from repo root):
  .venv\Scripts\python.exe gadget\gadget_capture.py [port]   # default 27042
"""
import importlib.util
import os
import sys
import time

import frida  # library (gadget\ has no frida subdir, so no shadowing)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CAP_PY = os.path.join(ROOT, "frida", "capture.py")
AGENT = os.path.join(ROOT, "frida", "agent", "_capture.js")


def _load_capture_class():
    # Reuse Capture (message handling + persistence) from the existing capture.py
    # without importing it as a package (its dir is named 'frida' and would shadow
    # the library). frida is already imported above, so capture.py's own
    # `import frida` resolves to the cached library.
    spec = importlib.util.spec_from_file_location("ba_capture_impl", CAP_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Capture


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 27042
    if not os.path.exists(AGENT):
        print(f"[-] {AGENT} not found. Build it: cd frida\\agent && npm run build:capture")
        sys.exit(1)

    Capture = _load_capture_class()

    device = frida.get_device_manager().add_remote_device(f"127.0.0.1:{port}")
    print(f"[+] connecting to gadget at 127.0.0.1:{port}")
    try:
        session = device.attach("Gadget")
    except Exception:
        procs = device.enumerate_processes()
        if not procs:
            print("[-] gadget not reachable. Did inject_gadget.py --mode listen run first?")
            sys.exit(1)
        session = device.attach(procs[0].pid)
    print("[+] attached to gadget session")

    cap = Capture(region="Global")
    with open(AGENT, encoding="utf-8") as fh:
        code = fh.read()
    script = session.create_script(code)
    script.on("message", cap.on_message)

    ended = {"flag": False}
    session.on("detached", lambda *a: ended.__setitem__("flag", True))
    script.load()
    print("[+] agent loaded; capturing. Ctrl+C to stop.")
    try:
        while not ended["flag"]:
            time.sleep(0.4)
        print("[+] gadget session ended.")
    except KeyboardInterrupt:
        print("\n[+] stopped by user.")
    finally:
        cap.snapshot()
        try:
            session.detach()
        except Exception:
            pass


if __name__ == "__main__":
    main()
