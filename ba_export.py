r"""
ba-sniff complete account export.

Transforms the raw captured profile (captures/profile_latest.json — game-native
protocol structures) into a clean, named, analysis-ready archive: one big
account.json plus flat CSVs you can drop into a spreadsheet or a planning tool.

    .venv\Scripts\python.exe ba_export.py                 # export latest -> captures/export/
    .venv\Scripts\python.exe ba_export.py path\to\out     # custom output dir

Names for students come from data/names.json (resolved in-client by the GUI).
Item/equipment names fill in when resolved; otherwise IDs are exported (still
math-usable). Nothing here needs the game running.
"""
import os
import sys
import csv
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# reuse the GUI's pure data helpers (importing does not open any window)
from ba_gui import (extract_roster, extract_currencies, compute_summary,  # noqa: E402
                    pretty_currency, load_names, PROFILE_LATEST)

NAMES_ITEMS = os.path.join(HERE, "data", "names_items.json")
# JP name caches (parallel to the EN ones); populated by JP captures. Same UniqueId keys.
NAMES_JP = os.path.join(HERE, "data", "names_jp.json")
NAMES_ITEMS_JP = os.path.join(HERE, "data", "names_items_jp.json")
GAMEDATA = os.path.join(HERE, "data", "gamedata.json")
PLANDATA = os.path.join(HERE, "data", "plan_costs.json")


def _load(path):
    try:
        return {str(k): v for k, v in json.load(open(path, encoding="utf-8")).items()}
    except Exception:
        return {}


def login_bundle(profile):
    """The login bundle (roster + all sub-responses), found regardless of the protocol key
    it arrived under: Account_LoginSync (Global 1019) or e.g. Protocol_1017 (JP). Content-based
    so it works even on profiles captured before the capture-time alias existed."""
    P = profile.get("protocols", {})
    ls = P.get("Account_LoginSync")
    if isinstance(ls, dict):
        return ls
    for v in P.values():
        if isinstance(v, dict) and "CharacterListResponse" in v:
            return v
    return {}


def detect_region(profile):
    """Which client produced this capture. Explicit profile['region'] wins; otherwise
    infer from publisher-specific data (Yostar billing => JP, Nexon => Global; or the
    login-bundle's native protocol number: 1017 JP, 1019 Global)."""
    r = profile.get("region")
    if r in ("JP", "Global"):
        return r
    ls = login_bundle(profile)
    if "BillingPurchaseListByYostarResponse" in ls:
        return "JP"
    if any("Billing" in k and "Yostar" not in k for k in ls):
        return "Global"
    if ls.get("Protocol") == 1017:
        return "JP"
    return "Global"


# Item categorization by name (offline). Order matters: investment materials first.
# Exact categories exist in the game's ItemExcel; these name rules cover the
# planning-relevant buckets well (gifts are best-effort).
def item_category(name):
    n = (name or "").lower()
    if "eleph" in n: return "Eleph"
    if "activity report" in n: return "Activity Report (EXP)"
    if "tech notes" in n or "blu-ray" in n or "blu ray" in n: return "Skill Material"
    if "enhancement stone" in n: return "Enhancement Stone (Gear)"
    if "keystone" in n: return "Keystone (UE)"
    if "coin" in n: return "Coin"
    if "ticket" in n: return "Ticket"
    if any(k in n for k in ("raffle box", "choice box", "selector", "voucher")): return "Box / Selector"
    if any(k in n for k in ("aether", "debris", "shard", "essence", " dust", "mechanism",
                            "codex", "manuscript", "battery", " piece", "handicraft")): return "Craft Material"
    if any(k in n for k in ("chocolate", "cookie", "doll", " egg", "bite", "cushion", "bouquet",
                            "bell", "ribbon", " pen", "clock", " fan", "charm", "plush", "snack")): return "Gift (bond)"
    if "eligma" in n or "point" in n: return "Currency-like"
    return "Other"


def _eleph_owner(item_name):
    """'Aru's Eleph' -> 'Aru', 'Aris' Eleph' -> 'Aris' (matches the game's names)."""
    for suf in ("'s Eleph", "' Eleph", "’s Eleph", "’ Eleph"):
        if item_name.endswith(suf):
            return item_name[:-len(suf)]
    return None


def build_export(profile, names, out_dir, item_names=None, gamedata=None, plandata=None,
                 names_jp=None, item_names_jp=None):
    os.makedirs(out_dir, exist_ok=True)
    item_names = item_names or {}
    gamedata = gamedata or {}
    plandata = plandata or {}
    names_jp = names_jp or {}
    item_names_jp = item_names_jp or {}
    imap = item_names.get("item", {}) if isinstance(item_names, dict) else {}
    emap = item_names.get("equip", {}) if isinstance(item_names, dict) else {}
    imap_jp = item_names_jp.get("item", {}) if isinstance(item_names_jp, dict) else {}
    emap_jp = item_names_jp.get("equip", {}) if isinstance(item_names_jp, dict) else {}
    region = detect_region(profile)
    P = profile.get("protocols", {})
    # normalize: make the login bundle reachable under the stable name so the roster
    # extractor (and everything downstream) works even for un-aliased/JP profiles.
    if "Account_LoginSync" not in P:
        bundle = login_bundle(profile)
        if bundle:
            P["Account_LoginSync"] = bundle
    ls = P.get("Account_LoginSync", {})
    roster, weapons, _, acc = extract_roster(profile)
    uid_of_server = {c.get("ServerId"): c.get("UniqueId") for c in roster}

    # Canonical (UniqueId) entry with independent en/jp slots; display prefers this
    # region's language, falling back to whichever is populated, then the id.
    def _pick(en, jp):
        return (jp if region == "JP" else en) or en or jp

    def cname(uid):
        return _pick(names.get(str(uid)), names_jp.get(str(uid))) or f"Student {uid}"

    def iname(uid):
        return _pick(imap.get(str(uid)), imap_jp.get(str(uid))) or f"Item {uid}"

    def ename(uid):
        return _pick(emap.get(str(uid)), emap_jp.get(str(uid))) or f"Equip {uid}"

    # ---- account ----
    account = {
        "server_id": acc.get("ServerId"),
        "nickname": acc.get("Nickname"),
        "level": acc.get("Level"),
        "comment": acc.get("Comment"),
        "created": acc.get("CreateDate"),
        "last_connect": acc.get("LastConnectTime"),
        "friend_code": ls.get("FriendCode"),
        "friend_count": ls.get("FriendCount"),
        "rep_character": cname(uid_of_server.get(acc.get("RepresentCharacterServerId"))) if acc.get("RepresentCharacterServerId") in uid_of_server else acc.get("RepresentCharacterServerId"),
    }

    # ---- currencies ----
    currencies = {pretty_currency(k): v for k, v in extract_currencies(ls).items()}

    # ---- eleph owned per student (name-join: item "<Name>'s Eleph") ----
    eleph_owned = {}
    for x in P.get("Protocol_4000", {}).get("ItemDBs", []):
        nm = iname(x.get("UniqueId"))
        owner = _eleph_owner(nm)
        if owner:
            eleph_owned[owner] = x.get("StackCount", 0)

    # ---- students ----
    students = []
    for c in roster:
        wep = weapons.get(c.get("ServerId")) or {}
        students.append({
            "id": c.get("UniqueId"),
            "name": cname(c.get("UniqueId")),
            "name_en": names.get(str(c.get("UniqueId"))) or "",
            "name_jp": names_jp.get(str(c.get("UniqueId"))) or "",
            "level": c.get("Level"),
            "exp": c.get("Exp"),
            "star_grade": c.get("StarGrade"),
            "ue_star": wep.get("StarGrade", 0),
            "ue_level": wep.get("Level", 0),
            "bond": c.get("FavorRank"),
            "bond_exp": c.get("FavorExp"),
            "eleph_owned": eleph_owned.get(cname(c.get("UniqueId")), 0),
            "skill_ex": c.get("ExSkillLevel"),
            "skill_basic": c.get("PublicSkillLevel"),
            "skill_enhanced": c.get("PassiveSkillLevel"),
            "skill_sub": c.get("ExtraPassiveSkillLevel"),
            "equipment_tier_slots": len([e for e in (c.get("EquipmentServerIds") or []) if e]),
        })
    # ---- planning: EXP to max level (needs the game's exp curve in gamedata) ----
    planning = {}
    cum = {int(k): v for k, v in (gamedata.get("char_total_exp") or {}).items()}
    if cum:
        max_lv = gamedata.get("max_char_level", 90)
        max_exp = cum.get(max_lv - 1) or cum.get(max_lv)
        rep_exp = gamedata.get("activity_report_exp", {})
        total_rem, not_maxed = 0, 0
        for s in students:
            rem = max(0, max_exp - (cum.get((s["level"] or 1) - 1, 0) + (s["exp"] or 0)))
            s["exp_to_max"] = rem
            total_rem += rem
            not_maxed += 1 if rem > 0 else 0
        # reports owned (matched from the item inventory by name)
        owned = {t: 0 for t in ("Novice", "Normal", "Advanced", "Superior")}
        for x in P.get("Protocol_4000", {}).get("ItemDBs", []):
            nm = iname(x.get("UniqueId"))
            for t in owned:
                if nm.startswith(f"{t} Activity Report"):
                    owned[t] = x.get("StackCount", 0)
        owned_exp = sum(owned[t] * rep_exp.get(t, 0) for t in owned)
        planning["exp"] = {
            "target_level": max_lv,
            "students_below_max": not_maxed,
            "total_exp_to_max_all": total_rem,
            "activity_reports_owned": owned,
            "report_exp_owned": owned_exp,
            "report_exp_short_or_surplus": owned_exp - total_rem,
            "superior_reports_equiv_needed": (total_rem + rep_exp.get("Superior", 8000) - 1) // rep_exp.get("Superior", 8000) if rep_exp.get("Superior") else None,
            "note": "EXP curve read from the game; Activity Report EXP values are Global constants.",
        }

    # ---- planning: skill materials to max (from data/plan_costs.json) ----
    skill_gap = None
    skills_raw = plandata.get("skills") or {}
    if skills_raw:
        owned_by_id = {str(x.get("UniqueId")): x.get("StackCount", 0)
                       for x in P.get("Protocol_4000", {}).get("ItemDBs", [])}
        agg, credits_needed = {}, 0
        for uid, mats in skills_raw.items():
            credits = mtot = 0
            for k, amt in mats.items():
                if k.startswith("Currency:"):
                    credits += amt
                else:
                    agg[k] = agg.get(k, 0) + amt
                    mtot += amt
            credits_needed += credits
            sd = next((s for s in students if str(s["id"]) == str(uid)), None)
            if sd:
                sd["skill_credits_to_max"] = credits
                sd["skill_mats_to_max"] = mtot
        materials = []
        for k, need in agg.items():
            iid = k.split(":", 1)[1]
            own = owned_by_id.get(iid, 0)
            materials.append({"id": int(iid), "name": imap.get(iid) or f"Item {iid}",
                              "needed": need, "owned": own, "deficit": max(0, need - own)})
        materials.sort(key=lambda m: -m["deficit"])
        skill_gap = {"credits_to_max_all": credits_needed, "materials": materials,
                     "note": "Skill materials to max all 4 skills (EX->5, others->10) across the roster, "
                             "from the client's own cost calculator. deficit = needed - owned."}
        planning["skills"] = skill_gap

    # ---- planning: eleph to max star (5*) + UE weapon (from data/plan_costs.json) ----
    grades_raw = plandata.get("grades") or {}
    if grades_raw:
        below_5, ue_count, total_deficit, total_needed = 0, 0, 0, 0
        for s in students:
            g = grades_raw.get(str(s["id"])) or {}
            es, eu = g.get("eleph_star", 0), g.get("eleph_ue", 0)
            need = es + eu                       # star + UE both draw the same student eleph
            deficit = max(0, need - (s["eleph_owned"] or 0))
            s["eleph_to_5star"] = es
            s["eleph_ue_to_max"] = eu
            s["eleph_needed"] = need
            s["eleph_deficit"] = deficit
            below_5 += 1 if es > 0 else 0
            ue_count += 1 if eu > 0 else 0
            total_deficit += deficit
            total_needed += need
        planning["grades"] = {
            "students_below_5star": below_5,
            "students_with_ue_to_max": ue_count,
            "total_eleph_needed": total_needed,
            "total_eleph_deficit": total_deficit,
            "note": "Eleph to reach 5* star grade + max UE weapon, from the client's calculators. "
                    "Eleph is per-student; deficit = per-student (star+UE) need minus that student's owned eleph.",
        }

    # ---- planning: gear tier-up materials to max (from data/plan_costs.json) ----
    gear_raw = plandata.get("gear") or {}
    if gear_raw:
        equip_owned = {str(x.get("UniqueId")): x.get("StackCount", 0)
                       for x in (ls.get("EquipmentItemListResponse", {}) or {}).get("EquipmentDBs", [])}
        agg, credits_needed = {}, 0
        for uid, mats in gear_raw.items():
            credits = ptot = 0
            for k, amt in mats.items():
                if k.startswith("Currency:"):
                    credits += amt
                else:
                    agg[k] = agg.get(k, 0) + amt
                    ptot += amt
            credits_needed += credits
            sd = next((s for s in students if str(s["id"]) == str(uid)), None)
            if sd:
                sd["gear_credits_to_max"] = credits
                sd["gear_pieces_to_max"] = ptot
        pieces = []
        for k, need in agg.items():
            eid = k.split(":", 1)[1]
            own = equip_owned.get(eid, 0)
            pieces.append({"id": int(eid), "name": emap.get(eid) or f"Equip {eid}",
                           "needed": need, "owned": own, "deficit": max(0, need - own)})
        pieces.sort(key=lambda m: -m["deficit"])
        planning["gear"] = {"credits_to_max_all": credits_needed, "pieces": pieces,
                            "note": "Equipment tier-up pieces to max all equipped gear (to T9) across the "
                                    "roster, from the client's own recipe costs. deficit = needed - owned."}

    planning = planning or None
    students.sort(key=lambda s: (-(s["level"] or 0), s["name"]))

    # ---- inventory (items + equipment) ----
    items = []
    for x in P.get("Protocol_4000", {}).get("ItemDBs", []):
        nm = iname(x.get("UniqueId"))
        items.append({"id": x.get("UniqueId"), "name": nm, "category": item_category(nm),
                      "count": x.get("StackCount")})
    items.sort(key=lambda x: -(x["count"] or 0))
    # grouped summary for planning
    inv_by_cat = {}
    for it in items:
        g = inv_by_cat.setdefault(it["category"], {"types": 0, "total": 0, "top": []})
        g["types"] += 1
        g["total"] += it["count"] or 0
    for cat, g in inv_by_cat.items():
        g["top"] = [(it["name"], it["count"]) for it in items if it["category"] == cat][:5]
    inv_by_cat = dict(sorted(inv_by_cat.items(), key=lambda kv: -kv[1]["total"]))
    equipment = [{"id": x.get("UniqueId"), "name": ename(x.get("UniqueId")), "count": x.get("StackCount")}
                 for x in (ls.get("EquipmentItemListResponse", {}) or {}).get("EquipmentDBs", [])]
    equipment.sort(key=lambda x: (x["id"] or 0))

    # ---- echelons (saved teams) ----
    echelons = []
    for e in (ls.get("EchelonListResponse", {}) or {}).get("EchelonDBs", []):
        members = [cname(uid_of_server.get(s)) for s in (e.get("MainSlotServerIds") or []) if uid_of_server.get(s)]
        supports = [cname(uid_of_server.get(s)) for s in (e.get("SupportSlotServerIds") or []) if uid_of_server.get(s)]
        echelons.append({"type": e.get("EchelonType"), "number": e.get("EchelonNumber"),
                         "strikers": members, "specials": supports})

    # ---- campaign ----
    stages = (ls.get("CampaignListResponse", {}) or {}).get("StageHistoryDBs", [])
    campaign = {
        "stages_cleared": len(stages),
        "three_star": sum(1 for s in stages if s.get("Star1Flag") and s.get("Star2Flag") and s.get("Star3Flag")),
        "stages": [{"chapter": s.get("ChapterUniqueId"), "stage": s.get("StageUniqueId"),
                    "star1": bool(s.get("Star1Flag")), "star2": bool(s.get("Star2Flag")),
                    "star3": bool(s.get("Star3Flag")), "clear_count": s.get("TacticClearCountWithRankSRecord")}
                   for s in stages],
    }

    # ---- pvp / raids ----
    arena = (ls.get("ArenaLoginResponse", {}) or {}).get("ArenaPlayerInfoDB", {}) or {}
    raid = ls.get("RaidLoginResponse", {}) or {}
    pvp = {
        "arena_rank": arena.get("CurrentRank"),
        "arena_season_record": arena.get("SeasonRecord"),
        "arena_all_time_best": arena.get("AllTimeRecord"),
        "raid_tier": raid.get("LastSettledTier"),
        "raid_rank": raid.get("LastSettledRanking"),
    }

    # ---- cafe / social / memory / story / missions ----
    cafes = (ls.get("CafeGetInfoResponse", {}) or {}).get("CafeDBs", []) or []
    cafe = {"count": len(cafes), "ranks": [cc.get("CafeRank") for cc in cafes],
            "furniture_owned": sum(len(cc.get("FurnitureDBs") or []) for cc in cafes)}
    momotalk = [{"id": m.get("CharacterId"), "name": cname(m.get("CharacterId"))}
                for m in (ls.get("MomotalkOutlineResponse", {}) or {}).get("MomoTalkOutLineDBs", [])]
    story = {"read": len((ls.get("ScenarioListResponse", {}) or {}).get("ScenarioHistoryDBs", [])),
             "groups": len((ls.get("ScenarioListResponse", {}) or {}).get("ScenarioGroupHistoryDBs", []))}
    missions = {"history": len((P.get("Mission_List", {}) or {}).get("MissionHistoryUniqueIds", []))}
    memory_lobby = [m.get("MemoryLobbyUniqueId") for m in (ls.get("MemoryLobbyListResponse", {}) or {}).get("MemoryLobbyDBs", [])]

    summary = compute_summary(roster, weapons, ls)

    account_doc = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "game": f"Blue Archive ({region})",
        "region": region,
        "source_captured_at": profile.get("captured_at"),
        "account": account,
        "summary": summary,
        "planning": planning,
        "currencies": currencies,
        "inventory_by_category": inv_by_cat,
        "students": students,
        "items": items,
        "equipment": equipment,
        "echelons": echelons,
        "campaign": campaign,
        "pvp": pvp,
        "cafe": cafe,
        "story": story,
        "missions": missions,
        "momotalk": momotalk,
        "memory_lobby": memory_lobby,
        "captured_protocols": sorted(P.keys()),
    }

    # ---- write everything ----
    _wjson(os.path.join(out_dir, "account.json"), account_doc)
    _wcsv(os.path.join(out_dir, "students.csv"), students,
          ["id", "name", "name_en", "name_jp", "level", "exp", "exp_to_max", "star_grade", "eleph_owned",
           "eleph_to_5star", "eleph_ue_to_max", "eleph_needed", "eleph_deficit", "ue_star", "ue_level",
           "bond", "bond_exp", "skill_ex", "skill_basic", "skill_enhanced", "skill_sub",
           "skill_credits_to_max", "skill_mats_to_max",
           "equipment_tier_slots", "gear_credits_to_max", "gear_pieces_to_max"])
    if planning and planning.get("skills"):
        _wcsv(os.path.join(out_dir, "skill_materials.csv"), planning["skills"]["materials"],
              ["id", "name", "needed", "owned", "deficit"])
    if planning and planning.get("gear"):
        _wcsv(os.path.join(out_dir, "gear_pieces.csv"), planning["gear"]["pieces"],
              ["id", "name", "needed", "owned", "deficit"])
    _wcsv(os.path.join(out_dir, "items.csv"), items, ["id", "name", "category", "count"])
    _wcsv(os.path.join(out_dir, "equipment.csv"), equipment, ["id", "name", "count"])
    _wcsv(os.path.join(out_dir, "currencies.csv"),
          [{"currency": k, "amount": v} for k, v in currencies.items()], ["currency", "amount"])
    _wcsv(os.path.join(out_dir, "campaign.csv"), campaign["stages"],
          ["chapter", "stage", "star1", "star2", "star3", "clear_count"])

    return {"out_dir": out_dir, "students": len(students), "items": len(items),
            "equipment": len(equipment), "echelons": len(echelons), "currencies": len(currencies)}


def _wjson(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def _wcsv(path, rows, cols):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    # optional args: [1] = profile path (default: the Global capture), [2] = output dir.
    # Region (Global/JP) is auto-detected from the profile; output dir defaults per region.
    prof_path = sys.argv[1] if len(sys.argv) > 1 else PROFILE_LATEST
    try:
        profile = json.load(open(prof_path, encoding="utf-8"))
    except Exception as e:
        print(f"No capture at {prof_path} ({e}). Run the game + capture first."); sys.exit(1)
    region = detect_region(profile)
    names = load_names()                 # EN names (data/names.json)
    names_jp = _load(NAMES_JP)           # JP names (data/names_jp.json), if present
    item_names = _load(NAMES_ITEMS)
    item_names_jp = _load(NAMES_ITEMS_JP)
    gamedata = _load(GAMEDATA)
    plandata = _load(PLANDATA)
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(PROFILE_LATEST), "export_jp" if region == "JP" else "export")
    res = build_export(profile, names, out, item_names, gamedata, plandata, names_jp, item_names_jp)
    print(f"Exported [{region}] to", res["out_dir"])
    for k in ("students", "items", "equipment", "echelons", "currencies"):
        print(f"  {k}: {res[k]}")
    print("Files: account.json + students.csv, items.csv, equipment.csv, currencies.csv, campaign.csv")


if __name__ == "__main__":
    main()
