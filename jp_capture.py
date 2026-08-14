r"""
JP capture launcher (Blue Archive JP / Yostar).

JP ships XIGNCODE3 anti-cheat, so we can't attach to a running client — we must
inject at process creation via an IFEO Debugger shim. This script:
  1. self-elevates (writing the IFEO key needs admin),
  2. arms the IFEO key so Windows routes the game's launch through frida/jp_shim.py,
  3. tells you to press Launch in the Yostar launcher and log in,
  4. waits for the account to be captured (shim writes captures/profile_jp_latest.json),
  5. removes the IFEO key (the shim self-cleans too; this is a safety net),
  6. runs the exporter -> captures/export_jp/.

Usage (normally via launch_jp.bat):  .venv\Scripts\python.exe jp_capture.py

WARNING: JP has anti-cheat. Injection is currently undetected but this is
anti-cheat-adjacent — use at your own risk / on an account you accept risk on.
"""
import ctypes
import os
import sys
import json
import time
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
SHIM = os.path.join(HERE, "frida", "jp_shim.py")
KEY = r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\BlueArchive.exe"
CAPTURES = os.path.join(HERE, "captures")
STATUS = os.path.join(CAPTURES, "jp_capture_status.json")
JP_PROFILE = os.path.join(CAPTURES, "profile_jp_latest.json")


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _reg(*a):
    return subprocess.run(["reg", *a], capture_output=True, text=True)


def main():
    if not _is_admin():
        # relaunch elevated (setting the IFEO key needs admin, and the game runs elevated)
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, '"%s"' % os.path.abspath(__file__), None, 1)
        return
    if not os.path.exists(SHIM):
        print("[-] missing", SHIM); return
    if not os.path.exists(os.path.join(HERE, "frida", "agent", "_capture_refresh.js")):
        print("[-] agent not built. cd frida/agent && npm run build:refresh"); return

    # fresh status so we can tell a stale run from this one
    try:
        os.makedirs(CAPTURES, exist_ok=True)
        if os.path.exists(STATUS):
            os.remove(STATUS)
    except Exception:
        pass

    dbg = '"%s" "%s"' % (PY, SHIM)
    _reg("add", KEY, "/v", "Debugger", "/t", "REG_SZ", "/d", dbg, "/f")
    print("=" * 64)
    print("  ARMED.  Now press LAUNCH in the Blue Archive JP (Yostar) launcher.")
    print("  Log in and reach the lobby. A shim console will flash first — normal.")
    print("=" * 64)
    print("Waiting for capture (Ctrl+C to abort)...")
    captured = False
    try:
        for _ in range(900):  # ~30 min window
            time.sleep(2)
            try:
                st = json.load(open(STATUS, encoding="utf-8"))
            except Exception:
                continue
            phase = st.get("phase", "")
            # require fresh packets this session (the profile is seeded from prior runs, so
            # has_loginsync alone can read true before this login actually flushes).
            if st.get("has_loginsync") and st.get("packets", 0) >= 5:
                print("[+] account captured. You can keep playing or close the game.")
                captured = True
                break
            if phase.startswith("error"):
                print("[!] shim error:", st.get("error")); break
    except KeyboardInterrupt:
        print("\n[+] aborted.")
    finally:
        # safety net: the shim self-removes the key on fire, but ensure it's gone
        _reg("delete", KEY, "/f")

    if captured:
        print("[*] exporting JP account...")
        subprocess.run([PY, os.path.join(HERE, "ba_export.py"), JP_PROFILE])
        print("[+] done -> captures/export_jp/")
    else:
        print("[-] no account captured (launcher not pressed, or aborted).")
    try:
        input("Press Enter to close.")
    except EOFError:
        pass


if __name__ == "__main__":
    main()
