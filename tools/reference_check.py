"""Independent cross-check of the published tables.

    uv run python tools/reference_check.py --data-dir data --warehouse warehouse

tools/check.py only checks shape and consistency. This script recomputes the three tables from the raw
files with plain Python (sqlite3 + csv + datetime, no SQL, no dbt, no DuckDB) following BRIEF.md rule by rule,
then compares the result with what is in Iceberg. If both agree, the SQL implements the rules.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from pyiceberg.catalog.sql import SqlCatalog

START = date(2026, 8, 31)
ARABIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789.")


def parse_cost(text: str | None) -> float | None:
    if text is None:
        return None
    t = text.strip().translate(ARABIC).replace("٬", "").replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if v == v and abs(v) != float("inf") else None


def expected(data: Path):
    with closing(sqlite3.connect(data / "qafila_oltp.sqlite")) as con:
        products = {pid: (sku.strip(), cat) for pid, sku, cat in con.execute("SELECT product_id, sku, category FROM products")}
        city = dict(con.execute("SELECT store_id, city FROM stores"))
        orders = {oid: (sid, st, created) for oid, sid, st, created in
                  con.execute("SELECT order_id, store_id, status, created_at FROM orders")}
        items = list(con.execute("SELECT order_id, product_id, quantity, unit_price, discount FROM order_items"))
    known = {s for s, _ in products.values()}

    # --- supplier rows: quarantine + accepted
    quarantine, accepted = [], {}  # accepted[(sku, valid_from)] = (file, cost)
    for f in sorted((data / "suppliers").glob("prices_*.csv")):  # name order = "last file wins"
        with f.open(newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                sku = r["sku"].strip()
                raw = r.get("unit_cost") or r.get("cost_sar") or ""
                cost = parse_cost(raw)
                vf = date.fromisoformat(r["valid_from"].strip())
                if sku not in known:
                    quarantine.append((f.name, sku, vf, raw, "unknown_sku"))
                elif cost is None or cost <= 0:
                    quarantine.append((f.name, sku, vf, raw, "invalid_cost"))
                else:
                    accepted[(sku, vf)] = round(cost, 2)  # later file overwrites earlier: rule 5

    # --- SCD2
    by_sku = defaultdict(list)
    for (sku, vf), cost in accepted.items():
        by_sku[sku].append((vf, cost))
    history = {}
    for sku, rows in by_sku.items():
        rows.sort()
        versions = []
        for vf, cost in rows:
            if not versions or versions[-1][1] != cost:
                versions.append((vf, cost))
        history[sku] = [(vf, cost, versions[i + 1][0] if i + 1 < len(versions) else None)
                        for i, (vf, cost) in enumerate(versions)]

    # --- margin
    agg = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0])  # lines, revenue, costed_rev, cost, uncosted
    for oid, pid, qty, price, disc in items:
        sid, status, created = orders[oid]
        d = (datetime.fromisoformat(created) + timedelta(hours=3)).date()
        if status != "delivered" or qty <= 0 or d < START:
            continue
        sku, cat = products[pid]
        rev = round(qty * price - disc, 2)
        cost = next((c for vf, c, vt in history.get(sku, []) if vf <= d and (vt is None or d < vt)), None)
        a = agg[(d.isocalendar()[1], city[sid], cat)]
        a[0] += 1
        a[1] += rev
        if cost is None:
            a[4] += 1
        else:
            a[2] += rev
            a[3] += qty * cost
    return quarantine, history, agg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    a = ap.parse_args()
    wh = a.warehouse.resolve()
    cat = SqlCatalog("qafila", uri=f"sqlite:///{(wh / 'catalog.db').as_posix()}", warehouse=wh.as_posix())
    quarantine, history, agg = expected(a.data_dir)
    ok = True

    got_q = sorted((r["source_file"], r["sku"], r["valid_from"], r["raw_cost"], r["reason"])
                   for r in cat.load_table("quality.price_quarantine").scan().to_arrow().to_pylist())
    exp_q = sorted(quarantine)
    print(f"quarantine: expected {len(exp_q)}, got {len(got_q)} ->", "OK" if got_q == exp_q else "MISMATCH")
    ok &= got_q == exp_q

    got_h = {}
    for r in cat.load_table("silver.product_cost_history").scan().to_arrow().to_pylist():
        got_h.setdefault(r["sku"], []).append((r["valid_from"], r["unit_cost_sar"], r["valid_to"]))
    got_h = {k: sorted(v) for k, v in got_h.items()}
    bad = [s for s in set(got_h) | set(history) if got_h.get(s) != history.get(s)]
    print(f"history: {sum(len(v) for v in history.values())} versions for {len(history)} SKUs; "
          f"SKUs that differ: {len(bad)} ->", "OK" if not bad else f"MISMATCH {bad[:5]}")
    ok &= not bad

    got_m = {(r["iso_week"], r["city"], r["category"]): r for r in cat.load_table("gold.weekly_margin").scan().to_arrow().to_pylist()}
    diffs = 0
    for k in set(got_m) | set(agg):
        e, g = agg.get(k), got_m.get(k)
        if e is None or g is None:
            diffs += 1
            continue
        exp_vals = (e[0], round(e[1], 2), round(e[2], 2), round(e[3], 2), round(e[2] - e[3], 2), e[4])
        got_vals = (g["lines"], g["revenue_sar"], g["costed_revenue_sar"], g["cost_sar"], g["margin_sar"], g["uncosted_lines"])
        if exp_vals[0] != got_vals[0] or exp_vals[5] != got_vals[5] or any(abs(x - y) > 0.02 for x, y in zip(exp_vals[1:5], got_vals[1:5])):
            diffs += 1
    print(f"weekly_margin: {len(agg)} expected rows, {len(got_m)} got, rows that differ (>0.02): {diffs} ->",
          "OK" if not diffs else "MISMATCH")
    ok &= not diffs
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
