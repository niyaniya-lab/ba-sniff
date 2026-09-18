# Patched Frida — source patches

These patches turn a pristine **frida/frida.git** checkout into the
anti-detection–patched Frida that this project builds and installs into
`.venv`. They are kept as a patch set (not a full source copy) so this repo
stays small; the Frida source is fetched on demand.

## Base

All patches apply on top of frida.git commit:

    b42f672daabcb8a480e9d79bbe25ceba803d0007   ("submodules: Bump outdated")

with its submodules initialised at their pinned commits
(`git submodule update --init --recursive`).

## What each patch changes

| Patch | Applies in | Purpose |
|-------|------------|---------|
| `01-frida-core.patch` | `subprojects/frida-core` | New `lib/agent/v8-msvc-intrinsics-stub.c` (STL/`__ltof3` intrinsics the prebuilt V8/GLib need); wire it into `lib/agent/meson.build`; anti-detection edits to `server.vala`, `agent-container.vala`, `windows-host-session.vala`. |
| `02-frida-gum.patch` | `subprojects/frida-core/subprojects/frida-gum` | Drop the duplicate `intrinsics-stub.c` from `quickcompile` (its `__ltof3`/`__ultof3` now come from the CRT). |
| `03-releng.patch` | `releng` | `winenv.py`: let `vswhere` see VS **BuildTools** (needed to pick the newer MSVC toolset that ships the `__ltof3` intrinsics); plus `env.py`. |
| `04-frida-core-releng.patch` | `subprojects/frida-core/releng` | Same `winenv.py` toolset-detection fix. |

## Why the toolset matters

The prebuilt x86 GLib/V8 need `__ltof3`/`__ultof3` and the STL vectorized
`__std_*` helpers. VS2022 **Community 14.35** lacks the CRT ones; **BuildTools
14.39** ships them. The `winenv.py` fix makes Frida's build select the newer
toolset, and the intrinsics stub supplies the `__std_*` helpers V8 expects.

## Prerequisites (build machine)

- Windows 10/11 x64
- Visual Studio 2022 **BuildTools 14.39+** (or Community updated to 17.9+)
- Python with `meson`/`lief` for the Frida build scripts (the bootstrap uses
  `C:\ProgramData\Anaconda3\python.exe` by default — adjust if needed)

## Apply manually

```powershell
git clone https://github.com/frida/frida.git frida-patched
cd frida-patched
git checkout b42f672daabcb8a480e9d79bbe25ceba803d0007
git submodule update --init --recursive
git -C subprojects/frida-core                       apply <this-dir>\01-frida-core.patch
git -C subprojects/frida-core/subprojects/frida-gum apply <this-dir>\02-frida-gum.patch
git -C releng                                       apply <this-dir>\03-releng.patch
git -C subprojects/frida-core/releng                apply <this-dir>\04-frida-core-releng.patch
```

Or just run `..\bootstrap-patched-frida.ps1`, which does all of the above and
then builds + installs the wheel into `.venv`.
