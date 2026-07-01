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

Current analytical results are exposed through `v_pcr_current_result`,
`v_pcr_result_history`, `v_pcr_run_qc`, `v_pcr_episodes`, and
`v_pcr_review_queue`.
