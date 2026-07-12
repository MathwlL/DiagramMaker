"""
SQL Diagram Tool
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog, colorchooser
import re, math, json, copy, subprocess, os, tempfile, sys

from core.nosql import DiagramProject, LocalJsonProvider, ProviderError, make_project

try:
    from PIL import Image, ImageGrab, ImageTk
    PIL_OK = True
except ImportError:
    PIL_OK = False

import ctypes
from ctypes import wintypes

if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

if sys.platform == "win32":
    user32 = ctypes.windll.user32

    try:
        _SetWindowLongPtrW = user32.SetWindowLongPtrW
    except AttributeError:
        _SetWindowLongPtrW = user32.SetWindowLongW
    try:
        _GetWindowLongPtrW = user32.GetWindowLongPtrW
    except AttributeError:
        _GetWindowLongPtrW = user32.GetWindowLongW

    _SetWindowLongPtrW.restype = ctypes.c_void_p
    _SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _GetWindowLongPtrW.restype = ctypes.c_void_p
    _GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]

    user32.CallWindowProcW.restype = ctypes.c_long
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                                        wintypes.WPARAM, wintypes.LPARAM]
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, ctypes.c_uint,
                                  wintypes.WPARAM, wintypes.LPARAM)

GWLP_WNDPROC   = -4
WM_NCCALCSIZE  = 0x0083
WM_NCHITTEST   = 0x0084
WM_NCACTIVATE  = 0x0086
WM_SIZE        = 0x0005
SIZE_MAXIMIZED = 2
SIZE_RESTORED  = 0
HTCLIENT       = 1
HTLEFT, HTRIGHT, HTTOP, HTBOTTOM = 10, 11, 12, 15
HTTOPLEFT, HTTOPRIGHT, HTBOTTOMLEFT, HTBOTTOMRIGHT = 13, 14, 16, 17
RESIZE_BORDER  = 8
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND = 1

#  SQL 

def _extract_table_bodies(sql):
    results = []
    pat = re.compile(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?(\w+)[`"\]]?\s*\(',
        re.IGNORECASE)
    i = 0
    while i < len(sql):
        m = pat.search(sql, i)
        if not m: break
        tname = m.group(1)
        start = m.end(); depth = 1; j = start
        while j < len(sql) and depth > 0:
            if sql[j] == '(':   depth += 1
            elif sql[j] == ')': depth -= 1
            j += 1
        body = sql[start:j-1]
        results.append((tname.upper(), body))
        i = j
    return results

def _split_defs(text):
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch == '(':   depth += 1
        elif ch == ')': depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(cur).strip()); cur = []
        else:
            cur.append(ch)
    if cur: parts.append(''.join(cur).strip())
    return parts

def parse_sql(sql: str) -> dict:
    tables = {}; relations = []; rel_seen = set()
    sql = re.sub(r'--[^\n]*', '', sql)
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    CONSTRAINT_KW = re.compile(
        r'^(PRIMARY|FOREIGN|UNIQUE|CHECK|CONSTRAINT|KEY|INDEX|FULLTEXT|SPATIAL)\b', re.IGNORECASE)
    fk_pat = re.compile(
        r'(?:CONSTRAINT\s+\w+\s+)?FOREIGN\s+KEY\s*\(([^)]+)\)'
        r'\s+REFERENCES\s+[`"\[]?(\w+)[`"\]]?\s*\(([^)]+)\)', re.IGNORECASE)
    col_pat = re.compile(r'^[`"\[]?(\w+)[`"\]]?\s+(\w+\s*(?:\([^)]*\))?)(.*)',
                         re.IGNORECASE | re.DOTALL)
    for tname, body in _extract_table_bodies(sql):
        columns, pk_cols, fk_map = [], set(), {}
        pk_tbl = re.search(r'PRIMARY\s+KEY\s*\(([^)]+)\)', body, re.IGNORECASE)
        if pk_tbl:
            for c in pk_tbl.group(1).split(','):
                pk_cols.add(c.strip().strip('`"[] \t').upper())
        for fk in fk_pat.finditer(body):
            lcs = [c.strip().strip('`"[] \t').upper() for c in fk.group(1).split(',')]
            rt  = fk.group(2).upper()
            rcs = [c.strip().strip('`"[] \t').upper() for c in fk.group(3).split(',')]
            for lc, rc in zip(lcs, rcs):
                fk_map[lc] = (rt, rc)
                key = (tname, lc, rt)
                if key not in rel_seen:
                    rel_seen.add(key)
                    relations.append({"from": tname, "from_col": lc,
                                      "to": rt, "to_col": rc,
                                      "card_from": "1,1", "card_to": "0,n",
                                      "label_offset": [0, 0]})
        for defn in _split_defs(body):
            defn = defn.strip()
            if not defn or CONSTRAINT_KW.match(defn): continue
            m = col_pat.match(defn)
            if not m: continue
            cn   = m.group(1).upper()
            ct   = re.sub(r'\s+', '', m.group(2)).upper()
            rest = m.group(3)
            is_pk = (cn in pk_cols) or bool(re.search(r'\bPRIMARY\s+KEY\b', rest, re.IGNORECASE))
            if is_pk: pk_cols.add(cn)
            ref_table = None
            ifk = re.search(r'\bREFERENCES\s+[`"\[]?(\w+)[`"\]]?\s*\(([^)]+)\)', rest, re.IGNORECASE)
            if ifk:
                ref_table = ifk.group(1).upper()
                rc = ifk.group(2).strip().strip('`"[] \t').upper()
                if cn not in fk_map:
                    fk_map[cn] = (ref_table, rc)
                    key = (tname, cn, ref_table)
                    if key not in rel_seen:
                        rel_seen.add(key)
                        relations.append({"from": tname, "from_col": cn,
                                          "to": ref_table, "to_col": rc,
                                          "card_from": "1,1", "card_to": "0,n",
                                          "label_offset": [0, 0]})
            if cn in fk_map: ref_table = fk_map[cn][0]
            columns.append({"name": cn, "type": ct, "pk": is_pk, "fk": cn in fk_map, "ref": ref_table})
        if columns: tables[tname] = columns
    return {"tables": tables, "relations": relations}

CARDINALITIES = ("1,1", "0,1", "1,n", "0,n")

def _norm_name(value):
    return (value or "").strip().upper()

def relation_key(rel):
    """Stable identity for one relation, including columns when they exist."""
    return (
        _norm_name(rel.get("from")),
        _norm_name(rel.get("from_col")),
        _norm_name(rel.get("to")),
        _norm_name(rel.get("to_col")),
    )

def dedupe_relations(relations):
    cleaned, seen = [], set()
    for rel in relations:
        key = relation_key(rel)
        if key in seen:
            continue
        seen.add(key)
        rel.setdefault("card_from", "1,1")
        rel.setdefault("card_to", "0,n")
        rel.setdefault("label_offset", [0, 0])
        rel.setdefault("label_offset_from", [0, 0])
        rel.setdefault("label_offset_to", [0, 0])
        rel.setdefault("waypoints", [])
        cleaned.append(rel)
    return cleaned

def tables_to_sql(tables, relations):
    """Convert the internal tables/relations structure back to SQL."""
    lines = []
    fk_info = {}
    for rel in relations:
        fk_info[(rel["from"], rel.get("from_col", ""))] = (rel["to"], rel.get("to_col", ""))
    for tname, cols in tables.items():
        lines.append(f"CREATE TABLE {tname} (")
        col_defs = []
        fk_defs = []
        pk_cols = [c["name"] for c in cols if c["pk"]]
        for col in cols:
            dtype = col.get("type", "VARCHAR(100)")
            if not dtype: dtype = "VARCHAR(100)"
            notnull = " NOT NULL" if col["pk"] else ""
            col_defs.append(f"    {col['name']} {dtype}{notnull}")
        if pk_cols:
            col_defs.append(f"    PRIMARY KEY ({', '.join(pk_cols)})")
        for col in cols:
            if col.get("fk") and col.get("ref"):
                for rel in relations:
                    if rel["from"] == tname and rel.get("from_col","").upper() == col["name"].upper():
                        fk_defs.append(
                            f"    FOREIGN KEY ({col['name']}) REFERENCES {rel['to']}({rel.get('to_col', 'id')})")
                        break
        all_defs = col_defs + fk_defs
        lines.append(",\n".join(all_defs))
        lines.append(");\n")
    return "\n".join(lines)

def tables_to_nosql(tables, relations, provider="MongoDB"):
    """Generate starter NoSQL code from the current diagram model."""
    provider = (provider or "MongoDB").lower()
    rel_map = {}
    for rel in relations:
        rel_map.setdefault(rel.get("from"), []).append(rel)

    def js_type(dtype):
        dtype = (dtype or "").upper()
        if any(x in dtype for x in ("INT", "DECIMAL", "FLOAT", "DOUBLE", "NUMERIC", "BIGINT")):
            return "Number"
        if any(x in dtype for x in ("BOOL", "BIT")):
            return "Boolean"
        if any(x in dtype for x in ("DATE", "TIME")):
            return "Date"
        if "JSON" in dtype:
            return "Object"
        return "String"

    def sample_value(dtype):
        kind = js_type(dtype)
        return {
            "Number": 0,
            "Boolean": False,
            "Date": "2026-01-01T00:00:00.000Z",
            "Object": {},
        }.get(kind, "")

    if provider == "mongoose":
        lines = ["const mongoose = require('mongoose');", ""]
        for table, cols in tables.items():
            lines.append(f"const {table.title().replace('_', '')}Schema = new mongoose.Schema({{")
            for col in cols:
                opts = [f"type: {js_type(col.get('type'))}"]
                if col.get("pk"):
                    opts.append("unique: true")
                    opts.append("required: true")
                if col.get("fk") or col.get("ref"):
                    opts.append(f"ref: '{col.get('ref') or 'Document'}'")
                lines.append(f"  {col['name'].lower()}: {{ {', '.join(opts)} }},")
            for rel in rel_map.get(table, []):
                if rel.get("card_to", "").lower().endswith("n"):
                    lines.append(f"  {rel['to'].lower()}Refs: [{{ type: mongoose.Schema.Types.ObjectId, ref: '{rel['to']}' }}],")
            lines.append("}, { timestamps: true });")
            lines.append(f"module.exports.{table.title().replace('_', '')} = mongoose.model('{table}', {table.title().replace('_', '')}Schema);")
            lines.append("")
        return "\n".join(lines).rstrip()

    if provider == "firebase":
        data = {}
        for table, cols in tables.items():
            data[table.lower()] = {
                "documentId": {
                    col["name"].lower(): sample_value(col.get("type"))
                    for col in cols
                }
            }
        return json.dumps(data, indent=2, ensure_ascii=False)

    lines = ["// MongoDB collection schema plan", ""]
    for table, cols in tables.items():
        lines.append(f"db.createCollection('{table.lower()}', {{")
        lines.append("  validator: {")
        lines.append("    $jsonSchema: {")
        lines.append("      bsonType: 'object',")
        lines.append(f"      title: '{table}',")
        required = [c["name"].lower() for c in cols if c.get("pk")]
        if required:
            lines.append(f"      required: {json.dumps(required)},")
        lines.append("      properties: {")
        for col in cols:
            kind = js_type(col.get("type"))
            bson = {"Number": "number", "Boolean": "bool", "Date": "date", "Object": "object"}.get(kind, "string")
            lines.append(f"        {col['name'].lower()}: {{ bsonType: '{bson}' }},")
        lines.append("      }")
        lines.append("    }")
        lines.append("  }")
        lines.append("});")
        lines.append("")
    return "\n".join(lines).rstrip()

#  LAYOUT

def auto_layout(tables, relations, cw=1600, ch=1100):
    names = list(tables.keys()); n = len(names)
    if not n: return {}
    cols_n = max(1, math.ceil(math.sqrt(n)))
    pos = {}
    for i, name in enumerate(names):
        r, c = divmod(i, cols_n)
        pos[name] = [80 + c*210, 80 + r*180]
    for _ in range(150):
        forces = {nm: [0.0, 0.0] for nm in names}
        for i in range(len(names)):
            for j in range(i+1, len(names)):
                a, b = names[i], names[j]
                dx = pos[b][0]-pos[a][0]; dy = pos[b][1]-pos[a][1]
                d = max(1, math.hypot(dx, dy))
                rep = 22000 / (d*d)
                fx = rep*dx/d; fy = rep*dy/d
                forces[a][0] -= fx; forces[a][1] -= fy
                forces[b][0] += fx; forces[b][1] += fy
        for rel in relations:
            f, t = rel["from"], rel["to"]
            if f not in pos or t not in pos: continue
            dx = pos[t][0]-pos[f][0]; dy = pos[t][1]-pos[f][1]
            d = max(1, math.hypot(dx, dy))
            att = d * 0.055
            fx = att*dx/d; fy = att*dy/d
            forces[f][0] += fx; forces[f][1] += fy
            forces[t][0] -= fx; forces[t][1] -= fy
        for nm in names:
            pos[nm][0] = max(60, min(cw-180, pos[nm][0] + max(-25, min(25, forces[nm][0]))))
            pos[nm][1] = max(60, min(ch-140, pos[nm][1] + max(-25, min(25, forces[nm][1]))))
    return {nm: (int(p[0]), int(p[1])) for nm, p in pos.items()}

#  THEMES

DEFAULT_THEMES = {
    "Dark Blue": {
        "bg": "#F0F2F5", "table_bg": "#FFFFFF", "header_bg": "#1A2340",
        "header_fg": "#FFFFFF", "pk_bg": "#FFF3CD", "fk_bg": "#D4EDDA",
        "text": "#1A2340", "sub": "#607090", "border": "#1A2340",
        "rel": "#2563EB", "sel": "#E53935", "grid": "#DDE1EA", "shadow": "#B0B8C8"
    },
    "Light": {
        "bg": "#FAFAFA", "table_bg": "#FFFFFF", "header_bg": "#374151",
        "header_fg": "#FFFFFF", "pk_bg": "#FEF9C3", "fk_bg": "#DCFCE7",
        "text": "#111827", "sub": "#6B7280", "border": "#374151",
        "rel": "#7C3AED", "sel": "#DC2626", "grid": "#E5E7EB", "shadow": "#D1D5DB"
    },
    "Warm": {
        "bg": "#FDF6EC", "table_bg": "#FFFDF8", "header_bg": "#7C3003",
        "header_fg": "#FEF3C7", "pk_bg": "#FEF9C3", "fk_bg": "#FEF3C7",
        "text": "#451A03", "sub": "#92400E", "border": "#7C3003",
        "rel": "#B45309", "sel": "#DC2626", "grid": "#FDE68A", "shadow": "#D97706"
    },
    "Night": {
        "bg": "#0D1117", "table_bg": "#161B22", "header_bg": "#21262D",
        "header_fg": "#E6EDF3", "pk_bg": "#3D2B00", "fk_bg": "#0D2E1A",
        "text": "#E6EDF3", "sub": "#8B949E", "border": "#30363D",
        "rel": "#58A6FF", "sel": "#F85149", "grid": "#21262D", "shadow": "#010409"
    },
    "Solarized": {
        "bg": "#FDF6E3", "table_bg": "#EEE8D5", "header_bg": "#073642",
        "header_fg": "#839496", "pk_bg": "#FFF8DC", "fk_bg": "#E8F5E9",
        "text": "#657B83", "sub": "#93A1A1", "border": "#073642",
        "rel": "#268BD2", "sel": "#DC322F", "grid": "#DDD8C4", "shadow": "#C9C3B0"
    },
    "Custom": {}
}

TH = dict(DEFAULT_THEMES["Dark Blue"])

UI = {
    "hud_bg": "#111827",
    "hud_bg_2": "#0F1729",
    "panel_bg": "#0D1526",
    "accent": "#2563EB",
    "accent_2": "#059669",
    "neutral": "#374151",
    "danger": "#E53935",
    "text": "#F8FAFC",
    "muted": "#90A8C8",
    "dim": "#4B6080",
    "editor_bg": "#0D1117",
}

CSS_THEME_MAP = {
    "canvas-bg": ("theme", "bg"),
    "table-bg": ("theme", "table_bg"),
    "table-header-bg": ("theme", "header_bg"),
    "table-header-text": ("theme", "header_fg"),
    "table-text": ("theme", "text"),
    "table-muted-text": ("theme", "sub"),
    "table-border": ("theme", "border"),
    "pk-bg": ("theme", "pk_bg"),
    "fk-bg": ("theme", "fk_bg"),
    "relation": ("theme", "rel"),
    "selected": ("theme", "sel"),
    "grid": ("theme", "grid"),
    "shadow": ("theme", "shadow"),
    "hud-bg": ("ui", "hud_bg"),
    "hud-bg-2": ("ui", "hud_bg_2"),
    "panel-bg": ("ui", "panel_bg"),
    "accent": ("ui", "accent"),
    "accent-2": ("ui", "accent_2"),
    "neutral": ("ui", "neutral"),
    "danger": ("ui", "danger"),
    "hud-text": ("ui", "text"),
    "hud-muted": ("ui", "muted"),
    "hud-dim": ("ui", "dim"),
    "editor-bg": ("ui", "editor_bg"),
}

def resource_path(path):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, path)

def load_css_theme(path="style.css"):
    css_path = path if os.path.isabs(path) else resource_path(path)
    if not os.path.exists(css_path) and not os.path.isabs(path):
        css_path = os.path.join(os.getcwd(), path)
    if not os.path.exists(css_path):
        return
    try:
        with open(css_path, "r", encoding="utf-8", errors="replace") as f:
            css = f.read()
    except OSError:
        return
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
    for name, value in re.findall(r'--([\w-]+)\s*:\s*([^;]+);', css):
        value = value.strip()
        target = CSS_THEME_MAP.get(name.strip().lower())
        if not target:
            continue
        group, key = target
        if group == "theme":
            TH[key] = value
        else:
            UI[key] = value

load_css_theme()

TABLE_W  = 170
ROW_H    = 22
HEADER_H = 30
PAD      = 6

FONT_HDR  = ("Segoe UI", 9, "bold")
FONT_COL  = ("Segoe UI", 8)
FONT_TYPE = ("Segoe UI", 7)
FONT_CARD = ("Segoe UI", 8, "bold")

def _draw_crow_foot(canvas, x, y, dx, dy, cardinality, color, scale):
    L = 14 * scale
    length = math.hypot(dx, dy)
    if length == 0: return
    ux, uy = dx/length, dy/length
    px, py = -uy, ux
    tx, ty = x + ux*L, y + uy*L
    s = 5*scale
    card = cardinality.replace(" ", "")
    many  = card.endswith("n") or card.endswith("N") or card.endswith("*")
    zero  = card.startswith("0")
    if many:
        canvas.create_line(x, y, tx, ty, fill=color, width=max(1,int(1.5*scale)))
        canvas.create_line(x+px*s, y+py*s, tx, ty, fill=color, width=max(1,int(1.5*scale)))
        canvas.create_line(x-px*s, y-py*s, tx, ty, fill=color, width=max(1,int(1.5*scale)))
    else:
        canvas.create_line(x, y, tx, ty, fill=color, width=max(1,int(1.5*scale)))
        canvas.create_line(x+px*s, y+py*s, x-px*s, y-py*s, fill=color, width=max(1,int(1.5*scale)))
    if zero:
        cx2, cy2 = tx + ux*s, ty + uy*s
        r = 4*scale
        canvas.create_oval(cx2-r, cy2-r, cx2+r, cy2+r, outline=color, fill=TH["bg"], width=max(1,int(1.5*scale)))
    else:
        bx, by = tx + ux*6*scale, ty + uy*6*scale
        canvas.create_line(bx+px*s, by+py*s, bx-px*s, by-py*s, fill=color, width=max(1,int(1.5*scale)))

def _draw_chen(canvas, x, y, dx, dy, cardinality, color, scale):
    card = cardinality.replace(" ","")
    if "n" in card.lower(): label = "N"
    elif card == "1,1": label = "1"
    elif card == "0,1": label = "0..1"
    else: label = cardinality
    length = math.hypot(dx, dy)
    if length == 0: return
    ux, uy = dx/length, dy/length
    lx = x + ux*20*scale; ly = y + uy*20*scale
    canvas.create_text(lx, ly, text=label, fill=color,
                       font=(FONT_CARD[0], max(6, int(FONT_CARD[1]*scale)), FONT_CARD[2]))

def _draw_uml(canvas, x, y, dx, dy, cardinality, color, scale):
    card = cardinality.replace(" ","")
    if card == "1,1": label = "1"
    elif card == "0,1": label = "0..1"
    elif card == "0,n": label = "0..*"
    elif card == "1,n": label = "1..*"
    else: label = cardinality
    length = math.hypot(dx, dy)
    if length == 0: return
    ux, uy = dx/length, dy/length
    py2, px2 = -uy, ux
    lx = x + ux*18*scale + px2*10*scale
    ly = y + uy*18*scale + py2*10*scale
    canvas.create_text(lx, ly, text=label, fill=color,
                       font=(FONT_CARD[0], max(6, int(FONT_CARD[1]*scale)), FONT_CARD[2]))

def _draw_simple(canvas, x, y, dx, dy, cardinality, color, scale):
    length = math.hypot(dx, dy)
    if length == 0: return
    ux, uy = dx/length, dy/length
    py2, px2 = -uy, ux
    lx = x + ux*22*scale + px2*10*scale
    ly = y + uy*22*scale + py2*10*scale
    canvas.create_text(lx, ly, text=f"({cardinality})", fill=color,
                       font=(FONT_CARD[0], max(6, int(FONT_CARD[1]*scale)), FONT_CARD[2]))

def _format_cardinality(cardinality, notation):
    card = cardinality.replace(" ", "").lower()
    if notation == "UML":
        return {"1,1": "1", "0,1": "0..1", "1,n": "1..*", "0,n": "0..*"}.get(card, cardinality)
    if notation == "Chen":
        return {"1,1": "1", "0,1": "0..1", "1,n": "N", "0,n": "0..N"}.get(card, cardinality)
    return f"({cardinality})"

NOTATION_FUNS = {
    "None": None,
    "Simple (a,b)": _draw_simple,
    "Crow's Foot": _draw_crow_foot,
    "Chen": _draw_chen,
    "UML": _draw_uml,
}
DIAGRAM_MODES = ["Conceitual", "Logico", "Fisico"]

#CANVAS

class ERCanvas(tk.Canvas):
    def __init__(self, master, app, **kw):
        kw.setdefault("highlightthickness", 0)
        super().__init__(master, bg=TH["bg"], cursor="hand2", **kw)
        self.app       = app
        self.tables    = {}
        self.positions = {}
        self.relations = []
        self._drag_table   = None
        self._drag_offset  = (0, 0)
        self._selected     = None
        self._scale        = 1.0
        self._pan_start    = None
        self._pan_origin   = {}
        self._pan_rel_origin = []
        self._selected_relation = None
        self._drag_waypoint = None
        self._drag_label   = None
        self._drag_label_start = (0, 0)
        self._drag_label_offset_start = [0, 0]

        self.bind("<ButtonPress-1>",   self._on_press)
        self.bind("<B1-Motion>",       self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<ButtonPress-3>",   self._on_rclick)
        self.bind("<ButtonPress-2>",   self._on_pan_start)
        self.bind("<B2-Motion>",       self._on_pan)
        self.bind("<MouseWheel>",      self._on_scroll)
        self.bind("<Button-4>",        self._on_scroll)
        self.bind("<Button-5>",        self._on_scroll)

    def load(self, tables, relations, positions):
        self.tables    = tables
        self.relations = dedupe_relations(relations)
        self.positions = {k: list(v) for k, v in positions.items()}
        self._selected = None
        self._selected_relation = None
        self.redraw()

    def redraw(self):
        self.delete("all")
        self.configure(bg=TH["bg"])
        self._draw_grid()
        self._draw_relations()
        self._draw_tables()
        self.app._update_inspector()

    def fit_to_screen(self):
        if not self.positions: return
        xs = [p[0] for p in self.positions.values()]
        ys = [p[1] for p in self.positions.values()]
        min_x, max_x = min(xs)-20, max(xs)+TABLE_W+40
        min_y, max_y = min(ys)-20, max(ys)+200
        w = self.winfo_width() or 900; h = self.winfo_height() or 600
        sx = w / max(1, max_x-min_x); sy = h / max(1, max_y-min_y)
        self._scale = max(0.25, min(2.0, min(sx, sy)*0.92))
        dx = -min_x+30; dy = -min_y+30
        for k in self.positions:
            self.positions[k][0] += dx
            self.positions[k][1] += dy
        self.redraw()

    def _s(self, v): return v * self._scale
    def _sx(self, x): return x * self._scale
    def _sy(self, y): return y * self._scale

    def _table_height(self, name):
        mode = self.app.diagram_mode.get()
        cols = self.tables.get(name, [])
        if mode == "Conceitual": return HEADER_H + PAD*2
        return HEADER_H + len(cols)*ROW_H + PAD

    def _table_rect(self, name):
        x, y = self.positions[name]
        return (self._sx(x), self._sy(y),
                self._sx(x)+self._s(TABLE_W),
                self._sy(y)+self._s(self._table_height(name)))

    def _draw_grid(self):
        step = int(40*self._scale)
        if step < 8: return
        w = self.winfo_width() or 1600; h = self.winfo_height() or 900
        for x in range(0, w+step, step):
            self.create_line(x, 0, x, h, fill=TH["grid"], width=1)
        for y in range(0, h+step, step):
            self.create_line(0, y, w, y, fill=TH["grid"], width=1)

    def _draw_tables(self):
        mode = self.app.diagram_mode.get()
        for name in self.tables:
            if name in self.positions:
                self._draw_table(name, mode)

    def _draw_table(self, name, mode):
        cols   = self.tables[name]
        x, y   = self.positions[name]
        sx, sy = self._sx(x), self._sy(y)
        sw     = self._s(TABLE_W)
        sh     = self._s(self._table_height(name))
        shdr   = self._s(HEADER_H)
        is_sel = (name == self._selected)
        sc     = self._scale

        self.create_rectangle(sx+3, sy+3, sx+sw+3, sy+sh+3,
                              fill=TH["shadow"], outline="", tags=("table", name))
        self.create_rectangle(sx, sy, sx+sw, sy+sh,
                              fill=TH["table_bg"],
                              outline=TH["sel"] if is_sel else TH["border"],
                              width=2 if is_sel else 1, tags=("table", name))
        if mode == "Conceitual":
            self.create_rectangle(sx, sy, sx+sw, sy+shdr,
                                  fill=TH["sel"] if is_sel else TH["header_bg"],
                                  outline="", tags=("table", name))
            self.create_text(sx+sw/2, sy+shdr/2, text=name,
                            fill=TH["header_fg"],
                            font=("Segoe UI", max(7, int(10*sc)), "bold"),
                            anchor="center", tags=("table", name))
            return

        self.create_rectangle(sx, sy, sx+sw, sy+shdr,
                              fill=TH["sel"] if is_sel else TH["header_bg"],
                              outline="", tags=("table", name))
        self.create_text(sx+sw/2, sy+shdr/2, text=name,
                        fill=TH["header_fg"],
                        font=(FONT_HDR[0], max(6, int(FONT_HDR[1]*sc)), "bold"),
                        anchor="center", tags=("table", name))

        rh = self._s(ROW_H); fy = sy+shdr
        show_types = (mode == "Fisico")
        for col in cols:
            bg = TH["pk_bg"] if col["pk"] else (TH["fk_bg"] if col["fk"] else TH["table_bg"])
            self.create_rectangle(sx+1, fy, sx+sw-1, fy+rh, fill=bg, outline="", tags=("table", name))
            self.create_line(sx+1, fy+rh, sx+sw-1, fy+rh, fill=TH["grid"], tags=("table", name))
            icon = "PK" if col["pk"] else ("FK" if col["fk"] else "  ")
            icon_color = "#B7791F" if col["pk"] else ("#276749" if col["fk"] else TH["sub"])
            self.create_text(sx+5, fy+rh/2, text=icon,
                            fill=icon_color,
                            font=("Segoe UI", max(5, int(6*sc)), "bold"),
                            anchor="w", tags=("table", name))
            self.create_text(sx+26, fy+rh/2, text=col["name"],
                            fill=TH["text"],
                            font=(FONT_COL[0], max(5, int(FONT_COL[1]*sc))),
                            anchor="w", tags=("table", name))
            if show_types:
                self.create_text(sx+sw-4, fy+rh/2, text=col["type"],
                                fill=TH["sub"],
                                font=(FONT_TYPE[0], max(4, int(FONT_TYPE[1]*sc))),
                                anchor="e", tags=("table", name))
            fy += rh

    def _column_anchor_y(self, name, col_name):
        if not col_name:
            return None
        cols = self.tables.get(name, [])
        for i, col in enumerate(cols):
            if col.get("name", "").upper() == col_name.upper():
                return self.positions[name][1] + HEADER_H + i*ROW_H + ROW_H/2
        return None

    def _edge_point_toward(self, name, target_x, target_y, preferred_col=None, slot_offset=0):
        x, y   = self.positions[name]
        w = TABLE_W; h = self._table_height(name)
        cx, cy = x+w/2, y+h/2
        dx, dy = target_x-cx, target_y-cy
        if abs(dx) >= abs(dy):
            ay = self._column_anchor_y(name, preferred_col) or y+h/2
            ay = max(y+HEADER_H/2, min(y+h-PAD, ay + slot_offset))
            if dx >= 0: return x+w, ay, 1, 0
            else:       return x,   ay, -1, 0
        else:
            ax = max(x+PAD, min(x+w-PAD, x+w/2 + slot_offset))
            if dy >= 0: return ax, y+h, 0, 1
            else:       return ax, y,   0, -1

    def _edge_point(self, name, other_name, preferred_col=None, slot_offset=0):
        ox, oy = self.positions[other_name]
        return self._edge_point_toward(name, ox+TABLE_W/2, oy+self._table_height(other_name)/2,
                                       preferred_col, slot_offset)

    def _relation_slot_offsets(self, rel_index, rel):
        def side_for(table, other):
            x, y = self.positions[table]
            ox, oy = self.positions[other]
            dx = ox + TABLE_W/2 - (x + TABLE_W/2)
            dy = oy + self._table_height(other)/2 - (y + self._table_height(table)/2)
            if abs(dx) >= abs(dy):
                return "right" if dx >= 0 else "left"
            return "bottom" if dy >= 0 else "top"

        result = []
        for endpoint, table_key, other_key in [("from", "from", "to"), ("to", "to", "from")]:
            table = rel[table_key]
            other = rel[other_key]
            side = side_for(table, other)
            siblings = []
            for i, r in enumerate(self.relations):
                if r.get(table_key) == table and r.get(other_key) in self.positions:
                    try:
                        if side_for(r[table_key], r[other_key]) == side:
                            siblings.append(i)
                    except KeyError:
                        pass
            siblings.sort()
            count = max(1, len(siblings))
            rank = siblings.index(rel_index) if rel_index in siblings else 0
            spacing = 18
            result.append((rank - (count-1)/2) * spacing)
        return result[0], result[1]

    def _default_route(self, start, end):
        fx, fy, fdx, fdy = start
        tx, ty, tdx, tdy = end
        if fdx and tdx:
            gap = max(70, min(180, abs(tx-fx) * 0.45))
            sx = fx + fdx * gap
            ex = tx + tdx * gap
            return [(fx, fy), (sx, fy), (sx, ty), (tx, ty)] if fdx == -tdx else [(fx, fy), (sx, fy), (ex, ty), (tx, ty)]
        if fdy and tdy:
            gap = max(70, min(180, abs(ty-fy) * 0.45))
            sy = fy + fdy * gap
            ey = ty + tdy * gap
            return [(fx, fy), (fx, sy), (tx, sy), (tx, ty)] if fdy == -tdy else [(fx, fy), (fx, sy), (tx, ey), (tx, ty)]
        p1 = (fx + fdx*70, fy + fdy*70) if fdx or fdy else (fx, fy)
        p2 = (tx + tdx*70, ty + tdy*70) if tdx or tdy else (tx, ty)
        return [(fx, fy), p1, (p1[0], p2[1]), p2, (tx, ty)]

    def _relation_points(self, rel, rel_index=0):
        frm, to = rel["from"], rel["to"]
        waypoints = rel.get("waypoints") or []
        from_slot, to_slot = self._relation_slot_offsets(rel_index, rel)
        if waypoints:
            fx, fy, fdx, fdy = self._edge_point_toward(frm, waypoints[0][0], waypoints[0][1], rel.get("from_col"), from_slot)
            tx, ty, tdx, tdy = self._edge_point_toward(to, waypoints[-1][0], waypoints[-1][1], rel.get("to_col"), to_slot)
        else:
            fx, fy, fdx, fdy = self._edge_point(frm, to, rel.get("from_col"), from_slot)
            tx, ty, tdx, tdy = self._edge_point(to, frm, rel.get("to_col"), to_slot)
        if waypoints:
            pts = [(fx, fy)] + [(p[0], p[1]) for p in waypoints] + [(tx, ty)]
        else:
            pts = self._default_route((fx, fy, fdx, fdy), (tx, ty, tdx, tdy))
        return pts

    def _relation_canvas_points(self, rel, rel_index=0):
        return [(self._sx(x), self._sy(y)) for x, y in self._relation_points(rel, rel_index)]

    def _flatten_points(self, pts):
        flat = []
        for x, y in pts:
            flat.extend([x, y])
        return flat

    def _card_label_pos(self, rel, side, pts=None):
        pts = pts or self._relation_canvas_points(rel)
        if side == "from":
            anchor, next_pt = pts[0], pts[1]
            off = rel.get("label_offset_from", [0, 0])
        else:
            anchor, next_pt = pts[-1], pts[-2]
            off = rel.get("label_offset_to", [0, 0])
        dx, dy = next_pt[0] - anchor[0], next_pt[1] - anchor[1]
        length = max(1, math.hypot(dx, dy))
        ux, uy = dx / length, dy / length
        nx, ny = -uy, ux
        x = anchor[0] + ux*42*self._scale + nx*20*self._scale + off[0]
        y = anchor[1] + uy*42*self._scale + ny*20*self._scale + off[1]
        margin = 18*self._scale
        for table in self.tables:
            if table not in self.positions:
                continue
            x1, y1, x2, y2 = self._table_rect(table)
            if x1-margin <= x <= x2+margin and y1-margin <= y <= y2+margin:
                x += nx*34*self._scale
                y += ny*34*self._scale
        return x, y

    def _draw_relations(self):
        notation = self.app.notation_var.get()
        mode = self.app.diagram_mode.get()
        for idx, rel in enumerate(self.relations):
            frm, to = rel["from"], rel["to"]
            if frm not in self.positions or to not in self.positions: continue
            pts = self._relation_canvas_points(rel, idx)
            sfx, sfy = pts[0]
            stx, sty = pts[-1]
            mx = sum(p[0] for p in pts) / len(pts)
            my = sum(p[1] for p in pts) / len(pts)
            lw = max(1, int(1.5*self._scale))
            is_sel = idx == self._selected_relation
            self.create_line(*self._flatten_points(pts),
                            smooth=False, fill=TH["sel"] if is_sel else TH["rel"],
                            width=lw + (1 if is_sel else 0), tags=("relation", f"rel_{idx}"))

            vx1 = pts[1][0]-sfx; vy1 = pts[1][1]-sfy
            vx2 = pts[-2][0]-stx; vy2 = pts[-2][1]-sty
            draw_fn = NOTATION_FUNS.get(notation)

            off = rel.get("label_offset", [0, 0])
            off_from = rel.get("label_offset_from", [0, 0])
            off_to   = rel.get("label_offset_to", [0, 0])

            if draw_fn and notation == "Crow's Foot":
                draw_fn(self, sfx, sfy, vx1, vy1, rel.get("card_from","1,1"), TH["rel"], self._scale)
                draw_fn(self, stx, sty, vx2, vy2, rel.get("card_to","0,n"),   TH["rel"], self._scale)

            card_fs = max(6, int(8*self._scale))
            lx_from, ly_from = self._card_label_pos(rel, "from", pts)
            lx_to, ly_to = self._card_label_pos(rel, "to", pts)

            cf_text = _format_cardinality(rel.get("card_from","1,1"), notation)
            ct_text = _format_cardinality(rel.get("card_to","0,n"), notation)

            self.create_text(lx_from, ly_from, text=cf_text, fill=TH["rel"],
                            font=("Segoe UI", card_fs, "bold"),
                            tags=("card_label", f"card_from_{idx}"))
            self.create_text(lx_to, ly_to, text=ct_text, fill=TH["rel"],
                            font=("Segoe UI", card_fs, "bold"),
                            tags=("card_label", f"card_to_{idx}"))

            if mode in ("Conceitual", "Logico"):
                rname = f"{frm[:4]}→{to[:4]}"
                lfs = max(5, int(7*self._scale))
                self.create_text(mx + off[0], my + off[1] - 10,
                                text=rname, fill=TH["rel"],
                                font=("Segoe UI", lfs, "italic"),
                                tags=("rel_label", f"rellbl_{idx}"))

            if is_sel:
                r = max(4, int(5*self._scale))
                for wp_index, (wx, wy) in enumerate(rel.get("waypoints", [])):
                    swx, swy = self._sx(wx), self._sy(wy)
                    self.create_rectangle(swx-r, swy-r, swx+r, swy+r,
                                          fill=TH["table_bg"], outline=TH["sel"], width=2,
                                          tags=("route_handle", f"rel_{idx}_wp_{wp_index}"))

    def _find_table_at(self, cx, cy):
        for name in self.tables:
            if name not in self.positions: continue
            x1,y1,x2,y2 = self._table_rect(name)
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                return name
        return None

    def _find_card_label_at(self, cx, cy, tol=14):
        """Return (rel_index, 'from'|'to') if near a cardinality label."""
        for idx, rel in enumerate(self.relations):
            frm, to = rel["from"], rel["to"]
            if frm not in self.positions or to not in self.positions: continue
            pts = self._relation_canvas_points(rel, idx)
            lx_from, ly_from = self._card_label_pos(rel, "from", pts)
            lx_to, ly_to = self._card_label_pos(rel, "to", pts)

            if math.hypot(cx - lx_from, cy - ly_from) < tol*self._scale:
                return idx, "from"
            if math.hypot(cx - lx_to, cy - ly_to) < tol*self._scale:
                return idx, "to"
        return None

    def _point_to_segment_distance(self, px, py, ax, ay, bx, by):
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay), 0.0
        t = max(0.0, min(1.0, ((px - ax)*dx + (py - ay)*dy) / (dx*dx + dy*dy)))
        nx, ny = ax + t*dx, ay + t*dy
        return math.hypot(px - nx, py - ny), t

    def _find_waypoint_at(self, cx, cy, tol=10):
        for idx, rel in enumerate(self.relations):
            for wp_index, (wx, wy) in enumerate(rel.get("waypoints", [])):
                if math.hypot(cx - self._sx(wx), cy - self._sy(wy)) <= tol*self._scale:
                    return idx, wp_index
        return None

    def _find_relation_near(self, cx, cy, tol=14):
        best = None
        best_dist = None
        for i, rel in enumerate(self.relations):
            frm, to = rel["from"], rel["to"]
            if frm not in self.positions or to not in self.positions: continue
            pts = self._relation_canvas_points(rel, i)
            for seg_index in range(len(pts)-1):
                dist, t = self._point_to_segment_distance(cx, cy, *pts[seg_index], *pts[seg_index+1])
                if dist <= tol*self._scale and (best_dist is None or dist < best_dist):
                    best = (i, seg_index, t)
                    best_dist = dist
        return best

    def _on_press(self, event):
        hit = self._find_card_label_at(event.x, event.y)
        if hit:
            self.app._push_undo()
            idx, side = hit
            self._drag_label = (idx, side)
            self._drag_label_start = (event.x, event.y)
            key = "label_offset_from" if side == "from" else "label_offset_to"
            self._drag_label_offset_start = list(self.relations[idx].get(key, [0, 0]))
            self.configure(cursor="fleur")
            return

        wp_hit = self._find_waypoint_at(event.x, event.y)
        if wp_hit:
            self.app._push_undo()
            self._selected_relation, wp_index = wp_hit
            self._selected = None
            self._drag_waypoint = (self._selected_relation, wp_index)
            self.configure(cursor="fleur")
            self.redraw()
            return

        name = self._find_table_at(event.x, event.y)
        self._selected = name
        if name:
            self.app._push_undo()
            self._drag_table  = name
            px, py = self.positions[name]
            self._drag_offset = (event.x - self._sx(px), event.y - self._sy(py))
            self._selected_relation = None
        else:
            rel_hit = self._find_relation_near(event.x, event.y)
            if rel_hit:
                self.app._push_undo()
                idx, seg_index, _ = rel_hit
                rel = self.relations[idx]
                rel.setdefault("waypoints", [])
                mx, my = event.x / self._scale, event.y / self._scale
                wp_insert = max(0, min(len(rel["waypoints"]), seg_index))
                rel["waypoints"].insert(wp_insert, [mx, my])
                self._selected_relation = idx
                self._drag_waypoint = (idx, wp_insert)
                self.configure(cursor="fleur")
                self.redraw()
                return
            self._selected_relation = None
        self.redraw()

    def _on_drag(self, event):
        if self._drag_waypoint:
            idx, wp_index = self._drag_waypoint
            if idx < len(self.relations) and wp_index < len(self.relations[idx].get("waypoints", [])):
                self.relations[idx]["waypoints"][wp_index] = [event.x / self._scale, event.y / self._scale]
                self.redraw()
            return
        if self._drag_label:
            idx, side = self._drag_label
            key = "label_offset_from" if side == "from" else "label_offset_to"
            sx0, sy0 = self._drag_label_start
            ox, oy = self._drag_label_offset_start
            self.relations[idx][key] = [ox + event.x - sx0, oy + event.y - sy0]
            self.redraw()
            return
        if self._drag_table:
            ox, oy = self._drag_offset
            self.positions[self._drag_table] = [
                (event.x-ox)/self._scale,
                (event.y-oy)/self._scale
            ]
            self.redraw()

    def _on_release(self, event):
        self._drag_table = None
        self._drag_label = None
        self._drag_waypoint = None
        self.configure(cursor="hand2")

    def _on_rclick(self, event):
        rel_hit = self._find_relation_near(event.x, event.y)
        if rel_hit is not None:
            idx, seg_index, _ = rel_hit
            self._selected_relation = idx
            self.redraw()
            self._relation_context(idx, seg_index, event.x, event.y, event.x_root, event.y_root); return
        name = self._find_table_at(event.x, event.y)
        if name:
            self._table_context(name, event.x_root, event.y_root)

    def _on_pan_start(self, event):
        self.app._push_undo()
        self._pan_start  = (event.x, event.y)
        self._pan_origin = {k: list(v) for k, v in self.positions.items()}
        self._pan_rel_origin = copy.deepcopy(self.relations)

    def _on_pan(self, event):
        if self._pan_start:
            dx = (event.x - self._pan_start[0]) / self._scale
            dy = (event.y - self._pan_start[1]) / self._scale
            for nm in self.positions:
                self.positions[nm][0] = self._pan_origin[nm][0] + dx
                self.positions[nm][1] = self._pan_origin[nm][1] + dy
            self.relations = copy.deepcopy(self._pan_rel_origin)
            for rel in self.relations:
                for wp in rel.get("waypoints", []):
                    wp[0] += dx
                    wp[1] += dy
            self.redraw()

    def _on_scroll(self, event):
        d = 0.1 if (event.num == 4 or event.delta > 0) else -0.1
        self._scale = max(0.15, min(3.5, self._scale+d))
        self.redraw()

    #popups 

    def _edit_relation(self, idx, rx, ry):
        rel = self.relations[idx]
        popup = tk.Toplevel(self)
        popup.title("Edit Relation")
        popup.geometry(f"+{rx}+{ry}")
        popup.resizable(False, False)
        popup.configure(bg="#1A2340")
        popup.grab_set()
        tk.Label(popup, text=f"  {rel['from']}  ──  {rel['to']}  ",
                bg="#1A2340", fg="white",
                font=("Segoe UI",10,"bold")).grid(row=0, column=0, columnspan=2, padx=12, pady=(10,4))
        tk.Label(popup, text=f"Cardinality at {rel['from']}:", bg="#1A2340", fg="#90A8C8",
                font=("Segoe UI",9)).grid(row=1, column=0, sticky="w", padx=12, pady=2)
        cf_var = tk.StringVar(value=rel.get("card_from","1,1"))
        ttk.Combobox(popup, textvariable=cf_var, values=CARDINALITIES, width=8, state="normal").grid(row=1, column=1, padx=12, pady=2)
        tk.Label(popup, text=f"Cardinality at {rel['to']}:", bg="#1A2340", fg="#90A8C8",
                font=("Segoe UI",9)).grid(row=2, column=0, sticky="w", padx=12, pady=2)
        ct_var = tk.StringVar(value=rel.get("card_to","0,n"))
        ttk.Combobox(popup, textvariable=ct_var, values=CARDINALITIES, width=8, state="normal").grid(row=2, column=1, padx=12, pady=2)
        tk.Label(popup, text="Hint: drag labels on diagram to reposition", bg="#1A2340", fg="#4B6080",
                font=("Segoe UI",7)).grid(row=3, column=0, columnspan=2, pady=(0,2))
        def apply():
            self.relations[idx]["card_from"] = cf_var.get()
            self.relations[idx]["card_to"]   = ct_var.get()
            self.redraw(); popup.destroy()
        tk.Button(popup, text="Apply", command=apply, bg="#2563EB", fg="white",
                 font=("Segoe UI",9,"bold"), relief="flat", padx=16, pady=4,
                 cursor="hand2").grid(row=4, column=0, columnspan=2, pady=10)
        popup.bind("<Return>", lambda e: apply())

    def _relation_context(self, idx, seg_index, cx, cy, rx, ry):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="  Edit cardinality", command=lambda: self._edit_relation(idx, rx, ry))
        menu.add_command(label="  Add bend point", command=lambda: self._add_waypoint(idx, seg_index, cx, cy))
        menu.add_command(label="  Reset route", command=lambda: self._reset_relation_route(idx))
        menu.post(rx, ry)

    def _add_waypoint(self, idx, seg_index, cx, cy):
        self.app._push_undo()
        rel = self.relations[idx]
        rel.setdefault("waypoints", [])
        wp_insert = max(0, min(len(rel["waypoints"]), seg_index))
        rel["waypoints"].insert(wp_insert, [cx / self._scale, cy / self._scale])
        self._selected_relation = idx
        self.redraw()

    def _reset_relation_route(self, idx):
        self.app._push_undo()
        self.relations[idx]["waypoints"] = []
        self._selected_relation = idx
        self.redraw()

    def _table_context(self, name, rx, ry):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=f"  Table: {name}", state="disabled")
        menu.add_separator()
        menu.add_command(label="  Bring to Front", command=lambda: self._bring_front(name))
        menu.add_command(label="  Rename…",        command=lambda: self._rename_table(name, rx, ry))
        menu.post(rx, ry)

    def _bring_front(self, name):
        self.tag_raise(name)

    def _rename_table(self, old_name, rx, ry):
        popup = tk.Toplevel(self)
        popup.title("Rename Table"); popup.geometry(f"+{rx}+{ry}")
        popup.configure(bg="#1A2340"); popup.grab_set()
        tk.Label(popup, text="New name:", bg="#1A2340", fg="white", font=("Segoe UI",9)).pack(padx=12, pady=(10,2))
        var = tk.StringVar(value=old_name)
        e = tk.Entry(popup, textvariable=var, font=("Segoe UI",10))
        e.pack(padx=12, pady=4); e.select_range(0,"end"); e.focus_set()
        def ok():
            nn = var.get().strip().upper()
            if not nn or nn == old_name: popup.destroy(); return
            self.tables[nn] = self.tables.pop(old_name)
            self.positions[nn] = self.positions.pop(old_name)
            for rel in self.relations:
                if rel["from"] == old_name: rel["from"] = nn
                if rel["to"]   == old_name: rel["to"]   = nn
            if self._selected == old_name: self._selected = nn
            self.redraw(); popup.destroy()
        tk.Button(popup, text="OK", command=ok, bg="#2563EB", fg="white",
                 relief="flat", font=("Segoe UI",9,"bold"), padx=14, pady=4,
                 cursor="hand2").pack(pady=8)
        popup.bind("<Return>", lambda e: ok())


#  SETTINGS

class SettingsPanel(tk.Frame):
    COLOR_KEYS = [
        ("bg",        "Canvas Background"),
        ("table_bg",  "Table Background"),
        ("header_bg", "Header Background"),
        ("header_fg", "Header Text"),
        ("pk_bg",     "PK Row Background"),
        ("fk_bg",     "FK Row Background"),
        ("text",      "Column Text"),
        ("sub",       "Subtext / Type"),
        ("border",    "Table Border"),
        ("rel",       "Relation Line"),
        ("sel",       "Selection Color"),
        ("grid",      "Grid Lines"),
        ("shadow",    "Table Shadow"),
    ]

    def __init__(self, master, app, **kw):
        super().__init__(master, bg="#0F1729", **kw)
        self.app = app
        self._build()

    def _build(self):
        tk.Label(self, text="⚙  Customization", bg="#0F1729", fg="#E2E8F0",
                font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=20, pady=(18,4))
        tk.Label(self, text="Personalize colors, fonts, and diagram look.", bg="#0F1729", fg="#607090",
                font=("Segoe UI", 9)).pack(anchor="w", padx=20, pady=(0,14))

        tf = tk.LabelFrame(self, text="  Theme Presets  ", bg="#0F1729", fg="#90A8C8",
                          font=("Segoe UI",9,"bold"), bd=1, relief="groove")
        tf.pack(fill="x", padx=20, pady=(0,12))
        row = tk.Frame(tf, bg="#0F1729"); row.pack(fill="x", padx=8, pady=8)
        for name in DEFAULT_THEMES:
            if name == "Custom": continue
            tk.Button(row, text=name, bg="#1A2340", fg="#E2E8F0",
                     font=("Segoe UI",8), relief="flat", padx=10, pady=4, cursor="hand2",
                     command=lambda n=name: self._apply_preset(n)).pack(side="left", padx=4)

        cf = tk.LabelFrame(self, text="  Custom Colors  ", bg="#0F1729", fg="#90A8C8",
                          font=("Segoe UI",9,"bold"), bd=1, relief="groove")
        cf.pack(fill="both", expand=True, padx=20, pady=(0,12))

        canvas = tk.Canvas(cf, bg="#0F1729", highlightthickness=0)
        vsb = ttk.Scrollbar(cf, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg="#0F1729")
        cwin = canvas.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(cwin, width=e.width))

        self._color_vars = {}
        for i, (key, label) in enumerate(self.COLOR_KEYS):
            row2 = tk.Frame(inner, bg="#0F1729"); row2.pack(fill="x", padx=8, pady=3)
            tk.Label(row2, text=label, bg="#0F1729", fg="#90A8C8",
                    font=("Segoe UI",9), width=22, anchor="w").pack(side="left")
            var = tk.StringVar(value=TH.get(key, "#FFFFFF"))
            self._color_vars[key] = var
            swatch = tk.Label(row2, bg=TH.get(key,"#FFFFFF"), width=4, relief="groove")
            swatch.pack(side="left", padx=4)
            ent = tk.Entry(row2, textvariable=var, font=("Consolas",9), width=10,
                          bg="#0D1526", fg="#E2E8F0", insertbackground="white", relief="flat")
            ent.pack(side="left", padx=4)
            def pick(k=key, sw=swatch, v=var):
                c = colorchooser.askcolor(title=f"Pick {k}", color=v.get())
                if c[1]:
                    v.set(c[1]); sw.configure(bg=c[1])
            tk.Button(row2, text="Pick", command=pick, bg="#2563EB", fg="white",
                     font=("Segoe UI",8), relief="flat", padx=8, pady=1,
                     cursor="hand2").pack(side="left", padx=2)
            var.trace_add("write", lambda *a, k=key, sw=swatch, v=var: self._update_color(k, v, sw))

        ff = tk.LabelFrame(self, text="  Table Width  ", bg="#0F1729", fg="#90A8C8",
                          font=("Segoe UI",9,"bold"), bd=1, relief="groove")
        ff.pack(fill="x", padx=20, pady=(0,12))
        fr2 = tk.Frame(ff, bg="#0F1729"); fr2.pack(fill="x", padx=8, pady=8)
        tk.Label(fr2, text="Table width:", bg="#0F1729", fg="#90A8C8", font=("Segoe UI",9)).pack(side="left")
        self._tw_var = tk.IntVar(value=TABLE_W)
        tk.Scale(fr2, from_=120, to=300, orient="horizontal", variable=self._tw_var,
                bg="#0F1729", fg="#E2E8F0", highlightthickness=0, troughcolor="#1A2340",
                command=self._update_tw).pack(side="left", padx=8, fill="x", expand=True)

        tk.Button(self, text="✔ Apply Custom Theme", command=self._apply_custom,
                 bg="#2563EB", fg="white", font=("Segoe UI",10,"bold"),
                 relief="flat", padx=20, pady=8, cursor="hand2").pack(pady=12)

    def _update_color(self, key, var, swatch):
        try:
            v = var.get()
            swatch.configure(bg=v)
        except: pass

    def _apply_preset(self, name):
        global TH
        TH = dict(DEFAULT_THEMES[name])
        self.app.theme_var.set(name)
        for key, var in self._color_vars.items():
            var.set(TH.get(key, "#FFFFFF"))
        self.app.er_canvas.redraw()

    def _apply_custom(self):
        global TH
        for key, var in self._color_vars.items():
            try: TH[key] = var.get()
            except: pass
        self.app.theme_var.set("Custom")
        self.app.er_canvas.redraw()

    def _update_tw(self, val):
        global TABLE_W
        TABLE_W = int(val)
        self.app.er_canvas.redraw()


#  VISUAL BUILDER 


COL_TYPES = ["INT", "VARCHAR(100)", "VARCHAR(50)", "VARCHAR(20)", "TEXT",
             "DECIMAL(10,2)", "DATE", "DATETIME", "BOOLEAN", "FLOAT", "BIGINT", "CHAR(1)"]

class VisualBuilder(tk.Frame):
    """GUI-driven DB builder: add tables, columns, relations → generate SQL."""

    def __init__(self, master, app, **kw):
        super().__init__(master, bg="#0F1729", **kw)
        self.app = app
        self._selected_table = None
        self._build()

    def _build(self):
        left = tk.Frame(self, bg="#0D1526", width=220)
        left.pack(side="left", fill="y", padx=0)
        left.pack_propagate(False)

        tk.Label(left, text="Tables", bg="#0D1526", fg="#90A8C8",
                font=("Segoe UI",10,"bold")).pack(anchor="w", padx=10, pady=(14,4))

        self.table_listbox = tk.Listbox(left, bg="#0D1526", fg="#E2E8F0",
                                        selectbackground="#2563EB", font=("Segoe UI",10),
                                        relief="flat", bd=0, activestyle="none")
        self.table_listbox.pack(fill="both", expand=True, padx=8, pady=4)
        self.table_listbox.bind("<<ListboxSelect>>", self._on_table_select)

        btn_row = tk.Frame(left, bg="#0D1526"); btn_row.pack(fill="x", padx=8, pady=6)
        self._icon_btn(btn_row, "＋ Add Table", self._add_table).pack(side="left", fill="x", expand=True)
        self._icon_btn(btn_row, "✕ Delete", self._delete_table, color="#E53935").pack(side="left", padx=(4,0))

        # ── COLLUM EDITOR / EDITOR DE COLUNAS ─────────────────────────────────────────

        mid = tk.Frame(self, bg="#0F1729")
        mid.pack(side="left", fill="both", expand=True, padx=0)

        self.col_label = tk.Label(mid, text="Select a table", bg="#0F1729", fg="#90A8C8",
                                  font=("Segoe UI",10,"bold"))
        self.col_label.pack(anchor="w", padx=16, pady=(14,4))

        cols_frame = tk.Frame(mid, bg="#0F1729")
        cols_frame.pack(fill="both", expand=True, padx=16)
        cols_vsb = ttk.Scrollbar(cols_frame, orient="vertical")
        self.col_tree = ttk.Treeview(cols_frame, columns=("name","type","pk","fk"),
                                     show="headings", yscrollcommand=cols_vsb.set,
                                     height=12)
        cols_vsb.configure(command=self.col_tree.yview)
        cols_vsb.pack(side="right", fill="y")
        self.col_tree.pack(fill="both", expand=True)
        for col, w in [("name",130),("type",130),("pk",45),("fk",45)]:
            self.col_tree.heading(col, text=col.upper())
            self.col_tree.column(col, width=w, anchor="center" if col in ("pk","fk") else "w")

        add_row = tk.Frame(mid, bg="#0F1729"); add_row.pack(fill="x", padx=16, pady=4)
        tk.Label(add_row, text="Name:", bg="#0F1729", fg="#90A8C8", font=("Segoe UI",9)).pack(side="left")
        self._col_name = tk.StringVar()
        tk.Entry(add_row, textvariable=self._col_name, font=("Segoe UI",9), width=14,
                bg="#0D1526", fg="#E2E8F0", insertbackground="white", relief="flat").pack(side="left", padx=4)
        tk.Label(add_row, text="Type:", bg="#0F1729", fg="#90A8C8", font=("Segoe UI",9)).pack(side="left")
        self._col_type = tk.StringVar(value="VARCHAR(100)")
        ttk.Combobox(add_row, textvariable=self._col_type, values=COL_TYPES,
                    width=14, state="normal").pack(side="left", padx=4)
        self._col_pk = tk.BooleanVar()
        tk.Checkbutton(add_row, text="PK", variable=self._col_pk, bg="#0F1729", fg="#E2E8F0",
                      selectcolor="#2563EB", activebackground="#0F1729").pack(side="left", padx=2)
        self._icon_btn(add_row, "＋ Column", self._add_column).pack(side="left", padx=4)
        self._icon_btn(add_row, "✕ Del Col", self._delete_column, color="#E53935").pack(side="left")

        # ── Relations Editor / Editor de Relações ───────────────────────────────────────
        right = tk.Frame(self, bg="#0D1526", width=280)
        right.pack(side="right", fill="y", padx=0)
        right.pack_propagate(False)

        tk.Label(right, text="Relations", bg="#0D1526", fg="#90A8C8",
                font=("Segoe UI",10,"bold")).pack(anchor="w", padx=10, pady=(14,4))

        self.rel_listbox = tk.Listbox(right, bg="#0D1526", fg="#E2E8F0",
                                      selectbackground="#2563EB", font=("Segoe UI",8),
                                      relief="flat", bd=0, activestyle="none")
        self.rel_listbox.pack(fill="both", expand=True, padx=8, pady=4)

        rr = tk.Frame(right, bg="#0D1526"); rr.pack(fill="x", padx=8, pady=4)
        for label, r in [("From table:", "rel_from"), ("To table:", "rel_to")]:
            tk.Label(rr, text=label, bg="#0D1526", fg="#90A8C8", font=("Segoe UI",9)).pack(anchor="w", pady=1)
            var = tk.StringVar()
            setattr(self, f"_{r}_var", var)
            cb = ttk.Combobox(rr, textvariable=var, width=22, state="readonly")
            setattr(self, f"_{r}_cb", cb)
            cb.pack(fill="x", pady=2)
            cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_relation_column_choices())

        for label, r in [("From column:", "rel_from_col"), ("To column:", "rel_to_col")]:
            tk.Label(rr, text=label, bg="#0D1526", fg="#90A8C8", font=("Segoe UI",9)).pack(anchor="w", pady=1)
            var = tk.StringVar()
            setattr(self, f"_{r}_var", var)
            cb = ttk.Combobox(rr, textvariable=var, width=22, state="readonly")
            setattr(self, f"_{r}_cb", cb)
            cb.pack(fill="x", pady=2)

        for label, attr in [("Card From:", "_rel_cf"), ("Card To:", "_rel_ct")]:
            tk.Label(rr, text=label, bg="#0D1526", fg="#90A8C8", font=("Segoe UI",9)).pack(anchor="w", pady=1)
            var = tk.StringVar(value="1,1" if "From" in label else "0,n")
            setattr(self, f"{attr}_var", var)
            ttk.Combobox(rr, textvariable=var, values=CARDINALITIES, width=8, state="readonly").pack(anchor="w", pady=2)

        br = tk.Frame(right, bg="#0D1526"); br.pack(fill="x", padx=8, pady=4)
        self._icon_btn(br, "＋ Add Relation", self._add_relation).pack(fill="x", pady=2)
        self._icon_btn(br, "✕ Del Relation", self._delete_relation, color="#E53935").pack(fill="x", pady=2)
        bot = tk.Frame(self, bg="#0F1729")
        bot2 = tk.Frame(mid, bg="#0F1729"); bot2.pack(fill="x", padx=16, pady=8)
        self._icon_btn(bot2, "🖥 Send to Diagram", self._send_to_diagram, color="#059669").pack(side="left", padx=4)
        self._icon_btn(bot2, "📋 Generate SQL", self._generate_sql, color="#7C3AED").pack(side="left", padx=4)

    def _icon_btn(self, parent, text, cmd, color="#2563EB"):
        return tk.Button(parent, text=text, command=cmd, bg=color, fg="white",
                        font=("Segoe UI",8,"bold"), relief="flat", padx=8, pady=4, cursor="hand2")

    def _refresh_table_list(self):
        self.table_listbox.delete(0, "end")
        tables = self.app.er_canvas.tables
        for t in tables:
            self.table_listbox.insert("end", t)
        names = list(tables.keys())
        self._rel_from_cb["values"] = names
        self._rel_to_cb["values"] = names
        self._refresh_relation_column_choices()
        self._refresh_rel_list()

    def _refresh_relation_column_choices(self):
        tables = self.app.er_canvas.tables
        for table_var, col_var, col_cb in [
            (self._rel_from_var, self._rel_from_col_var, self._rel_from_col_cb),
            (self._rel_to_var, self._rel_to_col_var, self._rel_to_col_cb),
        ]:
            table_name = table_var.get()
            values = [c["name"] for c in tables.get(table_name, [])]
            col_cb["values"] = values
            if values and col_var.get() not in values:
                col_var.set(values[0])
            elif not values:
                col_var.set("")

    def _refresh_rel_list(self):
        self.rel_listbox.delete(0, "end")
        for rel in self.app.er_canvas.relations:
            self.rel_listbox.insert("end",
                f"{rel['from']} ({rel.get('card_from','?')}) → ({rel.get('card_to','?')}) {rel['to']}")

    def _on_table_select(self, event=None):
        sel = self.table_listbox.curselection()
        if not sel: return
        name = self.table_listbox.get(sel[0])
        self._selected_table = name
        self.col_label.config(text=f"Columns of  {name}")
        self._refresh_col_tree(name)

    def _refresh_col_tree(self, name):
        for row in self.col_tree.get_children():
            self.col_tree.delete(row)
        for col in self.app.er_canvas.tables.get(name, []):
            self.col_tree.insert("", "end", values=(
                col["name"], col["type"],
                "✓" if col["pk"] else "", "✓" if col["fk"] else ""))

    def _add_table(self):
        popup = tk.Toplevel(self)
        popup.title("New Table"); popup.configure(bg="#1A2340"); popup.grab_set()
        tk.Label(popup, text="Table name:", bg="#1A2340", fg="white", font=("Segoe UI",9)).pack(padx=16, pady=(12,2))
        var = tk.StringVar()
        e = tk.Entry(popup, textvariable=var, font=("Segoe UI",10))
        e.pack(padx=16, pady=4); e.focus_set()
        def ok():
            name = var.get().strip().upper()
            if not name: return
            canvas = self.app.er_canvas
            if name in canvas.tables:
                messagebox.showerror("Duplicate", f"Table '{name}' already exists."); return
            canvas.tables[name] = []
            w = canvas.winfo_width() or 800; h = canvas.winfo_height() or 600
            canvas.positions[name] = [50 + (len(canvas.tables)%5)*200, 50 + (len(canvas.tables)//5)*180]
            canvas.redraw()
            self._refresh_table_list()
            popup.destroy()
        tk.Button(popup, text="Create", command=ok, bg="#2563EB", fg="white",
                 relief="flat", font=("Segoe UI",9,"bold"), padx=14, pady=4,
                 cursor="hand2").pack(pady=10)
        popup.bind("<Return>", lambda e: ok())

    def _delete_table(self):
        if not self._selected_table: return
        if not messagebox.askyesno("Delete", f"Delete table '{self._selected_table}'?"): return
        canvas = self.app.er_canvas
        canvas.tables.pop(self._selected_table, None)
        canvas.positions.pop(self._selected_table, None)
        canvas.relations = [r for r in canvas.relations
                           if r["from"] != self._selected_table and r["to"] != self._selected_table]
        self._selected_table = None
        canvas.redraw()
        self._refresh_table_list()

    def _add_column(self):
        if not self._selected_table:
            messagebox.showinfo("Select Table", "Please select a table first."); return
        name = self._col_name.get().strip().upper()
        if not name:
            messagebox.showinfo("Name", "Column name required."); return
        dtype = self._col_type.get().strip() or "VARCHAR(100)"
        is_pk = self._col_pk.get()
        canvas = self.app.er_canvas
        cols = canvas.tables.get(self._selected_table, [])
        if any(c["name"].upper() == name for c in cols):
            messagebox.showerror("Duplicate", f"Column '{name}' already exists in {self._selected_table}."); return
        cols.append({"name": name, "type": dtype, "pk": is_pk, "fk": False, "ref": None})
        canvas.tables[self._selected_table] = cols
        self._col_name.set("")
        self._col_pk.set(False)
        canvas.redraw()
        self._refresh_col_tree(self._selected_table)

    def _delete_column(self):
        if not self._selected_table: return
        sel = self.col_tree.selection()
        if not sel: return
        vals = self.col_tree.item(sel[0])["values"]
        col_name = vals[0]
        canvas = self.app.er_canvas
        cols = canvas.tables.get(self._selected_table, [])
        canvas.tables[self._selected_table] = [c for c in cols if c["name"] != col_name]
        canvas.relations = [r for r in canvas.relations
                           if not (r["from"] == self._selected_table and r.get("from_col") == col_name)
                           and not (r["to"] == self._selected_table and r.get("to_col") == col_name)]
        self._sync_fk_flags()
        canvas.redraw()
        self._refresh_col_tree(self._selected_table)
        self._refresh_rel_list()

    def _add_relation(self):
        frm = self._rel_from_var.get()
        to  = self._rel_to_var.get()
        if not frm or not to:
            messagebox.showinfo("Relation", "Select both From and To tables."); return
        canvas = self.app.er_canvas
        rel = {
            "from": frm, "from_col": self._rel_from_col_var.get(),
            "to": to, "to_col": self._rel_to_col_var.get(),
            "card_from": self._rel_cf_var.get(),
            "card_to":   self._rel_ct_var.get(),
            "label_offset": [0,0], "label_offset_from": [0,0], "label_offset_to": [0,0]
        }
        if relation_key(rel) in {relation_key(r) for r in canvas.relations}:
            messagebox.showinfo("Duplicate", "That relation already exists."); return
        canvas.relations.append(rel)
        self._sync_fk_flags()
        canvas.redraw()
        self._refresh_rel_list()

    def _delete_relation(self):
        sel = self.rel_listbox.curselection()
        if not sel: return
        canvas = self.app.er_canvas
        canvas.relations.pop(sel[0])
        self._sync_fk_flags()
        canvas.redraw()
        self._refresh_rel_list()

    def _sync_fk_flags(self):
        canvas = self.app.er_canvas
        rel_refs = {(r["from"], r.get("from_col", "")): r["to"] for r in canvas.relations if r.get("from_col")}
        for tname, cols in canvas.tables.items():
            for col in cols:
                ref = rel_refs.get((tname, col["name"]))
                col["fk"] = bool(ref)
                col["ref"] = ref
        if self._selected_table:
            self._refresh_col_tree(self._selected_table)

    def _send_to_diagram(self):
        self.app.notebook.select(self.app.tab_diagram)
        self.app.er_canvas.redraw()

    def _generate_sql(self):
        canvas = self.app.er_canvas
        sql = tables_to_sql(canvas.tables, canvas.relations)
        self.app.sql_editor.delete("1.0", "end")
        self.app.sql_editor.insert("1.0", sql)
        self.app.notebook.select(self.app.tab_sql)
        self.app.status.config(text="✔ SQL generated from visual builder. Switch to SQL tab to view/edit.")

    def on_show(self):
        self._refresh_table_list()
        if self._selected_table:
            self._refresh_col_tree(self._selected_table)


#  MAIN APP / APLICATIVO


EXAMPLE_SQL = """\
-- SQL Example

CREATE DATABASE shop_db;

-- Use database
USE shop_db;

-- Users table
CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100),
    email VARCHAR(100) UNIQUE
);

-- Products table
CREATE TABLE products (
    id INT PRIMARY KEY AUTO_INCREMENT,
    product_name VARCHAR(100),
    price DECIMAL(10,2)
);

-- Orders table with relationships
CREATE TABLE orders (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT,
    product_id INT,
    order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Relations
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
"""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Diagrama Studio")
        self.geometry("1440x900")
        self.configure(bg=UI["hud_bg"])
        self.minsize(900, 600)
        self._set_window_icon()
        # Custom chrome: instead of overrideredirect() (which pulls the
        # window entirely out of Windows' window-manager control — no
        # taskbar entry, no Alt-Tab, and it can drop behind other windows
        # on focus loss with no way back, which looks exactly like the
        # app closing), we strip only the native title bar from the real
        # window style on Windows. The window stays fully WM-managed:
        # normal taskbar entry, normal Alt-Tab, normal focus/stacking
        # behavior. Maximize is handled manually (see _toggle_maximize)
        # since removing the caption also breaks Windows' own taskbar-
        # aware zoom logic.
        self.use_custom_chrome = (sys.platform == "win32")
        if self.use_custom_chrome:
            self.update_idletasks()
            self._strip_native_titlebar()
            self._is_maximized = False

        self._parsed    = {"tables":{}, "relations":[]}
        self._positions = {}
        self._undo_stack = []
        self._max_undo = 60
        self.project_store = LocalJsonProvider()
        self.current_project_id = None
        self.current_project_path = None
        self._window_restore_geometry = None
        self._icon_images = {}

        self.diagram_mode  = tk.StringVar(value="Fisico")
        self.notation_var  = tk.StringVar(value="Simple (a,b)")
        self.theme_var     = tk.StringVar(value="Dark Blue")
        self.nosql_provider_var = tk.StringVar(value="MongoDB")

        self._build_ui()
        self._bind_shortcuts()
        self.sql_editor.insert("1.0", EXAMPLE_SQL)
        self._generate()

    # UI

    def _resource_path(self, *parts):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, *parts)

    def _set_window_icon(self):
        icon_path = self._resource_path("icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except tk.TclError:
                pass

    def _load_icon(self, name, size=22):
        key = (name, size)
        if key in self._icon_images:
            return self._icon_images[key]
        path = self._resource_path("ui", "Icons", name)
        if not os.path.exists(path):
            return None
        try:
            if PIL_OK:
                img = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
            else:
                photo = tk.PhotoImage(file=path)
                if photo.width() > size:
                    photo = photo.subsample(max(1, photo.width() // size))
            self._icon_images[key] = photo
            return photo
        except Exception:
            return None

    def _strip_native_titlebar(self):
        if sys.platform != "win32":
            return
        try:
            GWL_STYLE = -16
            WS_CAPTION = 0x00C00000
            WS_THICKFRAME = 0x00040000
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020

            hwnd = user32.GetParent(self.winfo_id())
            self._hwnd = hwnd
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            style = (style & ~WS_CAPTION) | WS_THICKFRAME
            user32.SetWindowLongW(hwnd, GWL_STYLE, style)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)

            try:
                pref = ctypes.c_int(DWMWCP_DONOTROUND)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                    ctypes.byref(pref), ctypes.sizeof(pref))
            except Exception:
                pass

            self._wndproc_ref = WNDPROC(self._wnd_proc)
            self._old_wndproc = _SetWindowLongPtrW(hwnd, GWLP_WNDPROC, self._wndproc_ref)
        except Exception:
            pass

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_NCCALCSIZE:
            return 0
        if msg == WM_NCACTIVATE:
            return 1
        if msg == WM_NCHITTEST:
            return self._hit_test(hwnd, lparam)
        if msg == WM_SIZE:
            if wparam == SIZE_MAXIMIZED and not self._is_maximized:
                self._is_maximized = True
                self.after_idle(self._refresh_maximize_icon)
            elif wparam == SIZE_RESTORED and self._is_maximized:
                self._is_maximized = False
                self.after_idle(self._refresh_maximize_icon)
        return user32.CallWindowProcW(self._old_wndproc, hwnd, msg, wparam, lparam)

    def _hit_test(self, hwnd, lparam):
        if getattr(self, "_is_maximized", False):
            return HTCLIENT
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        b = RESIZE_BORDER
        on_left, on_right = x < rect.left + b, x >= rect.right - b
        on_top, on_bottom = y < rect.top + b, y >= rect.bottom - b
        if on_top and on_left:     return HTTOPLEFT
        if on_top and on_right:    return HTTOPRIGHT
        if on_bottom and on_left:  return HTBOTTOMLEFT
        if on_bottom and on_right: return HTBOTTOMRIGHT
        if on_left:   return HTLEFT
        if on_right:  return HTRIGHT
        if on_top:    return HTTOP
        if on_bottom: return HTBOTTOM
        return HTCLIENT

    def _set_thickframe(self, enabled):
        """Toggle the resizable-border style bit. We turn it off while
        maximized so the window sits perfectly flush with the work area
        instead of showing a sliver of resize-border/dead space around
        the edges (which is what made the old maximize look 'fake')."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            GWL_STYLE = -16
            WS_THICKFRAME = 0x00040000
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            style = (style | WS_THICKFRAME) if enabled else (style & ~WS_THICKFRAME)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
        except Exception:
            pass

    def _start_window_drag(self, event):
        if getattr(self, "_is_maximized", False):
            # Real Windows apps restore-then-drag when you grab a
            # maximized window's title bar; do the same instead of
            # dragging the full work-area-sized window around.
            self._toggle_maximize()
            self.update_idletasks()
        self._drag_start = (event.x_root, event.y_root, self.winfo_x(), self.winfo_y())

    def _drag_window(self, event):
        if not hasattr(self, "_drag_start"):
            return
        sx, sy, wx, wy = self._drag_start
        new_x = wx + event.x_root - sx
        new_y = wy + event.y_root - sy
        if sys.platform == "win32" and hasattr(self, "_hwnd"):
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            user32.SetWindowPos(self._hwnd, 0, new_x, new_y, 0, 0,
                                 SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        else:
            self.geometry(f"+{new_x}+{new_y}")

    def _minimize_window(self):
        self.iconify()

    def _get_work_area(self):
        """Return (x, y, width, height) of the work area (screen minus
        taskbar) of whichever monitor the window currently sits on."""
        if sys.platform == "win32":
            try:
                import ctypes

                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                                ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

                MONITOR_DEFAULTTONEAREST = 2
                hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
                monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    r = info.rcWork
                    return r.left, r.top, r.right - r.left, r.bottom - r.top
            except Exception:
                pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def _toggle_maximize(self):
        if self._is_maximized:
            if self._window_restore_geometry:
                self.geometry(self._window_restore_geometry)
                self._window_restore_geometry = None
            self._is_maximized = False
        else:
            self._window_restore_geometry = self.geometry()
            x, y, w, h = self._get_work_area()
            if sys.platform == "win32" and hasattr(self, "_hwnd"):
                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                user32.SetWindowPos(self._hwnd, 0, x, y, w, h,
                                     SWP_NOZORDER | SWP_NOACTIVATE)
            else:
                self.geometry(f"{w}x{h}+{x}+{y}")
            self._is_maximized = True
        self._refresh_maximize_icon()

    def _refresh_maximize_icon(self):
        pass  # replaced with a real redraw once the title bar is built

    def _build_title_bar(self):
        BAR_BG = "#0B1220"
        titlebar = tk.Frame(self, bg=BAR_BG, height=36)
        titlebar.pack(fill="x", side="top")
        titlebar.pack_propagate(False)

        drag_targets = [titlebar]

        icon = self._load_icon("Iconn.png", 18)
        if icon:
            icon_lbl = tk.Label(titlebar, image=icon, bg=BAR_BG)
            icon_lbl.pack(side="left", padx=(12, 8))
            drag_targets.append(icon_lbl)

        title = tk.Label(titlebar, text="Diagrama Studio", bg=BAR_BG, fg=UI["text"],
                         font=("Segoe UI", 10, "bold"))
        title.pack(side="left")
        drag_targets.append(title)

        self.project_title_label = tk.Label(titlebar, text="Untitled Project", bg=BAR_BG,
                                            fg=UI["muted"], font=("Segoe UI", 9))
        self.project_title_label.pack(side="left", padx=(10, 0))
        drag_targets.append(self.project_title_label)

        for w in drag_targets:
            w.bind("<ButtonPress-1>", self._start_window_drag)
            w.bind("<B1-Motion>", self._drag_window)
            w.bind("<Double-Button-1>", lambda e: self._toggle_maximize())

        def win_btn(kind, cmd, hover):
            w, h = 46, 36
            wrap = tk.Frame(titlebar, bg=BAR_BG, width=w, height=h, cursor="hand2")
            wrap.pack(side="right", fill="y")
            wrap.pack_propagate(False)
            cv = tk.Canvas(wrap, width=w, height=h, bg=BAR_BG,
                            highlightthickness=0, cursor="hand2")
            cv.pack(fill="both", expand=True)
            color = UI["text"]

            def draw():
                cv.delete("all")
                cx, cy, s = w // 2, h // 2, 5
                if kind == "minimize":
                    cv.create_line(cx - s, cy, cx + s, cy, fill=color, width=1)
                elif kind == "maximize":
                    if getattr(self, "_is_maximized", False):
                        off = 3
                        cv.create_rectangle(cx - s + off, cy - s, cx + s, cy + s - off,
                                            outline=color, width=1, fill=BAR_BG)
                        cv.create_rectangle(cx - s, cy - s + off, cx + s - off, cy + s,
                                            outline=color, width=1, fill=BAR_BG)
                    else:
                        cv.create_rectangle(cx - s, cy - s, cx + s, cy + s,
                                            outline=color, width=1)
                elif kind == "close":
                    cv.create_line(cx - s, cy - s, cx + s, cy + s, fill=color, width=1)
                    cv.create_line(cx - s, cy + s, cx + s, cy - s, fill=color, width=1)

            draw()
            if kind == "maximize":
                self._refresh_maximize_icon = draw

            def on_enter(_e):
                wrap.config(bg=hover); cv.config(bg=hover)
            def on_leave(_e):
                wrap.config(bg=BAR_BG); cv.config(bg=BAR_BG)
            def on_click(_e):
                cmd()

            for widget in (wrap, cv):
                widget.bind("<Enter>", on_enter)
                widget.bind("<Leave>", on_leave)
                widget.bind("<Button-1>", on_click)
            return wrap

        win_btn("close", self.destroy, UI["danger"])
        win_btn("maximize", self._toggle_maximize, UI["neutral"])
        win_btn("minimize", self._minimize_window, UI["neutral"])

    def _build_ui(self):

        if self.use_custom_chrome:
            self._build_title_bar()

        topbar = tk.Frame(self, bg=UI["hud_bg"], height=28)
        topbar.pack(fill="x", side="top"); topbar.pack_propagate(False)

        def menu_btn(text, items):
            b = tk.Button(topbar, text=text, bg=UI["hud_bg"], fg=UI["text"],
                         activebackground=UI["hud_bg_2"], activeforeground=UI["text"],
                         font=("Segoe UI",8), relief="flat", padx=8, pady=2,
                         cursor="hand2")
            b.pack(side="left")
            menu = tk.Menu(self, tearoff=0, bg=UI["panel_bg"], fg=UI["text"],
                          activebackground=UI["accent"], activeforeground="white",
                          disabledforeground=UI["dim"], relief="flat", bd=1)
            for item in items:
                if item is None:
                    menu.add_separator()
                    continue
                label, command, accelerator = item
                menu.add_command(label=label, command=command, accelerator=accelerator)
            b.configure(command=lambda btn=b, m=menu: m.post(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height()))

        menu_btn("File", [
            ("New Diagram", self._new_diagram, "Ctrl+N"),
            ("Open Project", self._open_project, ""),
            ("Save Project", self._save_project, ""),
            ("Save Project As", self._save_project_as, ""),
            None,
            ("Load SQL", self._load_file, "Ctrl+O"),
            ("Save SQL", self._save_file, "Ctrl+S"),
            None,
            ("Export PNG", lambda: self._export_image("png"), ""),
            ("Export JPEG", lambda: self._export_image("jpeg"), ""),
            ("Export PostScript", self._export_ps, ""),
            ("Export PDF", lambda: self._export_image("pdf"), ""),
        ])
        menu_btn("Edit", [
            ("Undo", self._undo, "Ctrl+Z"),
            ("Delete Selection", self._delete_active_selection, "Del"),
            None,
            ("Fit View", self._fit, "F"),
            ("Reset Layout", self._reset_layout, ""),
        ])
        menu_btn("Diagram", [
            ("Generate Diagram", self._generate, "Ctrl+G"),
            ("SQL to Builder", self._sql_to_builder, ""),
            ("Generate NoSQL Code", self._generate_nosql_code, ""),
            None,
            ("Add Table", self._diagram_add_table, ""),
            ("Add Relation", self._diagram_add_relation, ""),
        ])
        menu_btn("Window", [
            ("Layout Workspace", lambda: self.notebook.select(self.tab_diagram), ""),
            ("SQL Editor", lambda: self.notebook.select(self.tab_sql), ""),
            ("Visual Builder", lambda: self.notebook.select(self.tab_builder), ""),
            ("Settings", lambda: self.notebook.select(self.tab_settings), ""),
        ])
        menu_btn("Help", [
            ("Keyboard Shortcuts", self._show_shortcuts, ""),
            ("About Diagrama Studio", self._show_about, ""),
        ])


        bar2 = tk.Frame(self, bg=UI["hud_bg_2"], height=36)
        bar2.pack(fill="x", side="top"); bar2.pack_propagate(False)

        for name, target in [("Layout", "tab_diagram"), ("SQL", "tab_sql"), ("Builder", "tab_builder"), ("Settings", "tab_settings")]:
            tk.Button(bar2, text=name, bg=UI["hud_bg_2"], fg=UI["muted"],
                     activebackground=UI["panel_bg"], activeforeground=UI["text"],
                     font=("Segoe UI",8,"bold"), relief="flat", padx=10, pady=2,
                     cursor="hand2",
                     command=lambda t=target: self.notebook.select(getattr(self, t))).pack(side="left", padx=(2,0), pady=4)

        def lbl(text):
            tk.Label(bar2, text=text, bg=UI["hud_bg_2"], fg=UI["muted"],
                    font=("Segoe UI",8)).pack(side="left", padx=(16,2), pady=6)

        lbl("Mode:")
        for m in DIAGRAM_MODES:
            tk.Radiobutton(bar2, text=m, variable=self.diagram_mode, value=m,
                          bg=UI["hud_bg_2"], fg=UI["text"], selectcolor=UI["accent"],
                          activebackground=UI["hud_bg_2"], activeforeground=UI["text"],
                          font=("Segoe UI",8,"bold"),
                          command=self._on_mode_change).pack(side="left", padx=4)

        ttk.Separator(bar2, orient="vertical").pack(side="left", fill="y", padx=8, pady=4)
        lbl("Notation:")
        notation_cb = ttk.Combobox(bar2, textvariable=self.notation_var,
                                   values=list(NOTATION_FUNS.keys()), width=14, state="readonly")
        notation_cb.pack(side="left", padx=4, pady=6)
        notation_cb.bind("<<ComboboxSelected>>", lambda e: self.er_canvas.redraw())

        self.info_label = tk.Label(bar2, text="", bg=UI["hud_bg_2"], fg=UI["muted"], font=("Segoe UI",8))
        self.info_label.pack(side="right", padx=12)
        hint = tk.Label(bar2, text="Drag lines to add/move bend points | Right-click lines for route tools | Drag tables/labels | Scroll=zoom",
                       bg=UI["hud_bg_2"], fg=UI["dim"], font=("Segoe UI",8))
        hint.pack(side="right", padx=8)

        #Notebook 
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=UI["hud_bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=UI["panel_bg"], foreground=UI["muted"],
                        padding=[18,8], font=("Segoe UI",9,"bold"))
        style.map("TNotebook.Tab", background=[("selected",UI["accent"])],
                 foreground=[("selected",UI["text"])])

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_change)

        # SQL Editor
        self.tab_sql = tk.Frame(self.notebook, bg="#0D1526")
        self.notebook.add(self.tab_sql, text="  SQL Editor  ")
        self._build_sql_tab()

        # Diagram
        self.tab_diagram = tk.Frame(self.notebook, bg=TH["bg"])
        self.notebook.add(self.tab_diagram, text="  ER Diagram  ")
        self._build_diagram_toolbar()
        self.diagram_body = tk.Frame(self.tab_diagram, bg=UI["hud_bg"])
        self.diagram_body.pack(fill="both", expand=True)
        self._build_tool_rail()
        self.er_canvas = ERCanvas(self.diagram_body, self, highlightthickness=0)
        self.er_canvas.pack(side="left", fill="both", expand=True)
        self._build_inspector_panel()

        # Visual Builder
        self.tab_builder = tk.Frame(self.notebook, bg="#0F1729")
        self.notebook.add(self.tab_builder, text="  Visual Builder  ")
        self.visual_builder = VisualBuilder(self.tab_builder, self)
        self.visual_builder.pack(fill="both", expand=True)

        # Settings
        self.tab_settings = tk.Frame(self.notebook, bg="#0F1729")
        self.notebook.add(self.tab_settings, text="  Settings  ")
        SettingsPanel(self.tab_settings, self).pack(fill="both", expand=True)

        # The topbar above already has buttons that call notebook.select(...)
        # for each of these tabs, so the notebook's own clickable tab strip
        # is redundant. Hide the tab headers (not the tabs themselves) —
        # .select() still works from code, only the visible row of tab
        # buttons goes away.
        for tab_id in self.notebook.tabs():
            self.notebook.tab(tab_id, state="hidden")

        self.status = tk.Label(self, text="Ready. Load SQL or use the Visual Builder.", bg=UI["hud_bg_2"],
                              fg=UI["muted"], font=("Segoe UI",8), anchor="w")
        self.status.pack(fill="x", side="bottom")

    def _build_sql_tab(self):
        top = tk.Frame(self.tab_sql, bg=UI["panel_bg"])
        top.pack(fill="x", padx=0)

        btn_row = tk.Frame(top, bg=UI["panel_bg"]); btn_row.pack(fill="x", padx=10, pady=8)

        def sbtn(text, cmd, color="#2563EB"):
            tk.Button(btn_row, text=text, command=cmd, bg=color, fg="white",
                     font=("Segoe UI",9,"bold"), relief="flat", padx=10, pady=4,
                     cursor="hand2").pack(side="left", padx=3)

        sbtn("▶ Generate Diagram", self._generate)
        sbtn("📋 SQL → Builder",   self._sql_to_builder, "#059669")
        sbtn("NoSQL Code", self._generate_nosql_code, "#7C3AED")
        sbtn("📂 Load File",       self._load_file)
        sbtn("💾 Save File",       self._save_file)

        tk.Label(btn_row, text="NoSQL:", bg=UI["panel_bg"], fg=UI["muted"],
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(14, 3))
        ttk.Combobox(btn_row, textvariable=self.nosql_provider_var,
                     values=("MongoDB", "Mongoose", "Firebase"), width=11,
                     state="readonly").pack(side="left", padx=3)

        tk.Label(top, text="Paste or write your SQL CREATE TABLE statements here:",
                bg=UI["panel_bg"], fg=UI["muted"], font=("Segoe UI",8)).pack(anchor="w", padx=12, pady=(0,4))

        self.sql_editor = scrolledtext.ScrolledText(
            self.tab_sql, font=("Consolas",10),
            bg=UI["editor_bg"], fg=UI["text"],
            insertbackground="white",
            selectbackground="#2563EB",
            relief="flat", wrap="none", undo=True)
        self.sql_editor.pack(fill="both", expand=True, padx=0, pady=0)

    def _build_diagram_toolbar(self):
        bar = tk.Frame(self.tab_diagram, bg=UI["hud_bg_2"], height=46)
        bar.pack(fill="x", side="top")

        def dbtn(text, cmd, color=None):
            color = color or UI["accent"]
            tk.Button(bar, text=text, command=cmd, bg=color, fg="white",
                     font=("Segoe UI",9,"bold"), relief="flat", padx=10, pady=5,
                     cursor="hand2").pack(side="left", padx=4, pady=6)

        tk.Label(bar, text="Drag tables and labels. Right-click a relation to edit cardinality.",
                bg=UI["hud_bg_2"], fg=UI["muted"], font=("Segoe UI",8)).pack(side="right", padx=12)

    def _build_tool_rail(self):
        rail = tk.Frame(self.diagram_body, bg=UI["panel_bg"], width=56)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)

        def tool(icon_name, fallback, cmd, tip):
            icon = self._load_icon(icon_name, 28) if icon_name else None
            b = tk.Button(rail, text=fallback if not icon else "", image=icon, command=cmd,
                         bg=UI["panel_bg"], fg=UI["text"],
                         activebackground=UI["accent"], activeforeground=UI["text"],
                         relief="flat", font=("Segoe UI",10,"bold"), cursor="hand2",
                         compound="center")
            b.pack(fill="x", padx=6, pady=4, ipady=6)
            b.bind("<Enter>", lambda e: self.status.config(text=tip))
            return b

        tool("PlusTable.png", "+", self._diagram_add_table, "Add table")
        tool("RelationTable.png", "R", self._diagram_add_relation, "Add relation")
        tool("FitTable.png", "F", self._fit, "Fit canvas")
        tool(None, "+", lambda: self._zoom_canvas(0.12), "Zoom in")
        tool(None, "-", lambda: self._zoom_canvas(-0.12), "Zoom out")
        tool("DeleteTable.png", "Del", lambda: self._diagram_delete_selected(confirm=False), "Delete selected table or relation")

    def _build_inspector_panel(self):
        panel = tk.Frame(self.diagram_body, bg=UI["panel_bg"], width=240)
        panel.pack(side="right", fill="y")
        panel.pack_propagate(False)
        tk.Label(panel, text="Properties", bg=UI["panel_bg"], fg=UI["text"],
                font=("Segoe UI",11,"bold")).pack(anchor="w", padx=14, pady=(14,6))
        self.inspect_label = tk.Label(panel, text="Select a table or relation", bg=UI["panel_bg"],
                                     fg=UI["muted"], font=("Segoe UI",8), justify="left", anchor="nw")
        self.inspect_label.pack(fill="x", padx=14, pady=(0,12))

        for text, cmd in [
            ("Auto Layout", self._reset_layout),
            ("Reset Route", self._reset_selected_route),
            ("Generate SQL", self._builder_generate_sql_proxy),
        ]:
            tk.Button(panel, text=text, command=cmd, bg=UI["neutral"], fg=UI["text"],
                     relief="flat", font=("Segoe UI",9,"bold"), cursor="hand2").pack(fill="x", padx=14, pady=4)

    def _on_tab_change(self, event):
        tab = self.notebook.index(self.notebook.select())
        if tab == 2:
            self.visual_builder.on_show()

    def _bind_shortcuts(self):
        for seq in ("<Control-z>", "<Control-Z>"):
            self.bind_all(seq, lambda e: self._undo())
        for seq in ("<Control-n>", "<Control-N>"):
            self.bind_all(seq, lambda e: self._new_diagram())
        self.bind_all("<Delete>", lambda e: self._delete_active_selection())
        self.bind_all("<BackSpace>", lambda e: self._delete_active_selection())
        self.bind_all("<Control-s>", lambda e: self._save_file())
        self.bind_all("<Control-o>", lambda e: self._load_file())
        self.bind_all("<Control-g>", lambda e: self._generate())
        self.bind_all("<Control-plus>", lambda e: self._zoom_canvas(0.12))
        self.bind_all("<Control-minus>", lambda e: self._zoom_canvas(-0.12))
        self.bind_all("<F>", lambda e: self._fit())

    def _snapshot_state(self):
        return {
            "tables": copy.deepcopy(self.er_canvas.tables),
            "relations": copy.deepcopy(self.er_canvas.relations),
            "positions": copy.deepcopy(self.er_canvas.positions),
            "selected": self.er_canvas._selected,
            "selected_relation": self.er_canvas._selected_relation,
            "scale": self.er_canvas._scale,
        }

    def _push_undo(self):
        if not hasattr(self, "er_canvas"):
            return
        self._undo_stack.append(self._snapshot_state())
        if len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)

    def _restore_state(self, state):
        self.er_canvas.tables = copy.deepcopy(state["tables"])
        self.er_canvas.relations = copy.deepcopy(state["relations"])
        self.er_canvas.positions = copy.deepcopy(state["positions"])
        self.er_canvas._selected = state.get("selected")
        self.er_canvas._selected_relation = state.get("selected_relation")
        self.er_canvas._scale = state.get("scale", 1.0)
        self.er_canvas.redraw()
        self._refresh_counts()
        if hasattr(self, "visual_builder"):
            self.visual_builder.on_show()

    def _undo(self):
        if self.focus_get() == getattr(self, "sql_editor", None):
            try:
                self.sql_editor.edit_undo()
            except tk.TclError:
                pass
            return "break"
        if not self._undo_stack:
            self.status.config(text="Nothing to undo.")
            return "break"
        self._restore_state(self._undo_stack.pop())
        self.status.config(text="Undo.")
        return "break"

    def _delete_active_selection(self):
        if hasattr(self, "tab_diagram") and self.notebook.select() == str(self.tab_diagram):
            self._diagram_delete_selected(confirm=False)
            return "break"
        return None

    def _update_inspector(self):
        if not hasattr(self, "inspect_label"):
            return
        if self.er_canvas._selected:
            name = self.er_canvas._selected
            cols = self.er_canvas.tables.get(name, [])
            pk = sum(1 for c in cols if c.get("pk"))
            fk = sum(1 for c in cols if c.get("fk"))
            self.inspect_label.config(text=f"Table: {name}\nColumns: {len(cols)}\nPK: {pk}   FK: {fk}")
        elif self.er_canvas._selected_relation is not None and self.er_canvas._selected_relation < len(self.er_canvas.relations):
            rel = self.er_canvas.relations[self.er_canvas._selected_relation]
            self.inspect_label.config(text=f"Relation\n{rel['from']} -> {rel['to']}\n{rel.get('card_from')} -> {rel.get('card_to')}\nBend points: {len(rel.get('waypoints', []))}")
        else:
            self.inspect_label.config(text="Select a table or relation")

    def _reset_selected_route(self):
        idx = self.er_canvas._selected_relation
        if idx is None or idx >= len(self.er_canvas.relations):
            self.status.config(text="Select a relation first.")
            return
        self.er_canvas._reset_relation_route(idx)

    def _builder_generate_sql_proxy(self):
        self.visual_builder._generate_sql()

    def _project_payload(self):
        return {
            "tables": copy.deepcopy(self.er_canvas.tables),
            "relations": copy.deepcopy(self.er_canvas.relations),
            "positions": copy.deepcopy(self.er_canvas.positions),
            "diagram_mode": self.diagram_mode.get(),
            "notation": self.notation_var.get(),
            "sql": self.sql_editor.get("1.0", "end"),
        }

    def _load_project_payload(self, payload):
        self.diagram_mode.set(payload.get("diagram_mode", self.diagram_mode.get()))
        self.notation_var.set(payload.get("notation", self.notation_var.get()))
        self.sql_editor.delete("1.0", "end")
        self.sql_editor.insert("1.0", payload.get("sql", ""))
        self.er_canvas.load(
            payload.get("tables", {}),
            payload.get("relations", []),
            payload.get("positions", {}),
        )
        self._refresh_counts()
        self.notebook.select(self.tab_diagram)

    def _set_project_title(self, name=None):
        if not hasattr(self, "project_title_label"):
            return
        if not name and self.current_project_path:
            name = os.path.splitext(os.path.basename(self.current_project_path))[0]
        self.project_title_label.config(text=name or "Untitled Project")

    def _sync_project_store(self, project):
        try:
            existing = self.project_store.get(project.id)
            if existing:
                self.project_store.update(project.id, project.payload)
            else:
                self.project_store.create(project)
        except ProviderError:
            self.project_store.create(project)

    def _project_file_payload(self):
        name = "Untitled Project"
        if self.current_project_path:
            name = os.path.splitext(os.path.basename(self.current_project_path))[0]
        elif self.current_project_id:
            name = self.current_project_id
        project = make_project(name, "er", self._project_payload())
        if self.current_project_id:
            project.id = self.current_project_id
        return project

    def _write_project_file(self, path):
        project = self._project_file_payload()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(project.to_dict(), f, indent=2, ensure_ascii=False)
        self.current_project_id = project.id
        self.current_project_path = path
        self._sync_project_store(project)
        self._set_project_title(project.name)
        self.status.config(text=f"Project saved: {path}")

    def _save_project(self):
        if not self.current_project_path:
            return self._save_project_as()
        self._write_project_file(self.current_project_path)

    def _save_project_as(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".dgmproj",
            filetypes=[("Diagrama project", "*.dgmproj"), ("JSON project", "*.json"), ("All files", "*.*")],
        )
        if path:
            self._write_project_file(path)

    def _open_project(self):
        path = filedialog.askopenfilename(
            filetypes=[("Diagrama project", "*.dgmproj"), ("JSON project", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            project = DiagramProject.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            messagebox.showerror("Open Project", f"Could not open project:\n{exc}")
            return
        self.current_project_id = project.id
        self.current_project_path = path
        self._sync_project_store(project)
        self._load_project_payload(project.payload)
        self._set_project_title(project.name)
        self.status.config(text=f"Project loaded: {path}")

    def _new_diagram(self):
        if self.er_canvas.tables or self.sql_editor.get("1.0", "end").strip():
            if not messagebox.askyesno("New Diagram", "Clear the current diagram and SQL editor?"):
                return "break"
            self._push_undo()
        self.current_project_id = None
        self.current_project_path = None
        self.sql_editor.delete("1.0", "end")
        self.er_canvas.load({}, [], {})
        self._refresh_counts()
        if hasattr(self, "visual_builder"):
            self.visual_builder.on_show()
        self._set_project_title()
        self.notebook.select(self.tab_diagram)
        self.status.config(text="New diagram.")
        return "break"

    def _show_shortcuts(self):
        messagebox.showinfo(
            "Keyboard Shortcuts",
            "Ctrl+N  New diagram\n"
            "Ctrl+O  Load SQL\n"
            "Ctrl+S  Save SQL\n"
            "Ctrl+G  Generate diagram\n"
            "Ctrl+Z  Undo\n"
            "Delete  Delete selected table or relation\n"
            "F       Fit view\n"
            "Ctrl++  Zoom in\n"
            "Ctrl+-  Zoom out",
        )

    def _show_about(self):
        messagebox.showinfo(
            "About Diagrama Studio",
            "Diagrama Studio\n\nA desktop database diagram maker for SQL, ER, UML, Chen, and Crow's Foot notation.",
        )

    def _refresh_counts(self):
        nt = len(self.er_canvas.tables)
        nr = len(self.er_canvas.relations)
        self.info_label.config(text=f"{nt} tables · {nr} relations")

    def _zoom_canvas(self, delta):
        self.er_canvas._scale = max(0.15, min(3.5, self.er_canvas._scale + delta))
        self.er_canvas.redraw()

    def _sync_canvas_fk_flags(self):
        rel_refs = {(r["from"], r.get("from_col", "")): r["to"]
                    for r in self.er_canvas.relations if r.get("from_col")}
        for tname, cols in self.er_canvas.tables.items():
            for col in cols:
                ref = rel_refs.get((tname, col["name"]))
                col["fk"] = bool(ref)
                col["ref"] = ref
        if hasattr(self, "visual_builder"):
            self.visual_builder.on_show()

    def _diagram_add_table(self):
        popup = tk.Toplevel(self)
        popup.title("Add Table")
        popup.configure(bg=UI["panel_bg"])
        popup.resizable(False, False)
        popup.grab_set()
        popup.transient(self)
        popup.geometry("+%d+%d" % (self.winfo_rootx() + 120, self.winfo_rooty() + 120))

        header = tk.Frame(popup, bg=UI["hud_bg_2"])
        header.pack(fill="x")
        tk.Label(header, text="Create Table", bg=UI["hud_bg_2"], fg=UI["text"],
                font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(header, text="Add a new entity to the ER diagram.", bg=UI["hud_bg_2"], fg=UI["muted"],
                font=("Segoe UI", 8)).pack(anchor="w", padx=18, pady=(0, 12))

        body = tk.Frame(popup, bg=UI["panel_bg"])
        body.pack(fill="both", padx=18, pady=14)
        tk.Label(body, text="Table name", bg=UI["panel_bg"], fg=UI["muted"],
                font=("Segoe UI",9,"bold")).pack(anchor="w", pady=(0, 5))
        name_var = tk.StringVar()
        entry = tk.Entry(body, textvariable=name_var, font=("Segoe UI",10), width=34,
                         bg=UI["hud_bg"], fg=UI["text"], insertbackground="white", relief="flat")
        entry.pack(fill="x", ipady=6)
        add_id = tk.BooleanVar(value=True)
        tk.Checkbutton(body, text="Create ID primary key", variable=add_id,
                      bg=UI["panel_bg"], fg=UI["text"], selectcolor=UI["accent"],
                      activebackground=UI["panel_bg"], activeforeground="white",
                      font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 0))

        def ok():
            name = _norm_name(name_var.get())
            if not name:
                return
            if name in self.er_canvas.tables:
                messagebox.showerror("Duplicate", f"Table '{name}' already exists.")
                return
            self._push_undo()
            self.er_canvas.tables[name] = []
            if add_id.get():
                self.er_canvas.tables[name].append({"name": f"ID_{name}", "type": "INT", "pk": True, "fk": False, "ref": None})
            count = len(self.er_canvas.tables)
            self.er_canvas.positions[name] = [60 + (count % 4) * 220, 70 + (count // 4) * 180]
            self.er_canvas.redraw()
            self._refresh_counts()
            if hasattr(self, "visual_builder"):
                self.visual_builder.on_show()
            popup.destroy()

        footer = tk.Frame(popup, bg=UI["hud_bg_2"])
        footer.pack(fill="x")
        tk.Button(footer, text="Cancel", command=popup.destroy, bg=UI["neutral"], fg=UI["text"],
                 relief="flat", font=("Segoe UI",9,"bold"), padx=14, pady=5,
                 cursor="hand2").pack(side="right", padx=(4, 18), pady=12)
        tk.Button(footer, text="Create Table", command=ok, bg=UI["accent_2"], fg="white",
                 relief="flat", font=("Segoe UI",9,"bold"), padx=16, pady=5,
                 cursor="hand2").pack(side="right", padx=4, pady=12)
        entry.focus_set()
        popup.bind("<Return>", lambda e: ok())

    def _diagram_add_relation(self):
        names = list(self.er_canvas.tables.keys())
        if len(names) < 2:
            messagebox.showinfo("Relation", "Create at least two tables first.")
            return
        popup = tk.Toplevel(self)
        popup.title("Add Relation")
        popup.configure(bg="#1A2340")
        popup.resizable(False, False)
        popup.grab_set()

        vars_by_name = {key: tk.StringVar() for key in ("from", "from_col", "to", "to_col", "cf", "ct")}
        vars_by_name["cf"].set("1,1")
        vars_by_name["ct"].set("0,n")
        cbs = {}

        def row(label, var, values, r, width=24):
            tk.Label(popup, text=label, bg="#1A2340", fg="#90A8C8",
                    font=("Segoe UI",9)).grid(row=r, column=0, sticky="w", padx=12, pady=3)
            cb = ttk.Combobox(popup, textvariable=var, values=values, width=width, state="readonly")
            cb.grid(row=r, column=1, padx=12, pady=3)
            return cb

        cbs["from"] = row("From table", vars_by_name["from"], names, 0)
        cbs["from_col"] = row("From column", vars_by_name["from_col"], [], 1)
        cbs["to"] = row("To table", vars_by_name["to"], names, 2)
        cbs["to_col"] = row("To column", vars_by_name["to_col"], [], 3)
        row("From cardinality", vars_by_name["cf"], CARDINALITIES, 4, 8)
        row("To cardinality", vars_by_name["ct"], CARDINALITIES, 5, 8)

        def refresh_cols(event=None):
            for side in ("from", "to"):
                table = vars_by_name[side].get()
                values = [c["name"] for c in self.er_canvas.tables.get(table, [])]
                key = f"{side}_col"
                cbs[key]["values"] = values
                if values and vars_by_name[key].get() not in values:
                    vars_by_name[key].set(values[0])
                elif not values:
                    vars_by_name[key].set("")

        vars_by_name["from"].set(names[0])
        vars_by_name["to"].set(names[1])
        cbs["from"].bind("<<ComboboxSelected>>", refresh_cols)
        cbs["to"].bind("<<ComboboxSelected>>", refresh_cols)
        refresh_cols()

        def ok():
            rel = {
                "from": vars_by_name["from"].get(),
                "from_col": vars_by_name["from_col"].get(),
                "to": vars_by_name["to"].get(),
                "to_col": vars_by_name["to_col"].get(),
                "card_from": vars_by_name["cf"].get(),
                "card_to": vars_by_name["ct"].get(),
                "label_offset": [0, 0],
                "label_offset_from": [0, 0],
                "label_offset_to": [0, 0],
            }
            if not rel["from"] or not rel["to"]:
                return
            if relation_key(rel) in {relation_key(r) for r in self.er_canvas.relations}:
                messagebox.showinfo("Duplicate", "That relation already exists.")
                return
            self._push_undo()
            self.er_canvas.relations.append(rel)
            self._sync_canvas_fk_flags()
            self.er_canvas.redraw()
            self._refresh_counts()
            popup.destroy()

        tk.Button(popup, text="Create Relation", command=ok, bg="#2563EB", fg="white",
                 relief="flat", font=("Segoe UI",9,"bold"), padx=16, pady=5,
                 cursor="hand2").grid(row=6, column=0, columnspan=2, pady=12)

    def _diagram_delete_selected(self, confirm=True):
        name = self.er_canvas._selected
        rel_idx = self.er_canvas._selected_relation
        if rel_idx is not None and 0 <= rel_idx < len(self.er_canvas.relations):
            self._push_undo()
            self.er_canvas.relations.pop(rel_idx)
            self.er_canvas._selected_relation = None
            self._sync_canvas_fk_flags()
            self.er_canvas.redraw()
            self._refresh_counts()
            return
        if not name:
            if confirm:
                messagebox.showinfo("Delete", "Select a table or relation on the diagram first.")
            return
        if confirm and not messagebox.askyesno("Delete", f"Delete table '{name}'?"):
            return
        self._push_undo()
        self.er_canvas.tables.pop(name, None)
        self.er_canvas.positions.pop(name, None)
        self.er_canvas.relations = [r for r in self.er_canvas.relations if r["from"] != name and r["to"] != name]
        self.er_canvas._selected = None
        self._sync_canvas_fk_flags()
        self.er_canvas.redraw()
        self._refresh_counts()

    def _generate(self):
        sql = self.sql_editor.get("1.0", "end")
        try: parsed = parse_sql(sql)
        except Exception as e:
            messagebox.showerror("Parse Error", str(e)); return
        if not parsed["tables"]:
            self.status.config(text="⚠ No CREATE TABLE statements found."); return
        self._push_undo()
        w = self.er_canvas.winfo_width() or 1200
        h = self.er_canvas.winfo_height() or 700
        positions = auto_layout(parsed["tables"], parsed["relations"], w-40, h-40)
        self._parsed    = parsed
        self._positions = positions
        self.er_canvas.load(parsed["tables"], parsed["relations"], positions)
        nt = len(parsed["tables"]); nr = len(parsed["relations"])
        self._refresh_counts()
        self.info_label.config(text=f"{nt} tables · {nr} relations")
        self.status.config(text=f"✔ {nt} tables, {nr} relations. Drag labels to reposition. Right-click relations to edit.")
        self.after(80, self.er_canvas.fit_to_screen)
        self.notebook.select(self.tab_diagram)

    def _sql_to_builder(self):
        sql = self.sql_editor.get("1.0", "end")
        try: parsed = parse_sql(sql)
        except Exception as e:
            messagebox.showerror("Parse Error", str(e)); return
        if not parsed["tables"]:
            self.status.config(text="⚠ No tables found."); return
        self._push_undo()
        w = self.er_canvas.winfo_width() or 1200
        h = self.er_canvas.winfo_height() or 700
        positions = auto_layout(parsed["tables"], parsed["relations"], w-40, h-40)
        self.er_canvas.load(parsed["tables"], parsed["relations"], positions)
        self.notebook.select(self.tab_builder)
        self.visual_builder.on_show()
        self.status.config(text=f"✔ Loaded {len(parsed['tables'])} tables into Visual Builder.")

    def _generate_nosql_code(self):
        tables = self.er_canvas.tables
        relations = self.er_canvas.relations
        if not tables:
            try:
                parsed = parse_sql(self.sql_editor.get("1.0", "end"))
            except Exception as e:
                messagebox.showerror("NoSQL Generator", str(e))
                return
            tables = parsed.get("tables", {})
            relations = parsed.get("relations", [])
        if not tables:
            messagebox.showinfo("NoSQL Generator", "Create a diagram or paste SQL before generating NoSQL code.")
            return
        code = tables_to_nosql(tables, relations, self.nosql_provider_var.get())
        self.sql_editor.delete("1.0", "end")
        self.sql_editor.insert("1.0", code)
        self.notebook.select(self.tab_sql)
        self.status.config(text=f"NoSQL code generated for {self.nosql_provider_var.get()}.")

    def _fit(self):
        self.er_canvas.fit_to_screen()

    def _reset_layout(self):
        if not self.er_canvas.tables: return
        self._push_undo()
        w = self.er_canvas.winfo_width() or 1200
        h = self.er_canvas.winfo_height() or 700
        pos = auto_layout(self.er_canvas.tables, self.er_canvas.relations, w-40, h-40)
        self.er_canvas.load(self.er_canvas.tables, self.er_canvas.relations, pos)
        self.after(80, self.er_canvas.fit_to_screen)

    def _on_mode_change(self):
        self.er_canvas.redraw()

    def _load_file(self):
        path = filedialog.askopenfilename(filetypes=[("SQL files","*.sql"),("All","*.*")])
        if path:
            with open(path,"r",encoding="utf-8",errors="replace") as f:
                self.sql_editor.delete("1.0","end")
                self.sql_editor.insert("1.0", f.read())
            self._generate()

    def _save_file(self):
        path = filedialog.asksaveasfilename(defaultextension=".sql",
                                            filetypes=[("SQL files","*.sql"),("All","*.*")])
        if path:
            with open(path,"w",encoding="utf-8") as f:
                f.write(self.sql_editor.get("1.0","end"))
            self.status.config(text=f"💾 Saved: {path}")

    def _show_export_menu(self, btn):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="📄 Export as PNG",  command=lambda: self._export_image("png"))
        menu.add_command(label="📷 Export as JPEG", command=lambda: self._export_image("jpeg"))
        menu.add_command(label="📃 Export as PostScript", command=self._export_ps)
        if PIL_OK:
            menu.add_command(label="📕 Export as PDF", command=lambda: self._export_image("pdf"))
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height()
        menu.post(x, y)

    def _export_ps(self):
        path = filedialog.asksaveasfilename(defaultextension=".ps",
                                            filetypes=[("PostScript","*.ps"),("All","*.*")])
        if path:
            self.er_canvas.postscript(file=path, colormode="color")
            self.status.config(text=f"✔ Exported PostScript: {path}")

    def _export_image(self, fmt):
        if not PIL_OK:
            messagebox.showerror("Missing library",
                "Pillow (PIL) is required for image export.\n"
                "Install with:  pip install pillow"); return

        ext = {"png":"png","jpeg":"jpg","pdf":"pdf"}.get(fmt,"png")
        path = filedialog.asksaveasfilename(
            defaultextension=f".{ext}",
            filetypes=[(fmt.upper(), f"*.{ext}"), ("All","*.*")])
        if not path: return

        with tempfile.NamedTemporaryFile(suffix=".ps", delete=False) as tmp:
            ps_path = tmp.name
        try:
            self.er_canvas.postscript(file=ps_path, colormode="color")
            png_path = ps_path.replace(".ps", ".png")
            result = subprocess.run(
                ["gs", "-dNOPAUSE", "-dBATCH", "-sDEVICE=png16m", "-r150",
                 f"-sOutputFile={png_path}", ps_path],
                capture_output=True)
            if result.returncode == 0 and os.path.exists(png_path):
                img = Image.open(png_path)
                if fmt == "jpeg":
                    img = img.convert("RGB")
                    img.save(path, "JPEG", quality=95)
                elif fmt == "pdf":
                    img.convert("RGB").save(path, "PDF")
                else:
                    img.save(path, "PNG")
                self.status.config(text=f"✔ Exported {fmt.upper()}: {path}")
                os.unlink(png_path)
            else:
                import shutil
                shutil.copy(ps_path, path.replace(f".{ext}", ".ps"))
                messagebox.showinfo("Ghostscript not found",
                    "For PNG/JPEG/PDF export, install Ghostscript.\n"
                    "A .ps file was saved instead — you can open it in any PS viewer.")
                self.status.config(text="⚠ Ghostscript not found; saved as PostScript.")
        except FileNotFoundError:
            self._export_ps_fallback(path, ext)
        finally:
            if os.path.exists(ps_path): os.unlink(ps_path)

    def _export_ps_fallback(self, path, ext):
        ps_path = path.rsplit(".", 1)[0] + ".ps"
        self.er_canvas.postscript(file=ps_path, colormode="color")
        messagebox.showinfo("Ghostscript not installed",
            f"PNG/JPEG/PDF export requires Ghostscript.\n"
            f"Saved as PostScript: {ps_path}\n\n"
            "Install Ghostscript from https://ghostscript.com")
        self.status.config(text=f"⚠ Saved as PostScript (Ghostscript needed for images): {ps_path}")

    def _export_image(self, fmt):
        if not PIL_OK:
            messagebox.showerror("Missing library",
                "Pillow (PIL) is required for image export.\n"
                "Install with:  pip install pillow"); return

        ext = {"png":"png","jpeg":"jpg","pdf":"pdf"}.get(fmt,"png")
        path = filedialog.asksaveasfilename(
            defaultextension=f".{ext}",
            filetypes=[(fmt.upper(), f"*.{ext}"), ("All","*.*")])
        if not path: return

        try:
            self.update_idletasks()
            x1 = self.er_canvas.winfo_rootx()
            y1 = self.er_canvas.winfo_rooty()
            x2 = x1 + self.er_canvas.winfo_width()
            y2 = y1 + self.er_canvas.winfo_height()
            img = ImageGrab.grab(bbox=(x1, y1, x2, y2))
            if fmt == "jpeg":
                img.convert("RGB").save(path, "JPEG", quality=95)
            elif fmt == "pdf":
                img.convert("RGB").save(path, "PDF")
            else:
                img.save(path, "PNG")
            self.status.config(text=f"Exported {fmt.upper()}: {path}")
        except Exception as e:
            messagebox.showerror("Export failed", f"Could not export {fmt.upper()}:\n{e}")
            self.status.config(text=f"Export failed: {e}")

if __name__ == "__main__":
    App().mainloop() 