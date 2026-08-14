r"""
Grab static reference data from the running game (cost curves etc.) into
data/gamedata.json, so planning math in ba_export can run offline afterward.

Run with the game open + logged in:
    .venv\Scripts\python.exe grab_reference.py
"""
import os
import sys
import json
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(HERE, "frida", "agent", "_capture_refresh.js")
OUT = os.path.join(HERE, "data", "gamedata.json")
PROC = "BlueArchive.exe"
MAX_CHAR_LEVEL = 90


def main():
    if not os.path.exists(AGENT):
        print("Agent not built. cd frida/agent && npm run build:refresh"); sys.exit(1)
    try:
        s = frida.attach(PROC)
    except Exception:
        print(f"{PROC} not running. Launch BA + log in first."); sys.exit(1)
    sc = s.create_script(open(AGENT, encoding="utf-8").read())
    sc.on("message", lambda m, d: None)
    sc.set_log_handler(lambda l, t: None)
    sc.load(); time.sleep(0.4)

    print("grabbing character EXP curve…")
    curve = {int(k): int(v) for k, v in sc.exports_sync.char_exp_curve(MAX_CHAR_LEVEL).items() if v}

    print("computing skill materials to max (per student, via the client calculator)…")
    skills = sc.exports_sync.skill_materials_to_max()   # {uid: {"Type:id": amount}}

    print("computing eleph to max star / UE (per student, via the client calculator)…")
    grades = sc.exports_sync.grade_costs_to_max()       # {uid: {eleph_star, eleph_ue}}

    print("computing gear tier-up materials to max (per student, via the client calculator)…")
    gear = sc.exports_sync.gear_materials_to_max()      # {uid: {"Type:id": amount}}

    # name every referenced material (incl. ones you don't own) so the export labels them.
    # Items (ParcelType 4): skill mats.  Equipment (ParcelType 3): gear tier-up pieces.
    def _ids(prefix, *sources):
        return sorted({int(k.split(":", 1)[1]) for src in sources for mats in src.values()
                       for k in mats if k.startswith(prefix)})
    item_ids = _ids("Item:", skills, gear)
    equip_ids = _ids("Equipment:", skills, gear)
    item_names = {k: v for k, v in sc.exports_sync.resolve_typed(4, item_ids).items() if v} if item_ids else {}
    equip_names = {k: v for k, v in sc.exports_sync.resolve_typed(3, equip_ids).items() if v} if equip_ids else {}
    s.detach()

    if item_names or equip_names:
        ni = os.path.join(HERE, "data", "names_items.json")
        try:
            cache = json.load(open(ni, encoding="utf-8"))
        except Exception:
            cache = {}
        cache.setdefault("item", {}).update(item_names)
        cache.setdefault("equip", {}).update(equip_names)
        json.dump(cache, open(ni, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        print(f"named {len(item_names)} materials + {len(equip_names)} gear pieces")

    if not curve:
        print("failed to read EXP curve (game data not loaded?)."); sys.exit(1)

    # account-specific planning data (skill costs) — separate from the static reference
    PLAN = os.path.join(HERE, "data", "plan_costs.json")
    os.makedirs(os.path.dirname(PLAN), exist_ok=True)
    json.dump({"grabbed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "skills": skills, "grades": grades, "gear": gear},
              open(PLAN, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"saved {PLAN}  ({len(skills)} skill, {len(grades)} grade, {len(gear)} gear costs)")

    data = {
        "grabbed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "max_char_level": max(curve),
        "char_total_exp": curve,   # {level: cumulative exp to reach that level}
        # Activity report EXP values (Global constants). Reports are the standard
        # character-EXP items; used to convert remaining EXP -> reports needed.
        "activity_report_exp": {"Novice": 50, "Normal": 500, "Advanced": 2000, "Superior": 8000},
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"saved {OUT}")
    print(f"  levels: {min(curve)}..{max(curve)}   TotalExp(90) = {curve.get(90):,}")


if __name__ == "__main__":
    main()
