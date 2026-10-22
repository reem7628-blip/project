# Project: What does Qafila actually earn?

**Individual · one day · submitted on GitHub · graded automatically on data you haven't seen**

Qafila's finance team can see revenue, but not **gross margin**: what's left after paying suppliers. Suppliers send a price file every week. The files are messy, they change shape, some costs are wrong, and one supplier sends a correction late. Your job is to build the pipeline that turns these files and the app database into a trustworthy weekly margin table, and that keeps giving the right answer when files are re-processed or corrected.

You'll use what you practised in the labs: loading files (Labs 1.2, 1.3), SCD2 history (Lab 1.4), cleaning and modelling (Labs 2.1, 2.2), dbt and its tests (Labs 2.3, 2.4) and Iceberg (Lab 3.1).

## Setup (15 minutes)

1. **Fork** [SDAIAAcademy/qafila-margin-project](https://github.com/SDAIAAcademy/qafila-margin-project) to your own GitHub account and keep the fork **public**.
2. At the top of your fork's `README.md`, replace `<your name>` in the line

       Submitted by: <your name> — academy: @SDAIAAcademy

3. Clone your fork, then:

```bash
uv sync                                        # the environment: duckdb, polars, pyarrow, dbt-duckdb, pyiceberg
uv run python tools/make_data.py               # data/ (≈35 MB, gitignored)
uv run python run.py                           # your pipeline (fails until you write it)
uv run python tools/check.py                   # checks your outputs against the contract below
```

Don't add packages: the grader runs your code in this exact environment.

## The data

| Source | Where | What you need |
|---|---|---|
| App database (SQLite) | `data/qafila_oltp.sqlite` | `orders`, `order_items`, `products`, `stores` |
| Supplier price files | `data/suppliers/prices_2026-W36.csv` … `W39.csv` | One file per week, listing the products whose cost was quoted that week |
| The late correction | `data/suppliers/prices_2026-W39_fix.csv` | Arrives later: `uv run python tools/make_data.py --add-correction` |

`data/DATA_ISSUES.txt` lists the known problems. You'll meet them in the supplier files: decimal commas, Arabic-Indic digits, a byte-order mark, a renamed column, zero or negative costs, SKUs that Qafila doesn't sell, and SKUs with trailing spaces.

## What to build

| Step | Build | Time |
|---|---|---|
| 1 · Land | `ingest()` in `run.py`: the four app tables and every supplier file into DuckDB schema `raw`, keeping each row's file name | 1 h |
| 2 · Clean | dbt staging models: parse costs, trim SKUs, convert order times to Riyadh dates | 1 h |
| 3 · History | `product_cost_history`: an SCD2 table of supplier costs, plus `price_quarantine` for rows you can't use | 1.5 h |
| 4 · Margin | `weekly_margin`: each order line costed at the price **valid on its order date** | 1.5 h |
| 5 · Publish | `publish()` in `run.py`: the three tables into Iceberg, updated in place so their history is kept | 45 min |
| 6 · Prove it | At least 6 dbt tests; re-run safely; add the correction and re-run; write your README | 45 min |

`run.py` already handles the arguments, runs dbt and connects to the Iceberg catalog. You write `ingest()`, `publish()` and the dbt project in `dbt/`.

## Rules

Your numbers must follow these rules exactly. The grader computes the right answer with them.

**Supplier costs**
1. A row's cost is in `unit_cost` or, from week 39 on, `cost_sar`. Read `9,49` and `٩٫٤٩` as 9.49.
2. Compare SKUs after trimming spaces, on both the supplier side and the product side.
3. **Quarantine** a row if its SKU isn't in `products` (reason `unknown_sku`). Otherwise quarantine it if its cost isn't a positive number (reason `invalid_cost`). Quarantined rows are ignored everywhere else.
4. A weekly file only lists the products quoted that week. A product that's missing from a file **keeps its previous cost**.
5. If the same SKU and `valid_from` appear in more than one file, the row from the file whose name sorts **last** wins. This means `prices_2026-W39_fix.csv` replaces `prices_2026-W39.csv`.
6. **SCD2:** a new version starts only when a SKU's cost **changes**. `valid_to` is the next version's `valid_from` (exclusive) and is empty for the current version. Round costs to 2 decimals.

**Margin**
7. An order line counts when the order's `status` is `delivered`, its `quantity` is greater than 0, and its **order date** is on or after **2026-08-31**, the first supplier week. The order date is `created_at` (UTC) converted to Riyadh time (UTC+3).
8. Line revenue is `round(quantity × unit_price − discount, 2)`.
9. A line's cost is `quantity × unit_cost_sar` of the version where `valid_from ≤ order date < valid_to`. A line with no valid version is **uncosted**. It still counts in `lines` and `revenue_sar`, but not in the cost columns.
10. City comes from the order's store, and category from the product. `iso_week` is the ISO week of the order date.

## Output contract

Publish exactly these three Iceberg tables in the catalog `get_catalog()` returns. Extra columns are fine.

**`silver.product_cost_history`**: one row per SKU per cost version

| Column | Type | Meaning |
|---|---|---|
| `sku` | string | Trimmed |
| `unit_cost_sar` | double | Cost in this version |
| `valid_from` | date | First day this cost applies |
| `valid_to` | date, empty when current | First day it no longer applies |
| `is_current` | boolean | True for the newest version |

**`gold.weekly_margin`**: one row per `iso_week` × `city` × `category` that has at least one counted line

| Column | Type | Meaning |
|---|---|---|
| `iso_week` | int | ISO week of the order date |
| `city`, `category` | string | |
| `lines` | int | Counted order lines |
| `revenue_sar` | double | Sum of line revenue, rounded to 2 decimals |
| `costed_revenue_sar` | double | Revenue of lines that have a cost |
| `cost_sar` | double | Sum of line costs |
| `margin_sar` | double | `costed_revenue_sar − cost_sar` |
| `uncosted_lines` | int | Lines without a valid cost |

**`quality.price_quarantine`**: one row per rejected supplier row

| Column | Type | Meaning |
|---|---|---|
| `source_file` | string | For example `prices_2026-W39.csv` |
| `sku` | string | |
| `valid_from` | date | |
| `raw_cost` | string | The cost exactly as it appeared in the file |
| `reason` | string | `unknown_sku` or `invalid_cost` |

**Behaviour**
- `uv run python run.py --data-dir <folder> --warehouse <folder>` works on an empty warehouse, and running it again on the same data **changes nothing** in the three tables.
- When the correction file appears and you run again, the tables update. **Update the Iceberg tables in place, never drop and recreate them:** an older snapshot of `gold.weekly_margin` must still show the numbers from before the correction.
- Nothing is hard-coded: file names, weeks (except the 2026-08-31 start), SKUs, row counts. Test on a second dataset: `uv run python tools/make_data.py --seed 11 --out data_11`.

`tools/check.py` checks this contract and the internal consistency of your tables. Passing it doesn't mean your margins are right: only following the rules does.

## How it's graded (100 points)

The grader clones your repository and runs your pipeline on a **new dataset** generated with a seed you don't know.

| Check | Points | How |
|---|---|---|
| Runs | 10 | `run.py` exits cleanly and all three tables exist |
| Cost history | 15 | Share of SKUs whose versions match the right answer exactly |
| Weekly margin | 20 | Share of rows that match (within 0.02 SAR) |
| Quarantine | 5 | The right rows with the right reasons |
| Idempotent | 10 | A second run on the same data leaves all three tables identical |
| Correction | 10 | After the correction file arrives, history and margin match again |
| Time travel | 5 | An older snapshot of `gold.weekly_margin` still holds your pre-correction numbers |
| dbt tests | 5 | At least 6 tests, all passing |
| Code and modelling | 10 | Clear layers, the grain written down, readable SCD2 and point-in-time logic, no hard-coding |
| README | 5 | Your design, the data problems you found (with counts), and the evidence for re-runs and time travel |
| Working practice | 5 | Commits through the day with meaningful messages; no data or warehouse files committed |

The first 80 points are measured automatically. The last 20 come from a code review.

## Submit

1. Push your final commit to your public fork, then make sure `uv sync && uv run python tools/make_data.py && uv run python run.py` works from a fresh clone.
2. Check that your name is on the `Submitted by:` line of your `README.md`.
3. Put your name and your fork's URL (`https://github.com/<you>/qafila-margin-project`) in the Google Sheet. Your last commit before the deadline is graded.

This is individual work. Write your own code and README. If you used an AI assistant, say how in your README. Every fork is public, so everyone's code is visible. Submissions are compared with each other, and copied SQL shows up.
