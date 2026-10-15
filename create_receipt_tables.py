#!/usr/bin/env python3
"""
Add `receipt` (header) and `receipt_detail` (lines) to receipts.db — one-to-many —
and insert the sample receipt from sample_receipt.txt.

    python create_receipt_tables.py receipts.db
"""
import sqlite3
import sys
from decimal import Decimal, ROUND_HALF_UP

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS receipt (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_no    TEXT NOT NULL UNIQUE,              -- เลขที่  e.g. '0038364'
    order_no      TEXT NOT NULL DEFAULT '',          -- เลขที่ใบสั่งสินค้า e.g. 'OU52044163'
    receipt_date  TEXT NOT NULL,                     -- ISO 'YYYY-MM-DD'
    customer_id   INTEGER NOT NULL
                  REFERENCES customer(id) ON UPDATE CASCADE ON DELETE RESTRICT,
    subtotal      NUMERIC NOT NULL DEFAULT 0,        -- sum of line amounts before discount
    discount_total NUMERIC NOT NULL DEFAULT 0,
    total         NUMERIC NOT NULL DEFAULT 0,        -- net amount (จำนวนเงินรวม)
    total_text    TEXT NOT NULL DEFAULT '',          -- Thai baht text for printing
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_receipt_customer ON receipt(customer_id);
CREATE INDEX IF NOT EXISTS idx_receipt_date     ON receipt(receipt_date);

CREATE TABLE IF NOT EXISTS receipt_detail (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id   INTEGER NOT NULL
                 REFERENCES receipt(id) ON UPDATE CASCADE ON DELETE CASCADE,
    line_no      INTEGER NOT NULL,                   -- ลำดับ
    goods_id     INTEGER NOT NULL
                 REFERENCES goods(id) ON UPDATE CASCADE ON DELETE RESTRICT,
    -- snapshot of goods at time of sale, so later price/name edits
    -- never change an already-issued receipt
    goods_name   TEXT NOT NULL,
    unit         TEXT NOT NULL DEFAULT '',
    quantity     NUMERIC NOT NULL CHECK (quantity > 0),
    unit_price   NUMERIC NOT NULL CHECK (unit_price >= 0),
    amount       NUMERIC NOT NULL,                   -- quantity * unit_price
    discount_id  INTEGER REFERENCES discount(id),    -- optional, from discount table
    discount_code   TEXT NOT NULL DEFAULT '',        -- snapshot of discount.code
    discount_method TEXT NOT NULL DEFAULT '',        -- snapshot of discount.method (printed on receipt)
    discount_amount NUMERIC NOT NULL DEFAULT 0,
    net_amount   NUMERIC NOT NULL,                   -- amount - discount_amount
    UNIQUE (receipt_id, line_no)
);
CREATE INDEX IF NOT EXISTS idx_detail_receipt ON receipt_detail(receipt_id);
CREATE INDEX IF NOT EXISTS idx_detail_goods   ON receipt_detail(goods_id);
"""

# ---------- Thai baht text (ห้าพันแปดร้อยห้าบาทถ้วน) ----------
_D = ["ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"]
_P = ["", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน"]


def _below_million(n: int, has_higher: bool) -> str:
    s = str(n)
    out = ""
    for i, ch in enumerate(s):
        d, pos = int(ch), len(s) - i - 1
        if d == 0:
            continue
        if pos == 0 and d == 1 and (len(s) > 1 or has_higher):
            out += "เอ็ด"
        elif pos == 1 and d == 2:
            out += "ยี่สิบ"
        elif pos == 1 and d == 1:
            out += "สิบ"
        else:
            out += _D[d] + _P[pos]
    return out


def baht_text(amount) -> str:
    amount = Decimal(amount).quantize(Decimal("0.01"), ROUND_HALF_UP)
    baht, satang = int(amount), int((amount - int(amount)) * 100)
    parts, first = [], True
    while True:  # split into groups of 6 digits, joined by 'ล้าน'
        baht, chunk = divmod(baht, 1_000_000)
        parts.append(chunk)
        if baht == 0:
            break
    text = ""
    for i, chunk in enumerate(reversed(parts)):
        if chunk:
            text += _below_million(chunk, has_higher=(i > 0))
        if i < len(parts) - 1:
            text += "ล้าน"
    text = text or "ศูนย์"
    text += "บาท"
    return text + ("ถ้วน" if satang == 0 else _below_million(satang, False) + "สตางค์")


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)


# ---------- create a receipt ----------
def _one(con, sql, arg, what):
    rows = con.execute(sql, (arg,)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"{what} {arg!r}: expected 1 match, found {len(rows)}")
    return rows[0]


def create_receipt(con, receipt_no, order_no, receipt_date, customer_code, items):
    """items = [(goods_code, quantity), ...] in print order. Returns receipt id."""
    (cust_id,) = _one(con, "SELECT id FROM customer WHERE code=?", customer_code, "customer")
    lines, subtotal = [], Decimal(0)
    for no, (gcode, qty) in enumerate(items, 1):
        gid, name, unit, price = _one(
            con, "SELECT id,name,unit,price FROM goods WHERE code=?", gcode, "goods")
        price = money(price)
        amt = money(Decimal(str(qty)) * price)
        subtotal += amt
        lines.append((no, gid, name, unit, qty, price, amt))
    total = subtotal  # no discount on this sample

    with con:  # one transaction: header + all lines, or nothing
        cur = con.execute(
            "INSERT INTO receipt(receipt_no,order_no,receipt_date,customer_id,"
            "subtotal,discount_total,total,total_text) VALUES (?,?,?,?,?,?,?,?)",
            (receipt_no, order_no, receipt_date, cust_id,
             float(subtotal), 0, float(total), baht_text(total)))
        rid = cur.lastrowid
        con.executemany(
            "INSERT INTO receipt_detail(receipt_id,line_no,goods_id,goods_name,unit,"
            "quantity,unit_price,amount,discount_amount,net_amount) "
            "VALUES (?,?,?,?,?,?,?,?,0,?)",
            [(rid, no, gid, nm, un, q, float(p), float(a), float(a))
             for no, gid, nm, un, q, p, a in lines])
    return rid


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "receipts.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)

    if not con.execute("SELECT 1 FROM receipt WHERE receipt_no='0038364'").fetchone():
        rid = create_receipt(
            con, receipt_no="0038364", order_no="OU52044163",
            receipt_date="2026-09-25", customer_code="F11",
            items=[("01", 12), ("02", 10), ("06", 10), ("03", 15)])
        print("created receipt id", rid)

    r = con.execute("SELECT receipt_no,receipt_date,total,total_text FROM receipt").fetchall()
    print(r)
    assert r[0][2] == 5805 and r[0][3] == "ห้าพันแปดร้อยห้าบาทถ้วน"
    for row in con.execute("""
        SELECT d.line_no, d.goods_name, d.quantity, d.unit, d.unit_price, d.net_amount
        FROM receipt_detail d JOIN receipt r ON r.id=d.receipt_id ORDER BY d.line_no"""):
        print(row)
    print("FK check:", con.execute("PRAGMA foreign_key_check").fetchall())
    for n in (0, 1, 11, 21, 101, 1021, 1_000_001, 12.50):
        print(n, baht_text(n))
