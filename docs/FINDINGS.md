# ba-sniff — Engineering Findings & Context

> Purpose: durable brain-dump so a future session (human or LLM) can pick this up
> without re-deriving anything. Written 2026-07-10. Everything below was verified
> against the live **Blue Archive Global (Steam)** client, Unity IL2CPP,
> `GameAssembly.dll` (~224 MB), Windows x64.

---

## 1. Goal & scope

Export **your own** Blue Archive account data to local JSON — player level,
currency (pyroxene), campaign clears, student roster, momotalk, etc. — for
personal progress tracking. Currently a **standalone project** (a Discord/kei-bot
ingest is deferred). Read-only, single account, on your own PC.

Repo lives at `%USERPROFILE%\Documents\GitHub\ba-sniff` (its own git repo,
sibling to `kei-bot`). Reference private-server clone at
`%USERPROFILE%\Documents\GitHub\Shittim-Server` (protocol/crypto reference only).

---

## 2. The headline conclusion

**Passive network sniffing CANNOT decrypt the data. Frida (reading from inside
the client) is the only viable approach, and it works well.**

### Why passive fails (fully investigated, do not retry)
- BA's gateway (`nxm-*-bagl.nexon.com`, path `/api/gateway`) uses app-layer crypto
  on top of TLS. Packet layering (reverse-engineered and confirmed):
  - **request** `mx` multipart field =
    `[crc u32][typeConversion i32][keyLen u8][ivLen u8][key][iv][ XOR(0xD9) → gzip → AES(json) ]`
  - **response** = `base64 → AES(json)` (whole body; in-session responses are NOT
    a `{protocol,packet}` envelope — that's the private-server's simplified form).
- The AES **session key** is delivered in `Queuing_GetCryptoKeys` as a **256-byte
  RSA-2048 ciphertext** wrapped to **Nexon's** gateway public key. Only Nexon holds
  the private key → unrecoverable by observation. The clear 32-byte value in the
  request header (`99aa1f5d…`, static across sessions) decrypts nothing.
- Shittim-Server only decrypts this because it's a **private server** that patches
  the client to embed **its own** keypair. That does not help passive sniffing, so
  reusing its C# crypto is a dead end for our use case.

Passive tooling still exists in the repo (`ba_sniff.py`, `ba_codec.py`, `run.ps1`,
`install-cert.ps1`) and correctly does selective-TLS interception + packet framing,
but it stops at the RSA wall. Kept for reference; not the product path.

### Why Frida works
BA Global Steam is Unity **IL2CPP**, attaches cleanly with Frida, **no anti-tamper,
no anti-cheat** (verified — no GameGuard/nProtect/etc. modules). We read each packet
as plaintext **after the client decrypts it**, by hooking the client's own
`Newtonsoft.Json` deserializer. This sidesteps all crypto and auto-adapts to key
rotation.

---

## 3. How the capture works (the core hook)

Hook, by NAME (durable across patches — no offsets):
`Newtonsoft.Json.JsonConvert.DeserializeObject(string, …)` and
`Newtonsoft.Json.JsonSerializer.Deserialize(string, …)` — all overloads whose
**first parameter is `System.String`** and that have a non-null `virtualAddress`.
Use `Interceptor.attach` (read-only; never `.implementation`).

In `onEnter`, read `new Il2Cpp.String(args[0]).content`. MX packets are JSON objects
with a top-level `"Protocol"` field → filter on `s.indexOf('"Protocol"')`. Forward
via `send({type:"packet", json:s})`. Dedup with a `Set` of `len+":"+s.slice(0,48)`.

**Proven:** captured live `Account_Auth` (proto 1002) with the real
`Nickname`, `Level` and `ServerId` fields populated.

### Full account snapshot at login (no navigation needed)
`Account_LoginSync` (proto **1019**) fires once at login and bundles the **entire
account** in ~31 sub-responses: `CharacterListResponse` (roster, 168 students),
`AccountCurrencySyncResponse` (Gem/pyroxene, Gold), `CampaignListResponse`
(`StageHistoryDBs`, 253 clears), `MomotalkOutlineResponse` (160), `ScenarioListResponse`,
`EquipmentItemListResponse`, `CharacterGearListResponse`, `EchelonListResponse`,
`ClanLoginResponse`, arena/raid, etc. **Just log in with the tool attached and you
get everything.** (The roster has NO standalone request — it only ships in LoginSync.)

---

## 4. On-demand refresh (fire a protocol without navigating)

The client uses a `*NetworkTask` per protocol (e.g. `CampaignListNetworkTask`,
`ItemListNetworkTask`). Dispatch recipe that WORKS:

1. `Il2Cpp.gc.choose(BA.class("Assets._MX.Program.Scripts.Network.NetworkTaskManager"))[0]`
   → the live manager (a `MonoSingleton`).
2. Create the task via a **throwaway** `GameObject`:
   `const go = coreImg.class("UnityEngine.GameObject").new();`
   `const task = go.method("AddComponent",1).invoke(taskClass.type.object);`
   - Tasks extend `UnityEngine.MonoBehaviour`, so `taskClass.new()` yields
     `m_CachedPtr = 0` and faults. You MUST `AddComponent`.
   - Tasks are `[DisallowMultipleComponent]`, so a second `AddComponent` of the same
     type on the same GameObject throws → use a fresh GameObject per fire.
3. `mgr.method("Request").invoke(task, NULL)` — null `Action<TaskState>` callback is
   fine; the reply is caught by the capture hook. (`NetworkTaskManager.Request` starts
   the coroutine internally.)
4. Destroy the throwaway GameObject ~15 s later (`UnityEngine.Object.Destroy`) so they
   don't accumulate.

### CRITICAL threading gotcha (cost a lot of time — read this)
The dispatch (native Unity calls `get_gameObject`/`AddComponent`/`Request`) **only
works inside the real Unity player loop on the main thread**. What we learned:
- `Il2Cpp.perform(fn, "main")` **from an RPC thread** reports `currentThread == mainThread`
  but STILL faults on native Unity calls (pure-il2cpp calls like `gc.choose` work).
  Do NOT rely on it for Unity API calls triggered from RPC.
- Hooking `UnityEngine.Time.get_frameCount` and draining there → **froze the game**
  (called thousands of times/frame, some off-main; plus `gc.choose` forces a GC).
- **Solution that works:** hook **`GameMain.Update`** (BlueArchive assembly; once per
  frame; guaranteed main-thread player loop). Queue fires from RPC; drain the queue
  inside `GameMain.Update`'s `onEnter`. Only run `gc.choose`/dispatch while the queue
  is non-empty → no per-frame cost, no freeze.

### The refresh "button" = AccountLoginSyncNoPart
- `AccountLoginSyncNetworkTask` (the heavy one) **cannot be fired cold** — it needs
  login-flow context (a populated session request) → "system error".
- **`AccountLoginSyncNoPartNetworkTask`** (the LIGHT variant) **fires cold** and returns
  the entire `Account_LoginSync` payload (419 KB): roster (168, current), currency,
  campaign, momotalk, gear, echelons, scenario — 25 sub-responses. **This is the full
  refresh.** Verified end-to-end: fired → captured → stored → roster 168.
- Individual list tasks that fire cleanly (same simple pattern as CampaignList):
  `CampaignListNetworkTask` (6000), `ItemListNetworkTask` (4000),
  `EquipmentListNetworkTask` (3000), `CharacterGearListNetworkTask`,
  `EchelonListNetworkTask` (5000), `MissionListNetworkTask`.

### Student names from the game itself (no external data — WIRED)
The client resolves character UniqueId → localized display name in memory:
`MX.Data.LocalizeEtcData` (instance; `gc.choose` → 1 live) method
`GetName(FlatData.ParcelType, System.Int64)` with `ParcelType.Character` (=`1`).
Verified live: `GetName(1, 10000) → "Aru"`, `10004 → "Hina"`, `10022 → "Hina (Swimsuit)"`
— exactly as the game displays, in the client's language (English on Global).
Resolved all 168 owned ids. Exposed as `rpc.resolveNames(ids)` in
`capture_refresh.ts`; the GUI caches results to `data/names.json` (git-ignored).
This is why we do NOT need SchaleDB for names. (SchaleDB IDs do match `UniqueId`
if a static source is ever wanted, but the in-game resolver is self-contained.)

### In-game reference data resolvers — the `*Data.TryGetValue(id, out Excel)` pattern (WIRED)
The client exposes small `MX.Data.*Data` helper singletons (`gc.choose` → 1 live) that
wrap the FlatBuffer Excel tables. This is how we read all reference/static data from the
game itself — no external sources, always matches the installed version.

- **Names** — `MX.Data.LocalizeEtcData.GetName(ParcelType, Int64 id)`.
  `ParcelType` == each DB's `Type` field: **Character=1, Equipment=3, Item=4**, Currency, etc.
  `GetName(1,10000)→"Aru"`, `GetName(4,23)→"Eligma"`, `GetName(3,1000)→"Plain Baseball Cap"`.
  RPC `resolveTyped(parcelType, ids)` / `resolveNames(ids)`.
- **AP cap** — `MX.Data.AccountExpInfoData.TryGetValue(level, out AccountLevelExcel)`
  `.get_APAutoChargeMax()` (Lv90→240, Lv80→220). RPC `apCap(level)`.
- **Character EXP curve** — `MX.Data.CharacterData.GetLevelExpData(level).get_TotalExp()`
  (cumulative EXP; `TotalExp(90)=1,249,185`). RPC `charExpCurve(maxLevel)`.
- **Item category / rarity** — `MX.Data.ItemData.TryGetValue(id, out ItemExcel)`
  `.get_ItemCategory()` → enum name (`SecretStone`/`CharacterExpGrowth`/`Coin`/`Material`).
  (Coarser than our name heuristics, so the export keeps name-based buckets.)

**Out-param calling pattern (frida-il2cpp-bridge):** for `TryGetValue(id, out Excel)` where
`Excel` is a FlatBuffer value type: `const ref = Il2Cpp.reference(ExcelClass.alloc());
inst.method("TryGetValue",2).invoke(id, ref); ref.value.method("get_X").invoke();`.
Call on the **instance** (`gc.choose(...)[0]`), not the class. Enum-typed returns stringify
to their name in template literals.

**Upgrade costs — use the client's OWN cost calculators (don't reimplement recipes).**
Costs are a recipe system (`upgrade → RecipeId → RecipeData.GetCraftData → ingredients`),
but the client exposes STATIC calculators that return a `ParcelCost` directly — call those:
- **Skills (DONE)** — `MX.GameLogic.Service.CharacterDBService.GetSkillLevelUpMaterialParcelInfos(
  CharacterDB, SkillSlot, int targetLevel)` (static). Feed a **live CharacterDB**
  (`gc.choose(MX.GameLogic.DBModel.CharacterDB)` → 168 live). Slots: EX=`ExSkill01`,
  Basic=`PublicSkill01`, Enhanced=`Passive01`, Sub=`ExtraPassive01` (enum
  `MX.Logic.BattleEntities.SkillSlot`). Cost is **per-step** → sum levels current+1..cap
  (EX cap 5, others 10). RPC `skillMaterialsToMax()`; see §12.
- **Star + UE eleph (DONE)** — the client has direct eleph calculators, no recipe walk needed.
  Both static on `MX.GameLogic.Service.CharacterDBService`:
  `Int64 CalcRemainEligmaCountForCharacterMaxGrade(Int32 curStarGrade, Int64 charId)` → eleph to 5*
  (1★→330, 2★→300, 3★→220; 5★→0); `Int64 CalcRemainEligmaCountForWeaponMaxGrade(Int64 charId,
  Int32 curWeaponStar)` → eleph to max UE (0★→0, 1★→500, 2★→380). Star grade from `CharacterDB`;
  weapon star from `WeaponDB.get_StarGrade()` matched by `get_BoundCharacterServerId → CharacterDB
  ServerId`. RPC `gradeCostsToMax()` → `{uid:{eleph_star,eleph_ue}}`; see §12.
- **Gear tier-up (DONE)** — walk each equipped `EquipmentDB` up its tier chain:
  `EquipmentDBService.TryGetTierUpRecipe(EquipmentDB, out MX.Data.RecipeInfo)` (false at max) →
  `RecipeInfo.get_RecipeIngredientId()` → `RecipeService.CraftCost(id)` → `ParcelCost` (credits +
  `Equipment:` tier pieces). Advance with `TryGetNextTierEquipmentId(curId, out nextId)`, then
  **synthesize** the next-tier DB (`EquipmentDB.alloc()` + `set_UniqueId(nextId)` + `set_Tier(t+1)`)
  and repeat — the calculator reads UniqueId+Tier, so a synthesized DB walks the full chain.
  Group equipped `EquipmentDB` by `get_BoundCharacterServerId` (0 = unequipped, skip). Gear pieces
  are `ParcelType.Equipment` (=3) → name via `resolveTyped(3, ids)`. RPC `gearMaterialsToMax()` →
  `{uid:{"Type:id":amount}}`; see §12.

**Reading `ParcelCost`:** `get_ParcelInfos()` → `List<ParcelInfo>`; each `ParcelInfo` has
`get_Key()` (`ParcelKeyPair` = `get_Type` enum + `get_Id`) and `get_Amount()`. **Read the enum
type via `String(...)`** ("Currency"/"Item"/"Equipment") — `Number(enum)` yields NaN. `Currency:1`
= credits. **All five investment axes are implemented — EXP, skills, star eleph, UE eleph, gear (§12).**

### In-memory account model singletons (found, not wired)
`AccountInfo` (`AccountDB` + `get_Level/Nickname/Comment/…`) and `AccountCurrencyInfo`
(`get_DBCache()` → `AccountCurrencyDB`) are live singletons — a viable no-network read path
for account/currency. `CharacterDBService` has 0 instances (roster comes via LoginSync, not
a memory walk).

---

## 5. Key protocol IDs (from Shittim-Server `ProtocolData.cs`)

`1002 Account_Auth` · `1003 Account_CurrencySync` · `1019 Account_LoginSync` ·
`2000 Character_List` (no standalone task — LoginSync only) · `3000 Equipment_List` ·
`4000 Item_List` · `5000 Echelon_List` · `6000 Campaign_List` · `8000 Mission_List` ·
`8005 Mission_Sync` · `19000 Scenario_List` · `44000 CharacterGear_List` ·
`50000 Queuing_GetTicket` · `50001 Queuing_GetCryptoKeys` · `50002 Queuing_GetAuthTicket`.
Full name↔id map in `ba_codec.py` (`PROTOCOL_NAMES`).

Roster field names (Shittim `CharacterDB.cs`): `UniqueId, StarGrade, Level, Exp,
FavorRank(bond), PublicSkillLevel, ExSkillLevel, PassiveSkillLevel,
ExtraPassiveSkillLevel, IsFavorite, EquipmentServerIds[3], PotentialStats`.

---

## 6. File map

```
ba_gui.py                 COMPANION GUI — auto-attach, Overview (account/currencies/stats)
                          + Students tab (searchable, click detail), live AP counter,
                          auto-sync, name/item resolution, Export button
ba_export.py              COMPLETE ACCOUNT EXPORT — account.json + CSVs (students/items/
                          equipment/currencies/campaign); names, categories, EXP-to-max,
                          eleph-owned. build_export(profile, names, out, item_names, gamedata)
grab_reference.py         one-shot: dump game cost curves (char EXP curve) -> data/gamedata.json
run_gui.ps1               launches the GUI (pythonw)
ba_codec.py               protocol id<->name map + (legacy) passive packet codec + tests
ba_sniff.py               LEGACY passive mitmproxy addon (kept for reference; RSA-blocked)
run.ps1 / install-cert.ps1  legacy passive tooling (selective TLS intercept, CA install)
test_ba_codec.py          round-trip tests for the passive codec
exporters/                (future) SchaleDB / other-tracker exporters — see its README
data/                     git-ignored caches: names.json (students), names_items.json
                          (item/equip), gamedata.json (cost curves)
frida/
  capture.py              production capture runner (--watch resident mode)
  refresh.py              interactive capture + on-demand refresh (Enter = full sync)
  reftest.py              headless refresh tester (for autonomous iteration)
  spike_*.py              gating probe / recon runners (spike_recon2.py dumps to captures/recon2.txt)
  agent/                  frida-il2cpp-bridge agents (TypeScript, compiled w/ frida-compile)
    capture_refresh.ts    PRODUCTION agent. rpc: refresh(tasks), listTasks, resolveNames,
                          resolveTyped(parcelType,ids), apCap(level), charExpCurve(maxLevel),
                          skillMaterialsToMax(), gradeCostsToMax(), gearMaterialsToMax().
                          Capture hook + GameMain.Update dispatch pump.
    capture.ts hook.ts index.ts invoke3.ts recon2.ts   capture-only / spikes / recon
    package.json          build:refresh (main), build / build:hook / build:capture / build:invoke
captures/                 OUTPUT (git-ignored): profile_latest.json, snapshots, raw, export/
docs/FINDINGS.md          this file
```

Git-ignored: `captures/` (real account data + keys), `data/` (name/cost caches),
`.venv/`, `frida/agent/node_modules/`, compiled `frida/agent/_*.js`.

---

## 7. Environment

- `ba-sniff/.venv` = **Python 3.12** + `mitmproxy 12` + `frida-tools 17`. Build it:
  `py -3.12 -m venv .venv; .\.venv\Scripts\python -m pip install frida-tools mitmproxy`
  (conda base's Python 3.9 / mitmproxy 9 is TOO OLD — no `--mode local:` per-process capture.)
- Frida agent (`frida/agent/`): Node.js + `frida-il2cpp-bridge@0.13` + `frida-compile@19` +
  `@types/frida-gum@19`. `npm install` then `npm run build:refresh` (and other `build:*`).
- Frida `console.log` goes to the **log handler**, NOT `on('message')`. In Python use
  `script.set_log_handler(...)`; `on('message')` only gets `send()`/`error`.
- `Il2Cpp` calls in scripts: pure-il2cpp reads (String.content, gc.choose, class/field
  lookups) work in any `Interceptor.onEnter`; native **Unity** calls need the player loop.

---

## 8. Quick usage

```powershell
# build agent (once / after edits)
cd frida\agent; npm install; npm run build:refresh; cd ..

# capture only (login snapshot). --watch = resident, re-capture every launch
..\.venv\Scripts\python.exe capture.py --watch

# interactive refresh: Enter = full sync; or type a *NetworkTask name; `tasks` to list
..\.venv\Scripts\python.exe refresh.py

# GUI companion (from repo root) — recommended entry point
.\run_gui.ps1                 # or: .\.venv\Scripts\python.exe ba_gui.py

# one-time (game open): cache cost curves for planning math
.\.venv\Scripts\python.exe grab_reference.py

# complete named account export -> captures/export/ (account.json + CSVs)
.\.venv\Scripts\python.exe ba_export.py     # or the GUI "Export account" button

# headless test (autonomous iteration)
..\.venv\Scripts\python.exe reftest.py 10 CampaignListNetworkTask
```

Output: `captures/profile_latest.json` = `{captured_at, protocols:{Name: <native game object>}}`,
keyed by protocol name, verbatim game structures. Timestamped `profile_<ts>.json` snapshots
per session (these accumulate → future trend charts, no extra work). `captures/export/` holds
the clean archive.

---

## 9. Durability / auto-recovery reasoning

Chosen approach is the most update-resilient available:
- Capture hook resolves by **library name** (`Newtonsoft.Json.JsonConvert`) — no offsets,
  survives most patches; fails loudly (class not found) only on a JSON-lib swap.
- **Key-agnostic** — the client hands us plaintext, so key rotation never breaks it.
- Invoke resolves classes by name too (`NetworkTaskManager`, `GameMain`, task classes).
- Frida offset hooks (raw addresses) were deliberately avoided — those break every patch.
- Needs the game **running + logged in** (uses the live session). Nothing works with the
  game closed — inherent to the whole approach (RSA blocks any offline/external path).

---

## 10. Companion app + export (built) — §12 has detail

- **Three launch modes** (one-click `.bat` each): **GL passive** (`launch_gl_passive.bat` →
  `capture.py`, hook-only, read-only, lightest footprint), **GL full** (`launch_gl_full.bat` →
  `ba_gui.py`, live GUI + auto-sync + the materials-to-max calculators), **JP passive**
  (`launch_jp_passive.bat` → `jp_capture.py`, IFEO early-inject, passive-only — §11b). Passive =
  only the read-only JSON hook (no pump/task-firing/`gc.choose`); full adds the active extras.
- **GUI** (`ba_gui.py`, full mode): Overview + Students tabs, live AP counter, auto-sync, name
  resolution on attach. Global only (needs `gc.choose`/attach).
- **Export** (`ba_export.py`): region-tagged `account.json` + CSVs. Auto-detects Global/JP
  (Yostar vs Nexon billing, protocol 1017 vs 1019). Per-student `name_en`/`name_jp` (canonical
  UniqueId key; slots filled independently); full "materials to max" (EXP/skills/star/UE/gear,
  Global). `login_bundle()` finds the roster regardless of protocol key (client/version-agnostic).
- **Offline viewer** (`ba_view.py`, `launch_viewer.bat`): loads any captured profile and opens a
  self-contained **local** HTML report (sortable/searchable roster, items, currencies, planning) —
  no frida, no game contact, nothing uploaded. Global + JP.

## 11. Roadmap / open items

- [x] **Full "materials to max"** (skills/star/UE/gear) — DONE via the client's own cost
      calculators (§4). Grabbed once game-open into `data/plan_costs.json`; export builds the
      per-student + roster gap sheets offline.
- [ ] Exact Activity Report EXP values from the game (currently Global constants in
      `grab_reference.py`; the EXP curve itself is exact).
- [ ] Exporters (`exporters/`): SchaleDB `localStorage["chara"]` format
      `{studentId: {s,ws,wl,l,b,e1/e2/e3,s1/s2/s3}}` — map from `CharacterDBs`.
- [x] **Offline HTML viewer** (`ba_view.py`) — done.
- [x] **Region support + tagging** (Global/JP auto-detect, `name_en`/`name_jp`) — done.
- [ ] **JP/EN name backfill** (offline): SchaleDB (UniqueId-keyed, both langs, can lag) and/or
      game-file localization extraction (installed client's tables; always current). See §11b.
- [ ] Trend charts from the accumulated `profile_<ts>.json` snapshots.
- [ ] Portraits: NOT in local files (art streams from Nexon CDN via Addressables; a full
      bundle/atlas pipeline). Deferred; the tool is data-focused.
- [ ] Packaging into a distributable `.exe` — see §14.
- [ ] kei-bot ingest (deferred, standalone for now).

## 11b. Multi-version support — JP WORKS via IFEO early-injection (passive capture)

BA **JP** (Yostar, `C:\YostarGames\BlueArchive_JP\BlueArchive.exe`, launched via the `xldr_`
loader) is **code-identical** to Global — same `BlueArchive.exe`, same IL2CPP, `MX.GameLogic.*`
classes, `CharacterDB` present. It ships **Wellbia XIGNCODE3** (`xhunter1.sys` in `C:\WINDOWS\`);
Global (Nexon/Steam) has none. **Full capture + export confirmed on a JP guest account** (11 named
students, currencies, equipment, campaign) — the tool works on JP with the launch method below.

**What the anti-cheat actually does (measured, not guessed):**
- `frida.attach` to *any running* instance (launcher-started **or one we spawned ourselves**) →
  `VirtualAllocEx 0x5 ACCESS_DENIED` **even as elevated admin**. Once the game is running, XIGNCODE's
  kernel callback strips injection rights from new handles. **Post-boot attach is impossible.**
- `frida.spawn` (launch suspended, inject before any code runs) **succeeds** — XIGNCODE isn't armed
  yet at spawn. **This is the only injection window: at process creation, before resume.**
- XIGNCODE does **NOT** kill an agent injected at spawn — it survives resume, login, and the lobby
  indefinitely. (An earlier "XIGNCODE kills Frida at ~20 s" claim was WRONG and is retracted: a
  no-Frida control launch died at the same ~20 s, so that death was launch-context, not detection.)
- A bare `frida.spawn` of the exe self-exits ~20 s because it lacks the launcher's **environment**
  (the launcher hands the game state via env/handles; parent-process is *not* the check — our
  spawned game's parent is the shim, yet it runs fine when the env is present).
- `device.enable_spawn_gating()` → `NotSupportedError('not yet supported on this OS')`. Unavailable.
- **`session.enable_child_gating()` IS available on Windows** (frida 17.15.4) — a *different* API
  from the device-wide spawn gating above; don't conflate them. Verified standalone
  (`cmd.exe` → `PING.EXE` caught). It does not help here — see the run.bat experiment below.

- **`gc.choose` HANGS the JP client** (measured, both loading AND stable in-lobby heap). frida-il2cpp-
  bridge's object enumeration is a stop-the-world heap walk; Global tolerates it, JP does not (even a
  one-shot `gc.choose(LocalizeEtcData)` readiness probe flipped `Responding=False`). **Consequence: the
  active features are Global-only** — the on-demand refresh pump, the materials-to-max calculators
  (all use `gc.choose`), and in-client name resolution. **JP is passive capture only.** (An attempt at
  a gated in-lobby name pass crashed the client twice and was reverted — do not retry in-client.)

**The working method — IFEO `Debugger` shim** (productionized: `frida/jp_shim.py` = the IFEO target,
`jp_capture.py` = self-elevating arm + orchestrate, `launch_jp_passive.bat` = one-click).
Set `HKLM\...\Image File Execution Options\BlueArchive.exe /v Debugger` to `python jp_shim.py`. When
the **Yostar launcher** launches the game, Windows instead runs our shim with the game's command line,
**inside the launcher's process/environment**. The shim: (1) deletes its own IFEO key (one-shot;
restores normal launching), (2) `frida.spawn([exe]+args, cwd=…)` — inherits the launcher env, so the
game runs properly, (3) injects the **hook-only** agent (`_capture.js` — NO pump, NO `gc.choose`)
**before resume**, (4) resumes and captures passively. Runs elevated (inherited from the elevated
launcher). Beats the catch-22: launcher provides the env, we still inject at creation.

**Launching JP ourselves via the stock `run.bat` chain — FAILS, do not retry** (`frida/jp_spawn_probe.py`,
2026-08-14). The game folder ships a stock `run.bat` that self-elevates and runs
`xldr_BlueArchiveOnline_JP_loader_x64.exe BlueArchive.exe`, i.e. a complete launch with **no Yostar
launcher involved** — which suggested we could own the launch without the IFEO key. Two runs, both dead:

- **Run 1** — spawn the loader with child gating, resume each child as it appears. The loader creates
  **WELLBIA's `ucsvc.exe` FIRST**, then `BlueArchive.exe`. Resuming `ucsvc.exe` arms XIGNCODE, so
  attaching to the (still suspended) game child returned `VirtualAllocEx 0x5 ACCESS_DENIED` — the same
  denial as post-boot attach. **XIGNCODE protects the game process even while it is suspended.**
- **Run 2** — hold `ucsvc.exe` suspended until the game is injected. **The loader blocks on it** and
  never creates the game (15 s deadlock guard fired). So there is no ordering in which the game process
  exists while XIGNCODE is unarmed: `ucsvc` must run first, and once it does, injection is denied.

**Corollary — why IFEO works and this doesn't:** the Yostar launcher starts `BlueArchive.exe`
**directly, without the `xldr` loader**, so nothing is armed when the shim spawns the game. The
launcher-supplied **environment** (§11b above) remains the reason a bare spawn dies at ~20 s; the
loader is not a substitute for it. **The IFEO shim is the only JP injection method. Keep it.**

**JP data quirks:**
- The login bundle (roster + all sub-responses) is **`Protocol_1017` on JP, not `Account_LoginSync`
  (1019)** as on Global — identical structure. `capture.py` content-aliases any packet containing
  `CharacterListResponse` to `Account_LoginSync`, so the exporter is client/version-agnostic.
- **Student/item `UniqueId`s are shared across regions** → the Global `names.json` labels JP data.
- **Names:** in-client resolution is impossible on JP (`gc.choose`). EN names come from the shared
  cache; a unit with no cached name exports fully under its id as `Student <id>` (never lost). True
  JP names (and EN for JP-ahead units) are a **future offline job**: SchaleDB (UniqueId-keyed, both
  languages, but can lag weeks) or — the thorough option — **game-file extraction** (the installed
  client's localization tables; offline, no `gc.choose`, always current). Deferred.

**Constraints for a JP tool:** passive-only; everything goes through the **single at-spawn session**
(no re-attach — post-boot attach is denied; no `gc.choose`); the tool must **own the launch** (IFEO or
equivalent early catch). **Risk:** XIGNCODE is real anti-cheat with server-side detection — early-inject
is currently undetected, but this is anti-cheat-adjacent and carries ban risk on real accounts (tested
on a throwaway). Global is the low-risk default; JP is opt-in, at-your-own-risk.

## 12. Export schema & planning (built)

`ba_export.build_export()` → `captures/export/`:
- `account.json`: `account, summary, planning, currencies, inventory_by_category, students,
  items, equipment, echelons, campaign, pvp, cafe, story, missions, momotalk, memory_lobby,
  captured_protocols`.
- CSVs: `students.csv` (incl. `exp_to_max`, eleph, gear cols), `skill_materials.csv`,
  `gear_pieces.csv`, `items.csv` (+category), `equipment.csv`, `currencies.csv`, `campaign.csv`.
- **Planning axes** (all in `planning`): `exp` (EXP to max level), `skills` (skill mats to max),
  `grades` (eleph to 5★ + UE), `gear` (equipment tier pieces to max). Account-specific costs
  (skills/grades/gear) are grabbed once game-open via `grab_reference.py` → `data/plan_costs.json`;
  the export joins them against owned inventory offline for per-student + roster gap sheets.
- **EXP-to-max**: `remaining = TotalExp(maxLv) - (cum[level-1] + exp)`, where `cum` is the
  game EXP curve in `data/gamedata.json` (`cum[L]` = TotalExp to leave level L; `cum[89]==cum[90]`).
  A Lv90 student → 0. Roster total, reports-owned/needed in `planning`.
- **eleph_owned**: name-join — item `"<Name>'s Eleph"` / `"<Name>' Eleph"` → student by name
  (both from the same `GetName`). 156/168 matched. Star+UE deficit = per-student need − owned.
- **item categories** (name heuristics, planning buckets): Eleph, Activity Report (EXP),
  Skill Material (Tech Notes/Blu-ray), Enhancement Stone (Gear), Keystone (UE), Coin, Ticket,
  Box/Selector, Craft Material, Gift, Other.

**Verifying the cost numbers** (no unit test possible — costs are account-specific + live-client
only; sanity-check against the game instead, while it's open):
- **Star eleph** is the cleanest check — the in-game star-up screen shows "X eleph to 5★"
  verbatim; it must match `students.csv` `eleph_to_5star` (confirmed 1★→330, 2★→300, 3★→220).
- **Skills**: the skill level-up screen shows mats per level → compare to `skill_materials.csv`.
- **Gear**: the equipment tier-up screen shows pieces → compare to `gear_pieces.csv`.
- **Smell tests** (offline): a Lv90 5★ student → `exp_to_max=0` and `eleph_to_5star=0`; a student
  with no UE → `eleph_ue_to_max=0`. Roster snapshot at last grab: 153 below 5★, 12 UE-maxable,
  28,598 eleph deficit, 160M credits + 81 gear-piece types to max all equipped gear.

## 13. Ethics / scope

Personal, single-account, read-only. Ideally don't automate like a bot (pace refreshes),
don't share other players' data, expect breakage on client updates (inherent). The
on-demand refresh issues the same read requests normal play does — keep it to that.

**No warranty / no support** for anyone's account (suspension, ban, rollback, data loss) — the
tool injects into the client (ToS risk on Global even without anti-cheat; higher on XIGNCODE3 JP).
Use at your own risk; prefer a throwaway account.

Repo is published **private** (account data git-ignored: `captures/`, `data/`;
verified nothing sensitive tracked before the first push).

## 14. Packaging / distribution (for handing the tool to others)

Use **PyInstaller** (not the abandoned py2exe). The compiled frida agents (`frida/agent/_*.js`)
must ship as bundled data — end users then need **neither Node nor `npm run build`** (only the dev
machine builds the agents). Rough shape:
- `pyinstaller --onedir --add-data "frida/agent/_capture.js;frida/agent/agent" ... jp_capture.py`
  (one build per entry point, or a small menu launcher dispatching to capture/GUI/viewer).
  `--onedir` starts faster and trips AV less than `--onefile`. Resolve bundled paths via `sys._MEIPASS`.
- Include the agent `.js` + any `data/*.json` to seed (names).
- **Frida is a native dep** — usually needs `--collect-all frida` (or a hook) so the `_frida`
  binaries + version file are packaged.
- **Elevation:** the JP flow needs admin — keep the self-elevate-via-ShellExecuteW already in
  `jp_capture.py`, or ship an elevation manifest.

**Real hurdles (tell users honestly):** (1) **AV false positives** — a PyInstaller-packed binary
that injects via frida looks like malware to Defender/others; expect flags. Code-signing helps but
costs money; otherwise document an AV exception. (2) It still injects → same ToS/anti-cheat risk as
running from source (§13). For a personal tool, **running from source (current setup) avoids the AV
pain entirely** — only package if genuinely distributing.
