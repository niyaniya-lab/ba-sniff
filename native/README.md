# native/ — Frida-free capture for Blue Archive (Global)

A small native (C++) observer that captures Blue Archive's already-decrypted
network JSON by hooking the client's own `Newtonsoft.Json` deserializer — the
same data the Frida agent captures, with no Frida, no JS runtime, and no keys.
Everything here is hand-written and tested against ground truth before it ever
touches the game.

**Global only.** JP ships Wellbia XIGNCODE3, which denies injection; see the
repo's `docs/FINDINGS.md` §11b. Injecting into any client is a ToS risk — this is
personal tooling for your own account.

## How it works (the short version)

Server→client traffic is encrypted JSON. The client decrypts it in native code,
then hands the **plaintext** to `Newtonsoft.Json.JsonConvert.DeserializeObject`.
We stand at that call, read the string as it goes by, and let the original run
unchanged. Because we read *after* decryption and match on field names (not keys
or protocol numbers), we need no keys and survive client updates.

To stand there we install an **inline hook**: overwrite the function's first
bytes with a jump to our code, after copying the displaced instructions to a
**trampoline** that jumps back — so the original still runs. Two supporting
tools make that safe:

- **`lengthdis.h`** — an x86-64 instruction length + relocation decoder, so we
  displace whole instructions and fix up the position-dependent ones.
- **`hook.h`** — the trampoline engine (steal ≥14 bytes, relocate, patch with
  threads suspended).

`observe.cpp` ties it together via `il2cpp_min.h` (a thin wrapper over the
`il2cpp_*` API that `GameAssembly.dll` exports). It also **auto-fetches the id
card**: after login it calls `NetworkTaskManager.Request(new FriendListNetworkTask)`
through the runtime, so no manual friend-list navigation is needed.

**Wondering how you find which class/method to call** (e.g. how we knew about
`FriendListNetworkTask`)? See **`DISCOVERY.md`** — dumping the game's IL2CPP
metadata, reading signatures, and the runtime-enumeration alternative.

## Files

| File | Role |
|---|---|
| `lengthdis.h` | x86-64 length + relocation-field decoder (`ld_length`, `ld_decode`) |
| `hook.h` | inline-hook / trampoline engine (`install_hook`) |
| `il2cpp_min.h` | resolve + wrap the `il2cpp_*` API (reflection, string read, invoke, heap-walk) |
| `observe.cpp` | the observer DLL: hook `DeserializeObject`, filter, log, auto-fetch id card |
| `recon.cpp` | one-off recon probe (read-only): confirm the runtime is reachable |
| `build_profile.py` | fold `observe_raw.jsonl` into `captures/profile_latest.json` (reuses `capture.py`) |
| `inject.py` | pure-ctypes DLL injector (no Frida) |
| `build.bat` | `build.bat <src.cpp> [out.dll|out.exe]` — x64 MSVC build |
| `ld_test.py` / `ld_test_dll.cpp` | length-decoder test vs capstone over real system DLLs |
| `hook_test.cpp` | offline self-test of the trampoline engine |

Generated (untracked): `*.dll`, `*.exe`, `observe.log`, `observe_raw.jsonl`, `recon.log`.

## Build

Requires Visual Studio 2022 Build Tools (MSVC 14.39, Windows SDK 10.0.22621).
`build.bat` sources `vcvarsall.bat` itself.

```bat
build.bat observe.cpp observe.dll      REM the observer
build.bat recon.cpp   recon.dll        REM the recon probe (optional)
```

## Run (Global client)

From the repo root, with an **x64** Python (the injector is Frida-free, so any
x64 Python works). **Run the shell as Administrator** (for `OpenProcess`).

```bat
REM 1. Launch the Global client, reach the title screen.
REM 2. Inject the observer:
.venv\Scripts\python.exe native\inject.py native\observe.dll
REM    Watch for: "armed on N method(s)" and "[observe] live."
REM 3. Log in. The id card is auto-fetched after login (no manual nav).
REM 4. Build the Shittim profile from the capture:
.venv\Scripts\python.exe native\build_profile.py native\observe_raw.jsonl --region Global --out captures\profile_native.json
REM 5. Export for Shittim (existing tool, unchanged):
.venv\Scripts\python.exe exporters\shittim.py captures\profile_native.json
```

Note: `DllMain` runs once per load, so to re-test an updated `observe.dll` you
must **restart the game** before re-injecting. The hook is in-memory only —
closing the game removes it; nothing persists in the client.

Outputs next to the DLL:
- `observe.log` — readable match list + status
- `observe_raw.jsonl` — full captured JSON, one packet per line (input to `build_profile.py`)

## Tests (offline, no game)

```bat
REM length decoder vs capstone (pip install capstone once, into .venv):
build.bat ld_test_dll.cpp ld_test.dll
..\.venv\Scripts\python.exe ld_test.py

REM trampoline engine self-test:
build.bat hook_test.cpp hook_test.exe
hook_test.exe
```

`ld_test.py` checks the decoder against capstone over ~880k real instructions
from system DLLs; `hook_test.exe` proves hooking + rel32/RIP-relative relocation
on functions it builds itself. Both must pass before trusting a live hook.
