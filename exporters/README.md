# Exporters (future)

`profile_latest.json` stores account data in the **game's native structure**,
keyed by MX protocol name (e.g. `Account_Auth`, `Character_List`). That canonical
dump is intentionally decoupled from any tracker's format.

Exporters live here and transform the canonical dump into third-party formats.
None are implemented yet — planned targets:

- **SchaleDB** — `localStorage["chara"]` = `{ studentId: {s:starGrade, ws:UE-stars,
  wl:UE-level, l:level, b:bond, e1/e2/e3:equip tiers, s1/s2/s3:skills} }`.
  Maps from `Character_List` (proto 2000) fields: `UniqueId, StarGrade, Level,
  FavorRank, ExSkillLevel, PublicSkillLevel, PassiveSkillLevel,
  ExtraPassiveSkillLevel, EquipmentServerIds[3]` (see Shittim-Server CharacterDB.cs).
- Other browser trackers — TBD.

Each exporter should be a pure function: `canonical profile dict -> target format`,
with no capture/Frida dependencies, so it's easy to test in isolation.
