r"""
Inject any DLL into BlueArchive.exe -- Frida-free, pure-ctypes.

Generic version of the recon injector: give it a DLL path and it injects via
CreateRemoteThread + LoadLibraryW (Toolhelp for the PID lookup), then tails the
DLL's log (<dll-stem>.log next to the DLL) until it finishes.

Usage (repo root, x64 Python):
  python native\inject.py native\observe.dll
  python native\inject.py native\recon.dll --no-tail

Admin note: if OpenProcess fails with error 5, run the shell as Administrator.
"""
import argparse
import ctypes as C
import os
import sys
import time
from ctypes import wintypes as W

PROC = "BlueArchive.exe"
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_ALL_ACCESS = 0x1F0FFF
MEM_COMMIT_RESERVE = 0x3000
PAGE_READWRITE = 0x04


class PROCESSENTRY32W(C.Structure):
    _fields_ = [
        ("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
        ("th32DefaultHeapID", C.POINTER(C.c_ulong)), ("th32ModuleID", W.DWORD),
        ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
        ("pcPriClassBase", C.c_long), ("dwFlags", W.DWORD),
        ("szExeFile", W.WCHAR * 260),
    ]


def find_pid(name):
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
        e = PROCESSENTRY32W(); e.dwSize = C.sizeof(PROCESSENTRY32W)
        ok = k.Process32FirstW(snap, C.byref(e))
        while ok:
            if e.szExeFile.lower() == name.lower():
                return e.th32ProcessID
            ok = k.Process32NextW(snap, C.byref(e))
        return None
    finally:
        k.CloseHandle(snap)


def inject(pid, dll_path):
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
    th = k.CreateRemoteThread(h, None, 0, load_library, addr, 0, None)
    if not th:
        raise OSError(f"CreateRemoteThread failed, error {C.get_last_error()}")
    k.WaitForSingleObject(th, 15000)
    mod = W.DWORD(0)
    k.GetExitCodeThread(th, C.byref(mod))
    return mod.value != 0   # LoadLibraryW's (truncated) return; 0 = failed to load


def tail_log(path, seconds):
    print(f"[+] tailing {path} (Ctrl+C to stop)")
    deadline = time.time() + seconds
    seen = 0
    while time.time() < deadline:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(seen)
                chunk = fh.read()
                if chunk:
                    sys.stdout.write(chunk); sys.stdout.flush()
                    seen = fh.tell()
                    if ("FATAL" in chunk or "[observe] live." in chunk
                            or "[recon] done." in chunk or "[list] done." in chunk):
                        return
        time.sleep(0.3)


def main():
    ap = argparse.ArgumentParser(description="Inject a DLL into Blue Archive")
    ap.add_argument("dll", help="path to the DLL to inject")
    ap.add_argument("--proc", default=PROC)
    ap.add_argument("--no-tail", action="store_true")
    ap.add_argument("--tail-seconds", type=int, default=140)
    args = ap.parse_args()

    dll = os.path.abspath(args.dll)
    if not os.path.exists(dll):
        print(f"[-] {dll} missing. Build it first.")
        sys.exit(1)
    log = os.path.splitext(dll)[0] + ".log"

    pid = find_pid(args.proc)
    if pid is None:
        print(f"[-] '{args.proc}' not running. Launch the client first.")
        sys.exit(1)
    print(f"[+] target {args.proc} pid={pid}")

    if os.path.exists(log):
        try: os.remove(log)
        except OSError: pass

    if not inject(pid, dll):
        print("[-] injection reported failure (DLL did not load). Check x64 build / admin.")
        sys.exit(1)
    print(f"[+] injected {os.path.basename(dll)}")
    print(f"    Log: {log}")

    if not args.no_tail:
        tail_log(log, args.tail_seconds)


if __name__ == "__main__":
    main()
