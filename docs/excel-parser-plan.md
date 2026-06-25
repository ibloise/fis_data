# Excel Parser Infrastructure Plan

## Summary

Build the shared infrastructure for Excel parsing before implementing a concrete
business parser. The parser flow supports:

```text
entity -> file profile -> sheet profile -> row parser
```

This first increment adds audit tables, CLI integration, progress output,
reprocessing support, and tests using generated dummy workbooks only. It does
not inspect real data files and does not implement business-specific row
parsing yet.

## Key Changes

- Add `fis-data parse-excel --entity ENTITY`.
- Support `--file-id`, `--sheet-name`, `--batch-size`, and `--reprocess-all`.
- Keep progress and summaries on `stderr`.
- Keep the final machine-readable JSON result on `stdout`.
- Add shared parser infrastructure:
  - `ExcelParseJob`
  - `RawExcelRowsRepository`
  - `EXCEL_ENTITY_PARSERS`
  - `ExcelEntityParser`, `FileProfile`, and `SheetProfile`
- Add initial stub profiles:
  - `farmacia`: one `default` file profile.
  - `pcr`: file profile selection by normalized filename/path.

Unsupported entities and stubbed profile combinations are marked explicitly in
raw rows and audit tables instead of failing silently.

## Schema Changes

Two audit/control tables are added:

- `ctl_excel_parse_file`
  - tracks one parser execution per workbook.
  - stores run, file, entity, file kind, parser name/version, status, counts,
    error, details JSON, and timestamps.
- `ctl_excel_parse_sheet`
  - tracks one parser execution per workbook sheet.
  - stores file audit id, run, file, entity, sheet name, sheet kind, header row,
    status, counts, error, details JSON, and timestamps.

Excel parsing statuses used in `raw_excel_rows.parse_status` include:

- `RAW_ONLY`
- `PARSED_OK`
- `PARSE_ERROR`
- `SKIPPED_FILE_PROFILE`
- `SKIPPED_SHEET_PROFILE`
- `SKIPPED_HEADER`
- `SKIPPED_METADATA`
- `SCHEMA_MISMATCH`
- `UNSUPPORTED_ENTITY`

## Parsing Flow

`parse-excel` selects candidate rows from `raw_excel_rows`.

- By default, only `RAW_ONLY` rows are selected.
- With `--reprocess-all`, already parsed or skipped rows are selected too.
- With `--file-id`, execution is limited to one workbook.
- With `--sheet-name`, execution is limited to one sheet.

For each workbook:

1. Load file metadata from `ctl_file_registry`.
2. Select the entity parser by `entity_name`.
3. Detect the file profile from normalized filename/path.
4. Create a `ctl_excel_parse_file` audit record.

For each sheet:

1. Select the sheet profile within the file profile.
2. Scan initial rows to detect a header row.
3. Create a `ctl_excel_parse_sheet` audit record.
4. Mark unsupported or stubbed sheets with an explicit skipped status.

Future business row parsers will update `payload_json`, `parse_status`, and
`parse_error` in `raw_excel_rows`.

## Tests

The implementation uses temporary SQLite databases and generated dummy
workbooks.

Test coverage includes:

- unsupported entity returns `UNSUPPORTED_ENTITY`.
- `farmacia` uses the `default` file profile.
- `pcr` classifies file kind by dummy filename.
- `--file-id` limits processing to one workbook.
- `--sheet-name` limits processing to one sheet.
- `--reprocess-all` includes already processed rows.
- CLI keeps JSON in `stdout` and progress/summary in `stderr`.
- the full suite passes with `PYTHONPATH=src pytest`.

## Assumptions

- This first increment implements infrastructure only, not real business
  parsing.
- No real files under `data/` are inspected.
- PCR file profiles are inferred from filenames, but real patterns should be
  added later when examples are provided or data inspection is explicitly
  authorized.
- Farmacia currently has one format and uses a `default` profile.
