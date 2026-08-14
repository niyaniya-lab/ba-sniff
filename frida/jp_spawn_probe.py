r"""
EXPERIMENT: launch JP ourselves (no IFEO, no Yostar launcher) via the stock run.bat chain.

Why this might work where the earlier bare-spawn attempt failed (FINDINGS §11b): that test
spawned BlueArchive.exe *directly*, skipping the XIGNCODE loader, and the game self-exited
~20 s "lacking the launcher's environment". But the game folder ships a stock run.bat:

    start "" "%~dp0xldr_BlueArchiveOnline_JP_loader_x64.exe" "%~dp0BlueArchive.exe"

i.e. loader -> game-as-child is a *complete* launch with no Yostar launcher involved. So the
missing environment was plausibly the loader's, not the launcher's. This probe spawns the
loader under Frida with **child gating** (per-session; distinct from the device-wide
enable_spawn_gating() that FINDINGS §11b found unsupported on Windows) and injects into
BlueArchive.exe at creation — the same only-injection-window, without touching HKLM.

Run it (self-elevates; run.bat needs admin too):
    .venv\Scripts\python.exe frida\jp_spawn_probe.py

PASS = BlueArchive.exe reaches the title screen and is still alive well past ~20 s. If it
dies at ~20 s the hypothesis is wrong and the IFEO path in jp_capture.py stays the only way.
This does not modify jp_capture.py or the IFEO path — nothing is replaced until it passes.

RUN 1 RESULT (measured): child gating works — the loader's children are caught. But it
creates WELLBIA's `ucsvc.exe` *before* the game, and resuming it immediately let XIGNCODE
arm, so attaching to the game child returned `VirtualAllocEx 0x5 ACCESS_DENIED` — the same
denial FINDINGS §11b records for post-boot attach. (This also explains why IFEO works: the
xldr loader never runs there, so nothing is armed when the shim spawns the game.)

RUN 2 (this version) tests whether that ordering is the whole story: gated siblings are now
HELD suspended until the game has been injected, then released before the game is resumed.
Note this is a change in kind — run 1 raced the anti-cheat to an open window, this briefly
impedes its startup. Same read-only capture, but weigh it before running.

WARNING: JP ships XIGNCODE3. This is the same at-spawn injection the IFEO path already does,
but it is still anti-cheat-adjacent — use the account you already accept that risk on.
"""
import ctypes
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # .../frida
BA_ROOT = os.path.dirname(HERE)
AGENT = os.path.join(HERE, "agent", "_capture.js")         # hook-only, as the IFEO shim uses
CAPTURES = os.path.join(BA_ROOT, "captures")
JP_PROFILE = os.path.join(CAPTURES, "profile_jp_latest.json")

DEFAULT_GAME_DIR = r"C:\YostarGames\BlueArchive_JP"
LOADER = "xldr_BlueArchiveOnline_JP_loader_x64.exe"
EXE = "BlueArchive.exe"
SURVIVAL_SECS = 90          # the ~20 s self-exit is the thing we are trying to beat
WATCH_SECS = 1800           # keep capturing for up to 30 min of play, as the shim does
HOLD_TIMEOUT_SECS = 15      # give up holding siblings if the game never appears (deadlock)


def find_game_dir(argv_dir=None):
    """Resolve the JP install dir. Returns (dir, error) — error is None when usable.

    Checked in order: explicit argument, the known Yostar default. A dir only counts if it
    holds both halves of the run.bat chain (loader + exe), which is also what tells us the
    stock launch path is present.
    """
    for cand in (argv_dir, DEFAULT_GAME_DIR):
        if not cand:
            continue
        if not os.path.isdir(cand):
            if cand is argv_dir:
                return None, f"not a directory: {cand}"
            continue
        missing = [f for f in (LOADER, EXE) if not os.path.exists(os.path.join(cand, f))]
        if missing:
            return None, f"{cand} is missing {', '.join(missing)}"
        return cand, None
    return None, (f"JP install not found at {DEFAULT_GAME_DIR}. "
                  f"Pass the folder holding {EXE}:  jp_spawn_probe.py <game-dir>")


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def die(*lines):
    """Fail with the reason still on screen. After self-elevation this runs in a fresh
    console that closes on exit, so a bare sys.exit would flash the error and vanish."""
    for line in lines:
        print(line)
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass
    sys.exit(1)


def main():
    argv_dir = sys.argv[1] if len(sys.argv) > 1 else None

    if not _is_admin():
        # run.bat self-elevates, so the real launch is elevated; match it.
        print("[*] re-launching elevated (the stock run.bat requires admin too)...")
        params = '"%s"' % os.path.abspath(__file__)
        if argv_dir:
            params += ' "%s"' % argv_dir
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        return

    game_dir, err = find_game_dir(argv_dir)
    if err:
        die("[-] " + err)
    if not os.path.exists(AGENT):
        die("[-] agent not built. cd frida/agent && npm run build:capture")
    loader = os.path.join(game_dir, LOADER)
    exe = os.path.join(game_dir, EXE)
    print(f"[*] game dir : {game_dir}")
    print(f"[*] loader   : {LOADER}")

    import frida
    spec = importlib.util.spec_from_file_location("ba_capture", os.path.join(HERE, "capture.py"))
    ba_capture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ba_capture)

    cap = ba_capture.Capture(out_dir=CAPTURES, profile_latest=JP_PROFILE, region="JP")
    dev = frida.get_local_device()
    agent_src = open(AGENT, encoding="utf-8").read()

    state = {"child_pid": None, "injected": False, "child_died": None, "started": None}
    sessions = []
    held = []          # gated siblings kept suspended until the game is injected

    def release_held(why):
        """Resume the siblings we held, in creation order."""
        while held:
            child = held.pop(0)
            try:
                dev.resume(child.pid)
                print(f"[*] released {os.path.basename(child.path or '?')} (pid {child.pid}) — {why}")
            except Exception as e:
                print(f"[!] could not resume pid {child.pid}: {e!r}")

    def inject(pid, label):
        """Attach + load the hook-only agent, then resume. This is the only injection window."""
        session = dev.attach(pid)
        session.on("detached", lambda reason, *a: state.__setitem__("child_died", reason))
        script = session.create_script(agent_src)
        script.on("message", cap.on_message)
        script.load()
        sessions.append(session)
        print(f"[+] agent injected into {label} (pid {pid}) BEFORE resume")

    def on_child(child):
        # frida spawns gated children suspended; we get the only pre-resume window here.
        name = os.path.basename(child.path or "").lower()
        print(f"[child] pid={child.pid} path={child.path}")
        if name != EXE.lower():
            # Run 1 resumed these immediately and the game was then unreachable:
            # VirtualAllocEx 0x5. The loader starts WELLBIA's ucsvc.exe *before* the game,
            # so by the time the game child appeared XIGNCODE had already armed and stripped
            # our handle rights. Hold siblings suspended so nothing arms before we inject.
            held.append(child)
            print(f"        (not the game — HELD suspended, {len(held)} pending)")
            return
        state["child_pid"] = child.pid
        try:
            inject(child.pid, EXE)
            state["injected"] = True
        except Exception as e:
            print(f"[!] injection into the child FAILED: {e!r}")
            print("    resuming it anyway so the launch is not left hanging.")
        # Order matters: the game is injected and still suspended; let the anti-cheat
        # service start first so the game finds it running, then resume the game.
        release_held("game injected")
        dev.resume(child.pid)
        state["started"] = time.time()

    dev.on("child-added", on_child)

    print("[*] spawning the loader with child gating (mirrors run.bat's command line)...")
    try:
        parent_pid = dev.spawn([loader, exe], cwd=game_dir)
        parent_session = dev.attach(parent_pid)
        parent_session.enable_child_gating()
    except Exception as e:
        die(f"[-] could not spawn/gate the loader: {e!r}",
            "    If this is NotSupportedError, child gating is unavailable on this OS and",
            "    the IFEO path in jp_capture.py remains the only method.")
    print(f"[+] loader spawned suspended (pid {parent_pid}), child gating enabled")
    dev.resume(parent_pid)
    print("[*] loader resumed. Waiting for it to create BlueArchive.exe...")

    for i in range(120):
        if state["child_pid"]:
            break
        # Deadlock guard: if the loader is itself waiting on a sibling we are holding, it
        # will never get to creating the game. Release them and let the launch proceed —
        # injection will likely then fail as it did in run 1, but that is still a result.
        if held and i == int(HOLD_TIMEOUT_SECS * 2):
            print(f"[!] no game process after {HOLD_TIMEOUT_SECS}s while holding siblings —")
            print("    the loader may be blocked on one of them.")
            release_held("deadlock guard")
        time.sleep(0.5)
    if not state["child_pid"]:
        die("[-] the loader never created BlueArchive.exe (no child-added event in 60 s).",
            "    Child gating may not cover how xldr creates the process. IFEO stays.")
    if not state["injected"]:
        die("[-] the child appeared but injection failed — see the error above.")

    print()
    print("=" * 68)
    print(f"  VERDICT PENDING — watching for {SURVIVAL_SECS}s.")
    print(f"  The earlier bare-spawn attempt died at ~20s. Past that = hypothesis holds.")
    print("  Log in and reach the lobby to capture; Ctrl+C to stop early.")
    print("=" * 68)
    verdict = None
    try:
        for t in range(WATCH_SECS):
            time.sleep(1)
            alive = time.time() - state["started"]
            if state["child_died"]:
                # Only a death *before* the survival mark refutes the hypothesis — past it,
                # the game exiting just means you closed it, so keep the PASS.
                if verdict is None:
                    verdict = f"FAIL — game exited after {alive:.0f}s ({state['child_died']})"
                else:
                    print(f"\n[*] game closed after {alive:.0f}s ({state['child_died']}).")
                break
            if verdict is None and alive >= SURVIVAL_SECS:
                verdict = f"PASS — game alive past {SURVIVAL_SECS}s without the Yostar launcher"
                print(f"\n[+] {verdict}\n")
            if t % 10 == 0:
                print(f"[{alive:6.0f}s] packets={cap.packets_this_session} "
                      f"protocols={len(cap.protocols)} "
                      f"loginsync={'Account_LoginSync' in cap.protocols}")
    except KeyboardInterrupt:
        print("\n[*] stopped by user.")
    finally:
        release_held("cleanup")      # never leave a gated process suspended
        cap.snapshot()
        for s in sessions:
            try:
                s.detach()
            except Exception:
                pass

    print()
    print("=" * 68)
    print(f"  RESULT: {verdict or 'INCONCLUSIVE — stopped before the ' + str(SURVIVAL_SECS) + 's mark'}")
    print(f"  packets={cap.packets_this_session}  protocols={len(cap.protocols)}  "
          f"login bundle={'yes' if 'Account_LoginSync' in cap.protocols else 'no'}")
    print(f"  profile: {JP_PROFILE}")
    print("=" * 68)
    if cap.packets_this_session:
        print(f"Export with:  .venv\\Scripts\\python.exe ba_export.py {JP_PROFILE}")
    try:
        input("Press Enter to close.")
    except EOFError:
        pass


if __name__ == "__main__":
    main()
