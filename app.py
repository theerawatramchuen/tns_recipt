#!/usr/bin/env python3
"""RUETHAI KASIKRANUN RECEIPT SYSTEM  -  pip install flask ; python app.py ; open http://127.0.0.1:5000
Prints through the normal Windows printer driver (browser Print). Needs create_receipt_tables.py + receipts.db."""
import os, re, sqlite3, unicodedata
from decimal import Decimal, ROUND_HALF_UP
from flask import Flask, request, redirect, render_template_string, flash, g, jsonify, Response
from jinja2 import DictLoader
from markupsafe import Markup, escape
from create_receipt_tables import SCHEMA, baht_text

TITLE = "RUETHAI KASIKRANUN RECEIPT SYSTEM"
DB = os.environ.get("RECEIPT_DB", "receipts.db")
PRINT_FONT = os.environ.get("PRINT_FONT", "Tahoma")        # any Thai-capable font installed in Windows
PRINT_PT = os.environ.get("PRINT_FONT_PT", "9")          # font size on paper; lower it if the form is wider than the paper
PRINT_BOLD = os.environ.get("PRINT_FONT_WEIGHT", "normal")  # "bold" strikes harder on multi-part paper; "normal" for plain
PRINT_LINE_PT = os.environ.get("PRINT_LINE_PT", "13")     # row pitch in pt - fixed, so a larger font does not move the rows
PRINT_PAPER_IN = os.environ.get("PRINT_PAPER_IN", "9.5")   # paper width in inches (checks the form fits; browsers shrink anything wider)
FORM = os.environ.get("RECEIPT_FORM", "receipt_form.txt")   # editable print layout (plain text)
TABLES = {"customer": "Customers", "goods": "Goods", "discount": "Discounts"}

app = Flask(__name__)
app.secret_key = "receipt-local"

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db
@app.teardown_appcontext
def close(_):
    c = g.pop("db", None)
    if c: c.close()
def D(x): return Decimal(str(x or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)

# ---------- generic CRUD for customer / goods / discount ----------
def cols(t):
    return [c for c in db().execute(f"PRAGMA table_info({t})") if c["name"] != "id"]

@app.route("/t/<t>")
def tlist(t):
    if t not in TABLES: return "no such table", 404
    q = request.args.get("q", "")
    cs = cols(t); txt = [c["name"] for c in cs if c["type"] == "TEXT"]
    where = " OR ".join(f"{c} LIKE :q" for c in txt)
    rows = db().execute(f"SELECT * FROM {t} WHERE {where} ORDER BY code, id", {"q": f"%{q}%"}).fetchall() if q else \
           db().execute(f"SELECT * FROM {t} ORDER BY code, id").fetchall()
    return render_template_string(T_LIST, t=t, cs=cs, rows=rows, q=q)

@app.route("/t/<t>/new", methods=["GET", "POST"])
@app.route("/t/<t>/<int:i>", methods=["GET", "POST"])
def tedit(t, i=None):
    if t not in TABLES: return "no such table", 404
    cs = cols(t)
    row = db().execute(f"SELECT * FROM {t} WHERE id=?", (i,)).fetchone() if i else None
    if request.method == "POST":
        vals = {c["name"]: (float(request.form[c["name"]] or 0) if c["type"] in ("REAL", "NUMERIC")
                            else request.form[c["name"]].strip()) for c in cs}
        n = list(vals)
        if i: db().execute(f"UPDATE {t} SET {','.join(k+'=?' for k in n)} WHERE id=?", [*vals.values(), i])
        else: db().execute(f"INSERT INTO {t}({','.join(n)}) VALUES({','.join('?'*len(n))})", list(vals.values()))
        db().commit(); flash("Saved"); return redirect(f"/t/{t}")
    return render_template_string(T_FORM, t=t, cs=cs, row=row)

@app.post("/t/<t>/<int:i>/delete")
def tdel(t, i):
    if t not in TABLES: return "no such table", 404
    try: db().execute(f"DELETE FROM {t} WHERE id=?", (i,)); db().commit(); flash("Deleted")
    except sqlite3.IntegrityError: flash("Cannot delete: this row is used by a receipt", "err")
    return redirect(f"/t/{t}")

# ---------- receipts ----------
@app.route("/")
@app.route("/receipts")
def receipts():
    q = request.args.get("q", "").strip()
    rows = db().execute("""SELECT r.*, c.code ccode, c.name cname, c.branch_a FROM receipt r
        JOIN customer c ON c.id=r.customer_id
        WHERE :q='' OR r.receipt_no LIKE :l OR r.order_no LIKE :l OR r.receipt_date LIKE :l OR c.code LIKE :l
           OR c.name LIKE :l OR c.branch_a LIKE :l OR CAST(r.total AS TEXT) LIKE :l
        ORDER BY receipt_date DESC, receipt_no DESC""", {"q": q, "l": f"%{q}%"}).fetchall()
    return render_template_string(T_RLIST, rows=rows, q=q)

@app.route("/receipt/new")
@app.route("/receipt/<int:i>")
def receipt_form(i=None):
    c = db(); r, lines = None, []
    if i:
        r = c.execute("SELECT * FROM receipt WHERE id=?", (i,)).fetchone()
        lines = [dict(x) for x in c.execute("SELECT * FROM receipt_detail WHERE receipt_id=? ORDER BY line_no", (i,))]
    nxt = (c.execute("SELECT MAX(CAST(receipt_no AS INTEGER)) m FROM receipt").fetchone()["m"] or 0) + 1
    return render_template_string(T_RFORM, r=r, lines=lines, nxt=f"{nxt:07d}",
        customers=[dict(x) for x in c.execute("SELECT id,code,name,branch_a FROM customer ORDER BY code")],
        goods=[dict(x) for x in c.execute("SELECT id,code,name,unit,price FROM goods ORDER BY code")],
        discounts=[dict(x) for x in c.execute("SELECT id,code,method,factor FROM discount ORDER BY code")])

@app.post("/api/receipt")
def save_receipt():
    j = request.get_json(); c = db(); sub = Decimal(0); disc = Decimal(0); L = []
    for n, l in enumerate(j["lines"], 1):
        amt = D(Decimal(str(l["quantity"])) * D(l["unit_price"]))
        dr = c.execute("SELECT * FROM discount WHERE id=?", (l["discount_id"],)).fetchone() if l.get("discount_id") else None
        net = D(amt * Decimal(str(dr["factor"]))) if dr else amt     # net = amount x discount.factor
        sub += amt; disc += amt - net
        L.append((n, l["goods_id"], l["goods_name"], l["unit"], l["quantity"], float(D(l["unit_price"])), float(amt),
                  dr["id"] if dr else None, dr["code"] if dr else "", dr["method"] if dr else "",
                  float(amt - net), float(net)))
    if not L: return jsonify(error="Add at least one line"), 400
    tot = sub - disc
    try:
        with c:
            h = (j["receipt_no"], j["order_no"], j["receipt_date"], j["customer_id"],
                 float(sub), float(disc), float(tot), baht_text(tot))
            if j.get("id"):
                rid = j["id"]
                c.execute("UPDATE receipt SET receipt_no=?,order_no=?,receipt_date=?,customer_id=?,subtotal=?,"
                          "discount_total=?,total=?,total_text=? WHERE id=?", (*h, rid))
                c.execute("DELETE FROM receipt_detail WHERE receipt_id=?", (rid,))
            else:
                rid = c.execute("INSERT INTO receipt(receipt_no,order_no,receipt_date,customer_id,subtotal,"
                                "discount_total,total,total_text) VALUES(?,?,?,?,?,?,?,?)", h).lastrowid
            c.executemany("INSERT INTO receipt_detail(receipt_id,line_no,goods_id,goods_name,unit,quantity,"
                          "unit_price,amount,discount_id,discount_code,discount_method,discount_amount,net_amount) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          [(rid, *x) for x in L])
    except sqlite3.IntegrityError as e:
        return jsonify(error="Receipt number already exists" if "receipt_no" in str(e) else str(e)), 400
    return jsonify(id=rid)

@app.post("/receipt/<int:i>/delete")
def receipt_del(i):
    db().execute("DELETE FROM receipt WHERE id=?", (i,)); db().commit(); flash("Deleted"); return redirect("/receipts")

# ---------- printing: layout comes from the plain-text file receipt_form.txt ----------
def dw(s): return sum(0 if unicodedata.category(ch) == "Mn" else 1 for ch in s)  # Thai marks overstrike
def pad(s, w, a="l"):
    s = str(s); k = max(w - dw(s), 0)
    return s + " " * k if a == "l" else " " * k + s if a == "r" else " " * (k // 2) + s + " " * (k - k // 2)
def m(x): return f"{float(x):,.2f}"
TOK = re.compile(r"\{([<>^]?)([a-z_]+) *\}")     # {name   } = field as wide as the token; {>name} right, {^name} centre
ALIGN = {"": "left", "<": "left", ">": "right", "^": "center"}

def line_html(ln, v, W):
    """One template line -> absolutely positioned boxes, so columns stay aligned with proportional Thai fonts."""
    segs, col, last = [], 0, 0
    def static(txt, c0):
        for s in re.finditer(r"\S+(?: \S+)*", txt):
            rule = len(s[0]) > 3 and re.fullmatch(r"([-=_.*])\1*", s[0])     # ruled line: fill exactly its width
            segs.append((c0 + dw(txt[:s.start()]), len(s[0]) if rule else None, "left", s[0] * 2 if rule else s[0]))
    for t in TOK.finditer(ln):
        pre = ln[last:t.start()]; static(pre, col); col += dw(pre)
        segs.append((col, len(t[0]), ALIGN[t[1]], str(v.get(t[2], "")))); col += len(t[0]); last = t.end()
    static(ln[last:], col)
    if len(segs) == 1 and segs[0][1] is None and segs[0][0] > 0:       # text centred with spaces -> centre on the page
        lead, trail = segs[0][0], W - segs[0][0] - dw(segs[0][3])
        if abs(lead - trail) <= 2: segs = [(0, W, "center", segs[0][3])]
    h = "".join(f'<span style="left:{c}ch;' + (f'width:{w}ch;text-align:{a}"' if w else '"') + f'>{escape(t)}</span>'
                for c, w, a, t in segs)
    return f'<div class="l">{h}</div>'

def default_form(W=110):
    tk = lambda n, w, a="": "{" + (a + n).ljust(w - 2) + "}"
    w = [5, W - 5 - 14 - 11 - 13 - 11 - 13, 14, 11, 13, 11, 13]
    names = ["no", "name", "qty", "price", "amount", "discount", "net"]
    al = ["", ""] + [">"] * 5; h = W // 2 + 4
    hd = ["ลำดับ", "รายการ", "จำนวนสั่ง", "หน่วยละ", "จำนวนเงิน", "ส่วนลด", "จำนวนเงิน"]
    o = ["## RECEIPT FORM - edit with any text editor (save as UTF-8). Lines starting with ## are not printed.",
         "## {name   } = data field; the token's own width is the field width. {>name} = right-align, {^name} = centre.",
         "## Header fields: receipt_no order_no date cust_name cust_addr_a cust_addr_b cust_addr_c cust_branch_a",
         "##   cust_branch_b cust_other_a cust_other_b subtotal discount_total total total_text",
         "## Lines between #ITEMS and #END repeat for every item: no name qty price amount discount net",
         "## (discount prints the discount method only, e.g. 10% , 2%). Anything else is printed as typed.",
         "## #ITEMS 10 = item area is always 10 items tall, so the lines below it stay on a FIXED row (extra items are not printed).",
         "## #ROW 40 = the next line is printed on row 40 of the page (blank rows are added before it).",
         pad("ใบส่งของ / ใบเสร็จรับเงิน", W, "c"), pad("DELIVERY ORDER / RECEIPT", W, "c"),
         pad("เลขที่ใบสั่งสินค้า", 28) + pad("ฤทัย กสิกรานันท์", W - 50, "c") + "เลขที่ " + tk("receipt_no", 15, ">"),
         tk("order_no", 28) + pad("RUETHAI KASIKRANUN", W - 50, "c") + "วันที่ " + tk("date", 15, ">"),
         pad("511/364 ซอย 37 ถ.จรัญสนิทวงศ์ แขวงบางขุนศรี เขตบางกอกน้อย กรุงเทพฯ 10700", W, "c"),
         pad("โทร 02-4121787 โทรสาร 02-4120186 เลขประจำตัวผู้เสียภาษี 3102001314527", W, "c"),
         pad("ผู้จำหน่าย ข้าวหอมมะลิแปดริ้วแท้ ข้าวซ้อมมือ ข้าวหอมผสมมันปู ข้าวมันปูอนามัย และข้าวเหนียวอย่างดี", W, "c"), "",
         pad("นามผู้ซื้อ", h) + "สถานที่ส่ง"]
    for a, b in zip(["cust_name", "cust_addr_a", "cust_addr_b", "cust_addr_c"],
                    ["cust_branch_a", "cust_branch_b", "cust_other_a", "cust_other_b"]):
        o.append("   " + tk(a, h - 3) + tk(b, W - h))
    o += ["", "".join(pad(t, n, "l" if k < 2 else "r") for k, (t, n) in enumerate(zip(hd, w))), "-" * W, "#ITEMS 10",
          "".join(tk(n, x, a) for n, x, a in zip(names, w, al)), "#END", "-" * W,
          tk("total_text", W - 14) + tk("total", 14, ">")]
    return "\n".join(o) + "\n"

def load_form():
    if not os.path.exists(FORM):
        open(FORM, "w", encoding="utf-8").write(default_form())
    return open(FORM, encoding="utf-8-sig").read().splitlines()

def receipt_html(i):
    c = db(); r = c.execute("SELECT * FROM receipt WHERE id=?", (i,)).fetchone()
    cu = c.execute("SELECT * FROM customer WHERE id=?", (r["customer_id"],)).fetchone()
    ls = c.execute("SELECT * FROM receipt_detail WHERE receipt_id=? ORDER BY line_no", (i,)).fetchall()
    y, mo, d = r["receipt_date"].split("-")
    v = {"receipt_no": r["receipt_no"], "order_no": r["order_no"], "date": f"{d}/{mo}/{y}",
         "subtotal": m(r["subtotal"]), "discount_total": m(r["discount_total"]), "total": m(r["total"]),
         "total_text": r["total_text"], **{"cust_" + k: cu[k] for k in
         ("name", "addr_a", "addr_b", "addr_c", "branch_a", "branch_b", "other_a", "other_b")}}
    rows = [{"no": l["line_no"], "name": l["goods_name"], "qty": f"{l['quantity']:g} {l['unit']}",
             "price": m(l["unit_price"]), "amount": m(l["amount"]), "discount": l["discount_method"],
             "net": m(l["net_amount"])} for l in ls]
    tpl = [l for l in load_form() if not l.startswith("##")]
    W = max([dw(l) for l in tpl if not l.strip().startswith("#")] + [1])
    out, blk, mode, cap, warn = [], [], 0, None, ""
    blank = '<div class="l"></div>'
    for ln in tpl:
        s = ln.strip().split()
        if s and s[0] == "#ITEMS":
            mode, blk, cap = 1, [], (int(s[1]) if len(s) > 1 else None)
        elif s and s[0] == "#END":
            use = rows[:cap] if cap else rows
            if cap and len(rows) > cap: warn = f"This receipt has {len(rows)} items but the form prints only {cap} (#ITEMS {cap} in receipt_form.txt)."
            body = [line_html(b, rw, W) for rw in use for b in blk]
            out += body + [blank] * ((cap or 0) * len(blk) - len(body)); mode = 0
        elif s and s[0] == "#ROW":
            out += [blank] * (int(s[1]) - 1 - len(out))          # jump so the next line is on this row
        elif mode: blk.append(ln)
        else: out.append(line_html(ln, v, W))
    return W, Markup("".join(out)), warn

@app.route("/form/blank")
def form_blank():
    load_form()
    return Response(open(FORM, "rb").read(), mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=receipt_form.txt"})

@app.route("/receipt/<int:i>/print")
def receipt_print(i):
    W, body, warn = receipt_html(i)
    if warn: flash(warn, "err")
    pt = request.args.get("pt", PRINT_PT)                     # /print?pt=9.5&bold=0 overrides without restarting
    wt = "bold" if request.args.get("bold", "1" if PRINT_BOLD == "bold" else "0") == "1" else "normal"
    paper = request.args.get("paper", PRINT_PAPER_IN)
    try: float(pt)
    except ValueError: pt = PRINT_PT
    try: float(paper)
    except ValueError: paper = PRINT_PAPER_IN
    return render_template_string(T_PRINT, i=i, W=W, body=body, font=PRINT_FONT, pt=pt, wt=wt, lh=PRINT_LINE_PT, paper=paper)

# ---------- dashboard / summary reports ----------
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
DASH_W = "(:lo IS NULL OR r.receipt_date>=:lo) AND (:hi IS NULL OR r.receipt_date<=:hi)"

def fmt0(x): return f"{float(x):,.0f}"
def fmtq(x): return f"{float(x):,.0f}" if float(x) == int(float(x)) else f"{float(x):,.2f}"
def compact(n):
    n = float(n)
    if n >= 1e6: return f"{n/1e6:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1e3: return f"{n/1e3:.1f}".rstrip("0").rstrip(".") + "K"
    return f"{n:g}"
def dlabel(d): return f"{d.day} {MONTHS[d.month-1]} {d.year}"

def dash_range():
    """Date filter from the query string -> (lo, hi, preset, label); lo/hi are ISO strings or None (no limit)."""
    from datetime import date, timedelta
    t = date.today(); p = request.args.get("p", "all")
    def pd(s):
        try: return date.fromisoformat(s)
        except ValueError: return None
    f, to = request.args.get("from", ""), request.args.get("to", "")
    if f or to:
        lo, hi, p = pd(f), pd(to), "custom"
        label = (dlabel(lo) if lo else "Start") + " – " + (dlabel(hi) if hi else "today")
    elif p == "month": lo, hi, label = t.replace(day=1), t, "Month to date"
    elif p == "30": lo, hi, label = t - timedelta(days=29), t, "Last 30 days"
    elif p == "year": lo, hi, label = t.replace(month=1, day=1), t, "Year to date"
    else: p, lo, hi, label = "all", None, None, "All time"
    return (lo.isoformat() if lo else None, hi.isoformat() if hi else None, p, label)

def dash_reports(lo, hi):
    from datetime import date, timedelta
    c = db(); a = {"lo": lo, "hi": hi}; W = DASH_W
    kpi = c.execute(f"""SELECT COUNT(*) n, COALESCE(SUM(total),0) net, COALESCE(SUM(subtotal),0) gross,
        COALESCE(SUM(discount_total),0) disc, COUNT(DISTINCT customer_id) cust, MIN(receipt_date) d0, MAX(receipt_date) d1
        FROM receipt r WHERE {W}""", a).fetchone()
    units = c.execute(f"SELECT COALESCE(SUM(d.quantity),0) FROM receipt_detail d JOIN receipt r ON r.id=d.receipt_id WHERE {W}", a).fetchone()[0]
    days = {r["d"]: (r["n"], r["v"]) for r in c.execute(
        f"SELECT receipt_date d, COUNT(*) n, SUM(total) v FROM receipt r WHERE {W} GROUP BY receipt_date", a)}
    goods = c.execute(f"""SELECT g.code, MAX(d.goods_name) name, MAX(d.unit) unit, SUM(d.quantity) qty,
        COUNT(DISTINCT d.receipt_id) n, SUM(d.net_amount) net FROM receipt_detail d
        JOIN receipt r ON r.id=d.receipt_id JOIN goods g ON g.id=d.goods_id WHERE {W}
        GROUP BY d.goods_id ORDER BY net DESC, g.code""", a).fetchall()
    custs = c.execute(f"""SELECT cu.code, cu.name, cu.branch_a branch, COUNT(*) n, SUM(r.total) net FROM receipt r
        JOIN customer cu ON cu.id=r.customer_id WHERE {W} GROUP BY r.customer_id ORDER BY net DESC, cu.code""", a).fetchall()
    discs = c.execute(f"""SELECT d.discount_method method, COUNT(*) n, SUM(d.amount) gross, SUM(d.discount_amount) disc
        FROM receipt_detail d JOIN receipt r ON r.id=d.receipt_id WHERE {W} AND d.discount_method<>''
        GROUP BY d.discount_method ORDER BY disc DESC""", a).fetchall()
    # sales per day (range up to ~2 months) or per month, with empty periods kept so the time axis is honest
    series = []
    if kpi["n"]:
        d0, d1 = date.fromisoformat(kpi["d0"]), date.fromisoformat(kpi["d1"])
        if (d1 - d0).days <= 62:
            for k in range((d1 - d0).days + 1):
                dt = d0 + timedelta(days=k); n, v = days.get(dt.isoformat(), (0, 0))
                series.append(dict(key=dt.isoformat(), label=dlabel(dt), tick=f"{dt.day} {MONTHS[dt.month-1]}", n=n, v=v or 0))
        else:
            y, mo = d0.year, d0.month
            while (y, mo) <= (d1.year, d1.month):
                pre = f"{y}-{mo:02d}"; hit = [x for k2, x in days.items() if k2.startswith(pre)]
                series.append(dict(key=pre, label=f"{MONTHS[mo-1]} {y}", tick=f"{MONTHS[mo-1]} {str(y)[2:]}",
                                   n=sum(x[0] for x in hit), v=sum(x[1] for x in hit)))
                y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return dict(kpi=kpi, units=units, series=series, goods=goods, custs=custs, discs=discs)

def nice_axis(vmax):
    import math
    raw = max(vmax, 1) / 4; mag = 10 ** math.floor(math.log10(raw))
    step = next(k * mag for k in (1, 2, 2.5, 5, 10) if raw <= k * mag)
    return step * 4, [step * i for i in range(5)]

def hbars(rows, name, sub, value, n=8):
    top = list(rows)[:n]; mx = max([float(value(r)) for r in top] + [1])
    return [dict(name=name(r), sub=sub(r), v=fmt0(value(r)), pct=round(float(value(r)) / mx * 82, 1)) for r in top]

@app.route("/dashboard")
def dashboard():
    lo, hi, p, label = dash_range(); R = dash_reports(lo, hi); k = R["kpi"]; qs = request.query_string.decode()
    ctx = dict(p=p, label=label, lo=lo or "", hi=hi or "", qs=qs, n=k["n"])
    if k["n"]:
        top, ticks = nice_axis(max(s["v"] for s in R["series"]))
        nb = len(R["series"]); step = -(-nb // 12); mx = max(range(nb), key=lambda i: R["series"][i]["v"])
        bars = [dict(s, pct=round(s["v"] / top * 100, 1), val=m(s["v"]), sub=f"{s['n']} receipt{'s' if s['n'] != 1 else ''}",
                     show=(i % step == 0), mx=(i == mx and s["v"] > 0), short=fmt0(s["v"]),
                     side="l" if i < nb * .12 else "r" if i > nb * .88 else "") for i, s in enumerate(R["series"])]
        ctx.update(bars=bars, ticks=[dict(pct=t / top * 100, label=compact(t)) for t in ticks],
                   per="day" if "-" in R["series"][0]["key"][5:] else "month",
                   hero=m(k["net"]), avg=m(k["net"] / k["n"]), disc=m(k["disc"]), gross=m(k["gross"]),
                   dpct=f"{k['disc'] / k['gross'] * 100:.1f}" if k["gross"] else "0", units=fmtq(R["units"]), cust=k["cust"],
                   goods=hbars(R["goods"], lambda r: f"{r['code']} · {r['name']}", lambda r: f"{fmtq(r['qty'])} {r['unit']} in {r['n']} receipts", lambda r: r["net"]),
                   custs=hbars(R["custs"], lambda r: f"{r['code']} · {r['branch'] or r['name']}", lambda r: f"{r['name']} · {r['n']} receipts", lambda r: r["net"]),
                   goods_t=R["goods"][:50], custs_t=R["custs"][:50], discs=R["discs"], ngoods=len(R["goods"]), ncust=len(R["custs"]))
    return render_template_string(T_DASH, m=m, fmtq=fmtq, **ctx)

@app.route("/dashboard.csv")
def dashboard_csv():
    import csv, io
    lo, hi, _, _ = dash_range(); R = dash_reports(lo, hi); rep = request.args.get("report", "goods")
    sets = {"sales": (["period", "receipts", "net_sales"], [(s["key"], s["n"], s["v"]) for s in R["series"]]),
            "goods": (["code", "name", "unit", "quantity", "receipts", "net_sales"], [tuple(r) for r in R["goods"]]),
            "customers": (["code", "name", "branch", "receipts", "net_sales"], [tuple(r) for r in R["custs"]]),
            "discounts": (["discount", "lines", "gross_amount", "discount_amount"], [tuple(r) for r in R["discs"]])}
    if rep not in sets: return "unknown report", 404
    head, rows = sets[rep]; out = io.StringIO(); w = csv.writer(out); w.writerow(head); w.writerows(rows)
    return Response("﻿" + out.getvalue(), mimetype="text/csv; charset=utf-8",      # BOM so Excel reads Thai
                    headers={"Content-Disposition": f"attachment; filename={rep}_report.csv"})

# ---------- templates ----------
app.jinja_loader = DictLoader({"base": """<!doctype html><html lang="th"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{{ title }}</title><style>
:root{color-scheme:light dark;--ink:#1d2a24;--mut:#66736c;--line:#d5dcd7;--bg:#f6f8f6;--g:#1f5c4a;--r:#a4372b}
@media(prefers-color-scheme:dark){:root{--ink:#e6ece8;--mut:#9aa8a0;--line:#33413a;--bg:#161c19;--g:#6fc2a5;--r:#ef8f84}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Sarabun,Tahoma,"Noto Sans Thai",sans-serif}
header{background:var(--g);color:#fff;padding:.7rem 1.2rem;display:flex;flex-wrap:wrap;gap:.4rem 1.6rem;align-items:baseline}
header b{font-size:1.1rem;letter-spacing:.04em}header a{color:#fff;text-decoration:none;opacity:.85;padding:.1rem 0}header a.on,header a:hover{opacity:1;border-bottom:2px solid #fff}
main{padding:1.2rem;max-width:1200px;margin:auto}h1{font-size:1.3rem;margin:.2rem 0 1rem}
table{border-collapse:collapse;width:100%;background:Canvas}th,td{padding:.4rem .6rem;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-weight:600;color:var(--mut)}td.n,th.n{text-align:right}.sc{overflow-x:auto}
input,select,textarea,button,.btn{font:inherit;padding:.35rem .6rem;border:1px solid var(--line);border-radius:4px;background:Canvas;color:var(--ink)}
button,.btn{background:var(--g);color:#fff;border-color:var(--g);cursor:pointer;text-decoration:none;display:inline-block}
.b2{background:transparent;color:var(--g)}.dg{background:transparent;color:var(--r);border-color:var(--r)}
:focus-visible{outline:2px solid var(--g);outline-offset:1px}.bar{display:flex;gap:.6rem;margin-bottom:1rem;flex-wrap:wrap}
.f{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.8rem;margin-bottom:1rem}.f label{display:block;color:var(--mut);font-size:.85rem}.f input,.f select{width:100%}
.msg{padding:.5rem .8rem;border-left:4px solid var(--g);background:Canvas;margin-bottom:1rem}.msg.err{border-color:var(--r);color:var(--r)}
td input{width:100%;min-width:60px}form.i{display:inline}
</style><header><b>{{ title }}</b><nav style="display:flex;gap:1.2rem;flex-wrap:wrap">
<a href="/dashboard" class="{{ 'on' if nav=='d' }}">Dashboard</a>
<a href="/receipts" class="{{ 'on' if nav=='r' }}">Receipts</a>
{% for k,v in tables.items() %}<a href="/t/{{k}}" class="{{ 'on' if nav==k }}">{{v}}</a>{% endfor %}</nav></header><main>
{% for c,mm in get_flashed_messages(with_categories=true) %}<div class="msg {{c}}">{{mm}}</div>{% endfor %}
{% block body %}{% endblock %}</main></html>""",
"list": "", })
app.jinja_env.globals.update(title=TITLE, tables=TABLES)
def P(body, nav=""):
    return "{% extends 'base' %}{% set nav='" + nav + "' %}{% block body %}" + body + "{% endblock %}"
DEL = """<form class="i" method="post" action="{a}" onsubmit="return confirm('Delete this row?')"><button class="dg">Delete</button></form>"""

T_LIST = "{% extends 'base' %}{% set nav=t %}{% block body %}" + """
<h1>{{ tables[t] }}</h1><div class="bar"><a class="btn" href="/t/{{t}}/new">Add</a>
<form method="get"><input name="q" value="{{q}}" placeholder="Search"> <button class="b2">Search</button></form></div>
<div class="sc"><table><tr>{% for c in cs %}<th>{{c['name']}}</th>{% endfor %}<th></th></tr>
{% for r in rows %}<tr>{% for c in cs %}<td class="{{'n' if c['type'] in ('REAL','NUMERIC')}}">{{r[c['name']]}}</td>{% endfor %}
<td style="white-space:nowrap"><a class="btn b2" href="/t/{{t}}/{{r['id']}}">Edit</a>
<form class="i" method="post" action="/t/{{t}}/{{r['id']}}/delete" onsubmit="return confirm('Delete this row?')"><button class="dg">Delete</button></form></td></tr>
{% else %}<tr><td colspan="99">No rows yet. Select Add to create one.</td></tr>{% endfor %}</table></div>{% endblock %}"""

T_FORM = "{% extends 'base' %}{% set nav=t %}{% block body %}" + """
<h1>{{ 'Edit' if row else 'Add' }} · {{ tables[t] }}</h1><form method="post"><div class="f">
{% for c in cs %}<div><label>{{c['name']}}</label><input name="{{c['name']}}" value="{{row[c['name']] if row else ''}}"
 {% if c['type'] in ('REAL','NUMERIC') %}type="number" step="any"{% endif %} {{'required' if c['name'] in ('code','name','goods')}}></div>{% endfor %}
</div><button>Save</button> <a class="btn b2" href="/t/{{t}}">Cancel</a></form>{% endblock %}"""

T_RLIST = "{% extends 'base' %}{% set nav='r' %}{% block body %}" + """
<h1>Receipts</h1><div class="bar"><a class="btn" href="/receipt/new">New receipt</a><form method="get"><input name="q" value="{{q}}" placeholder="Search no., date, customer, total"> <button class="b2">Search</button></form>
<a class="btn b2" href="/form/blank" style="margin-left:auto">Blank form (.txt)</a></div>
<div class="sc"><table><tr><th>No.</th><th>Date</th><th>Order</th><th>Customer</th><th class="n">Total</th><th></th></tr>
{% for r in rows %}<tr><td>{{r.receipt_no}}</td><td>{{r.receipt_date}}</td><td>{{r.order_no}}</td><td>{{r.ccode}} · {{r.cname}} {{r.branch_a}}</td>
<td class="n">{{'{:,.2f}'.format(r.total)}}</td><td style="white-space:nowrap"><a class="btn" href="/receipt/{{r.id}}/print">Print</a>
<a class="btn b2" href="/receipt/{{r.id}}">Edit</a><form class="i" method="post" action="/receipt/{{r.id}}/delete" onsubmit="return confirm('Delete receipt {{r.receipt_no}} and its lines?')"><button class="dg">Delete</button></form></td></tr>
{% else %}<tr><td colspan="6">{{ 'No receipts match your search.' if q else 'No receipts yet.' }}</td></tr>{% endfor %}</table></div>{% endblock %}"""

T_RFORM = "{% extends 'base' %}{% set nav='r' %}{% block body %}" + """
<h1>{{ 'Edit receipt ' ~ r.receipt_no if r else 'New receipt' }}</h1>
<div class="f"><div><label>Receipt no.</label><input id="no" value="{{ r.receipt_no if r else nxt }}"></div>
<div><label>Order no.</label><input id="ord" value="{{ r.order_no if r else '' }}"></div>
<div><label>Date</label><input id="dt" type="date" value="{{ r.receipt_date if r else '' }}"></div>
<div style="grid-column:span 2"><label>Customer</label><select id="cu"></select></div></div>
<div class="sc"><table id="t"><thead><tr><th>#</th><th style="width:26%">Goods</th><th>Description</th><th>Unit</th>
<th class="n">Qty</th><th class="n">Unit price</th><th>Discount</th><th class="n">Net</th><th></th></tr></thead><tbody></tbody></table></div>
<div class="bar" style="margin-top:.8rem"><button class="b2" onclick="add()">Add line</button>
<span style="margin-left:auto;font-size:1.2rem">Total <b id="tot">0.00</b> THB</span></div>
<div class="bar"><button onclick="save(false)">Save</button><button class="b2" onclick="save(true)">Save and print</button>
<a class="btn b2" href="/receipts">Cancel</a></div><div id="err" class="msg err" hidden></div>
<script>
const DS={{ discounts|tojson }},G={{ goods|tojson }},C={{ customers|tojson }},RID={{ r.id if r else 'null' }},L0={{ lines|tojson }},$=s=>document.querySelector(s);
const cu=$('#cu');cu.innerHTML=C.map(c=>`<option value="${c.id}">${c.code} · ${c.name} ${c.branch_a}</option>`).join('');
{% if r %}cu.value={{ r.customer_id }};{% endif %}$('#dt').value=$('#dt').value||new Date().toISOString().slice(0,10);
const dso=d=>`<option value="">-- none --</option>`+DS.map(x=>`<option value="${x.id}" ${x.id==d?'selected':''}>${x.code} · ${x.method}</option>`).join('');
const go=g=>`<option value="">-- choose --</option>`+G.map(x=>`<option value="${x.id}" ${x.id==g?'selected':''}>${x.code} · ${x.name}</option>`).join('');
function add(l={}){const tr=document.createElement('tr');tr.innerHTML=`<td></td><td><select>${go(l.goods_id)}</select></td>
<td><input class=nm value="${l.goods_name||''}"></td><td><input class=un size=5 value="${l.unit||''}"></td>
<td><input class=q type=number step=any style="text-align:right" value="${l.quantity||''}"></td>
<td><input class=p type=number step=any style="text-align:right" value="${l.unit_price??''}"></td>
<td><select class=ds>${dso(l.discount_id)}</select></td><td class="n net"></td>
<td><button class=dg onclick="this.closest('tr').remove();calc()">✕</button></td>`;$('tbody').append(tr);
tr.querySelector('select:not(.ds)').onchange=e=>{const g=G.find(x=>x.id==e.target.value);if(g){tr.querySelector('.nm').value=g.name;
tr.querySelector('.un').value=g.unit;tr.querySelector('.p').value=g.price;tr.querySelector('.q').focus()}calc()};calc()}
function calc(){let t=0;document.querySelectorAll('tbody tr').forEach((tr,i)=>{tr.cells[0].textContent=i+1;
const f=(DS.find(x=>x.id==tr.querySelector('.ds').value)||{factor:1}).factor;
const n=Math.round(+tr.querySelector('.q').value*+tr.querySelector('.p').value*f*100)/100;
tr.querySelector('.net').textContent=n.toLocaleString('en',{minimumFractionDigits:2});t+=n});$('#tot').textContent=t.toLocaleString('en',{minimumFractionDigits:2})}
document.addEventListener('input',calc);(L0.length?L0:[{}]).forEach(add);
async function save(pr){const lines=[...document.querySelectorAll('tbody tr')].map(tr=>({goods_id:+tr.querySelector('select:not(.ds)').value,
goods_name:tr.querySelector('.nm').value,unit:tr.querySelector('.un').value,quantity:+tr.querySelector('.q').value,
unit_price:+tr.querySelector('.p').value,discount_id:+tr.querySelector('.ds').value||null})).filter(l=>l.goods_id&&l.quantity>0);
const r=await fetch('/api/receipt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:RID,
receipt_no:$('#no').value.trim(),order_no:$('#ord').value.trim(),receipt_date:$('#dt').value,customer_id:+cu.value,lines})});
const j=await r.json();if(!r.ok){const e=$('#err');e.textContent=j.error;e.hidden=false;return}location=pr?`/receipt/${j.id}/print`:'/receipts'}
</script>{% endblock %}"""

T_PRINT = "{% extends 'base' %}{% set nav='r' %}{% block body %}" + """
<style>#sheet{background:#fff;color:#000;box-sizing:content-box;width:{{W}}ch;margin:0 auto;padding:8mm;border:1px solid var(--line);
font:{{wt}} {{pt}}pt/{{lh}}pt {{font}},"Leelawadee UI","Noto Sans Thai",sans-serif;overflow-x:auto}
.l{position:relative;height:{{lh}}pt;white-space:pre}.l span{position:absolute;top:0;white-space:pre;overflow:hidden}
@media print{header,.noprint,.msg{display:none}main{padding:0;max-width:none}body{background:#fff}#sheet{border:0;padding:0;margin:0}@page{margin:6mm}}</style>
<div class="noprint"><h1>Print receipt</h1><p style="color:var(--mut)">Click on the form to fix any text before printing. Layout comes from <b>receipt_form.txt</b>. In the print dialog choose your LQ-590 printer.</p>
<div class="bar"><button onclick="window.print()">Print</button><a class="btn b2" href="/receipt/{{i}}">Edit receipt</a>
<form class="i" method="get" style="display:inline-flex;gap:.4rem;align-items:center">Font pt <input name="pt" value="{{pt}}" size="3"> Paper width in <input name="paper" value="{{paper}}" size="3"> <label><input type="checkbox" name="bold" value="1" {{'checked' if wt=='bold'}}> Bold</label><input type="hidden" name="bold" value="0"><button class="b2">Apply</button></form>
<a class="btn b2" href="/form/blank">Blank form (.txt)</a><a class="btn b2" href="/receipts">Back</a></div></div>
<div id="fit" class="msg noprint" hidden></div>
<div id="sheet" contenteditable="true" spellcheck="false">{{ body }}</div>
<script>
/* Browsers shrink a page that is wider than the paper, so a bigger Font pt can print the same size or smaller.
   This measures the form in this browser and says what will really print. */
(function(){const s=document.getElementById('sheet'),cs=getComputedStyle(s),el=document.getElementById('fit');
const inch=(s.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight))/96,
avail={{paper}}-2*6/25.4, pt={{pt}}, fit=Math.floor(pt*avail/inch*10)/10;
el.hidden=false;
if(inch>avail+0.01){el.classList.add('err');
 el.textContent='Form is '+inch.toFixed(2)+' in wide, but '+{{paper}}+' in paper prints only '+avail.toFixed(2)+' in. The browser will shrink the print to about '+fit+' pt, so a larger Font pt will not print larger. Use Font pt '+fit+' or less, or make the form narrower in receipt_form.txt.';}
else{el.textContent='Fits: form is '+inch.toFixed(2)+' in wide, paper prints '+avail.toFixed(2)+' in. Prints at the full '+pt+' pt. Largest size that still fits: '+fit+' pt.';}})();
</script>{% endblock %}"""

T_DASH = "{% extends 'base' %}{% set nav='d' %}{% block body %}" + """
<style>
.viz{--v-s1:#2a78d6;--v-surf:#fcfcfb;--v-ink:#0b0b0b;--v-ink2:#52514e;--v-mute:#898781;--v-grid:#e1e0d9;--v-axis:#c3c2b7;--v-ring:rgba(11,11,11,.10);
 font-family:system-ui,-apple-system,"Segoe UI",Tahoma,"Noto Sans Thai",sans-serif;position:relative}
@media(prefers-color-scheme:dark){.viz{--v-s1:#3987e5;--v-surf:#1a1a19;--v-ink:#f0efec;--v-ink2:#c3c2b7;--v-mute:#898781;--v-grid:#2c2c2a;--v-axis:#383835;--v-ring:rgba(255,255,255,.10)}}
.filters{display:flex;flex-wrap:wrap;gap:.6rem 1rem;align-items:center;margin-bottom:1.2rem}
.seg{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg a{padding:.35rem .9rem;text-decoration:none;color:var(--ink);border-right:1px solid var(--line)}
.seg a:last-child{border-right:0}.seg a:hover{background:rgba(128,128,128,.12)}
.seg a[aria-current]{font-weight:700;background:rgba(128,128,128,.18)}.seg a[aria-current]::before{content:"✓ "}
.filters form{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap}.filters input[type=date]{padding:.3rem .5rem}
.card{background:var(--v-surf);color:var(--v-ink);border:1px solid var(--v-ring);border-radius:6px;padding:1rem 1.3rem;margin-bottom:1rem}
.card h2{font-size:1rem;margin:0 0 .15rem}.card .sub{color:var(--v-ink2);font-size:.85rem;margin:0 0 .8rem}
.card .hd{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap}
.card .hd a{color:var(--v-ink2);font-size:.85rem}
.top{display:grid;grid-template-columns:minmax(280px,1.15fr) 2fr;gap:1rem;margin-bottom:1rem}.top .card{margin:0}
.hero .lb{color:var(--v-ink2)}.hero .v{font-size:3.4rem;font-weight:700;line-height:1.15}.hero .u{font-size:1.1rem;font-weight:500;color:var(--v-ink2);margin-left:.4rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1rem}.tiles .card{margin:0;padding:.8rem 1rem}
.tile .lb{color:var(--v-ink2);font-size:.85rem}.tile .v{font-size:1.7rem;font-weight:650;line-height:1.25}.tile .nt{color:var(--v-mute);font-size:.8rem}
.two{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.two .card{margin:0}
.plot{position:relative;height:200px;margin:.4rem 0 0 46px;border-bottom:1px solid var(--v-axis)}
.gl{position:absolute;left:0;right:0;border-top:1px solid var(--v-grid)}.gl span{position:absolute;right:calc(100% + 8px);top:-.65em;font-size:.75rem;color:var(--v-mute)}
.cols{position:absolute;inset:0;display:flex;gap:2px;align-items:flex-end}
.slot{flex:1;min-width:0;height:100%;position:relative;display:flex;align-items:flex-end;justify-content:center;outline:0}
.slot i{display:block;width:100%;max-width:24px;background:var(--v-s1);border-radius:4px 4px 0 0}
.slot:hover i,.slot:focus-visible i{filter:brightness(1.15)}.slot:focus-visible{outline:2px solid var(--v-ink2);outline-offset:-2px}
.slot em{position:absolute;font-style:normal;font-size:.8rem;color:var(--v-ink2);white-space:nowrap;left:50%;transform:translateX(-50%)}
.slot em.l{left:0;transform:none}.slot em.r{left:auto;right:0;transform:none}
.xl{display:flex;gap:2px;margin:.35rem 0 0 46px}.xl span{flex:1;min-width:0;text-align:center;font-size:.75rem;color:var(--v-mute);white-space:nowrap}
.hb{list-style:none;margin:0;padding:0}.hb li{display:grid;grid-template-columns:minmax(0,40%) 1fr;gap:10px;align-items:center;min-height:32px;outline:0}
.hb .nm{font-size:.88rem;color:var(--v-ink);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.3}
.hb .tr{position:relative;height:14px}.hb b{position:absolute;left:0;top:0;height:14px;background:var(--v-s1);border-radius:0 4px 4px 0}
.hb em{position:absolute;top:-2px;font-style:normal;font-size:.8rem;color:var(--v-ink2);white-space:nowrap}
.hb li:hover b,.hb li:focus-visible b{filter:brightness(1.15)}.hb li:focus-visible{outline:2px solid var(--v-ink2);outline-offset:1px}
.card details{margin-top:.8rem}.card summary{cursor:pointer;color:var(--v-ink2);font-size:.85rem}
.card table{background:transparent;margin-top:.4rem;font-size:.88rem}.card th{color:var(--v-ink2)}.card td,.card th{border-bottom:1px solid var(--v-grid)}
.empty{padding:2rem;text-align:center;color:var(--v-ink2)}
#vtip{position:fixed;z-index:20;pointer-events:none;background:var(--v-surf);color:var(--v-ink);border:1px solid var(--v-axis);border-radius:6px;padding:.45rem .7rem;max-width:280px}
#vtip strong{display:block;font-size:1rem}#vtip span{display:block;font-size:.85rem;color:var(--v-ink2)}#vtip small{display:block;font-size:.78rem;color:var(--v-mute)}
@media(max-width:800px){.top,.two{grid-template-columns:1fr}.hero .v{font-size:2.8rem}}
@media print{header,.noprint{display:none!important}main{padding:0;max-width:none}body{background:#fff}.card{break-inside:avoid}.two,.top{display:block}.two .card,.top .card{margin-bottom:1rem}}
</style>
<div class="viz">
<div class="filters noprint"><div class="seg">
{% for k,t in [('all','All time'),('year','Year to date'),('month','Month to date'),('30','Last 30 days')] %}<a href="/dashboard?p={{k}}" {{'aria-current=page' if p==k}}>{{t}}</a>{% endfor %}</div>
<form method="get" action="/dashboard"><input type="date" name="from" value="{{lo}}" aria-label="From date"> to <input type="date" name="to" value="{{hi}}" aria-label="To date"><button class="b2">Apply</button></form>
<button class="b2" style="margin-left:auto" onclick="window.print()">Print report</button></div>
<h1>Dashboard <span style="font-weight:400;color:var(--mut);font-size:1rem">· {{label}}</span></h1>
{% if not n %}<div class="card empty">No receipts in this range. Pick a wider range, or <a href="/receipt/new">create a receipt</a>.</div>
{% else %}
<div class="top"><div class="card hero"><div class="lb">Net sales</div><div><span class="v">{{hero}}</span><span class="u">THB</span></div>
<div class="nt" style="color:var(--v-mute);font-size:.85rem">{{n}} receipt{{'s' if n!=1}} · {{label}}</div></div>
<div class="tiles">
<div class="card tile"><div class="lb">Average receipt</div><div class="v">{{avg}}</div></div>
<div class="card tile"><div class="lb">Discounts given</div><div class="v">{{disc}}</div><div class="nt">{{dpct}}% of {{gross}} before discount</div></div>
<div class="card tile"><div class="lb">Units sold</div><div class="v">{{units}}</div></div>
<div class="card tile"><div class="lb">Customers served</div><div class="v">{{cust}}</div></div></div></div>

<div class="card"><div class="hd"><h2>Net sales by {{per}}</h2><a class="noprint" href="/dashboard.csv?report=sales&amp;{{qs}}">Download CSV</a></div>
<p class="sub">Receipt totals after discount, THB. Hover or tab to a column for the exact value.</p>
<div class="plot">{% for t in ticks %}<div class="gl" style="bottom:{{t.pct}}%"><span>{{t.label}}</span></div>{% endfor %}
<div class="cols">{% for b in bars %}<div class="slot" tabindex="0" data-label="{{b.label}}" data-value="{{b.val}} THB" data-sub="{{b.sub}}"><i style="height:{{b.pct}}%"></i>{% if b.mx %}<em class="{{b.side}}" style="bottom:calc({{b.pct}}% + 4px)">{{b.short}}</em>{% endif %}</div>{% endfor %}</div></div>
<div class="xl">{% for b in bars %}<span>{{b.tick if b.show}}</span>{% endfor %}</div>
<details><summary>Table view</summary><table><tr><th>Period</th><th class="n">Receipts</th><th class="n">Net sales</th></tr>
{% for b in bars %}<tr><td>{{b.label}}</td><td class="n">{{b.sub.split()[0]}}</td><td class="n">{{b.val}}</td></tr>{% endfor %}</table></details></div>

<div class="two">
<div class="card"><div class="hd"><h2>Top goods</h2><a class="noprint" href="/dashboard.csv?report=goods&amp;{{qs}}">Download CSV</a></div>
<p class="sub">Net sales, THB. Top {{goods|length}} of {{ngoods}}.</p>
<ol class="hb">{% for r in goods %}<li tabindex="0" data-label="{{r.name}}" data-value="{{r.v}} THB" data-sub="{{r.sub}}"><span class="nm" title="{{r.name}}">{{r.name}}</span><span class="tr"><b style="width:{{r.pct}}%"></b><em style="left:calc({{r.pct}}% + 6px)">{{r.v}}</em></span></li>{% endfor %}</ol>
<details><summary>Table view</summary><table><tr><th>Goods</th><th class="n">Qty</th><th class="n">Receipts</th><th class="n">Net sales</th></tr>
{% for r in goods_t %}<tr><td>{{r.code}} · {{r.name}}</td><td class="n">{{fmtq(r.qty)}} {{r.unit}}</td><td class="n">{{r.n}}</td><td class="n">{{m(r.net)}}</td></tr>{% endfor %}</table>{% if ngoods>50 %}<p class="sub">First 50 shown; the CSV has all {{ngoods}}.</p>{% endif %}</details></div>
<div class="card"><div class="hd"><h2>Top customers</h2><a class="noprint" href="/dashboard.csv?report=customers&amp;{{qs}}">Download CSV</a></div>
<p class="sub">Net sales, THB. Top {{custs|length}} of {{ncust}}.</p>
<ol class="hb">{% for r in custs %}<li tabindex="0" data-label="{{r.name}}" data-value="{{r.v}} THB" data-sub="{{r.sub}}"><span class="nm" title="{{r.name}}">{{r.name}}</span><span class="tr"><b style="width:{{r.pct}}%"></b><em style="left:calc({{r.pct}}% + 6px)">{{r.v}}</em></span></li>{% endfor %}</ol>
<details><summary>Table view</summary><table><tr><th>Customer</th><th class="n">Receipts</th><th class="n">Net sales</th></tr>
{% for r in custs_t %}<tr><td>{{r.code}} · {{r.name}} {{r.branch}}</td><td class="n">{{r.n}}</td><td class="n">{{m(r.net)}}</td></tr>{% endfor %}</table>{% if ncust>50 %}<p class="sub">First 50 shown; the CSV has all {{ncust}}.</p>{% endif %}</details></div></div>

<div class="card" style="margin-top:1rem"><div class="hd"><h2>Discounts given</h2><a class="noprint" href="/dashboard.csv?report=discounts&amp;{{qs}}">Download CSV</a></div>
{% if discs %}<p class="sub">By discount method, on lines that used one.</p><table><tr><th>Discount</th><th class="n">Lines</th><th class="n">Before discount</th><th class="n">Discount</th></tr>
{% for r in discs %}<tr><td>{{r.method}}</td><td class="n">{{r.n}}</td><td class="n">{{m(r.gross)}}</td><td class="n">{{m(r.disc)}}</td></tr>{% endfor %}</table>
{% else %}<p class="sub">No discounts were used in this range.</p>{% endif %}</div>
{% endif %}
</div>
<script>
(function(){const root=document.querySelector('.viz'),tip=document.createElement('div');tip.id='vtip';tip.hidden=true;root.append(tip);
function show(el,x,y){tip.replaceChildren();const v=document.createElement('strong');v.textContent=el.dataset.value;
 const l=document.createElement('span');l.textContent=el.dataset.label;tip.append(v,l);
 if(el.dataset.sub){const s=document.createElement('small');s.textContent=el.dataset.sub;tip.append(s)}
 tip.hidden=false;const w=tip.offsetWidth,h=tip.offsetHeight;
 tip.style.left=Math.max(4,Math.min(x+14,innerWidth-w-4))+'px';tip.style.top=Math.max(4,y-h-10)+'px'}
function hide(){tip.hidden=true}
root.querySelectorAll('[data-value]').forEach(el=>{
 el.addEventListener('pointermove',e=>show(el,e.clientX,e.clientY));el.addEventListener('pointerleave',hide);
 el.addEventListener('focus',()=>{const r=el.getBoundingClientRect();show(el,r.left+r.width/2,r.top)});el.addEventListener('blur',hide)});
let opened=[];addEventListener('beforeprint',()=>{opened=[...document.querySelectorAll('details:not([open])')];opened.forEach(d=>d.open=true)});
addEventListener('afterprint',()=>{opened.forEach(d=>d.open=false);opened=[]});})();
</script>{% endblock %}"""

with sqlite3.connect(DB) as _c:
    _c.executescript(SCHEMA)
    have = {r[1] for r in _c.execute("PRAGMA table_info(receipt_detail)")}
    for col in ("discount_code", "discount_method"):
        if col not in have: _c.execute(f"ALTER TABLE receipt_detail ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
