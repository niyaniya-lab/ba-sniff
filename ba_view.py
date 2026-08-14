r"""
Offline profile VIEWER for ba-sniff.

Loads a captured profile (captures/profile*.json) and opens a self-contained HTML
report in your default browser. Pure visualization of data you already captured —
NO game contact, nothing uploaded (the report is a local file:// page). Global + JP.

  .venv\Scripts\python.exe ba_view.py                          # file picker
  .venv\Scripts\python.exe ba_view.py captures\profile_latest.json
"""
import os
import sys
import json
import html
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ba_export  # reuse the parsing/export pipeline (offline; no frida)


def _pick_profile():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        p = filedialog.askopenfilename(
            title="Open a captured profile",
            initialdir=os.path.join(HERE, "captures"),
            filetypes=[("Profile JSON", "profile*.json"), ("All JSON", "*.json")])
        root.destroy()
        return p
    except Exception:
        return None


def build_doc(profile_path):
    """Load a profile and produce the account document (via the export pipeline)."""
    profile = json.load(open(profile_path, encoding="utf-8"))
    region = ba_export.detect_region(profile)
    names = ba_export.load_names()
    names_jp = ba_export._load(ba_export.NAMES_JP)
    item_names = ba_export._load(ba_export.NAMES_ITEMS)
    item_names_jp = ba_export._load(ba_export.NAMES_ITEMS_JP)
    gamedata = ba_export._load(ba_export.GAMEDATA)
    plandata = ba_export._load(ba_export.PLANDATA)
    out = os.path.join(os.path.dirname(ba_export.PROFILE_LATEST),
                       "export_jp" if region == "JP" else "export")
    ba_export.build_export(profile, names, out, item_names, gamedata, plandata, names_jp, item_names_jp)
    return json.load(open(os.path.join(out, "account.json"), encoding="utf-8"))


# ---------------------------------------------------------------- HTML rendering

def esc(x):
    return html.escape("" if x is None else str(x))


def _num_cell(v):
    """A numeric <td> that sorts by value but displays with thousands separators."""
    if isinstance(v, (int, float)):
        return f'<td class="num" data-sort="{v}">{v:,}</td>'
    return f"<td>{esc(v)}</td>"


def _tile(label, value):
    return f'<div class="tile"><div class="tv">{esc(value)}</div><div class="tl">{esc(label)}</div></div>'


def _table(cols, rows, tid, numeric_cols=()):
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body = []
    for r in rows:
        tds = []
        for i, cell in enumerate(r):
            tds.append(_num_cell(cell) if i in numeric_cols else f"<td>{esc(cell)}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return (f'<table id="{tid}" class="grid"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def _pretty_key(k):
    return k.replace("_", " ").title()


def render(doc):
    acc = doc.get("account", {}) or {}
    region = doc.get("region", "?")
    summary = doc.get("summary", {}) or {}
    currencies = doc.get("currencies", {}) or {}
    students = doc.get("students", []) or []
    items = doc.get("items", []) or []
    planning = doc.get("planning", {}) or {}
    campaign = doc.get("campaign", {}) or {}

    # ---- Overview ----
    header = (
        f'<div class="acc"><div class="nick">{esc(acc.get("nickname"))} '
        f'<span class="badge {"jp" if region=="JP" else "gl"}">{esc(region)}</span></div>'
        f'<div class="sub">Lv {esc(acc.get("level"))} · friend code {esc(acc.get("friend_code"))} · '
        f'exported {esc(doc.get("exported_at"))}</div></div>')
    tiles = [_tile("Students", len(students)), _tile("Items", len(items))]
    for k, v in summary.items():
        if isinstance(v, (int, float, str)):
            tiles.append(_tile(_pretty_key(k), v))
    cur_tiles = [_tile(k, f"{v:,}" if isinstance(v, (int, float)) else v) for k, v in currencies.items()]
    overview = (f'<div class="tiles">{"".join(tiles)}</div>'
                f'<h3>Currencies</h3><div class="tiles">{"".join(cur_tiles)}</div>')

    # ---- Students ----
    scols = ["id", "name", "name_jp", "lvl", "★", "eleph own", "eleph short",
             "UE★", "bond", "EX", "Basic", "Enh", "Sub", "exp→max"]
    srows = []
    for s in students:
        srows.append([
            s.get("id"), s.get("name"), s.get("name_jp"), s.get("level"), s.get("star_grade"),
            s.get("eleph_owned"), s.get("eleph_deficit"), s.get("ue_star"), s.get("bond"),
            s.get("skill_ex"), s.get("skill_basic"), s.get("skill_enhanced"), s.get("skill_sub"),
            s.get("exp_to_max"),
        ])
    snum = {0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}
    students_sec = (
        f'<input class="search" data-target="tblStudents" placeholder="filter students…">'
        + _table(scols, srows, "tblStudents", snum))

    # ---- Items ----
    icols = ["id", "name", "category", "count"]
    irows = [[it.get("id"), it.get("name"), it.get("category"), it.get("count")] for it in items]
    items_sec = (f'<input class="search" data-target="tblItems" placeholder="filter items…">'
                 + _table(icols, irows, "tblItems", {0, 3}))

    # ---- Planning ----
    cards = []
    exp = planning.get("exp")
    if exp:
        cards.append(f'<div class="card"><h4>EXP to max level</h4>'
                     f'<div>Students below max: <b>{esc(exp.get("students_below_max"))}</b></div>'
                     f'<div>Total EXP to max all: <b>{exp.get("total_exp_to_max_all", 0):,}</b></div>'
                     f'<div>Report EXP short/surplus: <b>{exp.get("report_exp_short_or_surplus", 0):,}</b></div></div>')
    gr = planning.get("grades")
    if gr:
        cards.append(f'<div class="card"><h4>Eleph (star + UE)</h4>'
                     f'<div>Below 5★: <b>{esc(gr.get("students_below_5star"))}</b></div>'
                     f'<div>UE to max: <b>{esc(gr.get("students_with_ue_to_max"))}</b></div>'
                     f'<div>Total eleph deficit: <b>{gr.get("total_eleph_deficit", 0):,}</b></div></div>')
    sk = planning.get("skills")
    if sk:
        top = "".join(f"<li>{esc(m.get('name'))}: need {m.get('needed',0):,}, short {m.get('deficit',0):,}</li>"
                      for m in (sk.get("materials") or [])[:8])
        cards.append(f'<div class="card"><h4>Skill materials to max</h4>'
                     f'<div>Credits: <b>{sk.get("credits_to_max_all", 0):,}</b></div><ul>{top}</ul></div>')
    ge = planning.get("gear")
    if ge:
        top = "".join(f"<li>{esc(p.get('name'))}: need {p.get('needed',0):,}, short {p.get('deficit',0):,}</li>"
                      for p in (ge.get("pieces") or [])[:8])
        cards.append(f'<div class="card"><h4>Gear pieces to max</h4>'
                     f'<div>Credits: <b>{ge.get("credits_to_max_all", 0):,}</b></div><ul>{top}</ul></div>')
    planning_sec = ('<div class="cards">' + "".join(cards) + '</div>') if cards else \
        '<p class="muted">No planning data (grab_reference / full-mode calculators not available for this capture).</p>'

    # ---- Campaign ----
    campaign_sec = (f'<div class="tiles">'
                    f'{_tile("Stages cleared", campaign.get("stages_cleared", 0))}'
                    f'{_tile("3-star", campaign.get("three_star", 0))}</div>')

    sections = {
        "Overview": overview,
        "Students": students_sec,
        "Items": items_sec,
        "Planning": planning_sec,
        "Campaign": campaign_sec,
    }
    nav = "".join(f'<button class="tab{" active" if i==0 else ""}" data-sec="sec{i}">{esc(name)}</button>'
                  for i, name in enumerate(sections))
    panes = "".join(f'<section id="sec{i}" class="pane{" active" if i==0 else ""}">{body}</section>'
                    for i, (name, body) in enumerate(sections.items()))

    return _PAGE.replace("__HEADER__", header).replace("__NAV__", nav).replace("__PANES__", panes)


_PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ba-sniff viewer</title><style>
:root{--bg:#0f1221;--card:#1a1f36;--card2:#232842;--fg:#e6e9f5;--muted:#8b93b8;--accent:#5b8cff;--ok:#3ecf8e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,Segoe UI,sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:20px}
.acc .nick{font-size:26px;font-weight:700}.acc .sub{color:var(--muted);margin-top:4px}
.badge{font-size:12px;padding:2px 8px;border-radius:10px;vertical-align:middle}
.badge.gl{background:#25406e}.badge.jp{background:#6e2540}
nav{display:flex;gap:6px;margin:18px 0;flex-wrap:wrap}
.tab{background:var(--card);color:var(--fg);border:0;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:14px}
.tab.active{background:var(--accent)}
.pane{display:none}.pane.active{display:block}
.tiles{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0}
.tile{background:var(--card);border-radius:10px;padding:12px 16px;min-width:120px}
.tile .tv{font-size:20px;font-weight:700}.tile .tl{color:var(--muted);font-size:12px}
h3{margin:22px 0 6px}h4{margin:0 0 8px}
.search{width:100%;max-width:360px;background:var(--card);border:1px solid var(--card2);color:var(--fg);
  padding:8px 12px;border-radius:8px;margin-bottom:10px}
table.grid{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden}
.grid th,.grid td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--card2)}
.grid th{background:var(--card2);cursor:pointer;user-select:none;position:sticky;top:0}
.grid td.num{text-align:right;font-variant-numeric:tabular-nums}
.grid tbody tr:hover{background:#20264a}
.cards{display:flex;flex-wrap:wrap;gap:12px}
.card{background:var(--card);border-radius:10px;padding:14px 16px;min-width:260px;flex:1}
.card ul{margin:8px 0 0;padding-left:18px;color:var(--muted)}
.muted{color:var(--muted)}
</style></head><body><div class="wrap">__HEADER__<nav>__NAV__</nav>__PANES__</div>
<script>
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.pane').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');document.getElementById(t.dataset.sec).classList.add('active');
});
document.querySelectorAll('.search').forEach(inp=>inp.oninput=()=>{
  const q=inp.value.toLowerCase(),tb=document.getElementById(inp.dataset.target).tBodies[0];
  for(const tr of tb.rows)tr.style.display=tr.textContent.toLowerCase().includes(q)?'':'none';
});
document.querySelectorAll('table.grid').forEach(tbl=>{
  tbl.querySelectorAll('th').forEach((th,i)=>th.onclick=()=>{
    const tb=tbl.tBodies[0],rows=[...tb.rows],asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
    const val=td=>{const c=td.children.length?td:td;const d=td.getAttribute('data-sort');
      return d!==null?parseFloat(d):td.textContent.toLowerCase();};
    rows.sort((a,b)=>{let x=val(a.cells[i]),y=val(b.cells[i]);
      if(typeof x==='number'&&typeof y==='number')return asc?x-y:y-x;
      x=''+x;y=''+y;return asc?x.localeCompare(y):y.localeCompare(x);});
    rows.forEach(r=>tb.appendChild(r));
  });
});
</script></body></html>"""


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else _pick_profile()
    if not path or not os.path.exists(path):
        print("No profile selected."); return
    print("loading", path)
    doc = build_doc(path)
    out = os.path.join(HERE, "captures", "view.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render(doc))
    url = "file:///" + out.replace("\\", "/")
    print("opening", url)
    webbrowser.open(url)


if __name__ == "__main__":
    main()
