#!/usr/bin/env python3
"""RUETHAI KASIKRANUN RECEIPT SYSTEM  -  pip install flask ; python app.py ; open http://127.0.0.1:5000
Needs create_receipt_tables.py + escp_thai_print.py + receipts.db in the same folder."""
import os, re, sqlite3, subprocess, unicodedata
from decimal import Decimal, ROUND_HALF_UP
from flask import Flask, request, redirect, render_template_string, flash, g, jsonify, Response
from jinja2 import DictLoader
from create_receipt_tables import SCHEMA, baht_text
import escp_thai_print as E

TITLE = "RUETHAI KASIKRANUN RECEIPT SYSTEM"
DB = os.environ.get("RECEIPT_DB", "receipts.db")
PRINTER = os.environ.get("LQ590_PRINTER", "EPSON LQ-590")
E.THAI_TABLE = int(os.environ.get("THAI_TABLE", "1"))   # ESC t n  (see LQ-590 manual)
COLS = int(os.environ.get("PRINT_COLS", "110"))          # characters per line (condensed)
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
def clip(s, w):
    o, n = "", 0
    for ch in str(s):
        k = 0 if unicodedata.category(ch) == "Mn" else 1
        if n + k > w: break
        o += ch; n += k
    return o
def m(x): return f"{float(x):,.2f}"
TOK = re.compile(r"\{([<>^]?)([a-z_]+) *\}")     # {name   } = field as wide as the token; {>name} right, {^name} centre
def fill(line, v):
    return TOK.sub(lambda t: pad(clip(v.get(t[2], ""), len(t[0])), len(t[0]), {"": "l", "<": "l", ">": "r", "^": "c"}[t[1]]), line)

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
    o += ["", "".join(pad(t, n, "l" if k < 2 else "r") for k, (t, n) in enumerate(zip(hd, w))), "-" * W, "#ITEMS",
          "".join(tk(n, x, a) for n, x, a in zip(names, w, al)), "#END", "-" * W,
          tk("total_text", W - 14) + tk("total", 14, ">")]
    return "\n".join(o) + "\n"

def load_form():
    if not os.path.exists(FORM):
        open(FORM, "w", encoding="utf-8").write(default_form())
    return open(FORM, encoding="utf-8-sig").read().splitlines()

def receipt_text(i):
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
    out, blk, mode = [], [], 0
    for ln in load_form():
        if ln.startswith("##"): continue
        if ln.strip() == "#ITEMS": mode, blk = 1, []
        elif ln.strip() == "#END":
            out += [fill(b, rw) for rw in rows for b in blk]; mode = 0
        elif mode: blk.append(ln)
        else: out.append(fill(ln, v).rstrip())
    return "\n".join(out) + "\n"

@app.route("/form/blank")
def form_blank():
    load_form()
    return Response(open(FORM, "rb").read(), mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=receipt_form.txt"})

@app.route("/receipt/<int:i>/print", methods=["GET", "POST"])
def receipt_print(i):
    if request.method == "POST":
        text = request.form["text"]
        data = E.INIT + E.NLQ_ON + E.select_thai() + E.CONDENSED_ON + \
               E.thai(text.replace("\r\n", "\n").replace("\n", "\r\n")) + E.FORMFEED
        if request.form["action"] == "prn":
            return Response(data, mimetype="application/octet-stream",
                            headers={"Content-Disposition": f"attachment; filename=receipt_{i}.prn"})
        try:
            if os.name == "nt": E.send_raw(data, PRINTER)
            else: subprocess.run(["lp", "-d", PRINTER, "-o", "raw"], input=data, check=True)
            flash("Sent to " + PRINTER)
        except Exception as e: flash(f"Print failed: {e}", "err")
    return render_template_string(T_PRINT, i=i, text=receipt_text(i), printer=PRINTER)

# ---------- templates ----------
app.jinja_loader = DictLoader({"base": """<!doctype html><html lang="th"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{{ title }}</title><style>
:root{--ink:#1d2a24;--mut:#66736c;--line:#d5dcd7;--bg:#f6f8f6;--g:#1f5c4a;--r:#a4372b}
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
<style>#pv{display:none}@media print{header,main{display:none}#pv{display:block;white-space:pre;font:8pt/1.25 "Courier New",monospace;margin:0}}</style>
<h1>Print receipt</h1><p style="color:var(--mut)">Edit the text below if needed. The layout comes from <b>receipt_form.txt</b>; change it in any text editor and reload this page. The preview font is approximate.</p>
<form method="post"><textarea id="tx" name="text" rows="22" wrap="off" style="width:100%;font:13px/1.3 'Courier New','Noto Sans Mono',monospace">{{text}}</textarea>
<div class="bar" style="margin-top:.8rem"><button name="action" value="send">Send to {{printer}}</button>
<button name="action" value="prn" class="b2">Download .prn</button>
<button type="button" class="b2" onclick="pv.textContent=tx.value;window.print()">Browser print</button>
<a class="btn b2" href="/form/blank">Blank form (.txt)</a><a class="btn b2" href="/receipt/{{i}}">Edit receipt</a><a class="btn b2" href="/receipts">Back</a></div></form><pre id="pv"></pre>{% endblock %}"""

with sqlite3.connect(DB) as _c:
    _c.executescript(SCHEMA)
    have = {r[1] for r in _c.execute("PRAGMA table_info(receipt_detail)")}
    for col in ("discount_code", "discount_method"):
        if col not in have: _c.execute(f"ALTER TABLE receipt_detail ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
