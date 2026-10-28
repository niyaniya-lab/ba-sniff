r"""
Stage 4: turn the native observer's capture into the Shittim-ready profile.

observe.cpp writes observe_raw.jsonl -- one full JSON packet per line, the exact
string the game deserialized. That is precisely what Capture._store_packet()
expects as {"json": <raw>}, so we REUSE the proven Frida-path pipeline verbatim:
protocol-id -> name mapping (ba_codec), the CharacterListResponse ->
Account_LoginSync content alias, the raw audit log, and the profile writer. No
capture logic is reimplemented here; this only feeds the existing class.

Output: captures/profile_latest.json  (feedable to exporters/shittim.py unchanged)

Usage (repo root, any x64 Python -- no Frida needed):
  python native\build_profile.py                        # uses native\observe_raw.jsonl
  python native\build_profile.py <raw.jsonl> --region Global
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "frida"))   # import capture
from capture import Capture                         # noqa: E402 (path set above)


def main():
    ap = argparse.ArgumentParser(description="Build profile_latest.json from a native capture")
    ap.add_argument("raw", nargs="?", default=os.path.join(HERE, "observe_raw.jsonl"),
                    help="observer raw jsonl (default: native/observe_raw.jsonl)")
    ap.add_argument("--region", default="Global", help="region tag stamped into the profile")
    ap.add_argument("--out", default=None,
                    help="output profile path (default: captures/profile_latest.json, which "
                         "accumulates across sessions). Use a fresh path for a native-only profile.")
    args = ap.parse_args()

    if not os.path.exists(args.raw):
        print(f"[-] {args.raw} not found. Run the observer first.")
        sys.exit(1)

    if args.out:
        cap = Capture(profile_latest=os.path.abspath(args.out), region=args.region)
    else:
        cap = Capture(region=args.region)
    n = 0
    with open(args.raw, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            n += 1
            cap._store_packet({"json": line})   # reuse the exact pipeline

    print(f"\n[+] fed {n} packets -> {len(cap.protocols)} unique protocols")
    print(f"[+] profile: {cap.profile_latest}")

    # Report the four load-bearing pieces the Shittim exporter needs.
    pr = cap.protocols
    def has(cond):
        return "yes" if cond else "NO"
    login = pr.get("Account_LoginSync") or {}
    print("    AccountDB           :", has(any(isinstance(v, dict) and "AccountDB" in v for v in pr.values())))
    print("    CharacterListResponse:", has(any(isinstance(v, dict) and "CharacterListResponse" in v for v in pr.values())))
    print("    ItemDBs             :", has(any(isinstance(v, dict) and "ItemDBs" in v for v in pr.values())))
    print("    FriendIdCardDB      :", has(any(isinstance(v, dict) and "FriendIdCardDB" in v for v in pr.values())))


if __name__ == "__main__":
    main()
