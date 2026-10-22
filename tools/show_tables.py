"""Show the three published Iceberg tables and their snapshot history.

    uv run python tools/show_tables.py                  # default warehouse/
    uv run python tools/show_tables.py --warehouse wh_11

Prints: row counts, the first rows of each table, every snapshot (id, time, operation) and, for
gold.weekly_margin, the totals stored in each snapshot (time travel: before vs. after the correction).
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
from pyiceberg.catalog.sql import SqlCatalog

ROOT = Path(__file__).resolve().parents[1]
TABLES = ["silver.product_cost_history", "gold.weekly_margin", "quality.price_quarantine"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse", type=Path, default=ROOT / "warehouse")
    ap.add_argument("--rows", type=int, default=8, help="rows to print per table")
    a = ap.parse_args()
    wh = a.warehouse.resolve()
    cat = SqlCatalog("qafila", uri=f"sqlite:///{(wh / 'catalog.db').as_posix()}", warehouse=wh.as_posix())
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)

    for name in TABLES:
        t = cat.load_table(name)
        df = t.scan().to_pandas()
        print(f"\n=== {name}: {len(df)} rows ===")
        print(df.head(a.rows).to_string(index=False))
        print(f"snapshots ({len(t.snapshots())}):")
        for s in t.snapshots():
            when = datetime.fromtimestamp(s.timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {s.snapshot_id}  {when}  {s.summary.operation.value}")

    t = cat.load_table("gold.weekly_margin")
    print("\n=== Time travel: gold.weekly_margin totals inside each snapshot ===")
    for s in t.snapshots():
        d = t.scan(snapshot_id=s.snapshot_id).to_pandas()
        print(f"  snapshot {s.snapshot_id}: rows={len(d)}  revenue={d.revenue_sar.sum():,.2f}  "
              f"cost={d.cost_sar.sum():,.2f}  margin={d.margin_sar.sum():,.2f}")


if __name__ == "__main__":
    main()
