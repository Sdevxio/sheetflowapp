# SheetFlow

SheetFlow imports an Excel workbook, checks it, and lets you explore the saved rows. The original file stays on disk. Dashboard numbers come from the database, not from a second parse of the spreadsheet. The installed app name is SheetFlow. This repository folder is unchanged.

There is no sample business workbook in this repository. `samples/synthetic_demo.xlsx` and `samples/synthetic_demo.xls` are labeled synthetic data for the demo and the tests. The sheets are not related and are never joined.

## Assumptions

- One person uses the app on their own machine. There is no login. Compose publishes ports on `127.0.0.1` only.
- The default locale is `en-US`: ambiguous dates such as `01/02/2024` are read as month/day/year and flagged. `de-DE` uses day/month/year and `.` as the thousands separator.
- A row is skipped when its first non-empty cell is exactly `Total` or `Grand Total`. Empty rows are counted and not stored.
- Duplicate rows and duplicate identifier values are kept and reported.
- Integer and decimal columns can be summed. Identifiers, percentages, text, dates, and booleans cannot. Percent text such as `8%` is stored as the fraction `0.08`. A numeric percent cell is kept as read.
- Leading zeros survive when Excel stored the cell as text. If Excel stored the identifier as a number, the import says so and does not invent the missing zeros.
- Formulas are not recalculated. See below.

## SheetFlow on a Mac

Build the app on the Mac she will use. This script produces an app for the processor of the machine it runs on, Apple silicon or Intel. It does not build a Windows installer. A Windows build has to be made on Windows, using the same `packaging/launcher.py`. On Windows the data folder is `%LOCALAPPDATA%\SheetFlow`.

```bash
packaging/build_macos_app.sh
open dist/SheetFlow.app
```

Double-clicking SheetFlow starts a private service on `127.0.0.1:8765` and opens the dashboard in her browser. A second click only opens the browser. Closing the browser tab leaves processing running. Quit from the Dock stops the service. The next launch finishes an import that was still processing, instead of leaving it stuck.

Her database, original workbooks, and `sheetflow.log` live in `~/Library/Application Support/SheetFlow`, separate from the app. History can download a zip of that database and those workbooks, and can restore it after confirmation. Restore waits until nothing is queued or processing.

The first open may require right-click → Open. The app is signed only on this computer, not by Apple. Fonts are stored in the app, so the dashboard does not need a network connection. She chooses a file while the app is open. SheetFlow does not watch a folder in the background.

## Local setup

Docker Compose remains available for development:

```bash
docker compose up --build
```

Open `http://127.0.0.1:5173`. The API is at `http://127.0.0.1:8000` and interactive docs are at `/docs`.

Copy `.env.example` to `.env` only if you want to override the defaults. The database password in that file is for the local Postgres container.

This workspace was verified without Docker, because Docker was not installed. The checks used Python 3.13, a local PostgreSQL 15 cluster, and the commands in the next section.

### Without Docker

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
# Postgres must already be running and the database must exist.
export DATABASE_URL=postgresql+psycopg://symphony:symphony@127.0.0.1:5432/symphony
export STORAGE_DIR="$PWD/storage"
cd backend && ../.venv/bin/alembic upgrade head
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend && npm install && npm run dev
```

Regenerate the sample workbooks with:

```bash
.venv/bin/python backend/scripts/build_samples.py
```

## Architecture

One FastAPI process serves the API and a background worker thread. The worker claims the oldest `queued` import in a normal transaction. One process handles imports, so the claim does not depend on a database-specific row lock. Parsing, validation, transformation, and the database write are separate steps:

`Upload → Inspect → Configure → Validate → Transform → Save → Summarize`

- Upload stores the file under a generated id. The original filename is only a label.
- Inspect reads sheet names, a preview, header guesses, and formula or merge notes.
- Configure stores the locale, selected sheets, header rows, and column types.
- The worker validates that configuration against the file, transforms rows, and commits them together with status `completed`.
- A failed job deletes any rows from that attempt and does not show them on the dashboard.
- On startup, imports left in `processing` are queued again and their rows are removed first, so a restart does not leave a job stuck or publish a partial dataset.
- Retrying a failed import deletes the previous rows for that id before inserting the new ones.

The installed app stores import metadata and one row per record in SQLite (`raw` and `transformed` JSON). Docker Compose can still use PostgreSQL. Alembic migration `001_initial` creates the tables for either database. Set `SHEETFLOW_DATA_DIR` to choose the SQLite file and upload folder together.

## Processing rules

| Count | Meaning |
| --- | --- |
| Preamble rows | Rows before the header. Not data. |
| Data rows inspected | Rows after the header that the reader returned. |
| Accepted | Saved and visible on the dashboard. |
| Rejected | At least one value failed validation. Raw value kept. Excluded from dashboard totals. |
| Skipped empty | Every selected cell is empty. Counted, not stored. |
| Skipped explicit | First non-empty cell is `Total` or `Grand Total`. Stored as skipped. |

These reconcile:

`data rows inspected = accepted + rejected + skipped empty + skipped explicit`

Invalid values stay invalid. They are not coerced to zero or to another type. The processing report records the locale, transform version (`1`), duration, warnings, and duplicate source rows.

### Formulas

`.xlsx` values come from pandas using the openpyxl engine, which returns Excel’s cached formula result. A second openpyxl pass records formula text (`data_type == "f"` only, so a text cell that merely starts with `=` stays text). If the cached result is empty, the cell is a warning (`FORMULA_NO_CACHE`) and the transformed value is empty. A formula that Excel calculated as blank can look the same. SheetFlow does not recalculate it.

`.xls` values come from pandas using xlrd. Formula records are located in the BIFF stream. xlrd 2.0 does not return the formula text, so the report says the text was unavailable. A formula whose stored result is an empty string is reported as missing a cache. Cached numeric results are used as-is.

Merged cells contribute the top-left value only. The other cells in the merge are left empty. External links are not opened. Macros are not executed. `.xlsm` and other extensions are rejected.

### Preview versus import

The preview grid is capped (default 50 rows, 500 characters per cell). That cap is labeled on screen. Import reads every row unless the sheet exceeds `MAX_ROWS_PER_SHEET` or `MAX_COLUMNS`. Over-limit sheets are rejected. Rows are not silently dropped.

## Limits

| Setting | Default |
| --- | --- |
| `MAX_UPLOAD_BYTES` | 20 MB |
| `MAX_ROWS_PER_SHEET` | 50,000 |
| `MAX_COLUMNS` | 200 |
| `MAX_SHEETS` | 30 |
| `PREVIEW_ROWS` | 50 |
| `STALE_JOB_SECONDS` | 1800 |

`GET /api/health` reports database, worker, and these limits. Logs are JSON and include the import id. They do not include cell values.

## Security

- Filenames are reduced to a basename and a safe character set. Files are stored as `{id}.xls` or `{id}.xlsx`.
- The bytes must match the extension (ZIP for `.xlsx`, OLE for `.xls`).
- Workbook relationship targets that leave the archive are rejected.
- CSV and Excel exports prefix text that starts with `=`, `+`, `-`, `@`, or a control character. Real numbers stay numbers.
- This build does not authenticate users. Do not expose it on a shared network until each user’s files, jobs, and rows are isolated.

## Tests

```bash
cd backend && ../.venv/bin/pytest
```

The suite covers `.xls` and `.xlsx`, header rows, leading zeros, invalid and ambiguous values, duplicates, repeated uploads, corrupt files, uncached formulas, failed jobs, retries, restart recovery, dashboard totals, and filter agreement between the table, the aggregate, and the CSV export.

## Troubleshooting

- **Upload says the workbook could not be read.** The file may be encrypted, truncated, or not actually Excel. SheetFlow does not guess past a bad file.
- **An import stays queued.** Check `GET /api/health`. The worker must be running. A process restart requeues a job that was `processing`.
- **Dashboard is empty.** Only `completed` imports are queryable. Rejected rows stay in the issues export and out of the totals.
- **Dates look swapped.** Set the locale on the setup page before processing, or enter an explicit date format. Ambiguous dates are warnings, not silent choices you cannot see.
- **Docker is missing.** Use the without-Docker steps and point `DATABASE_URL` at your Postgres, or run the Mac app, which uses SQLite.
- **The Dock icon stays after the browser tab closes.** That is the service. Quit SheetFlow from the Dock when she is finished. The log is `~/Library/Application Support/SheetFlow/sheetflow.log`.

## Limitations

- No accounts, roles, or per-user isolation.
- No business metrics beyond the column type you choose and the aggregation you select.
- Chart groups stop at 40 buckets. The headline number still uses every filtered row.
- `.xls` formula text is not recovered.
- A formula with a genuinely blank result is reported like a missing cache.
- Very large uncompressed workbooks can use more memory than the upload byte limit suggests. Row and column caps stop the import, but they are not a full zip-bomb defense.
- Docker Compose was not executed in this environment.
- The Mac app is ad-hoc signed. It is not notarized, and a Windows installer is not built from this Mac.
- SheetFlow does not watch a folder. Files are processed only while the app is open and she has chosen the workbook.
# sheetflowapp
