r"""
SPIKE: can MX.Data.LocalizeEtcData be reached without gc.choose?

Everything ba-sniff reads out of ExcelDB.db (names, EXP curve, item categories) comes from
the client's already-decrypted MX.Data.*Data wrappers — see FINDINGS §4. The instance is
currently located with `Il2Cpp.gc.choose` (lib/mx.ts:47), a stop-the-world heap walk that
hangs the JP client, and that — not decryption — is the sole reason JP has no name
resolution. If a static path to the same singleton exists, JP names come for free.

Run against **GLOBAL** (Steam), which has no anti-cheat, with the game at the title screen
or lobby (ExcelDB is loaded at startup, so the title screen is enough):

    cd frida\agent && npm run build:statics && cd ..\..
    .venv\Scripts\python.exe frida\spike_statics.py

Read-only: enumerates fields/methods and calls one pure lookup, GetName(1, 10000) -> "Aru".
A candidate only counts as a hit if that call actually returns the right name.
"""
import os
import sys
import time

import frida

PROC = "BlueArchive.exe"
AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent", "_statics.js")

hits = []
done = {"flag": False}


def on_message(msg, data):
    t = msg.get("type")
    if t == "log":
        print(msg.get("payload", ""))
        return
    if t == "error":
        print("[JS ERROR]", msg.get("description"))
        if msg.get("stack"):
            print(msg["stack"])
        done["flag"] = True
        return
    p = msg.get("payload") or {}
    k = p.get("type")
    if k == "start":
        print(f"[*] image={p.get('image')} target={p.get('target')}")
    elif k == "static_field":
        print(f"    static {p.get('on')}.{p.get('field')} : {p.get('ftype')}")
    elif k == "base_chain":
        print(f"[*] base chain: {' -> '.join(p.get('chain', []))}")
    elif k == "CANDIDATE":
        ok = p.get("works")
        mark = "HIT " if ok else "dud "
        print(f"[{mark}] {p.get('how')}  ({p.get('kind')})  GetName(1,10000) -> {p.get('resolved')!r}")
        if ok:
            hits.append(p.get("how"))
    elif k in ("candidate_read_failed", "candidate_call_failed", "proof_error"):
        print(f"    [!] {p.get('how', '')} {p.get('error')}")
    elif k == "scan_done":
        print(f"[*] scanned {p.get('scanned')} classes, {p.get('holders')} held a LocalizeEtcData static")
    elif k == "fatal":
        print("[-]", p.get("error"))
        done["flag"] = True
    elif k == "done":
        done["flag"] = True


def main():
    if not os.path.exists(AGENT):
        print("[-] agent not built. cd frida/agent && npm run build:statics")
        sys.exit(1)
    try:
        session = frida.attach(PROC)
    except frida.ProcessNotFoundError:
        print(f"[-] '{PROC}' not running. Launch Blue Archive GLOBAL (Steam) and reach the title screen.")
        sys.exit(1)
    print(f"[+] attached to {PROC}")
    script = session.create_script(open(AGENT, encoding="utf-8").read())
    script.on("message", on_message)
    script.load()

    for _ in range(600):          # image scan can take a while
        if done["flag"]:
            break
        time.sleep(0.1)
    try:
        session.detach()
    except Exception:
        pass

    print()
    print("=" * 68)
    if hits:
        print(f"  RESULT: {len(hits)} gc.choose-free path(s) to LocalizeEtcData:")
        for h in hits:
            print(f"    {h}")
        print("  -> port lib/mx.ts:resolveTypedNames onto this and JP gets names.")
    else:
        print("  RESULT: no static path found. gc.choose stays the only way to reach it,")
        print("  so JP name resolution remains blocked (FINDINGS §11b stands).")
    print("=" * 68)


if __name__ == "__main__":
    main()
