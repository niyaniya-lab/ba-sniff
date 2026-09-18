# frida-gadget (experimental)

An alternative injection path to the normal `frida.attach` capture. Instead of
attaching from outside, `frida-gadget.dll` runs Frida *inside* the target. This
exists mainly to experiment against clients that resist outside attach (e.g. JP);
for Global, `launch_gl_passive.bat` is simpler.

`frida-gadget.dll` is rebuilt from source and **not committed** (gitignored).
`rebuild-patched-frida.ps1 -BuildCore` (or `bootstrap-patched-frida.ps1`) builds
it and copies it here.

## Files

| File | Role |
|------|------|
| `frida-gadget.dll` | the gadget (x64; matches BlueArchive.exe). Gitignored. |
| `inject_gadget.py` | writes `frida-gadget.config`, then DLL-injects the gadget into the game (CreateRemoteThread + LoadLibraryW — no frida.attach, so only one Frida runtime runs in-process). |
| `gadget_capture.py` | listen-mode client: connects to the gadget socket, loads `_capture.js`, reuses `capture.py`'s pipeline (output → `captures\`). |
| `frida-gadget.config` | generated per run (gitignored). The gadget reads the `.config` named after itself. |

## The two modes

### listen  (recommended for Global testing)
The gadget opens a socket in-process; a Python client connects and drives it,
reusing your existing `_capture.js` and capture pipeline.

```
.venv\Scripts\python.exe gadget\inject_gadget.py --mode listen --port 27042
.venv\Scripts\python.exe gadget\gadget_capture.py 27042
```
Or just: `launch_gl_gadget_listen.bat` (does both).

### script  (standalone, no Python)
The gadget auto-runs a JS from disk on load.

```
.venv\Scripts\python.exe gadget\inject_gadget.py --mode script --script frida\agent\_capture.js
```
Or: `launch_gl_gadget_script.bat`.

**Limitation:** `_capture.js` `send()`s packets to a *client*. In script mode
there is no client, so those packets go to the gadget's log, **not** to
`captures\`. Script mode is therefore useful to confirm the hook arms standalone,
but true standalone persistence needs an agent that writes to disk itself. That
adapted agent is not built yet.

## Getting the gadget into the process

`inject_gadget.py` uses classic DLL injection, which works on Global (no
anti-cheat). Run the shell **as Administrator** if `OpenProcess` fails.

For a client that blocks injection/attach (the reason the gadget exists), you'd
instead get the DLL loaded by **DLL sideloading/hijacking** — placing it under a
name the game itself loads from its folder. That is client- and version-specific
and is not automated here.

## Status

Built and validated: the gadget loads, opens its listen socket, and runs scripts
(verified by injecting into a throwaway process). It has **not** been tested
against the live game — that's the experiment this harness is for.
