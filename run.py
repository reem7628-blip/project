"""Qafila gross margin pipeline: raw files -> DuckDB -> dbt -> Iceberg.

    uv run python run.py --data-dir data --warehouse warehouse

Must work on an empty warehouse folder, and must be safe to run again on the same one.
The grader runs it on a dataset you haven't seen, so never hard-code file names, row counts or SKUs.
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog

DBT_DIR = Path(__file__).resolve().parent / "dbt"


def get_catalog(warehouse: Path) -> SqlCatalog:
    """The Iceberg catalog the grader reads. Don't change the name, the URI or the warehouse."""
    warehouse.mkdir(parents=True, exist_ok=True)
    return SqlCatalog("qafila", uri=f"sqlite:///{(warehouse / 'catalog.db').as_posix()}", warehouse=warehouse.resolve().as_posix())


APP_TABLES = ["orders", "order_items", "products", "stores"]
# Columns of raw.supplier_prices. unit_cost (weeks 36-38) and cost_sar (week 39 on) both exist: the dbt
# staging model coalesces them. Any other column a future file brings is kept too.
SUPPLIER_BASE_COLS = ["source_file", "file_row", "supplier_id", "sku", "valid_from", "unit_cost", "cost_sar"]


def _read_supplier_files(files: list[Path]) -> pa.Table:
    """Read every supplier CSV as text (no parsing here: raw means raw), tagging each row with its file.

    utf-8-sig drops the byte-order mark that would otherwise glue itself to the first column name.
    Files may have different columns (renamed or added), so we take the union and fill gaps with NULL.
    """
    rows: list[dict] = []
    columns: list[str] = []
    for f in files:
        with f.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for c in reader.fieldnames or []:
                c = c.strip()
                if c not in columns:
                    columns.append(c)
            for i, r in enumerate(reader, start=1):
                row = {(k or "").strip(): v for k, v in r.items()}
                row["source_file"], row["file_row"] = f.name, i
                rows.append(row)
    all_cols = list(dict.fromkeys(SUPPLIER_BASE_COLS + columns))
    data = {c: [r.get(c) for r in rows] for c in all_cols}
    data["file_row"] = pa.array(data["file_row"], type=pa.int64())
    for c in all_cols:
        if c != "file_row":
            data[c] = pa.array(data[c], type=pa.string())
    return pa.table(data)


def ingest(data_dir: Path, db: Path) -> None:
    """Step 1: land the sources in DuckDB, schema `raw`, exactly as they arrive.

    - From data_dir/qafila_oltp.sqlite: orders, order_items, products, stores.
    - Every data_dir/suppliers/prices_*.csv, keeping the file name of each row.

    Running this twice must not duplicate anything: each raw table is rebuilt from the files
    (CREATE OR REPLACE), so raw always mirrors exactly what is in data_dir right now.
    SQLite is read with Python's sqlite3 (not DuckDB's sqlite extension) so nothing is downloaded.
    """
    data_dir = Path(data_dir)
    with duckdb.connect(str(db)) as con:  # closes the file before dbt opens it
        con.sql("CREATE SCHEMA IF NOT EXISTS raw")

        sqlite_path = data_dir / "qafila_oltp.sqlite"
        if not sqlite_path.exists():
            raise FileNotFoundError(f"{sqlite_path} not found: run tools/make_data.py first")
        with closing(sqlite3.connect(str(sqlite_path))) as src:  # plain path: works with spaces/drive letters on Windows
            for table in APP_TABLES:
                df = pd.read_sql_query(f"SELECT * FROM {table}", src)  # noqa: S608 - fixed table names
                con.register("_src", df)
                con.sql(f"CREATE OR REPLACE TABLE raw.{table} AS SELECT * FROM _src")
                con.unregister("_src")

        files = sorted((data_dir / "suppliers").glob("prices_*.csv"))
        con.register("_prices", _read_supplier_files(files))
        con.sql("CREATE OR REPLACE TABLE raw.supplier_prices AS SELECT * FROM _prices")
        con.unregister("_prices")


def transform(db: Path) -> None:
    """Steps 2-4: build and test the dbt project in dbt/ against the DuckDB file.

    dbt runs in its own process: in this one it would keep the DuckDB file open and block publish().
    """
    env = {**os.environ, "QAFILA_DUCKDB": str(db.resolve())}
    cmd = [sys.executable, "-c", "from dbt.cli.main import cli; cli()",
           "build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]
    if subprocess.run(cmd, env=env).returncode != 0:
        raise SystemExit("dbt build failed")


def _sql(name: str) -> str:
    """Final, deterministic SELECT for one published table (ordered so identical data gives identical rows)."""
    return {
        "silver.product_cost_history": """
            SELECT sku::VARCHAR AS sku, unit_cost_sar::DOUBLE AS unit_cost_sar, valid_from::DATE AS valid_from,
                   valid_to::DATE AS valid_to, is_current::BOOLEAN AS is_current
            FROM silver.product_cost_history ORDER BY sku, valid_from""",
        "gold.weekly_margin": """
            SELECT iso_week::INTEGER AS iso_week, city::VARCHAR AS city, category::VARCHAR AS category,
                   lines::INTEGER AS lines, revenue_sar::DOUBLE AS revenue_sar,
                   costed_revenue_sar::DOUBLE AS costed_revenue_sar, cost_sar::DOUBLE AS cost_sar,
                   margin_sar::DOUBLE AS margin_sar, uncosted_lines::INTEGER AS uncosted_lines
            FROM gold.weekly_margin ORDER BY iso_week, city, category""",
        "quality.price_quarantine": """
            SELECT source_file::VARCHAR AS source_file, sku::VARCHAR AS sku, valid_from::DATE AS valid_from,
                   raw_cost::VARCHAR AS raw_cost, reason::VARCHAR AS reason
            FROM quality.price_quarantine ORDER BY source_file, sku, valid_from, raw_cost, reason""",
    }[name]


PUBLISHED = ["silver.product_cost_history", "gold.weekly_margin", "quality.price_quarantine"]


def publish(db: Path, warehouse: Path) -> None:
    """Step 5: publish three dbt models to Iceberg (see the brief for the exact columns).

        silver.product_cost_history   gold.weekly_margin   quality.price_quarantine

    Update existing tables in place, never drop and recreate them: the table's snapshots are
    how we see what a number was before the supplier's correction arrived.

    - First run: the table is created and filled (snapshot 1).
    - Same data again: the new rows equal what is already in the table, so nothing is written
      and no snapshot is added ("changes nothing").
    - Changed data (e.g. the correction file): ONE new snapshot replaces the table's rows
      (delete old files + add new file in a single commit); the older snapshots stay readable.
    """
    from pyiceberg.io.pyarrow import _dataframe_to_data_files

    catalog = get_catalog(warehouse)
    with duckdb.connect(str(db), read_only=True) as con:
        new = {name: con.sql(_sql(name)).to_arrow_table() for name in PUBLISHED}

    for name in PUBLISHED:
        namespace = name.split(".")[0]
        catalog.create_namespace_if_not_exists(namespace)
        data = new[name]
        if not catalog.table_exists(name):
            table = catalog.create_table(name, schema=data.schema)
            table.append(data)
            print(f"  {name}: created, {data.num_rows} rows")
            continue

        table = catalog.load_table(name)
        current = table.scan().to_arrow().select(data.column_names).cast(data.schema)
        keys = [(c, "ascending") for c in data.column_names]
        if current.sort_by(keys).equals(data.sort_by(keys)):
            print(f"  {name}: unchanged ({data.num_rows} rows), no new snapshot")
            continue

        old_files = [task.file for task in table.scan().plan_files()]
        with table.transaction() as txn:
            with txn.update_snapshot().overwrite() as overwrite:
                for f in old_files:
                    overwrite.delete_data_file(f)
                for f in _dataframe_to_data_files(table_metadata=txn.table_metadata, write_uuid=overwrite.commit_uuid,
                                                  df=data, io=table.io):
                    overwrite.append_data_file(f)
        print(f"  {name}: updated in place, {data.num_rows} rows (was {current.num_rows})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    args = ap.parse_args()

    args.warehouse.mkdir(parents=True, exist_ok=True)
    db = args.warehouse / "qafila.duckdb"
    ingest(args.data_dir, db)
    transform(db)
    publish(db, args.warehouse)
    print(f"done: {args.warehouse.resolve()}")


if __name__ == "__main__":
    main()
