"""
Minimal ESC/P helper for printing Thai receipts on an Epson LQ-590.

UNTESTED ON HARDWARE - the printer's Thai character-table number differs by
model/firmware. Check the "Character tables" section of your LQ-590 manual or
run the printer's self-test, then set THAI_TABLE below.
"""
ESC = b"\x1b"
INIT = ESC + b"@"                      # reset printer
FORMFEED = b"\x0c"
NLQ_ON = ESC + b"x\x01"               # Near Letter Quality (needed for good Thai glyphs)
DRAFT_ON = ESC + b"x\x00"
CONDENSED_ON, CONDENSED_OFF = b"\x0f", b"\x12"

THAI_TABLE = 1   # <-- ESC t n : pick the Thai table from the manual / self-test


def select_thai(table: int = THAI_TABLE) -> bytes:
    return ESC + b"t" + bytes([table])   # ESC t n  select character table


def thai(text: str) -> bytes:
    # cp874 maps Thai to the same 0xA1-0xFB bytes as TIS-620, which is what
    # Thai dot-matrix tables expect. 'replace' avoids crashing on stray chars.
    return text.encode("cp874", errors="replace")


def build_receipt(lines):
    out = bytearray(INIT + NLQ_ON + select_thai())
    for ln in lines:
        out += thai(ln) + b"\r\n"
    out += FORMFEED
    return bytes(out)


def send_raw(data: bytes, printer: str):
    """Windows: send raw bytes to a printer by name (needs `pip install pywin32`)."""
    import win32print
    h = win32print.OpenPrinter(printer)
    try:
        win32print.StartDocPrinter(h, 1, ("Receipt", None, "RAW"))
        win32print.StartPagePrinter(h)
        win32print.WritePrinter(h, data)
        win32print.EndPagePrinter(h)
        win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)


if __name__ == "__main__":
    import sqlite3
    con = sqlite3.connect("receipts.db")
    lines = ["ใบเสร็จรับเงิน", "-" * 40]
    for name, unit, price in con.execute("SELECT name, unit, price FROM goods LIMIT 3"):
        lines.append(f"{name} ({unit})  {price:,.2f}")
    data = build_receipt(lines)
    open("receipt_test.prn", "wb").write(data)   # inspect / copy /b to the printer
    # send_raw(data, "EPSON LQ-590")
    print("wrote receipt_test.prn", len(data), "bytes")
