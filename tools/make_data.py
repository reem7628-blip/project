"""Generate the project data.

    uv run python tools/make_data.py                          # data/ (seed 2026, the course dataset)
    uv run python tools/make_data.py --seed 11 --out data_11  # a different dataset with the same shape and issues
    uv run python tools/make_data.py --add-correction         # the supplier's late fix for week 39 arrives

Test your pipeline on more than one seed: it's graded on a dataset you haven't seen.
"""

from __future__ import annotations

import argparse
import csv
import random
import sqlite3
from contextlib import closing
from pathlib import Path

import qafila_data

CORRECTION = "prices_2026-W39_fix.csv"


def add_correction(data: Path) -> Path:
    """Write the supplier's correction for week 39: the zero/negative costs fixed, plus a few renegotiated prices."""
    seed = int((data / "_SEED").read_text())
    rng = random.Random(seed * 1000 + 39)
    with closing(sqlite3.connect(data / "qafila_oltp.sqlite")) as con:
        price = {sku.strip(): p for sku, p in con.execute("SELECT sku, unit_price FROM products")}
    with (data / "suppliers" / "prices_2026-W39.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    fixed = []
    for r in rows:
        if r["sku"] in price and float(r["cost_sar"]) <= 0:
            fixed.append({**r, "cost_sar": round(price[r["sku"]] * rng.uniform(0.55, 0.8), 2)})
    good = [r for r in rows if r["sku"] in price and float(r["cost_sar"]) > 0]
    for r in rng.sample(good, 5):
        fixed.append({**r, "cost_sar": round(float(r["cost_sar"]) * rng.uniform(0.88, 0.97), 2)})

    path = data / "suppliers" / CORRECTION
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(fixed)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=qafila_data.SEED)
    ap.add_argument("--out", type=Path, default=qafila_data.RAW, help="output folder (default data/)")
    ap.add_argument("--add-correction", action="store_true", help=f"only add suppliers/{CORRECTION} to existing data")
    args = ap.parse_args()
    out = args.out.resolve()

    if args.add_correction:
        print(f"wrote {add_correction(out)}")
        return
    qafila_data.main(force=True, seed=args.seed, raw=out)
    (out / "_SEED").write_text(f"{args.seed}\n")


if __name__ == "__main__":
    main()
