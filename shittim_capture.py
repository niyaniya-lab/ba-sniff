r"""
One-click capture -> Shittim import file (Global).

Run it and walk away. It asks Steam to start Blue Archive, attaches the read-only hook, waits
until everything an import needs has come across, writes the envelope, and closes the game.

    shittim_capture.py               launch, capture, export, close the game
    shittim_capture.py --keep        leave the game running at the end
    shittim_capture.py --no-launch   do not touch Steam; wait for the game to be started

Already-running clients are attached to as they are, whichever mode. Normally launched by
launch_shittim.bat, or as the packaged ba-shittim-capture.exe.

WHAT IT WAITS FOR. Three of the four pieces arrive on their own during login:

    Account_Auth        the account row
    Account_LoginSync   roster, gear, echelons, cafe, campaign, story, currencies
    the item list       ships in its own packet

The fourth, the ID card (chosen background, represent character, show flags) and the
backgrounds you own, is only sent when the client asks for the friend list -- so it cannot
be waited for passively. Once the first three are in, this asks you to open the friend/ID
card screen and gives it a short window; skip it and everything else still imports.

Read-only throughout: the same Interceptor-based hook launch_gl_passive.bat uses, no
gc.choose, no writes to the client.
"""
import importlib.util
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = getattr(sys, "frozen", False)


def resource_path(*parts):
    """Read-only files shipped with the program. PyInstaller unpacks them to a temp dir."""
    base = getattr(sys, "_MEIPASS", HERE)
    return os.path.join(base, *parts)


def output_dir():
    """Where captures and the import file are written.

    Beside the executable when frozen, so the exe can be dropped in any folder and leaves
    its output right there. In a source checkout it stays in captures/, which is git-ignored.
    """
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.join(HERE, "captures")


# The refresh agent can FIRE a request as well as watch for one, which is how the ID card is
# fetched without making you walk to the friend screen. It falls back to the hook-only agent,
# where the ID card can only be waited for.
AGENT_REFRESH = resource_path("frida", "agent", "_capture_refresh.js")
AGENT_HOOK = resource_path("frida", "agent", "_capture.js")
PROC = "BlueArchive.exe"
STEAM_APPID = "3557620"   # Blue Archive on Steam

# What an import needs. The first three arrive on their own while you log in; the ID card is
# only sent when the client asks for the friend list.
STEPS = [
    ("Account_Auth", "account (name, level, rep character)"),
    ("Account_LoginSync", "roster, gear, echelons, cafe, story, currencies"),
    ("items", "item inventory"),
    ("id_card", "ID card + owned backgrounds"),
]
ESSENTIAL = ("Account_Auth", "Account_LoginSync", "items")

WAIT_FOR_GAME_SECS = 600      # how long to sit waiting for the client to appear
LOGIN_TIMEOUT_SECS = 300      # after attaching, how long the login bundle may take
ID_CARD_WAIT_SECS = 90        # optional extra window for the friend/ID card screen
CLOSE_COUNTDOWN_SECS = 5      # visible pause before the game is closed, so it can be aborted


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_game_pid():
    """PID of the running client, or None. Uses tasklist so there is no extra dependency."""
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {PROC}", "/NH"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0].lower() == PROC.lower():
            try:
                return int(parts[1])
            except (IndexError, ValueError):
                pass
    return None


def launch_via_steam():
    """Ask Steam to start the game. Returns False if the handler could not be invoked --
    Steam not installed, or the protocol not registered -- in which case the caller just
    waits for the game to be started by hand instead."""
    try:
        os.startfile(f"steam://rungameid/{STEAM_APPID}")
        return True
    except Exception:
        return False


def wait_for_game(timeout, prompt=None):
    """Block until the client is up. Returns its pid, or None on timeout."""
    print(prompt or f"[*] waiting for {PROC} -- start the game now (Ctrl+C to abort)")
    deadline = time.time() + timeout
    dots = 0
    while time.time() < deadline:
        pid = find_game_pid()
        if pid:
            print(f"\n[+] found {PROC} (pid {pid})")
            return pid
        dots = (dots + 1) % 4
        print("\r    waiting" + "." * dots + "   ", end="", flush=True)
        time.sleep(2)
    print()
    return None


def _enable_vt():
    """Windows consoles need ANSI escapes switched on explicitly. Returns whether redrawing
    in place is safe: false when output is redirected, or on an older console, in which case
    the checklist is printed only when it changes rather than continuously."""
    if not sys.stdout.isatty():
        return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        handle = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(k.SetConsoleMode(handle, mode.value | 0x0004))  # VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


def _silence(fn, *args):
    """Run fn with stdout discarded. Used for the per-packet logging, which would otherwise
    overwrite the checklist mid-redraw; the packets are still recorded, only the printing is
    dropped. Anything written to stderr still comes through."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args)


def render_checklist(fresh, note=""):
    """Show the checklist. Ticked once that piece has arrived THIS session.

    Redraws in place on a real console; elsewhere prints only when something changes, so a
    redirected log does not fill with escape codes or repeated blocks.
    """
    state = (tuple(k in fresh for k, _ in STEPS), note)
    if state == render_checklist.last:
        return
    lines = []
    for key, label in STEPS:
        mark = "[x]" if key in fresh else "[ ]"
        tail = "" if key in fresh or key in ESSENTIAL else "  <- optional"
        lines.append(f"   {mark} {label}{tail}")
    body = "\n".join(lines)

    if render_checklist.inplace and render_checklist.drawn:
        sys.stdout.write(f"\033[{len(STEPS) + 2}A")
    sys.stdout.write(body + "\n\n" + (note + " " * 40)[:78] + "\n")
    sys.stdout.flush()
    render_checklist.drawn = True
    render_checklist.last = state


render_checklist.drawn = False
render_checklist.last = None
render_checklist.inplace = _enable_vt()


def try_fetch_id_card(script):
    """Ask the client to pull its friend list, which is what carries the ID card.

    Returns the task name fired, or None when the agent cannot do it (hook-only build) or no
    friend task exists. Firing goes through the game's own NetworkTaskManager on its main
    thread, the same path the refresh feature uses.
    """
    try:
        tasks = script.exports_sync.list_tasks()
    except Exception:
        return None
    candidates = [t for t in tasks if "friend" in t.lower()]
    # The ID card rides the friend-list response; prefer the plainest match.
    for pref in ("FriendListNetworkTask", "FriendGetListNetworkTask"):
        if pref in candidates:
            candidates = [pref] + [c for c in candidates if c != pref]
            break
    if not candidates:
        return None
    try:
        script.exports_sync.refresh([candidates[0]])
        return candidates[0]
    except Exception:
        return None


def close_game(pid):
    """Ask the client to close, then insist. BA keeps its state server-side, so this loses
    nothing, but the polite request first lets it shut its own connection down."""
    print(f"[*] closing the game in {CLOSE_COUNTDOWN_SECS}s (Ctrl+C to leave it running)")
    try:
        for n in range(CLOSE_COUNTDOWN_SECS, 0, -1):
            print(f"\r    {n}... ", end="", flush=True)
            time.sleep(1)
        print()
    except KeyboardInterrupt:
        print("\n[*] leaving the game running.")
        return
    subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)
    for _ in range(10):
        time.sleep(1)
        if find_game_pid() is None:
            print("[+] game closed.")
            return
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    print("[+] game closed (forced).")


def main():
    keep_game = "--keep" in sys.argv
    no_launch = "--no-launch" in sys.argv
    if not (os.path.exists(AGENT_REFRESH) or os.path.exists(AGENT_HOOK)):
        print("[-] agent not built. cd frida/agent && npm run build:capture")
        print("    (and npm run build:refresh, to fetch the ID card without navigating)")
        sys.exit(1)

    import frida
    capture_mod = _load("ba_capture", resource_path("frida", "capture.py"))
    shittim = _load("shittim_exporter", resource_path("exporters", "shittim.py"))

    pid = find_game_pid()
    if not pid:
        if no_launch or not launch_via_steam():
            pid = wait_for_game(WAIT_FOR_GAME_SECS)
        else:
            pid = wait_for_game(WAIT_FOR_GAME_SECS,
                                "[*] asked Steam to launch Blue Archive -- waiting for it")
    if not pid:
        print(f"[-] {PROC} never appeared.")
        sys.exit(1)

    out_dir = output_dir()
    os.makedirs(out_dir, exist_ok=True)
    profile_path = os.path.join(out_dir, "profile_latest.json")
    cap = capture_mod.Capture(out_dir=out_dir, profile_latest=profile_path)
    # The profile is seeded from previous runs, so `cap.protocols` alone cannot tell us what
    # arrived NOW. Track this session's packets separately and require them to be fresh.
    fresh = set()
    inner = cap.on_message

    def on_message(msg, data):
        payload = msg.get("payload") or {}
        # Capture prints a line per packet, which lands in the middle of the checklist as it
        # redraws. The checklist IS the progress display here, so swallow that chatter and
        # keep everything else (hook armed, hook failed) visible.
        if payload.get("type") == "packet":
            _silence(inner, msg, data)
        else:
            inner(msg, data)
        if payload.get("type") != "packet":
            return
        # Identified by content rather than protocol name: the login bundle rides a different
        # protocol number per client, and the item list and ID card arrive in their own
        # packets whose numbers are not stable to rely on either.
        raw = payload.get("json", "")
        for marker, label in (('"AccountDB"', "Account_Auth"),
                              ('"CharacterListResponse"', "Account_LoginSync"),
                              ('"ItemDBs"', "items"),
                              ('"FriendIdCardDB"', "id_card")):
            if marker in raw:
                fresh.add(label)

    agent = AGENT_REFRESH if os.path.exists(AGENT_REFRESH) else AGENT_HOOK
    can_fetch = agent == AGENT_REFRESH
    print(f"[*] attaching to pid {pid}...")
    session = frida.attach(pid)
    script = session.create_script(open(agent, encoding="utf-8").read())
    script.on("message", on_message)
    script.load()
    print("[+] hooked -- log in and reach the lobby.\n")

    def have(*labels):
        return all(l in fresh for l in labels)

    try:
        render_checklist(fresh, "waiting for you to log in...")
        deadline = time.time() + LOGIN_TIMEOUT_SECS
        while time.time() < deadline and not have(*ESSENTIAL):
            render_checklist(fresh, "waiting for you to log in...")
            time.sleep(1)

        if not have(*ESSENTIAL):
            render_checklist(fresh, "timed out.")
            print("\n[-] the login bundle never arrived. Did you reach the lobby?")
            session.detach()
            sys.exit(1)

        # The ID card is only sent when the client asks for its friend list. Ask on your
        # behalf rather than making you walk there; fall back to asking if that is not
        # possible. Only after the lobby is up -- firing during loading destabilises the
        # client (docs/FINDINGS.md 11b).
        if "id_card" not in fresh:
            fired = try_fetch_id_card(script) if can_fetch else None
            if fired:
                render_checklist(fresh, f"asking the game for your ID card ({fired})...")
                wait_until = time.time() + 25
                while time.time() < wait_until and "id_card" not in fresh:
                    render_checklist(fresh, f"asking the game for your ID card ({fired})...")
                    time.sleep(1)

        if "id_card" not in fresh:
            render_checklist(fresh, "open the FRIENDS / ID CARD screen to include it, or wait to skip")
            id_deadline = time.time() + ID_CARD_WAIT_SECS
            while time.time() < id_deadline and "id_card" not in fresh:
                left = int(id_deadline - time.time())
                render_checklist(fresh, f"open the FRIENDS / ID CARD screen -- skipping in {left}s (Ctrl+C to skip now)")
                time.sleep(1)

        render_checklist(fresh, "done.")

    except KeyboardInterrupt:
        render_checklist(fresh, "skipped.")
    finally:
        cap.snapshot()
        try:
            session.detach()
        except Exception:
            pass

    print("\n[*] building the import file...")
    with open(profile_path, encoding="utf-8") as fh:
        profile = json.load(fh)
    try:
        account_data = shittim.build_account_data(profile)
    except ValueError as exc:
        print(f"[-] {exc}")
        sys.exit(1)

    out_path = shittim.default_out_path(profile_path)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(account_data, fh, ensure_ascii=False, indent=2)

    s = shittim.summarize(account_data)
    print(f"[+] {s['nickname']}  Lv{s['level']}")
    print(f"    {s['characters']} characters, {s['equipment']} equipment, {s['items']} items,")
    print(f"    {s['echelons']} echelons, {s['furniture']} furniture")
    print(f"[+] wrote {out_path}")

    if not keep_game:
        print()
        close_game(pid)

    print()
    print("=" * 66)
    print("  Import it:  Shittim Control Center -> Accounts -> New -> Browse")
    print(f"  and pick:   {out_path}")
    print("=" * 66)
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass


if __name__ == "__main__":
    main()
