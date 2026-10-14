#!/usr/bin/env python3
"""
Convert legacy Thai dBase III files (CUSTOMER / DISCOUNT / GOODS) to SQLite.

Usage:
    pip install dbfread
    python dbf_to_sqlite.py --src ./dbf --out receipts.db

Notes
- Thai DOS/Windows DBF files carry no language flag (byte 29 = 0), so the
  codepage must be given explicitly: cp874 (= TIS-620 + Windows extras).
- Everything is stored as UTF-8 TEXT in SQLite.
- Legacy keys are NOT unique in your data (GOODS has duplicate codes '49' and
  '69'; CUSTOMER has one blank code), so each table gets a surrogate integer
  primary key and the legacy code is kept in a plain (non-unique) index.
  Duplicates are reported rather than silently dropped.
"""
import argparse
import sqlite3
from pathlib import Path

from dbfread import DBF

ENCODING = "cp874"

SCHEMA = """
CREATE TABLE customer (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    code      TEXT NOT NULL,            -- C_CODE
    name      TEXT NOT NULL,            -- C_NAME
    addr_a    TEXT NOT NULL DEFAULT '', -- C_ADDR_A
    addr_b    TEXT NOT NULL DEFAULT '', -- C_ADDR_B
    addr_c    TEXT NOT NULL DEFAULT '', -- C_ADDR_C
    branch_a  TEXT NOT NULL DEFAULT '', -- BRANCE_A (sic)
    branch_b  TEXT NOT NULL DEFAULT '', -- BRANCE_B
    other_a   TEXT NOT NULL DEFAULT '', -- OTHER_A
    other_b   TEXT NOT NULL DEFAULT ''  -- OTHER_B
);
CREATE INDEX idx_customer_code ON customer(code);

CREATE TABLE discount (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    code      TEXT NOT NULL,            -- DISCODE
    method    TEXT NOT NULL DEFAULT '', -- METHOD  (e.g. '10% , 2%')
    first     REAL NOT NULL DEFAULT 0,  -- FIRST   (% off, step 1)
    second    REAL NOT NULL DEFAULT 0,  -- SECOND  (% off, step 2)
    third     REAL NOT NULL DEFAULT 0,  -- THIRD   (% off, step 3)
    factor    REAL NOT NULL DEFAULT 1   -- DISCNT  (net multiplier, e.g. 0.9)
);
CREATE INDEX idx_discount_code ON discount(code);

CREATE TABLE goods (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    code      TEXT NOT NULL,            -- G_CODE
    name      TEXT NOT NULL,            -- GOODS
    unit      TEXT NOT NULL DEFAULT '', -- UNIT
    price     NUMERIC NOT NULL DEFAULT 0 -- PRICE
);
CREATE INDEX idx_goods_code ON goods(code);
"""


def s(v):
    """Trim DBF space padding; None -> ''."""
    return (v or "").strip()


def n(v):
    return float(v) if v is not None else 0.0


def read(src: Path, name: str):
    t = DBF(
        str(src / f"{name}.DBF"),
        encoding=ENCODING,
        char_decode_errors="strict",   # fail loudly instead of corrupting Thai
        ignore_missing_memofile=True,
    )
    return list(t)  # dbfread skips deleted records by default


def report_dupes(table, rows):
    seen, dupes = set(), set()
    for r in rows:
        (dupes if r[0] in seen else seen).add(r[0])
    if dupes:
        print(f"  WARNING {table}: duplicate codes {sorted(dupes)}")
    if any(r[0] == "" for r in rows):
        print(f"  WARNING {table}: blank code present")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=".", help="folder containing the .DBF files")
    ap.add_argument("--out", default="receipts.db")
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    if out.exists():
        out.unlink()

    con = sqlite3.connect(out)
    con.executescript(SCHEMA)

    cust = [(s(r["C_CODE"]), s(r["C_NAME"]), s(r["C_ADDR_A"]), s(r["C_ADDR_B"]),
             s(r["C_ADDR_C"]), s(r["BRANCE_A"]), s(r["BRANCE_B"]),
             s(r["OTHER_A"]), s(r["OTHER_B"])) for r in read(src, "CUSTOMER")]
    disc = [(s(r["DISCODE"]), s(r["METHOD"]), n(r["FIRST"]), n(r["SECOND"]),
             n(r["THIRD"]), n(r["DISCNT"])) for r in read(src, "DISCOUNT")]
    goods = [(s(r["G_CODE"]), s(r["GOODS"]), s(r["UNIT"]), n(r["PRICE"]))
             for r in read(src, "GOODS")]

    with con:
        con.executemany("INSERT INTO customer(code,name,addr_a,addr_b,addr_c,"
                        "branch_a,branch_b,other_a,other_b) VALUES (?,?,?,?,?,?,?,?,?)", cust)
        con.executemany("INSERT INTO discount(code,method,first,second,third,factor)"
                        " VALUES (?,?,?,?,?,?)", disc)
        con.executemany("INSERT INTO goods(code,name,unit,price) VALUES (?,?,?,?)", goods)

    for tbl, rows in (("customer", cust), ("discount", disc), ("goods", goods)):
        cnt = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        assert cnt == len(rows)
        print(f"{tbl}: {cnt} rows")
        report_dupes(tbl, rows)
    con.close()
    print(f"Done -> {out}")


if __name__ == "__main__":
    main()
