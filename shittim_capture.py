r"""
One-click capture -> Shittim import file (Global).

Start it, then start the game. It waits for the client, attaches the read-only hook, waits
until everything an import needs has come across, writes the envelope, and closes the game.

    .venv\Scripts\python.exe shittim_capture.py            # wait, capture, export, close game
    .venv\Scripts\python.exe shittim_capture.py --keep     # leave the game running at the end

Normally launched by launch_shittim.bat.

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
AGENT = os.path.join(HERE, "frida", "agent", "_capture.js")
CAPTURES = os.path.join(HERE, "captures")
PROC = "BlueArchive.exe"

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


def wait_for_game(timeout):
    """Block until the client is up. Returns its pid, or None on timeout."""
    print(f"[*] waiting for {PROC} — start the game now (Ctrl+C to abort)")
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
    if not os.path.exists(AGENT):
        print("[-] agent not built. cd frida/agent && npm run build:capture")
        sys.exit(1)

    import frida
    capture_mod = _load("ba_capture", os.path.join(HERE, "frida", "capture.py"))
    shittim = _load("shittim_exporter", os.path.join(HERE, "exporters", "shittim.py"))

    pid = find_game_pid() or wait_for_game(WAIT_FOR_GAME_SECS)
    if not pid:
        print(f"[-] {PROC} never appeared.")
        sys.exit(1)

    cap = capture_mod.Capture(out_dir=CAPTURES, profile_latest=capture_mod.PROFILE_LATEST)
    # The profile is seeded from previous runs, so `cap.protocols` alone cannot tell us what
    # arrived NOW. Track this session's packets separately and require them to be fresh.
    fresh = set()
    inner = cap.on_message

    def on_message(msg, data):
        inner(msg, data)
        payload = msg.get("payload") or {}
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

    print(f"[*] attaching to pid {pid}...")
    session = frida.attach(pid)
    script = session.create_script(open(AGENT, encoding="utf-8").read())
    script.on("message", on_message)
    script.load()
    print("[+] hooked. Log in and reach the lobby.\n")

    def have(*labels):
        return all(l in fresh for l in labels)

    deadline = time.time() + LOGIN_TIMEOUT_SECS
    try:
        while time.time() < deadline and not have("Account_Auth", "Account_LoginSync", "items"):
            got = [l for l in ("Account_Auth", "Account_LoginSync", "items") if l in fresh]
            print(f"\r    captured: {', '.join(got) if got else '(nothing yet)':<48}", end="", flush=True)
            time.sleep(1)
        print()

        if not have("Account_Auth", "Account_LoginSync", "items"):
            print("[-] timed out before the login bundle arrived.")
            print("    Did you reach the lobby? Re-run and log in fully.")
            session.detach()
            sys.exit(1)

        print("[+] account, roster and items captured.")

        if "id_card" not in fresh:
            print()
            print("=" * 66)
            print("  Open the FRIENDS / ID CARD screen now to include your ID card")
            print("  (chosen background, represent character, show flags).")
            print(f"  Waiting {ID_CARD_WAIT_SECS}s — press Ctrl+C to skip and export without it.")
            print("=" * 66)
            id_deadline = time.time() + ID_CARD_WAIT_SECS
            while time.time() < id_deadline and "id_card" not in fresh:
                left = int(id_deadline - time.time())
                print(f"\r    {left}s remaining   ", end="", flush=True)
                time.sleep(1)
            print()
        print("[+] id card captured." if "id_card" in fresh else "[*] no id card — exporting without it.")

    except KeyboardInterrupt:
        print("\n[*] skipping ahead.")
    finally:
        cap.snapshot()
        try:
            session.detach()
        except Exception:
            pass

    print("\n[*] building the import file...")
    with open(capture_mod.PROFILE_LATEST, encoding="utf-8") as fh:
        profile = json.load(fh)
    try:
        account_data = shittim.build_account_data(profile)
    except ValueError as exc:
        print(f"[-] {exc}")
        sys.exit(1)

    out_path = shittim.default_out_path(capture_mod.PROFILE_LATEST)
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
