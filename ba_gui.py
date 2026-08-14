r"""
ba-sniff GUI — standalone Blue Archive companion app.

Auto-attaches to the running game, captures your account, resolves student names
from the game itself, and shows a rich Overview (account, all currencies,
collection + progress stats) and a searchable Students tab with click-through
detail. "Sync Now" does a full on-demand refresh (AccountLoginSyncNoPart).

Run:  .venv\Scripts\python.exe ba_gui.py    (or run_gui.ps1)
"""
import os
import re
import sys
import json
import time
import queue
import threading
from datetime import datetime, timedelta

import tkinter as tk
from tkinter import ttk

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
FRIDA_DIR = os.path.join(HERE, "frida")
sys.path.insert(0, FRIDA_DIR)
from capture import Capture, PROFILE_LATEST  # noqa: E402
from ba_codec import proto_name  # noqa: E402

PROC = "BlueArchive.exe"
AGENT = os.path.join(FRIDA_DIR, "agent", "_capture_refresh.js")
FULL_SYNC_TASK = "AccountLoginSyncNoPartNetworkTask"
CAPTURES_DIR = os.path.dirname(PROFILE_LATEST)
NAMES_CACHE = os.path.join(HERE, "data", "names.json")
NAMES_ITEMS = os.path.join(HERE, "data", "names_items.json")

BG = "#0f1221"; CARD = "#1a1f36"; CARD2 = "#232842"; FG = "#e6e9f5"; MUTED = "#8b93b8"
ACCENT = "#5b8cff"; OK = "#3ecf8e"; WARN = "#ffcc66"; ERR = "#ff6b6b"; ROW_ALT = "#161a30"
GOLD = "#ffcc66"; STARS = "★"

CURRENCY_LABELS = {
    "Gem": "Pyroxene", "GemPaid": "Pyroxene (Paid)", "GemBonus": "Pyroxene (Free)",
    "Gold": "Credits", "ActionPoint": "AP (Stamina)", "AcademyTicket": "Academy Ticket",
    "ArenaTicket": "Tactical Challenge", "RaidTicket": "Raid Ticket",
    "EliminateTicket": "Grand Assault Ticket", "MasterCoin": "Master Coin",
    "WeekDungeonChaserATicket": "Bounty Ticket", "WeekDungeonChaserBTicket": "Sched. Ticket",
    "WeekDungeonChaserCTicket": "Commission Ticket", "SchoolDungeonATicket": "School A Ticket",
    "SchoolDungeonBTicket": "School B Ticket", "SchoolDungeonCTicket": "School C Ticket",
    "TimeAttackDungeonTicket": "TA Dungeon Ticket", "WorldRaidTicketA": "World Raid Ticket",
    "ChaserTotalTicket": "Chaser Ticket",
}
CURRENCY_ORDER = ["Gem", "GemPaid", "GemBonus", "Gold", "ActionPoint", "MasterCoin",
                  "ArenaTicket", "RaidTicket", "EliminateTicket", "AcademyTicket"]


def pretty_currency(key):
    return CURRENCY_LABELS.get(key) or re.sub(r"([a-z])([A-Z])", r"\1 \2", key)


def load_names():
    try:
        return {str(k): v for k, v in json.load(open(NAMES_CACHE, encoding="utf-8")).items()}
    except Exception:
        return {}


def save_names(d):
    try:
        os.makedirs(os.path.dirname(NAMES_CACHE), exist_ok=True)
        json.dump(d, open(NAMES_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    except Exception:
        pass


def extract_roster(profile):
    prots = profile.get("protocols", {})
    ls = prots.get("Account_LoginSync") or {}
    clr = ls.get("CharacterListResponse") or prots.get("Character_List") or {}
    chars = clr.get("CharacterDBs") or []
    weps = clr.get("WeaponDBs") or []
    wep_by_char = {w.get("BoundCharacterServerId"): w for w in weps if w.get("BoundCharacterServerId")}
    acc = (prots.get("Account_Auth", {}) or {}).get("AccountDB") or {}
    return chars, wep_by_char, ls, acc


def extract_currencies(ls):
    cdb = (ls.get("AccountCurrencySyncResponse") or {}).get("AccountCurrencyDB") or {}
    cur = cdb.get("CurrencyDict")
    if isinstance(cur, dict):
        return {k: v for k, v in cur.items() if isinstance(v, int)}
    # fallback: any nested str->int dict
    for v in cdb.values():
        if isinstance(v, dict) and any(isinstance(x, int) for x in v.values()):
            return {k: x for k, x in v.items() if isinstance(x, int)}
    return {}


AP_REGEN_SECONDS = 360  # 1 AP per 6 minutes


def _ticks_to_dt(ticks):
    # .NET DateTime ticks (100ns since 0001-01-01) -> naive datetime (server tz)
    return datetime(1, 1, 1) + timedelta(microseconds=ticks / 10)


def compute_ap_anchor(profile):
    """Return (base_ap, elapsed_at_capture_s, monotonic_t0) so AP can tick live,
    matching the game's own base + (now - updateTime)/6min. None if unavailable."""
    ls = (profile.get("protocols", {}) or {}).get("Account_LoginSync") or {}
    cdb = (ls.get("AccountCurrencySyncResponse") or {}).get("AccountCurrencyDB") or {}
    cur = cdb.get("CurrencyDict") or {}
    upd = cdb.get("UpdateTimeDict") or {}
    if "ActionPoint" not in cur or "ActionPoint" not in upd:
        return None
    base = cur["ActionPoint"]
    try:
        upd_dt = datetime.fromisoformat(str(upd["ActionPoint"]))
    except Exception:
        return None
    ticks = ls.get("ServerTimeTicks")
    try:
        elapsed0 = (_ticks_to_dt(ticks) - upd_dt).total_seconds() if ticks else 0.0
    except Exception:
        elapsed0 = 0.0
    return (base, max(0.0, elapsed0), time.monotonic())


def compute_summary(roster, weapons, ls):
    n = len(roster)
    lv = [c.get("Level", 0) for c in roster]
    s = {
        "students": n,
        "max_level": sum(1 for x in lv if x >= 90),
        "three_plus": sum(1 for c in roster if c.get("StarGrade", 0) >= 3),
        "five_star": sum(1 for c in roster if c.get("StarGrade", 0) >= 5),
        "ue_unlocked": len(weapons),
        "avg_level": round(sum(lv) / n, 1) if n else 0,
        "bond_avg": round(sum(c.get("FavorRank", 0) for c in roster) / n, 1) if n else 0,
    }
    camp = (ls.get("CampaignListResponse") or {}).get("StageHistoryDBs") or []
    s["campaign"] = len(camp)
    s["camp_3star"] = sum(1 for x in camp if x.get("Star1Flag") and x.get("Star2Flag") and x.get("Star3Flag"))
    s["story"] = len((ls.get("ScenarioListResponse") or {}).get("ScenarioHistoryDBs") or [])
    s["momo"] = len((ls.get("MomotalkOutlineResponse") or {}).get("MomoTalkOutLineDBs") or [])
    s["equipment"] = len((ls.get("EquipmentItemListResponse") or {}).get("EquipmentDBs") or [])
    s["memory"] = len((ls.get("MemoryLobbyListResponse") or {}).get("MemoryLobbyDBs") or [])
    raid = ls.get("RaidLoginResponse") or {}
    s["raid_tier"] = raid.get("LastSettledTier")
    s["raid_rank"] = raid.get("LastSettledRanking")
    return s


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.exports = None; self.session = None; self.attached = False
        self.cap = Capture(); self.names = load_names()
        self.roster = []; self.weapons = {}; self.filter_text = ""
        self._sort_key = "level"; self._sort_rev = True
        self.ap_anchor = None
        self.ap_cap = 0; self.ap_cap_level = None
        self.autosync = tk.BooleanVar(value=True)
        self.autosync_min = tk.IntVar(value=5)
        self._last_autosync = 0.0

        self._build_ui()
        self._load_profile_into_ui()
        threading.Thread(target=self._session_loop, daemon=True).start()
        self.root.after(150, self._pump)
        self.root.after(1000, self._tick_ap)
        self.root.after(5000, self._auto_sync_loop)

    # ---------------- UI scaffold ----------------
    def _build_ui(self):
        self.root.title("ba-sniff — Blue Archive companion")
        self.root.configure(bg=BG); self.root.geometry("960x700"); self.root.minsize(820, 600)

        st = ttk.Style()
        try: st.theme_use("clam")
        except Exception: pass
        st.configure("TFrame", background=BG)
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=CARD, foreground=MUTED, padding=(16, 8), font=("Segoe UI", 10))
        st.map("TNotebook.Tab", background=[("selected", BG)], foreground=[("selected", FG)])
        st.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        st.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        st.configure("H.TLabel", background=BG, foreground=FG, font=("Segoe UI Semibold", 16))
        st.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG, rowheight=26,
                     font=("Segoe UI", 10), borderwidth=0)
        st.configure("Treeview.Heading", background=CARD2, foreground=MUTED, font=("Segoe UI Semibold", 9), relief="flat")
        st.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
        st.configure("Vertical.TScrollbar", background=CARD2, troughcolor=BG, borderwidth=0, arrowcolor=MUTED)

        # header
        top = ttk.Frame(self.root); top.pack(fill="x", padx=16, pady=(12, 4))
        ttk.Label(top, text="ba-sniff", style="H.TLabel").pack(side="left")
        self.dot = tk.Canvas(top, width=12, height=12, bg=BG, highlightthickness=0)
        self.dot.pack(side="right"); self._draw_dot(WARN)
        self.status = ttk.Label(top, text="starting…", style="Muted.TLabel"); self.status.pack(side="right", padx=(0, 8))
        self.sync_btn = tk.Button(top, text="⟳  Sync Now", command=self._sync, bg=ACCENT, fg="white", relief="flat",
                                  font=("Segoe UI Semibold", 11), activebackground="#4a76e0", activeforeground="white",
                                  cursor="hand2", state="disabled", padx=14, pady=6)
        self.sync_btn.pack(side="right", padx=10)

        # subheader: live energy (left) + auto-sync controls (right)
        sub = ttk.Frame(self.root); sub.pack(fill="x", padx=16, pady=(0, 2))
        self.ap_label = tk.Label(sub, text="⚡ AP —", bg=BG, fg=GOLD, font=("Segoe UI Semibold", 13))
        self.ap_label.pack(side="left")
        tk.Checkbutton(sub, text="auto-sync every", variable=self.autosync, bg=BG, fg=MUTED,
                       selectcolor=CARD, activebackground=BG, activeforeground=FG, font=("Segoe UI", 9),
                       highlightthickness=0, bd=0).pack(side="right")
        tk.Spinbox(sub, from_=1, to=60, width=3, textvariable=self.autosync_min, bg=CARD, fg=FG,
                   buttonbackground=CARD, relief="flat", font=("Segoe UI", 9),
                   highlightthickness=0).pack(side="right", padx=(4, 4))
        ttk.Label(sub, text="min", style="Muted.TLabel").pack(side="right")

        self.nb = ttk.Notebook(self.root); self.nb.pack(fill="both", expand=True, padx=12, pady=(4, 4))
        self.tab_overview = ttk.Frame(self.nb); self.tab_students = ttk.Frame(self.nb)
        self.nb.add(self.tab_overview, text="  Overview  "); self.nb.add(self.tab_students, text="  Students  ")
        self.ov_inner = self._make_scroll(self.tab_overview)
        self._build_students_tab(self.tab_students)

        bot = ttk.Frame(self.root); bot.pack(fill="x", padx=16, pady=(0, 8))
        tk.Button(bot, text="⬇ Export account", command=self._export_now, bg=OK, fg="#0b1a12", relief="flat",
                  font=("Segoe UI Semibold", 9), activebackground="#35b57c", activeforeground="#0b1a12",
                  cursor="hand2", padx=12, pady=5).pack(side="left")
        tk.Button(bot, text="📁 Open data folder", command=self._open_folder, bg=CARD, fg=FG, relief="flat",
                  font=("Segoe UI", 9), activebackground="#242a49", activeforeground=FG, cursor="hand2",
                  padx=10, pady=5).pack(side="left", padx=6)
        ttk.Label(bot, text="clean export → captures/export/ (account.json + CSVs)", style="Muted.TLabel").pack(side="left", padx=8)
        self.log_lbl = ttk.Label(bot, text="", style="Muted.TLabel"); self.log_lbl.pack(side="right")

    def _make_scroll(self, parent):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview, style="Vertical.TScrollbar")
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", lambda ev: canvas.yview_scroll(int(-ev.delta / 120), "units")))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        return inner

    def _build_students_tab(self, parent):
        sr = ttk.Frame(parent); sr.pack(fill="x", padx=6, pady=(8, 4))
        ttk.Label(sr, text="Roster", style="H.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(sr, text="", style="Muted.TLabel"); self.count_lbl.pack(side="left", padx=8)
        self.search_var = tk.StringVar(); self.search_var.trace_add("write", lambda *a: self._apply_filter())
        tk.Entry(sr, textvariable=self.search_var, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
                 font=("Segoe UI", 10), width=26).pack(side="right", ipady=4, ipadx=6)
        ttk.Label(sr, text="🔍 search", style="Muted.TLabel").pack(side="right", padx=(0, 6))

        wrap = ttk.Frame(parent); wrap.pack(fill="both", expand=True, padx=6, pady=(2, 8))
        cols = ("name", "level", "star", "ue", "bond")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for key, text, w in [("name", "Student", 300), ("level", "Level", 80), ("star", STARS, 80),
                             ("ue", "UE", 80), ("bond", "Bond", 80)]:
            self.tree.heading(key, text=text, command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=w, anchor="w" if key == "name" else "center", stretch=(key == "name"))
        self.tree.tag_configure("odd", background=ROW_ALT)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview, style="Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True); vs.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._open_detail); self.tree.bind("<Return>", self._open_detail)

    # ---------------- small widgets ----------------
    def _draw_dot(self, color): self.dot.delete("all"); self.dot.create_oval(1, 1, 11, 11, fill=color, outline=color)
    def _set_status(self, t, c): self.status.configure(text=t); self._draw_dot(c)
    def _flash(self, s): self.log_lbl.configure(text=s)

    def _tile(self, parent, label, value, accent=FG):
        c = tk.Frame(parent, bg=CARD)
        tk.Label(c, text=str(value), bg=CARD, fg=accent, font=("Segoe UI Semibold", 15)).pack(anchor="w", padx=12, pady=(8, 0))
        tk.Label(c, text=label.upper(), bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=12, pady=(0, 8))
        return c

    def _section(self, parent, title):
        tk.Label(parent, text=title, bg=BG, fg=FG, font=("Segoe UI Semibold", 13)).pack(anchor="w", padx=8, pady=(14, 4))
        f = tk.Frame(parent, bg=BG); f.pack(fill="x", padx=8)
        return f

    def _grid_tiles(self, frame, tiles, cols=4):
        for i, (label, value, *rest) in enumerate(tiles):
            accent = rest[0] if rest else FG
            t = self._tile(frame, label, value, accent)
            t.grid(row=i // cols, column=i % cols, sticky="nsew", padx=4, pady=4, ipadx=2)
        for cidx in range(cols):
            frame.columnconfigure(cidx, weight=1)

    # ---------------- data render ----------------
    def _load_profile_into_ui(self):
        try:
            prof = json.load(open(PROFILE_LATEST, encoding="utf-8"))
        except Exception:
            self._render_overview({}, {}, [], {}, None); return
        self.roster, self.weapons, ls, acc = extract_roster(prof)
        self._update_ap_anchor(prof)
        self._render_overview(acc, ls, self.roster, self.weapons, prof.get("captured_at"))
        self._render_table()

    def _render_overview(self, acc, ls, roster, weapons, when):
        for w in self.ov_inner.winfo_children():
            w.destroy()
        pad = tk.Frame(self.ov_inner, bg=BG); pad.pack(fill="x", padx=8, pady=(10, 0))
        if not acc and not roster:
            tk.Label(pad, text="No data yet — launch the game, log in, then Sync.", bg=BG, fg=MUTED,
                     font=("Segoe UI", 11)).pack(anchor="w", pady=20)
            return

        # account line
        rep = None
        rid = acc.get("RepresentCharacterServerId")
        if rid:
            c = next((x for x in roster if x.get("ServerId") == rid), None)
            if c: rep = self._name_for(c.get("UniqueId"))
        head = f"👤  {acc.get('Nickname','—')}     Level {acc.get('Level','—')}"
        tk.Label(pad, text=head, bg=BG, fg=FG, font=("Segoe UI Semibold", 18)).pack(anchor="w")
        sub = []
        if acc.get("Comment"): sub.append(f"“{acc['Comment']}”")
        if rep: sub.append(f"rep: {rep}")
        if acc.get("CreateDate"): sub.append("since " + str(acc["CreateDate"])[:10])
        if acc.get("ServerId"): sub.append(f"ID {acc['ServerId']}")
        tk.Label(pad, text="   ·   ".join(sub), bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 0))
        if when:
            tk.Label(pad, text=f"last sync: {when}", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))

        # currencies
        cur = extract_currencies(ls)
        if cur:
            f = self._section(self.ov_inner, "Currencies")
            ordered = [k for k in CURRENCY_ORDER if k in cur] + [k for k in cur if k not in CURRENCY_ORDER]
            tiles = []
            for k in ordered:
                if cur[k] == 0 and k not in CURRENCY_ORDER:
                    continue
                accent = GOLD if k in ("Gem", "GemBonus") else FG
                tiles.append((pretty_currency(k), f"{cur[k]:,}", accent))
            self._grid_tiles(f, tiles, cols=5)

        # collection
        s = compute_summary(roster, weapons, ls)
        f = self._section(self.ov_inner, "Collection")
        self._grid_tiles(f, [
            ("Students", s["students"], ACCENT),
            ("Lv 90", s["max_level"]),
            ("3★ and up", s["three_plus"]),
            ("5★", s["five_star"], GOLD),
            ("UE unlocked", s["ue_unlocked"]),
            ("Avg level", s["avg_level"]),
            ("Avg bond", s["bond_avg"]),
        ], cols=4)

        # progress
        f = self._section(self.ov_inner, "Progress")
        prog = [
            ("Campaign stages", s["campaign"]),
            ("Campaign 3★", s["camp_3star"]),
            ("Story read", s["story"]),
            ("MomoTalk", s["momo"]),
            ("Equipment", s["equipment"]),
            ("Memory Lobby", s["memory"]),
        ]
        if s.get("raid_tier") is not None:
            prog.append(("Raid tier", s["raid_tier"]))
        self._grid_tiles(f, prog, cols=4)
        tk.Frame(self.ov_inner, bg=BG, height=16).pack()

    def _name_for(self, uid): return self.names.get(str(uid)) or f"Student {uid}"

    def _render_table(self):
        self.tree.delete(*self.tree.get_children())
        rows = []
        for c in self.roster:
            uid = c.get("UniqueId"); name = self._name_for(uid)
            if self.filter_text and self.filter_text not in name.lower():
                continue
            wep = self.weapons.get(c.get("ServerId"))
            ue = wep.get("StarGrade") if wep else 0
            rows.append((name, c.get("Level", 0), c.get("StarGrade", 0), ue, c.get("FavorRank", 0), uid))
        idx = {"name": 0, "level": 1, "star": 2, "ue": 3, "bond": 4}[self._sort_key]
        rows.sort(key=lambda r: (r[idx].lower() if isinstance(r[idx], str) else r[idx]), reverse=self._sort_rev)
        for i, r in enumerate(rows):
            self.tree.insert("", "end", iid=str(r[5]),
                             values=(r[0], r[1], STARS * int(r[2]) if r[2] else "—",
                                     STARS * int(r[3]) if r[3] else "—", r[4]),
                             tags=("odd",) if i % 2 else ())
        self.count_lbl.configure(text=f"({len(rows)})")

    def _apply_filter(self):
        self.filter_text = self.search_var.get().strip().lower(); self._render_table()

    def _sort_by(self, key):
        if self._sort_key == key: self._sort_rev = not self._sort_rev
        else: self._sort_key, self._sort_rev = key, key != "name"
        self._render_table()

    def _open_detail(self, event=None):
        sel = self.tree.selection()
        if not sel: return
        uid = int(sel[0]); c = next((x for x in self.roster if x.get("UniqueId") == uid), None)
        if c: StudentDetail(self.root, self._name_for(uid), c, self.weapons.get(c.get("ServerId")))

    # ---------------- frida ----------------
    def _session_loop(self):
        while True:
            if not self.attached:
                try:
                    sess = frida.attach(PROC)
                except Exception:
                    self.q.put(("status", ("waiting for Blue Archive…", WARN))); time.sleep(2); continue
                try:
                    script = sess.create_script(open(AGENT, encoding="utf-8").read())
                    script.on("message", lambda m, d: self.q.put(("payload", (m.get("payload") or {}) if m.get("type") != "error"
                                                                  else {"type": "log_line", "text": "[js] " + str(m.get("description"))})))
                    script.set_log_handler(lambda level, text: self.q.put(("payload", {"type": "log_line", "text": str(text)})))
                    sess.on("detached", lambda *a: self.q.put(("detached", None)))
                    script.load()
                    self.session = sess; self.exports = script.exports_sync; self.attached = True
                    self.q.put(("attached", None))
                except Exception as e:
                    self.q.put(("payload", {"type": "log_line", "text": f"attach failed: {e}"})); time.sleep(2)
            time.sleep(1)

    def _resolve_names_async(self):
        if not self.exports or not self.roster: return
        missing = [c.get("UniqueId") for c in self.roster if str(c.get("UniqueId")) not in self.names]
        if not missing: return
        def work():
            try:
                res = self.exports.resolve_names(missing)
                self.q.put(("names", {str(k): v for k, v in res.items() if v}))
            except Exception as e:
                self.q.put(("payload", {"type": "log_line", "text": f"name resolve failed: {e}"}))
        threading.Thread(target=work, daemon=True).start()

    def _resolve_items_async(self):
        """Keep the item/equipment name cache fresh so exports are fully named."""
        if not self.exports: return
        P = self.cap.protocols
        item_ids = sorted({x.get("UniqueId") for x in P.get("Protocol_4000", {}).get("ItemDBs", []) if x.get("UniqueId")})
        eq_ids = sorted({x.get("UniqueId") for x in (P.get("Account_LoginSync", {}).get("EquipmentItemListResponse", {}) or {}).get("EquipmentDBs", []) if x.get("UniqueId")})
        try:
            cache = json.load(open(NAMES_ITEMS, encoding="utf-8"))
        except Exception:
            cache = {}
        imap, emap = cache.get("item", {}), cache.get("equip", {})
        miss_i = [i for i in item_ids if str(i) not in imap]
        miss_e = [i for i in eq_ids if str(i) not in emap]
        if not miss_i and not miss_e: return
        def work():
            try:
                im = {k: v for k, v in self.exports.resolve_typed(4, miss_i).items() if v} if miss_i else {}
                em = {k: v for k, v in self.exports.resolve_typed(3, miss_e).items() if v} if miss_e else {}
                self.q.put(("itemnames", (im, em)))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    # ---------------- pump ----------------
    def _pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "status": self._set_status(*val)
                elif kind == "attached":
                    self._set_status("attached — capturing", OK); self.sync_btn.configure(state="normal")
                    self._flash("attached; resolving names…"); self._resolve_names_async(); self._resolve_items_async()
                elif kind == "detached":
                    self.attached = False; self.exports = None
                    self.sync_btn.configure(state="disabled"); self._set_status("game closed — waiting…", WARN)
                elif kind == "apcap":
                    self.ap_cap = val
                elif kind == "itemnames":
                    im, em = val
                    try:
                        cache = json.load(open(NAMES_ITEMS, encoding="utf-8"))
                    except Exception:
                        cache = {}
                    cache.setdefault("item", {}).update(im)
                    cache.setdefault("equip", {}).update(em)
                    try:
                        os.makedirs(os.path.dirname(NAMES_ITEMS), exist_ok=True)
                        json.dump(cache, open(NAMES_ITEMS, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
                    except Exception:
                        pass
                    if im or em: self._flash(f"named {len(im)} items, {len(em)} equipment")
                elif kind == "names":
                    self.names.update(val); save_names(self.names)
                    self._render_table(); self._load_profile_into_ui(); self._flash(f"resolved {len(val)} names")
                elif kind == "payload": self._handle_payload(val)
        except queue.Empty:
            pass
        self.root.after(150, self._pump)

    def _handle_payload(self, p):
        pt = p.get("type")
        if pt == "packet":
            self.cap._store_packet(p)
            try: obj = json.loads(p.get("json", ""))
            except Exception: return
            name = proto_name(obj.get("Protocol", obj.get("protocol")))
            if name in ("Account_LoginSync", "Character_List", "Account_CurrencySync", "Account_Auth"):
                prof = {"protocols": self.cap.protocols, "captured_at": time.strftime("%Y-%m-%d %H:%M:%S")}
                self.roster, self.weapons, ls, acc = extract_roster(prof)
                self._update_ap_anchor(prof)
                self._render_overview(acc, ls, self.roster, self.weapons, prof["captured_at"])
                self._render_table(); self._resolve_names_async(); self._resolve_items_async()
                self._flash("account synced")
        elif pt == "fired": self._flash(f"requested {p.get('task')} …")
        elif pt == "fire_error":
            self._flash(f"refresh failed: {p.get('error')}"); self.sync_btn.configure(state="normal", text="⟳  Sync Now")
        elif pt == "log_line": self._flash(p.get("text", "")[:90])

    # ---------------- actions ----------------
    def _sync(self):
        if not self.exports: return
        self.sync_btn.configure(state="disabled", text="Syncing…"); self._flash("full refresh requested")
        try: self.exports.refresh([FULL_SYNC_TASK])
        except Exception as e: self._flash(f"sync failed: {e}")
        self.root.after(6000, lambda: self.sync_btn.configure(state="normal" if self.attached else "disabled", text="⟳  Sync Now"))

    def _open_folder(self):
        os.makedirs(CAPTURES_DIR, exist_ok=True)
        try: os.startfile(CAPTURES_DIR)
        except Exception: pass

    def _export_now(self):
        try:
            import ba_export  # lazy (avoids circular import at module load)
            def _rd(p):
                try: return json.load(open(p, encoding="utf-8"))
                except Exception: return {}
            item_names = _rd(ba_export.NAMES_ITEMS)
            gamedata = _rd(ba_export.GAMEDATA)
            plandata = _rd(ba_export.PLANDATA)
            profile = {"protocols": self.cap.protocols, "captured_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            out = os.path.join(CAPTURES_DIR, "export")
            res = ba_export.build_export(profile, self.names, out, item_names, gamedata, plandata)
            self._flash(f"exported {res['students']} students, {res['items']} items → export/")
            try: os.startfile(out)
            except Exception: pass
        except Exception as e:
            self._flash(f"export failed: {e}")

    # ---------------- live energy + auto-sync ----------------
    def _update_ap_anchor(self, profile=None):
        if profile is None:
            profile = {"protocols": self.cap.protocols}
        a = compute_ap_anchor(profile)
        if a:
            self.ap_anchor = a
        # fetch the AP cap for the account level (once per level)
        prots = profile.get("protocols", {})
        lvl = (prots.get("Account_Auth", {}) or {}).get("AccountDB", {}).get("Level")
        if not lvl:
            cdb = ((prots.get("Account_LoginSync", {}) or {}).get("AccountCurrencySyncResponse") or {}).get("AccountCurrencyDB") or {}
            lvl = cdb.get("AccountLevel")
        if lvl and self.exports and lvl != self.ap_cap_level:
            self.ap_cap_level = lvl
            def work(level=lvl):
                try:
                    cap = int(self.exports.ap_cap(int(level)))
                    if cap: self.q.put(("apcap", cap))
                except Exception:
                    pass
            threading.Thread(target=work, daemon=True).start()

    def _tick_ap(self):
        if self.ap_anchor:
            base, elapsed0, t0 = self.ap_anchor
            elapsed = elapsed0 + (time.monotonic() - t0)
            cur = base + int(elapsed // AP_REGEN_SECONDS)
            cap = self.ap_cap
            if cap and cur >= cap:
                self.ap_label.configure(text=f"⚡ AP  {cap} / {cap}   full", fg=OK)
            else:
                rem = AP_REGEN_SECONDS - int(elapsed % AP_REGEN_SECONDS)
                if cap:
                    secs_full = (cap - cur - 1) * AP_REGEN_SECONDS + rem
                    h, m = secs_full // 3600, (secs_full % 3600) // 60
                    eta = f"full in {h}h{m:02d}m" if h else f"full in {m}m"
                    self.ap_label.configure(text=f"⚡ AP  {cur} / {cap}    ({eta})", fg=GOLD)
                else:
                    self.ap_label.configure(text=f"⚡ AP  ~{cur}    (+1 in {rem // 60}:{rem % 60:02d})", fg=GOLD)
        self.root.after(1000, self._tick_ap)

    def _auto_sync_loop(self):
        try:
            if self.attached and self.exports and self.autosync.get():
                interval = max(1, int(self.autosync_min.get())) * 60
                if time.monotonic() - self._last_autosync >= interval:
                    self._last_autosync = time.monotonic()
                    try:
                        self.exports.refresh([FULL_SYNC_TASK])
                        self._flash("auto-sync…")
                    except Exception:
                        pass
        except Exception:
            pass
        self.root.after(5000, self._auto_sync_loop)


class StudentDetail(tk.Toplevel):
    def __init__(self, parent, name, c, wep):
        super().__init__(parent)
        self.title(name); self.configure(bg=BG); self.geometry("400x480"); self.resizable(False, False)
        tk.Label(self, text=name, bg=BG, fg=FG, font=("Segoe UI Semibold", 18)).pack(anchor="w", padx=18, pady=(16, 0))
        tk.Label(self, text=f"UniqueId {c.get('UniqueId')}   ·   {STARS * int(c.get('StarGrade', 0))}",
                 bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=18, pady=(0, 8))
        rows = [
            ("Level", f"{c.get('Level', '—')}   (exp {c.get('Exp', 0):,})"),
            ("Star grade", f"{STARS * int(c.get('StarGrade', 0))}  ({c.get('StarGrade', 0)})"),
            ("Bond", f"{c.get('FavorRank', '—')}   (exp {c.get('FavorExp', 0):,})"),
            ("§Skills", ""),
            ("EX skill", c.get("ExSkillLevel", "—")),
            ("Basic skill", c.get("PublicSkillLevel", "—")),
            ("Enhanced skill", c.get("PassiveSkillLevel", "—")),
            ("Sub skill", c.get("ExtraPassiveSkillLevel", "—")),
        ]
        if wep:
            rows += [("§Unique Weapon", ""),
                     ("UE grade", f"{STARS * int(wep.get('StarGrade', 0))}  ({wep.get('StarGrade', 0)})"),
                     ("UE level", wep.get("Level", "—"))]
        eq = [e for e in (c.get("EquipmentServerIds") or []) if e]
        rows += [("§Gear", ""), ("Equipment", f"{len(eq)} / 3 slots")]

        body = tk.Frame(self, bg=BG); body.pack(fill="both", expand=True, padx=18, pady=4)
        for label, val in rows:
            if label.startswith("§"):
                tk.Label(body, text=label[1:], bg=BG, fg=ACCENT, font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(10, 2))
                continue
            r = tk.Frame(body, bg=BG); r.pack(fill="x", pady=1)
            tk.Label(r, text=label, bg=BG, fg=MUTED, font=("Segoe UI", 10), width=15, anchor="w").pack(side="left")
            tk.Label(r, text=str(val), bg=BG, fg=FG, font=("Segoe UI Semibold", 11), anchor="w").pack(side="left")

        tk.Button(self, text="Close", command=self.destroy, bg=CARD, fg=FG, relief="flat",
                  font=("Segoe UI", 10), cursor="hand2", padx=16, pady=6).pack(pady=12)
        self.transient(parent); self.grab_set()


def main():
    if not os.path.exists(AGENT):
        print("Agent not built. Run:  cd frida\\agent && npm run build:refresh"); sys.exit(1)
    root = tk.Tk(); App(root); root.mainloop()


if __name__ == "__main__":
    main()
