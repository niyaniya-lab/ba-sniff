"""
Tests for the Shittim-Server exporter.

These run against the REAL captures in captures/ (no mocks, per the exporter's contract of
being a pure profile-dict -> envelope function). The envelope shape is dictated by
Shittim's Commands/AccountDataCommand.cs LoadData(), which indexes entries [1] and [3]
positionally and reads specific sub-responses off them — so the tests assert the exact
positions and keys that command dereferences, not merely "some JSON came out".

The JP profile is the interesting case: it is a low-level account, so it carries
AccountDB.Exp, which the max-level Global capture omits entirely.

Run:  python -m pytest test_shittim_export.py -v
   or python test_shittim_export.py     (built-in runner, no pytest needed)
"""

import copy
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("shittim", os.path.join(HERE, "exporters", "shittim.py"))
shittim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shittim)

GLOBAL_PROFILE = os.path.join(HERE, "captures", "profile_latest.json")
JP_PROFILE = os.path.join(HERE, "captures", "profile_jp_latest.json")


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _skip(path):
    if not os.path.exists(path):
        print(f"  SKIPPED - no capture at {path}")
        return True
    return False


def test_envelope_positions_match_what_loaddata_indexes():
    """LoadData() reads accountData[1] and accountData[3] by index and expects the
    REQUEST/RESPONSE alternation a real captured session has. Get the positions wrong and
    the import silently deserializes the wrong object."""
    if _skip(GLOBAL_PROFILE):
        return
    data = shittim.build_account_data(_load(GLOBAL_PROFILE))
    assert len(data) == 4, f"expected 4 entries, got {len(data)}"
    assert [e["type"] for e in data] == ["REQUEST", "RESPONSE", "REQUEST", "RESPONSE"], \
        f"alternation wrong: {[e['type'] for e in data]}"
    assert "AccountDB" in data[1]["payload"], "entry [1] must be the AccountAuthResponse"
    assert "CharacterListResponse" in data[3]["payload"], "entry [3] must be the LoginSyncResponse"
    print(f"  positions ok: [1]=AccountDB  [3]=CharacterListResponse")


def test_envelope_keys_are_lowercase():
    """Utils/Loaddata.cs binds these with [JsonPropertyName("payload")] / ("type") and
    System.Text.Json is case-sensitive by default. PascalCase keys bind to nothing, leaving
    Payload as a default JsonElement, and the import dies on GetRawText() with
    "Operation is not valid due to the current state of the object." -- which is exactly
    how this was found, on a live server."""
    if _skip(GLOBAL_PROFILE):
        return
    for i, entry in enumerate(shittim.build_account_data(_load(GLOBAL_PROFILE))):
        assert set(entry) == {"payload", "type"}, \
            f"entry [{i}] keys must be exactly payload/type, got {sorted(entry)}"
    print("  all entries use lowercase payload/type")


def test_every_subresponse_loaddata_touches_is_present():
    """LoadData() dereferences each of these directly (e.g.
    accountLoginSyncData.CharacterGearListResponse.GearDBs). A missing one is a
    NullReferenceException mid-import, after some rows have already been written."""
    if _skip(GLOBAL_PROFILE):
        return
    bundle = shittim.build_account_data(_load(GLOBAL_PROFILE))[3]["payload"]
    required = ["CharacterListResponse", "EquipmentItemListResponse", "CharacterGearListResponse",
                "EchelonListResponse", "MemoryLobbyListResponse", "CafeGetInfoResponse"]
    missing = [r for r in required if not isinstance(bundle.get(r), dict)]
    assert not missing, f"missing sub-responses: {missing}"
    print(f"  all {len(required)} sub-responses present")


def test_items_are_spliced_into_the_bundle_not_left_as_a_separate_entry():
    """Items ship in their own packet, but LoadData()'s separate-entry fallback assigns
    ItemListResponse without ever calling AddItems (the AddItems call lives in the `else`
    branch that fallback skips). Splicing into the bundle takes the branch that inserts."""
    if _skip(GLOBAL_PROFILE):
        return
    profile = _load(GLOBAL_PROFILE)
    bundle = shittim.build_account_data(profile)[3]["payload"]
    items = bundle.get("ItemListResponse")
    assert isinstance(items, dict), "ItemListResponse must be on the login bundle"
    assert len(items.get("ItemDBs", [])) > 0, "ItemDBs must be non-empty"
    # and it must really be the standalone packet's contents
    standalone = shittim.find_item_list(profile)
    assert len(items["ItemDBs"]) == len(standalone["ItemDBs"]), "spliced item list is truncated"
    print(f"  {len(items['ItemDBs'])} items spliced onto the bundle")


def test_character_rows_keep_the_fields_shittim_maps():
    """CharacterDBServer is populated by name from these. ServerId and EquipmentServerIds
    matter most: the ServerId remap keys off them, so dropping either silently detaches
    every character from its gear."""
    if _skip(GLOBAL_PROFILE):
        return
    bundle = shittim.build_account_data(_load(GLOBAL_PROFILE))[3]["payload"]
    chars = bundle["CharacterListResponse"]["CharacterDBs"]
    assert chars, "no characters"
    required = ["ServerId", "UniqueId", "StarGrade", "Level", "FavorRank",
                "PublicSkillLevel", "ExSkillLevel", "PassiveSkillLevel",
                "ExtraPassiveSkillLevel", "EquipmentServerIds"]
    missing = [f for f in required if f not in chars[0]]
    assert not missing, f"CharacterDB is missing {missing}"
    assert len(chars[0]["EquipmentServerIds"]) == 3, "expected 3 equipment slots"
    print(f"  {len(chars)} characters, first has all {len(required)} mapped fields")


def test_purity_input_profile_is_not_mutated():
    """The exporter splices ItemListResponse onto the bundle. If it did that in place it
    would corrupt the caller's profile dict - and ba_export/ba_view load the same dict."""
    if _skip(GLOBAL_PROFILE):
        return
    profile = _load(GLOBAL_PROFILE)
    before = copy.deepcopy(profile)
    shittim.build_account_data(profile)
    assert profile == before, "build_account_data mutated the profile it was given"
    print("  input profile untouched")


def test_jp_profile_carries_exp_that_global_omits():
    """The max-level Global account has no AccountDB.Exp at all; the low-level JP account
    does. Both must export - this is the regression guard for treating an absent Exp as an
    error rather than as the zero it means."""
    if _skip(JP_PROFILE):
        return
    jp = shittim.build_account_data(_load(JP_PROFILE))[1]["payload"]["AccountDB"]
    assert "Exp" in jp, "JP capture should carry Exp"
    assert jp["Exp"] > 0, f"expected non-zero Exp, got {jp['Exp']}"
    print(f"  JP: Lv{jp['Level']} exp={jp['Exp']}")

    if not os.path.exists(GLOBAL_PROFILE):
        return
    gl = shittim.build_account_data(_load(GLOBAL_PROFILE))[1]["payload"]["AccountDB"]
    assert "Exp" not in gl, "Global capture unexpectedly has Exp - revisit the omission rule"
    print(f"  Global: Lv{gl['Level']} exp absent (max level) -> imports as 0, which is correct")


def test_anonymize_removes_every_identifier_from_the_whole_document():
    """Swept as a substring search over the serialized JSON rather than a field checklist,
    because a checklist only catches the fields someone remembered. The account ServerId in
    particular also appears in CafeDBs[].AccountId, EchelonDBs[].AccountServerId,
    StickerBookDB.AccountId and AttachmentGetResponse -- none of them obvious."""
    if _skip(GLOBAL_PROFILE):
        return
    plain = shittim.build_account_data(_load(GLOBAL_PROFILE))
    account = plain[1]["payload"]["AccountDB"]
    bundle = plain[3]["payload"]

    secrets = [str(account[f]) for f in ("ServerId", "PublisherAccountId", "Nickname",
                                         "CallName", "CallNameKatakana", "CallNameKorean",
                                         "Comment") if account.get(f)]
    if bundle.get("FriendCode"):
        secrets.append(str(bundle["FriendCode"]))
    clan = bundle.get("ClanLoginResponse", {}).get("AccountClanDB", {})
    if clan.get("ClanName"):
        secrets.append(str(clan["ClanName"]))
    assert len(secrets) >= 5, f"expected several identifiers to scrub, found {secrets}"

    text = json.dumps(shittim.anonymize(plain), ensure_ascii=False)
    leaked = [s for s in secrets if s in text]
    assert not leaked, f"anonymize leaked: {leaked}"
    print(f"  {len(secrets)} identifiers scrubbed, none found anywhere in the document")


def test_anonymized_file_is_still_importable():
    """Scrubbing must not break the import: everything LoadData() dereferences has to
    survive, and RepresentCharacterServerId must be kept because it points at a character
    row the ServerId remap needs."""
    if _skip(GLOBAL_PROFILE):
        return
    plain = shittim.build_account_data(_load(GLOBAL_PROFILE))
    anon = shittim.anonymize(plain)

    assert [e["type"] for e in anon] == ["REQUEST", "RESPONSE", "REQUEST", "RESPONSE"]
    account = anon[1]["payload"]["AccountDB"]
    assert account.get("RepresentCharacterServerId"), "RepresentCharacterServerId must survive"
    for field in ("State", "Level"):
        assert field in account, f"{field} must survive - LoadData assigns it"

    bundle = anon[3]["payload"]
    for required in ("CharacterListResponse", "EquipmentItemListResponse",
                     "CharacterGearListResponse", "EchelonListResponse",
                     "MemoryLobbyListResponse", "CafeGetInfoResponse", "ItemListResponse"):
        assert isinstance(bundle.get(required), dict), f"{required} must survive"

    before = plain[3]["payload"]["CharacterListResponse"]["CharacterDBs"]
    after = bundle["CharacterListResponse"]["CharacterDBs"]
    assert len(before) == len(after), "roster changed size"
    assert before[0]["UniqueId"] == after[0]["UniqueId"], "roster contents altered"
    print(f"  roster intact ({len(after)}), rep character kept, all sub-responses survive")


def test_anonymize_does_not_mutate_its_input():
    if _skip(GLOBAL_PROFILE):
        return
    plain = shittim.build_account_data(_load(GLOBAL_PROFILE))
    before = copy.deepcopy(plain)
    shittim.anonymize(plain)
    assert plain == before, "anonymize mutated the envelope it was given"
    print("  input envelope untouched")


def test_missing_pieces_fail_loudly_rather_than_exporting_a_broken_file():
    """A profile with no login bundle must raise, not write a file that half-imports and
    leaves the account in a partial state."""
    for bad, why in [({"protocols": {}}, "empty profile"),
                     ({"protocols": {"Account_Auth": {"AccountDB": {"Nickname": "x"}}}}, "no login bundle")]:
        try:
            shittim.build_account_data(bad)
            assert False, f"{why}: expected ValueError, got a result"
        except ValueError as exc:
            print(f"  {why} -> ValueError: {exc}")


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            print(f"[RUN] {t.__name__}")
            t()
            print(f"[PASS] {t.__name__}\n")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {t.__name__}: {exc}\n")
        except Exception as exc:  # noqa
            failed += 1
            print(f"[ERROR] {t.__name__}: {type(exc).__name__}: {exc}\n")
    total = len(tests)
    print(f"==== {total - failed}/{total} passed ====")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if _run_all() else 0)
