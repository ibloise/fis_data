# FIS Data

Base repository for Python data management workflows.

## Development

Create a virtual environment and install development dependencies:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

Configure the default SQLite database path in `.env`:

```bash
FIS_DB_PATH=var/fis_data.sqlite
FIS_DB_ECHO=false
```

Run tests:

```bash
pytest
```

## Raw Ingestion

Initialize a SQLite database:

```bash
fis-data init-db
```

Ingest text files into the raw layer:

```bash
fis-data ingest-text --source-name SOURCE --entity ENTITY path/to/file.txt
```

You can pass glob patterns or a directory. Directory inputs ingest direct child files;
use a recursive glob when you need nested folders:

```bash
fis-data ingest-text --source-name SOURCE --entity ENTITY "path/to/folder/*.txt"
fis-data ingest-text --source-name SOURCE --entity ENTITY path/to/folder
fis-data ingest-text --source-name SOURCE --entity ENTITY "path/to/folder/**/*.txt"
```

Excel ingestion is scaffolded as a separate raw layer command:

```bash
fis-data ingest-excel --source-name SOURCE --entity ENTITY path/to/file.xlsx
fis-data ingest-excel --source-name SOURCE --entity ENTITY path/to/folder
```

Directory inputs for Excel are recursive and only ingest OpenXML workbooks
(`.xlsx`, `.xlsm`, `.xltx`, `.xltm`). Temporary Excel lock files are ignored.
Empty or invalid workbooks are skipped by default; use `--strict` to make the
command fail when one is found.

Parse pending Microb raw text rows into `payload_json`:

```bash
fis-data parse-microb --entity microb
fis-data parse-microb --entity microb --file-id 1
```

## Domain Materialization

Materialize parsed PCR payloads into normalized domain tables:

```bash
fis-data materialize --entity pcr
fis-data materialize --entity pcr --file-id 1
fis-data materialize --entity pcr --file-id 1 --reprocess-all
```

PCR workbooks are grouped into a run using the `File Name` value parsed from
their `Run Information` sheet. Files without that value are recorded as load
errors. Valid payloads are loaded even when the source file also contains parse
errors. Re-running the command skips loaded rows; `--reprocess-all` replaces the
domain facts derived from the selected files.

The normalized PCR layer contains runs, source files, run attributes, wells,
Cq results, and one RFU measurement per well and cycle or temperature. Every
fact retains its source file, raw Excel row, and ETL run identifiers. Well
identifiers are canonicalized as `A01` through `H12` across all PCR profiles.

## PCR Analytical Model

Build the rebuildable sample, melting interpretation, QC, episode, and preferred
result layers after PCR materialization:

```bash
fis-data derive-pcr --algorithm-version melting-v1 --rebuild
```

The default melting parameters use normalized-curve peak prominence `0.15`, a
minimum temperature of `70`, a raw derivative RFU threshold of `100`, Cq limits
`5` and `40`, and a `±0.5` temperature window around OXA48 (`79.5`), VIM (`82`),
NDM (`86`), and KPC (`88`). Screening curves can produce multiple target calls;
specific assays select one best peak for their canonical target.

QC is target-specific for panel assays. For Screening assays, controls are
global: at least one positive-control call must be positive, and every
negative-control call must be negative.

Current analytical results are exposed through `v_pcr_algorithmic_result`,
`v_pcr_result_history`, `v_pcr_run_qc`, `v_pcr_episodes`, and
`v_pcr_review_queue`.

The complete data dictionary for the 17 physical `pcr_*` tables and the public
`v_pcr_*` views is available in
[`docs/pcr-tables.md`](docs/pcr-tables.md).
Functional decisions that affect PCR interpretation are recorded in
[`docs/pcr-decisions.md`](docs/pcr-decisions.md).

## PCR Review Frontend

Start the read-only FastAPI application against the local database:

```bash
fis-data serve --db-path var/fis_data.sqlite
```

Open `http://127.0.0.1:8000` to review target QC and inspect amplification
(Cq/Ct) or melting curves by run, sample, and target. The browser uses the
same-origin `/api/pcr/*` endpoints; targets are rendered with stable colours.

## PCR Analytical Review

La guía completa de conceptos, comandos, overrides y flujo operativo está en
[`docs/pcr-review.md`](docs/pcr-review.md), incluyendo el detalle de los
estados `OPEN`/`RESOLVED` y de las causas analíticas que generan necesidades de
revisión para pozos, runs, episodios, QC y resultados.

Review items without updating analytical tables directly:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite list --format table
fis-data pcr-review --db-path var/fis_data.sqlite show \
  --type RESULT --subject sample:123:target:KPC
```

Every resolution or correction requires an explicit reviewer, reason,
derivation ID, and the current fingerprint printed by `list` or `show`. Commands under
`pcr-review override` provide validated corrections for wells, runs, episodes,
target QC, and final results. `resolve`, `reopen`, `revoke`, and `history` append
audit events; previous actions are never updated or deleted.

Apply active overrides through a new rebuildable derivation:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite apply \
  --algorithm-version melting-v1 --reviewer analyst
```

Review actions survive analytical rebuilds. Base evidence remains available in
the existing views, while `v_pcr_effective_result` exposes the effective final
call and whether it has a manual override. Use `derive-pcr --ignore-overrides`
to reproduce the unmodified algorithm output for diagnostics.
