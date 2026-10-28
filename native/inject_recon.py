r"""
Inject recon.dll into BlueArchive.exe -- a Frida-free, pure-ctypes injector.

This is deliberately dependency-free (no `import frida`) because the whole point
of the native effort is to shed the heavy Frida runtime. PID lookup uses the
Win32 Toolhelp snapshot API directly; injection is the same CreateRemoteThread +
LoadLibraryW dance as gadget/inject_gadget.py.

The recon DLL is READ-ONLY (it only reads IL2CPP metadata) so this cannot harm
the game. After injection the DLL writes native/recon.log next to itself; this
script prints that path and tails it for you.

Usage (run from the repo root, or anywhere -- paths are resolved absolutely):
  python native\inject_recon.py            # inject, then tail recon.log
  python native\inject_recon.py --no-tail  # inject only

Admin note: if OpenProcess fails with error 5, run the shell as Administrator.
"""
import argparse
import ctypes as C
import os
import sys
import time
from ctypes import wintypes as W

HERE = os.path.dirname(os.path.abspath(__file__))
DLL = os.path.join(HERE, "recon.dll")
LOG = os.path.join(HERE, "recon.log")
PROC = "BlueArchive.exe"

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_ALL_ACCESS = 0x1F0FFF
MEM_COMMIT_RESERVE = 0x3000
PAGE_READWRITE = 0x04


class PROCESSENTRY32W(C.Structure):
    _fields_ = [
        ("dwSize", W.DWORD),
        ("cntUsage", W.DWORD),
        ("th32ProcessID", W.DWORD),
        ("th32DefaultHeapID", C.POINTER(C.c_ulong)),
        ("th32ModuleID", W.DWORD),
        ("cntThreads", W.DWORD),
        ("th32ParentProcessID", W.DWORD),
        ("pcPriClassBase", C.c_long),
        ("dwFlags", W.DWORD),
        ("szExeFile", W.WCHAR * 260),
    ]


def find_pid(name):
    """Enumerate processes via Toolhelp and return the PID matching `name`."""
    k = C.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = W.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    k.Process32FirstW.argtypes = [W.HANDLE, C.POINTER(PROCESSENTRY32W)]
    k.Process32NextW.argtypes = [W.HANDLE, C.POINTER(PROCESSENTRY32W)]
    k.CloseHandle.argtypes = [W.HANDLE]

    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == W.HANDLE(-1).value:
        raise OSError(f"CreateToolhelp32Snapshot failed, error {C.get_last_error()}")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = C.sizeof(PROCESSENTRY32W)
        ok = k.Process32FirstW(snap, C.byref(entry))
        while ok:
            if entry.szExeFile.lower() == name.lower():
                return entry.th32ProcessID
            ok = k.Process32NextW(snap, C.byref(entry))
        return None
    finally:
        k.CloseHandle(snap)


def inject(pid, dll_path):
    """CreateRemoteThread(LoadLibraryW, dll_path) -- load our DLL into the target."""
    k = C.WinDLL("kernel32", use_last_error=True)
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
    k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    k.GetExitCodeThread.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]

    h = k.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    if not h:
        raise OSError(f"OpenProcess({pid}) failed, error {C.get_last_error()} -- try Administrator")

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
    # LoadLibraryW's return (module base, truncated to 32 bits) lands in the
    # thread exit code; 0 means the DLL failed to load.
    return module_handle.value != 0


def tail_log(path, seconds):
    """Print recon.log as it grows, for `seconds`, then stop."""
    print(f"[+] tailing {path} (Ctrl+C to stop early)")
    deadline = time.time() + seconds
    seen = 0
    # The DLL truncates recon.log on load; wait for it to (re)appear fresh.
    while time.time() < deadline:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(seen)
                chunk = fh.read()
                if chunk:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    seen = fh.tell()
                    if "[recon] done." in chunk or "FATAL" in chunk:
                        return
        time.sleep(0.3)


def main():
    ap = argparse.ArgumentParser(description="Inject read-only recon.dll into Blue Archive")
    ap.add_argument("--proc", default=PROC, help="target process name")
    ap.add_argument("--no-tail", action="store_true", help="inject only; do not tail recon.log")
    ap.add_argument("--tail-seconds", type=int, default=140, help="how long to tail (DLL waits up to 120s for the runtime)")
    args = ap.parse_args()

    if not os.path.exists(DLL):
        print(f"[-] {DLL} missing. Build it first: native\\build_recon.bat")
        sys.exit(1)

    pid = find_pid(args.proc)
    if pid is None:
        print(f"[-] '{args.proc}' not running. Launch the Global client first.")
        sys.exit(1)
    print(f"[+] target {args.proc} pid={pid}")

    # Remove a stale log so the tail only shows this run's output.
    if os.path.exists(LOG):
        try:
            os.remove(LOG)
        except OSError:
            pass

    if not inject(pid, DLL):
        print("[-] injection reported failure (DLL did not load). Check x64 build / admin.")
        sys.exit(1)
    print("[+] recon.dll injected.")
    print(f"    Log: {LOG}")

    if not args.no_tail:
        tail_log(LOG, args.tail_seconds)


if __name__ == "__main__":
    main()
