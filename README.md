# Qafila gross margin

Submitted by: Yousif El Debakiy — academy: @SDAIAAcademy

The task, rules and grading are in [BRIEF.md](BRIEF.md).

## Quick start

Requirements: Python 3.10-3.13 and [`uv`](https://docs.astral.sh/uv/) (on Windows:
`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`, then reopen the terminal).
Run everything from the folder that contains `pyproject.toml`.

**Windows shortcut:** double-click `RUN_ON_WINDOWS.bat`. It runs the five commands below in order and stops at the first error.

```bash
uv sync                                   # environment (pinned in uv.lock)
uv run python tools/make_data.py          # data/ (synthetic course dataset, ~35 MB, gitignored)
uv run python run.py                      # data/ -> warehouse/ (DuckDB + dbt + Iceberg)
uv run python tools/check.py              # contract self-check (shape + consistency): 22/22
uv run python tools/reference_check.py    # independent re-computation in plain Python vs. the Iceberg tables
uv run python tools/show_tables.py        # look at the 3 published tables, snapshots and time travel
uv run python tools/spot_check.py         # follow one SKU and one order line from raw files to the final tables

uv run python tools/make_data.py --add-correction   # the late W39 fix arrives
uv run python run.py                                # tables update in place (new Iceberg snapshot)
uv run python tools/make_data.py --seed 11 --out data_11 && uv run python run.py --data-dir data_11 --warehouse wh_11
```

More detail: `01_project_explanation.pdf` (what the project does and why, rule by rule) and `02_how_to_run.pdf`
(step-by-step run guide with the expected output of every command and troubleshooting).

**Troubleshooting:** `uv` not recognized -> reopen the terminal; `No pyproject.toml found` -> you are one folder too high,
`cd` into the project folder; `qafila_oltp.sqlite not found` -> run `make_data.py` first; permission errors -> extract to a
normal writable folder (not `Program Files`).

## Design

`files → DuckDB raw (Python) → dbt staging (views) → dbt silver / quality / gold (tables) → Iceberg (pyiceberg)`

| Model                        | Layer   | Grain (one row per ...)                                                     | Materialization |
| ---------------------------- | ------- | --------------------------------------------------------------------------- | --------------- |
| `stg_products`, `stg_stores` | staging | product / store (SKU trimmed)                                               | view            |
| `stg_orders`                 | staging | order; `order_date` = `created_at` UTC + 3 h                                | view            |
| `stg_order_items`            | staging | order line; `line_revenue_sar` = `round(qty*price - discount, 2)`           | view            |
| `stg_supplier_prices`        | staging | supplier file row (`source_file`, `file_row`); cost parsed, nothing dropped | view            |
| `price_quarantine`           | quality | rejected supplier row                                                       | table           |
| `product_cost_history`       | silver  | SKU × cost version (SCD2)                                                   | table           |
| `weekly_margin`              | gold    | ISO week × city × category with ≥ 1 counted line                            | table           |

- **Raw** keeps every value as text, plus `source_file` and `file_row`. Each raw table is rebuilt (`CREATE OR REPLACE`)
  from the files on every run, so raw always mirrors `data_dir` and a re-run cannot duplicate rows.
  SQLite is read with Python's `sqlite3` so nothing has to be downloaded (no DuckDB extension).
- **Costs** are parsed in one place (`stg_supplier_prices`): `coalesce(unit_cost, cost_sar)`, Arabic-Indic digits and `٫` are
  translated, a decimal comma becomes a point, `try_cast` gives NULL for anything that is not a number.
- **Publishing:** `publish()` creates each Iceberg table once and afterwards only updates it in place.

**How the SCD2 history is built:** accepted rows (not quarantined) are de-duplicated per (SKU, `valid_from`), the file whose
name sorts last wins, so `W39_fix` replaces `W39` with no special case. Then a row is kept only if its cost differs from the
SKU's previous quoted cost (`lag`), so a product missing from a week simply keeps its version, and a repeated unchanged cost
does not open a new one. `valid_to` is `lead(valid_from)`; the newest version has `valid_to` empty and `is_current`.

**How each order line finds its cost:** a left join to `product_cost_history` on SKU with
`valid_from <= order_date < coalesce(valid_to, ∞)`. Versions of a SKU never overlap, so no line is duplicated, and a line with
no version (an order before the SKU's first quote) stays in `lines`/`revenue_sar` as `uncosted_lines`.

**Decision on a case the brief leaves open:** the "last file wins" de-duplication runs on rows that were _not_ quarantined
(the brief says quarantined rows are ignored everywhere else). With the course data this makes no difference.

## Data problems I found (seed 2026)

| Problem                                                | Where                                  | Rows affected                       | What I did                                                           |
| ------------------------------------------------------ | -------------------------------------- | ----------------------------------- | -------------------------------------------------------------------- |
| Decimal comma (`9,49`)                                 | W38 `unit_cost`                        | 126 of 160                          | comma → point before cast                                            |
| Arabic-Indic digits (`٩٫٤٩`)                           | W38 `unit_cost`                        | 34 of 160                           | `translate` digits and `٫`                                           |
| Byte-order mark                                        | W38 header                             | 1 file                              | read as `utf-8-sig`, first column name stays clean                   |
| Column renamed `unit_cost` → `cost_sar` (+ `currency`) | W39                                    | 160                                 | `coalesce` of both columns; raw keeps union of columns               |
| Zero / negative cost                                   | W39                                    | 4                                   | `price_quarantine`, reason `invalid_cost` (fixed later by `W39_fix`) |
| SKU not sold by Qafila (`QF-NEW-…`)                    | W39                                    | 3                                   | `price_quarantine`, reason `unknown_sku`                             |
| Trailing spaces in SKU                                 | `products`                             | 2                                   | `trim` on both sides of every SKU comparison                         |
| Missing products in a weekly file                      | every file lists only 160 of ~350 SKUs | —                                   | previous cost kept (a version starts only when the cost changes)     |
| Same SKU + `valid_from` in two files                   | W39 vs `W39_fix`                       | 9                                   | last file name wins                                                  |
| Quantity ≤ 0                                           | `order_items`                          | 142 lines (129 on delivered orders) | excluded                                                             |
| Orders not delivered                                   | `orders`                               | 5,347 of 45,766                     | excluded (cancelled, refunded, failed_delivery)                      |
| Orders before 2026-08-31 (Riyadh date)                 | `orders`                               | 69,998 delivered lines              | excluded                                                             |
| Order lines with no cost version yet                   | `weekly_margin`                        | 20,626 of 82,404 counted lines      | `uncosted_lines`, kept in revenue                                    |

## Tests (34 dbt tests, all passing)

| Test                                 | On                                                                                          | What it protects against                                                                  |
| ------------------------------------ | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `unique` + `not_null`                | `stg_products.sku`, `product_id`, `stg_orders.order_id`                                     | a duplicated SKU would fan out every cost join                                            |
| `not_null`                           | order date, cost, `valid_from`, `iso_week`, `lines`, `revenue_sar`, `source_file`, `reason` | unparsable dates or costs leaking into the models                                         |
| `relationships`                      | `product_cost_history.sku` → `stg_products.sku`                                             | a cost version for a SKU Qafila does not sell                                             |
| `accepted_values`                    | `price_quarantine.reason`                                                                   | a reason outside `unknown_sku` / `invalid_cost`                                           |
| `history_one_current_per_sku`        | silver                                                                                      | zero or two current versions; `valid_to` empty ⇔ `is_current`                             |
| `history_versions_are_contiguous`    | silver                                                                                      | overlap or gap between versions; duplicate (sku, `valid_from`)                            |
| `history_new_version_only_on_change` | silver                                                                                      | a version opened with an unchanged cost; non-positive costs                               |
| `margin_grain_is_unique`             | gold                                                                                        | one row per week × city × category                                                        |
| `margin_adds_up`                     | gold                                                                                        | `margin = costed_revenue − cost`; costed ≤ revenue; uncosted ≤ lines                      |
| `margin_covers_every_eligible_line`  | gold                                                                                        | the point-in-time join losing or duplicating lines (line count and revenue equal staging) |
| `quarantine_rows_are_not_in_history` | silver                                                                                      | quarantined rows used anywhere else                                                       |

`tools/reference_check.py` recomputes the three tables from the raw files in plain Python (no SQL) and compares them with the
Iceberg tables: 0 differences on seed 2026 and seed 11, before and after the correction.

## Re-runs and the correction

- **Second run, same data:** `run.py` prints `unchanged ... no new snapshot` for the three tables; the snapshot lists do not grow.
  `publish()` compares the new rows with the table and writes only when they differ.
- **Correction** (`W39_fix.csv`, 9 rows): `product_cost_history` 633 → 637 versions, updated _in place_ (a single `overwrite`
  commit; the table is never dropped). `weekly_margin` snapshots for `gold.weekly_margin` (seed 2026, my run):

| Snapshot                                      | Operation | `cost_sar` total | `margin_sar` total |
| --------------------------------------------- | --------- | ---------------- | ------------------ |
| `8222429492191514699` (before the correction) | append    | 1,654,199.98     | 801,016.88         |
| `4626646759077178787` (after)                 | overwrite | 1,651,701.06     | 803,515.80         |

`table.scan(snapshot_id=8222429492191514699)` still returns the pre-correction numbers. (Snapshot ids differ on every run.)
`price_quarantine` did not change (the bad W39 rows are still in the W39 file), so it got no new snapshot.

## What I'd do next

With 100× more data the raw reload (`CREATE OR REPLACE` of everything) and the full-table Iceberg overwrite would break first:
I would load incrementally (only new supplier files, orders by `updated_at`), make `weekly_margin` an incremental dbt model
that recomputes only the weeks touched by a new or corrected price, and write only the changed partitions to Iceberg
(partitioned by `iso_week`). With daily supplier files, the "last file name wins" rule would need an explicit received-at
timestamp instead of relying on file names.
