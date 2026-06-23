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
