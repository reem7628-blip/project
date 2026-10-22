"""Spot-check by eye: follow ONE SKU and ONE order line from the raw files to the published tables.

    uv run python tools/spot_check.py                 # default data/ and warehouse/
    uv run python tools/spot_check.py --sku QF-DAI-1047

Everything is printed with the raw evidence next to the result, so you can verify the rules by hand.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from pyiceberg.catalog.sql import SqlCatalog

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--warehouse", type=Path, default=ROOT / "warehouse")
    ap.add_argument("--sku", help="SKU to follow (default: the first SKU with 3+ cost versions)")
    a = ap.parse_args()
    wh = a.warehouse.resolve()
    cat = SqlCatalog("qafila", uri=f"sqlite:///{(wh / 'catalog.db').as_posix()}", warehouse=wh.as_posix())
    hist = cat.load_table("silver.product_cost_history").scan().to_pandas()
    hist["valid_from"] = hist["valid_from"].astype(str)

    # ---- 1. one SKU: raw supplier rows -> SCD2 versions
    counts = hist.groupby("sku").size()
    sku = a.sku or counts[counts >= 3].index[0]
    print(f"=== 1. SKU {sku}: what each supplier file says ===")
    for f in sorted((a.data_dir / "suppliers").glob("prices_*.csv")):
        with f.open(newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if r["sku"].strip() == sku:
                    print(f"  {f.name:26} valid_from={r['valid_from']}  cost as written: {r.get('unit_cost') or r.get('cost_sar')!r}")
    print("  -> the published history (a new row only when the cost CHANGES; valid_to is exclusive):")
    print(hist[hist.sku == sku].sort_values("valid_from").to_string(index=False))

    # ---- 2. one order line: revenue, cost version, margin
    with closing(sqlite3.connect(a.data_dir / "qafila_oltp.sqlite")) as con:
        sku_to_pid = {s.strip(): p for p, s in con.execute("SELECT product_id, sku FROM products")}
        row = con.execute(
            """SELECT o.order_id, o.created_at, o.status, i.line_no, i.quantity, i.unit_price, i.discount, st.city, p.category
               FROM order_items i JOIN orders o USING (order_id) JOIN stores st USING (store_id) JOIN products p USING (product_id)
               WHERE i.product_id = ? AND o.status = 'delivered' AND i.quantity > 0
                 AND date(datetime(o.created_at, '+3 hours')) >= '2026-09-10' AND i.discount > 0
               ORDER BY o.order_id LIMIT 1""", (sku_to_pid[sku],)).fetchone()
    if row:
        oid, created, status, line, qty, price, disc, city, category = row
        riyadh = datetime.fromisoformat(created) + timedelta(hours=3)
        d = riyadh.date().isoformat()
        rev = round(qty * price - disc, 2)
        h = hist[hist.sku == sku].sort_values("valid_from")
        v = h[(h.valid_from <= d) & (h.valid_to.isna() | (h.valid_to.astype(str) > d))]
        print(f"\n=== 2. One order line of this SKU ===")
        print(f"  order {oid} line {line}: status={status}, created_at (UTC)={created} -> Riyadh {riyadh} -> order date {d}")
        print(f"  revenue = round({qty} x {price} - {disc}, 2) = {rev}")
        if len(v):
            c = float(v.iloc[0].unit_cost_sar)
            print(f"  cost version valid on {d}: {c} (valid_from={v.iloc[0].valid_from}, valid_to={v.iloc[0].valid_to})")
            print(f"  line cost = {qty} x {c} = {round(qty * c, 2)}  ->  line margin = {round(rev - qty * c, 2)}")
        else:
            print(f"  no cost version covers {d} -> this line is UNCOSTED (counts in lines/revenue only)")
        wm = cat.load_table("gold.weekly_margin").scan().to_pandas()
        week = datetime.fromisoformat(d).isocalendar()[1]
        print(f"  it belongs to the weekly_margin row (iso_week={week}, city={city}, category={category}):")
        print(wm[(wm.iso_week == week) & (wm.city == city) & (wm.category == category)].to_string(index=False))

    # ---- 3. quarantine
    print("\n=== 3. Quarantined supplier rows (cost exactly as in the file) ===")
    print(cat.load_table("quality.price_quarantine").scan().to_pandas().to_string(index=False))


if __name__ == "__main__":
    main()
