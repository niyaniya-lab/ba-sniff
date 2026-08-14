# ba-sniff

A companion app and full account exporter for **BA**. It reads your *own*
account — player level, currencies, roster, gear, campaign clears, and every MX protocol
the game syncs — out of your running client and writes it to local JSON/CSV for progress
tracking and planning math. Supports **Global (Steam/NX)** and **JP (YS)**.

Unofficial third-party tool, not affiliated with NX or YS. Use it entirely at your
own risk: no warranty, no support, and the author takes no responsibility for anything
that happens to your account.

> [!WARNING]
> **Never run this during PvP, raids, events, or any timed or scored content.**
> Close the tool before you queue, and capture from the lobby.
>
> The active modes fire account syncs and do stop-the-world heap walks inside the
> client. A brief freeze costs you nothing while you're sitting in a menu, and can cost
> you a run mid-battle — a dropped ranking match, a failed raid attempt, a lost event
> clear. Scored content is also the worst possible place to have an injected process
> attached to your client.

---

## What it does

- **Live companion (Global)** — a GUI showing your account, currencies, roster and a live
  AP counter, with a one-click export.
- **Full account export** — one `account.json` plus flat CSVs (students, items, equipment,
  currencies, campaign) you can drop into a spreadsheet or planner. Region is auto-detected
  and tagged.
- **"Materials to max" planning** — per-student and roster-wide gap sheets across five
  investment axes (character EXP, skills, star eleph, UE eleph, gear tier-up), computed
  from the client's own cost calculators and joined against your inventory offline.
- **Offline HTML viewer** — turn any captured profile into a sortable, searchable local
  report without touching the game.

## Requirements

- Windows
- Python 3.10+ (3.12 recommended — `py -3.12`)
- Node.js (only to build the Frida agents once)
- The BA client you want to read (Global via Steam, and/or JP via the YS launcher)

## One-time setup

Run from the repo root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install frida-tools

cd frida\agent
npm install
npm run build:capture    # hook-only agent — used by Global passive + JP
npm run build:refresh    # full agent — used by the GUI + grab_reference.py
cd ..\..
```

Build **both** agents: the passive/JP path loads `_capture.js`, the GUI and the planning
grab load `_capture_refresh.js`. The launchers call `.venv\Scripts\python.exe` directly,
so you never need to activate the venv yourself.

Rebuild the agents after you edit anything under `frida/agent/`.

---

## Quick start

Four entry points, each a `.bat` you double-click from the repo root.

### Global — passive capture (`launch_gl_passive.bat`)

Lightest touch: installs the read-only JSON hook and nothing else. No calculators in this
mode.

1. Launch BA from Steam and log in.
2. Double-click `launch_gl_passive.bat`. It attaches and starts capturing.
3. Play or navigate the screens whose data you want.
4. **Close the game** to finish. The exporter runs automatically → `captures\export\`.

### Global — full companion GUI (`launch_gl_full.bat`)

Adds the active features: live auto-sync, AP counter and the materials-to-max calculators.

1. Launch BA from Steam and log in.
2. Double-click `launch_gl_full.bat` (or `.\run_gui.ps1`, which launches it windowless).
3. In the GUI: **⟳ Sync Now** pulls a fresh account sync; tick *auto-sync every N min* to
   keep it refreshing. Browse the **Overview** and **Students** tabs.
4. Click **⬇ Export account** to write `captures\export\`, or **📁 Open data folder** to
   jump straight there.

### JP — passive capture (`launch_jp_passive.bat`)

The only JP mode. **Order matters** — the tool has to be armed before the game starts.

1. Double-click `launch_jp_passive.bat` and accept the UAC prompt. It arms an IFEO hook so
   Windows routes the game's launch through the capture shim.
2. Leave that window open. Now press **Launch** in the BA JP (Ys) launcher.
   A shim console flashes briefly — that's normal.
3. Log in and reach the lobby. The window reports `account captured` once it has the login
   bundle.
4. Keep playing or close the game. The exporter runs → `captures\export_jp\`.

The IFEO hook is one-shot: it removes itself the instant it fires, so your normal launches
aren't intercepted afterwards. JP is passive-only and has no calculators or in-client name
resolution.

### Offline viewer (`launch_viewer.bat`)

No game contact at all.

1. Double-click `launch_viewer.bat`.
2. Pick a captured profile from `captures\` in the file picker.
3. A self-contained HTML report opens in your browser — account, roster, items, currencies,
   planning — sortable and searchable. It's a local `file://` page; nothing is uploaded.

To skip the picker: `.venv\Scripts\python.exe ba_view.py captures\profile_latest.json`

---

## Where the output lands

Everything goes under `captures\` (git-ignored — it holds your real account data, so treat
it as private).

| Path | What it is |
|---|---|
| `profile_latest.json` | Global capture: `{captured_at, region, protocols:{...}}`, keyed by MX protocol name, verbatim game structures |
| `profile_jp_latest.json` | Same, from a JP capture |
| `profile_<timestamp>.json` | Per-session snapshot, kept for history |
| `raw_<timestamp>.jsonl` | Every packet as received (audit/debug) |
| `export\` / `export_jp\` | The clean archive — see below |

An export directory contains `account.json` (account, summary, planning, currencies,
inventory, students, items, equipment, echelons, campaign, pvp, cafe, …) plus
`students.csv`, `items.csv`, `equipment.csv`, `currencies.csv`, `campaign.csv`, and — when
planning data is available — `skill_materials.csv` and `gear_pieces.csv`.

Captures accumulate: each run merges into the existing profile rather than replacing it.

You can re-run the exporter at any time without the game open:

```powershell
.\.venv\Scripts\python.exe ba_export.py                                  # latest Global -> captures\export\
.\.venv\Scripts\python.exe ba_export.py captures\profile_jp_latest.json  # a specific profile
.\.venv\Scripts\python.exe ba_export.py captures\profile_latest.json captures\my_export
```

The first argument is the profile to export (region is auto-detected from it, and the
output directory defaults per region); the optional second argument overrides the output
directory.

## Optional: planning math (`grab_reference.py`)

The materials-to-max numbers need the game's static cost curves plus your account's own
to-max costs. Grab them once (and again after a big content patch):

1. Launch BA **Global** from Steam and log in.
2. Run `.\.venv\Scripts\python.exe grab_reference.py`

It writes `data\gamedata.json` (EXP curve, activity report values) and `data\plan_costs.json`
(per-student skill / eleph / gear costs), and caches material and gear names into
`data\names_items.json`. After that the planning math runs offline in `ba_export.py` and the
viewer. This uses the client's calculators, so it's Global-only.

## Troubleshooting

**"Agent not built."** — you skipped or partly ran the npm build. From `frida\agent`, run
`npm run build:capture` and `npm run build:refresh`.

**"BlueArchive.exe not running."** — the Global modes attach to a live client. Start the
game and get to the lobby first, then run the launcher.

**JP: nothing captures.** — you almost certainly pressed Launch in the YS launcher
before arming. JP can't be attached to once it's running; close the game, run
`launch_jp_passive.bat` first, *then* press Launch. If a run is killed hard mid-session, the
IFEO key normally still cleans itself up; to be sure, run elevated:
`reg delete "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\BlueArchive.exe" /f`

**No materials-to-max numbers in my export.** — either you used Global passive / JP (no
calculators in those modes) or you haven't run `grab_reference.py` yet.

**Some students show as `Student 10045`.** — that unit isn't in the name cache yet. Names
are resolved in-client on Global; `UniqueId`s are shared across regions, so running a Global
capture also labels your JP data. Unnamed units still export completely under their id.

**The venv or `pip install` fails.** — you're probably building against an old interpreter
(conda's Python 3.9 is a common culprit). Rebuild explicitly: `py -3.12 -m venv .venv`.

**The GUI shows no window / no errors.** — `run_gui.ps1` uses `pythonw.exe`, which has no
console. Run `.\.venv\Scripts\python.exe ba_gui.py` to see the output.

**It broke after a game update.** — expected. The hook is resolved by name rather than by
offset, so it usually survives, but client updates can still move things. Rebuild the agents
and check `docs/FINDINGS.md`.

---

## How it works (technical note)

The game encrypts its API with a per-session AES key that is RSA-2048-wrapped to the
publisher's public key, so passive network sniffing *cannot* decrypt it — only the publisher
holds the private key. (The full investigation is in `docs/FINDINGS.md`; the leftover
mitmproxy tooling — `ba_sniff.py`, `run.ps1`, `install-cert.ps1` — correctly does TLS
interception and packet framing but stops at that wall. It's kept for reference and isn't
part of the working path.)

Instead, ba-sniff reads each packet's JSON **from inside the client, after the game has
already decrypted it**, by hooking the client's own `Newtonsoft.Json` deserializer via
[Frida](https://frida.re). No keys, no hardcoded offsets (the hook is resolved by name), and
read-only — it uses `Interceptor.attach` and never modifies game behaviour.

**Passive vs full** is about how much the tool does in-process. Passive installs only the
read-only JSON hook: it reads packets the game already produced and does nothing else — no
per-frame pump, no network-task firing, no memory walk. Full adds the active extras: live
auto-sync and the materials-to-max calculators.

**Two regions, two injection methods:**

| | Global (Steam/NX) | JP (YS) |
|---|---|---|
| Anti-cheat | none | XIGNCODE3 (`xhunter1.sys`) |
| Injection | attach to the running client | inject at process creation (IFEO shim) |
| Active features (`gc.choose`) | available (full mode) | hang the client — unavailable |

`gc.choose` is a stop-the-world heap walk that the Global client tolerates and the JP client
does not, which is why JP has no calculators and no in-client name resolution.

**Cross-region naming** — `UniqueId` is the region-independent key; each student carries
`name_en` and `name_jp` slots, filled independently as sources become available. Global
captures fill EN in-client, and because UniqueIds are shared, that cache also labels JP data.
A student with no name yet still exports fully under its id as `Student <id>` — never lost,
just unlabeled. True JP names are a planned offline job (game-file extraction / SchaleDB).

Scope is personal and single-account: read-only, not a bot.
