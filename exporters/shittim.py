r"""
Shittim-Server exporter: canonical profile -> the AccountData[] envelope that the
server's `!accountdata load` command reads.

Shittim's Commands/AccountDataCommand.cs already imports a captured account and does the
hard part -- remapping every ServerId so characters, weapons, gear, equipment and echelons
still point at each other after insertion. It just wants the data in the shape a captured
session has: a list of alternating REQUEST/RESPONSE entries where [1] is an
AccountAuthResponse and [3] is an AccountLoginSyncResponse.

That is exactly what ba-sniff already stores. The game structures are identical on both
sides (both speak MX), so this is an envelope, not a translation: CharacterDB fields map
one-for-one onto Shittim's CharacterDBServer.

ITEMS -- why they go inside the login bundle: LoadData() looks for ItemListResponse on the
login bundle and, when absent, falls back to reading envelope entry [5]. That fallback is
broken upstream (it assigns ItemListResponse but the AddItems call sits in the `else`
branch it just skipped, so the items are silently dropped). Real captures keep the item
list in its own packet, so we splice it into the login bundle where the working branch
picks it up.

Usage:
    python exporters/shittim.py                                   # latest Global capture
    python exporters/shittim.py captures/profile_jp_latest.json    # a specific profile
    python exporters/shittim.py captures/profile_latest.json out.json

With no output path it writes beside the profile. Load it from the Shittim Control Center:
Accounts -> New -> Browse.
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BA_ROOT = os.path.dirname(HERE)
DEFAULT_PROFILE = os.path.join(BA_ROOT, "captures", "profile_latest.json")


def find_account_db(profile):
    """The AccountDB row (nickname/level/rep character). Lives in Account_Auth."""
    for obj in profile.get("protocols", {}).values():
        if isinstance(obj, dict) and isinstance(obj.get("AccountDB"), dict):
            return obj["AccountDB"]
    return None


def find_login_sync(profile):
    """The login bundle: roster + every sub-response hanging off it.

    capture.py already aliases the bundle to Account_LoginSync regardless of which
    protocol number carried it (Global 1019, JP 1017), so this is a direct lookup with a
    content-based fallback for profiles captured before that aliasing existed.
    """
    protocols = profile.get("protocols", {})
    bundle = protocols.get("Account_LoginSync")
    if isinstance(bundle, dict):
        return bundle
    for obj in protocols.values():
        if isinstance(obj, dict) and "CharacterListResponse" in obj:
            return obj
    return None


def find_item_list(profile):
    """The item list. Ships as its own packet rather than inside the login bundle."""
    protocols = profile.get("protocols", {})
    bundle = protocols.get("Account_LoginSync")
    if isinstance(bundle, dict) and isinstance(bundle.get("ItemListResponse"), dict):
        return bundle["ItemListResponse"]
    for obj in protocols.values():
        if isinstance(obj, dict) and isinstance(obj.get("ItemDBs"), list):
            return obj
    return None


def find_id_card(profile):
    """The ID card: its settings and the backgrounds you own.

    Ships in the friend-list packet rather than the login bundle, so it is fetched the same
    way the item list is. Returns (FriendIdCardDB, IdCardBackgroundDBs) with either half
    None/empty when the capture never saw that packet.
    """
    for obj in profile.get("protocols", {}).values():
        if not isinstance(obj, dict):
            continue
        if isinstance(obj.get("FriendIdCardDB"), dict) or isinstance(obj.get("IdCardBackgroundDBs"), list):
            return obj.get("FriendIdCardDB"), obj.get("IdCardBackgroundDBs")
    return None, None


def build_account_data(profile):
    """canonical profile dict -> Shittim AccountData[] (pure; does not mutate `profile`).

    Raises ValueError when the profile lacks a piece the importer cannot do without.
    """
    account_db = find_account_db(profile)
    if account_db is None:
        raise ValueError("no AccountDB in this profile -- capture Account_Auth (log in) first")
    login_sync = find_login_sync(profile)
    if login_sync is None:
        raise ValueError("no login bundle in this profile -- capture Account_LoginSync first")

    login_sync = copy.deepcopy(login_sync)
    items = find_item_list(profile)
    if items is not None:
        # splice onto the bundle so LoadData takes the branch that actually inserts them
        login_sync["ItemListResponse"] = copy.deepcopy(items)

    # The ID card (chosen background, represent character, show/permission flags) and the
    # backgrounds you own arrive in the friend-list packet, not the login bundle. Spliced on
    # for the same reason the item list is: the envelope has fixed positions, and LoadData
    # reads everything off the bundle. Deliberately does NOT carry FriendDBs -- those are
    # other players' nicknames and account ids, and nothing imports them.
    id_card, backgrounds = find_id_card(profile)
    if id_card is not None:
        login_sync["FriendIdCardDB"] = copy.deepcopy(id_card)
    if backgrounds:
        login_sync["IdCardBackgroundDBs"] = copy.deepcopy(backgrounds)

    # Alternating REQUEST/RESPONSE, matching what AccountDataCommand.ExportData writes.
    # The keys MUST be lowercase: Utils/Loaddata.cs binds them with
    # [JsonPropertyName("payload")] / ("type"), and System.Text.Json is case-sensitive by
    # default -- PascalCase keys leave Payload as a default JsonElement, and the load dies
    # on GetRawText() with "Operation is not valid due to the current state of the object."
    return [
        {"payload": {}, "type": "REQUEST"},
        {"payload": {"AccountDB": copy.deepcopy(account_db)}, "type": "RESPONSE"},
        {"payload": {}, "type": "REQUEST"},
        {"payload": login_sync, "type": "RESPONSE"},
    ]


ANON_NICKNAME = "Sensei"
# Dropped wholesale: the import never reads these, and they are the parts that are either
# somebody else's data (clan roster, president, notice) or none of a reader's business
# (purchase history, arena standing).
ANON_DROP_SUBRESPONSES = ("ClanLoginResponse", "ArenaLoginResponse",
                          "BillingPurchaseListByNexonResponse")
# FriendDBs / SentRequestFriendDBs are other players' nicknames, levels and account ids. The
# splice never carries them, but dropping them here too means anonymize() is safe on any
# envelope, including one built by a future version that does.
ANON_DROP_BUNDLE_FIELDS = ("FriendCode", "FriendCount", "FriendDBs", "SentRequestFriendDBs")
ANON_DROP_ACCOUNT_FIELDS = ("CallName", "CallNameKatakana", "CallNameKorean", "Comment",
                            "CreateDate", "LastConnectTime", "LinkRewardDate",
                            "LastReturningDate", "CallNameUpdateTime")


def _replace_scalars(obj, mapping):
    """Rewrite scalar values anywhere in the tree. Keyed by (type, value) so that True
    does not collide with 1."""
    if isinstance(obj, dict):
        return {k: _replace_scalars(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_scalars(v, mapping) for v in obj]
    return mapping.get((type(obj).__name__, obj), obj)


def anonymize(account_data):
    """Strip identifying data while keeping the envelope importable (pure).

    Purges by VALUE rather than by field name: the account's ServerId also appears in
    CafeDBs[].AccountId, EchelonDBs[].AccountServerId, StickerBookDB.AccountId and
    AttachmentGetResponse, and a field checklist would leave those behind. The import
    reassigns ownership from the connection anyway, so zeroing them costs nothing.

    RepresentCharacterServerId is deliberately kept -- it points at a character row the
    ServerId remap needs.
    """
    data = copy.deepcopy(account_data)
    account = data[1]["payload"]["AccountDB"]
    bundle = data[3]["payload"]

    mapping = {}

    def purge(value, replacement):
        if value is not None and value != "":
            mapping[(type(value).__name__, value)] = replacement

    purge(account.get("ServerId"), 0)
    purge(account.get("PublisherAccountId"), 0)
    purge(account.get("Nickname"), ANON_NICKNAME)
    for field in ("CallName", "CallNameKatakana", "CallNameKorean", "Comment"):
        purge(account.get(field), "")
    purge(bundle.get("FriendCode"), "")
    # The ID card carries its own copy of the friend code and greeting. Seed from it too, so a
    # capture that saw the friend-list packet but not the login-bundle FriendCode is still
    # scrubbed -- the value purge below then catches every other occurrence.
    id_card = bundle.get("FriendIdCardDB")
    if isinstance(id_card, dict):
        purge(id_card.get("FriendCode"), "")
        purge(id_card.get("Comment"), "")

    data = _replace_scalars(data, mapping)

    account = data[1]["payload"]["AccountDB"]
    bundle = data[3]["payload"]
    for field in ANON_DROP_ACCOUNT_FIELDS:
        account.pop(field, None)
    for field in ANON_DROP_BUNDLE_FIELDS:
        bundle.pop(field, None)
    for field in ANON_DROP_SUBRESPONSES:
        bundle.pop(field, None)
    return data


def summarize(account_data):
    """Counts for the console, so a bad import is obvious before you load it in-game."""
    account = account_data[1]["payload"]["AccountDB"]
    bundle = account_data[3]["payload"]
    chars = bundle.get("CharacterListResponse", {})

    def n(section, key):
        return len(bundle.get(section, {}).get(key, []) or [])

    return {
        "nickname": account.get("Nickname"),
        "level": account.get("Level"),
        "exp": account.get("Exp", 0),
        "characters": len(chars.get("CharacterDBs", []) or []),
        "weapons": len(chars.get("WeaponDBs", []) or []),
        "costumes": len(chars.get("CostumeDBs", []) or []),
        "equipment": n("EquipmentItemListResponse", "EquipmentDBs"),
        "gear": n("CharacterGearListResponse", "GearDBs"),
        "items": n("ItemListResponse", "ItemDBs"),
        "echelons": n("EchelonListResponse", "EchelonDBs"),
        "memory_lobby": n("MemoryLobbyListResponse", "MemoryLobbyDBs"),
        "furniture": n("CafeGetInfoResponse", "FurnitureDBs"),
    }


def default_out_path(profile_path):
    """Written next to the capture it came from.

    It used to try to locate a sibling Shittim-Server checkout and write straight into its
    AccountData folder. That only worked for one specific layout -- a source build in a
    sibling directory of that exact name -- and silently did nothing useful for anyone
    running the packaged Control Center, whose server lives elsewhere. The Control Center's
    Import button takes a file from anywhere on disk now, so there is nothing to guess.
    """
    stem = os.path.splitext(os.path.basename(profile_path))[0]
    name = stem.replace("profile_", "shittim_") + ".json"
    return os.path.join(os.path.dirname(os.path.abspath(profile_path)), name)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    anon = "--anonymize" in sys.argv

    profile_path = args[0] if args else DEFAULT_PROFILE
    if not os.path.exists(profile_path):
        print(f"[-] profile not found: {profile_path}")
        sys.exit(1)
    with open(profile_path, encoding="utf-8") as fh:
        profile = json.load(fh)

    try:
        account_data = build_account_data(profile)
    except ValueError as exc:
        print(f"[-] {exc}")
        sys.exit(1)
    if anon:
        account_data = anonymize(account_data)

    out_path = args[1] if len(args) > 1 else default_out_path(profile_path)
    if anon and len(args) < 2:
        stem, ext = os.path.splitext(out_path)
        out_path = stem + "_anon" + ext
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(account_data, fh, ensure_ascii=False, indent=2)

    s = summarize(account_data)
    print(f"[+] {profile_path}  (region {profile.get('region') or 'Global'})"
          + ("  [ANONYMIZED]" if anon else ""))
    print(f"    {s['nickname']}  Lv{s['level']} (exp {s['exp']})")
    print(f"    {s['characters']} characters, {s['weapons']} weapons, {s['equipment']} equipment,")
    print(f"    {s['items']} items, {s['gear']} gear, {s['echelons']} echelons,"
          f" {s['memory_lobby']} memory lobby, {s['furniture']} furniture")
    print(f"[+] wrote {out_path}")
    print("    load it: Shittim Control Center -> Accounts -> New -> Import a saved profile")
    print("             -> Browse, and point it at the file above")


if __name__ == "__main__":
    main()
