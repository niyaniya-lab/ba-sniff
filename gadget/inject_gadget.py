r"""
Load frida-gadget.dll into BlueArchive.exe for experimentation.

This is a plain Windows DLL injector (CreateRemoteThread + LoadLibraryW). It does
NOT use frida.attach to load the gadget -- that would run two Frida runtimes in
one process. frida is used only to look up the PID (a passive operation).

It writes frida-gadget.config next to the DLL first (the gadget reads the config
file named after itself), then injects.

Modes:
  listen  -- gadget opens a socket in-process; connect with gadget_capture.py.
  script  -- gadget auto-runs a JS from disk (no external client). See README:
             _capture.js send()s to a client, so in script mode its packets go to
             the gadget log, not to ../captures. Use this to verify the hook arms
             standalone; real standalone persistence needs an adapted agent.

Usage (run from the repo root, x64 Python from .venv):
  .venv\Scripts\python.exe gadget\inject_gadget.py --mode listen --port 27042
  .venv\Scripts\python.exe gadget\inject_gadget.py --mode script --script frida\agent\_capture.js

Admin note: if OpenProcess fails, run the shell as Administrator.
"""
import argparse
import ctypes as C
import json
import os
import sys
from ctypes import wintypes as W

import frida  # passive PID lookup only

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DLL = os.path.join(HERE, "frida-gadget.dll")
# The gadget reads a config file named after the DLL: frida-gadget.dll -> frida-gadget.config
CONFIG = os.path.join(HERE, "frida-gadget.config")
PROC = "BlueArchive.exe"


def find_pid(name):
    for p in frida.get_local_device().enumerate_processes():
        if p.name.lower() == name.lower():
            return p.pid
    return None


def write_config(mode, port, script_path):
    if mode == "listen":
        cfg = {"interaction": {"type": "listen", "address": "127.0.0.1", "port": port,
                               "on_port_conflict": "pick-next", "on_load": "resume"}}
    else:
        cfg = {"interaction": {"type": "script", "path": os.path.abspath(script_path),
                               "on_change": "reload"}}
    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    print(f"[+] wrote {os.path.basename(CONFIG)}: {json.dumps(cfg['interaction'])}")


def inject(pid, dll_path):
    k = C.WinDLL("kernel32", use_last_error=True)
    PROCESS_ALL_ACCESS = 0x1F0FFF
    MEM_COMMIT_RESERVE = 0x3000
    PAGE_READWRITE = 0x04

    k.OpenProcess.restype = W.HANDLE
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.VirtualAllocEx.restype = C.c_void_p
    k.VirtualAllocEx.argtypes = [W.HANDLE, C.c_void_p, C.c_size_t, W.DWORD, W.DWORD]
    k.WriteProcessMemory.restype = W.BOOL
    k.WriteProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
    k.GetModuleHandleW.restype = W.HMODULE
    k.GetModuleHandleW.argtypes = [W.LPCWSTR]
    k.GetProcAddress.restype = C.c_void_p
    k.GetProcAddress.argtypes = [W.HMODULE, C.c_char_p]
    k.CreateRemoteThread.restype = W.HANDLE
    k.CreateRemoteThread.argtypes = [W.HANDLE, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    k.WaitForSingleObject.restype = W.DWORD
    k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    k.GetExitCodeThread.restype = W.BOOL
    k.GetExitCodeThread.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]

    h = k.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    if not h:
        raise OSError(f"OpenProcess({pid}) failed, error {C.get_last_error()} -- try running as Administrator")

    buf = C.create_string_buffer((dll_path + "\0").encode("utf-16-le"))
    addr = k.VirtualAllocEx(h, None, len(buf), MEM_COMMIT_RESERVE, PAGE_READWRITE)
    if not addr:
        raise OSError(f"VirtualAllocEx failed, error {C.get_last_error()}")
    written = C.c_size_t(0)
    if not k.WriteProcessMemory(h, addr, buf, len(buf), C.byref(written)):
        raise OSError(f"WriteProcessMemory failed, error {C.get_last_error()}")

    load_library = k.GetProcAddress(k.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
    if not load_library:
        raise OSError("GetProcAddress(LoadLibraryW) failed")

    th = k.CreateRemoteThread(h, None, 0, load_library, addr, 0, None)
    if not th:
        raise OSError(f"CreateRemoteThread failed, error {C.get_last_error()}")
    k.WaitForSingleObject(th, 15000)
    module_handle = W.DWORD(0)
    k.GetExitCodeThread(th, C.byref(module_handle))
    # LoadLibraryW returns the module base (truncated to 32 bits in the thread exit code);
    # 0 means the DLL failed to load.
    return module_handle.value != 0


def main():
    ap = argparse.ArgumentParser(description="Inject frida-gadget.dll into Blue Archive")
    ap.add_argument("--mode", choices=["listen", "script"], default="listen")
    ap.add_argument("--port", type=int, default=27042, help="listen-mode port")
    ap.add_argument("--script", default=os.path.join(ROOT, "frida", "agent", "_capture.js"),
                    help="script-mode JS path")
    ap.add_argument("--proc", default=PROC, help="target process name")
    args = ap.parse_args()

    if not os.path.exists(DLL):
        print(f"[-] {DLL} missing. Build it: run rebuild-patched-frida.ps1 -BuildCore (gadget enabled).")
        sys.exit(1)

    pid = find_pid(args.proc)
    if pid is None:
        print(f"[-] '{args.proc}' not running. Launch the game first.")
        sys.exit(1)
    print(f"[+] target {args.proc} pid={pid}")

    write_config(args.mode, args.port, args.script)
    ok = inject(pid, DLL)
    if not ok:
        print("[-] injection reported failure (DLL did not load). Check bitness/admin.")
        sys.exit(1)
    print("[+] gadget injected.")
    if args.mode == "listen":
        print(f"    Now run:  .venv\\Scripts\\python.exe gadget\\gadget_capture.py {args.port}")
    else:
        print("    Script mode active; watch the gadget log / game for hook output.")


if __name__ == "__main__":
    main()
