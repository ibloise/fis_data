# PCR Excel Parser Plan

## Summary

Implement the first real Excel business parser for the `pcr` entity on top of
the shared Excel parser infrastructure. The current concrete parser covers the
sanitized format available in the repository conversation: PCR Quantification Cq
Results files.

No real files under `data/` are inspected. Additional PCR formats must be added
from sanitized examples or after explicit authorization to inspect restricted
data.

## Supported PCR Format

### `quantification_cq_results`

Filename pattern:

- any variable prefix ending exactly in `Quantification Cq Results.xlsx`

Sheet profiles:

- `cq_results`: the first data sheet, named `0` in current PCR exports. The
  parser also keeps compatibility with `Sheet1`, result, or Cq-like sheet names
  in sanitized tests.
- `run_information`: the `Run Information` sheet, a two-column key/value list
  used for future run matching.

Required canonical headers for `cq_results`:

- `well`
- `fluor`
- `target`
- `content`
- `sample`
- `cq`
- `cq_mean`
- `cq_std_dev`

Supported aliases for `cq_results`:

- `well`: `Well`, `Well Position`, `Position`
- `fluor`: `Fluor`, `Fluorophore`, `Dye`
- `target`: `Target`, `Target Name`, `Assay`
- `content`: `Content`
- `sample`: `Sample`, `Sample Name`, `Sample ID`
- `cq`: `Cq`, `Ct`, `C T`, `Cycle Threshold`
- `cq_mean`: `Cq Mean`, `Ct Mean`
- `cq_std_dev`: `Cq Std. Dev.`, `Cq Std Dev`, `Ct Std. Dev.`

The `run_information` sheet is parsed as headerless key/value rows. Column 1 is
stored as `key`; column 2 is stored as `value`.

### `quantification_amplification_results`

Filename pattern:

- any variable prefix ending exactly in `Quantification Amplification Results.xlsx`

Sheet profiles:

- `amplification_sybr`: the `SYBR` sheet, a wide amplification table.
- `run_information`: the `Run Information` sheet, parsed with the same
  key/value profile used by other PCR workbooks.

The `SYBR` sheet is parsed as follows:

- column 1 is ignored and can be empty.
- column 2 is `Cycle`, the PCR read cycle.
- all following columns are well IDs, such as `A1`, `A2`, and so on.
- values under well columns are RFU readings.

Each parsed SYBR row stores one PCR cycle and an RFU dictionary keyed by well.

### `melt_curve_rfu_results`

Filename pattern:

- any variable prefix ending exactly in `Melt Curve RFU Results.xlsx`

Sheet profiles:

- `melt_curve_sybr`: the `SYBR` sheet, a wide melt curve RFU table.
- `run_information`: the `Run Information` sheet, parsed with the same
  key/value profile used by other PCR workbooks.

The `SYBR` sheet is parsed as follows:

- column 1 is ignored and can be empty.
- column 2 is `Temperature`.
- all following columns are well IDs, such as `A1`, `A2`, and so on.
- values under well columns are RFU readings.

Each parsed SYBR row stores one temperature reading and an RFU dictionary keyed
by well.

### `melt_curve_derivative_results`

Filename pattern:

- any variable prefix ending exactly in `Melt Curve Derivative Results.xlsx`

Sheet profiles:

- `melt_curve_derivative_sybr`: the `SYBR` sheet, parsed with the same wide
  temperature/RFU table parser used by `melt_curve_rfu_results`.
- `run_information`: the `Run Information` sheet, parsed with the same
  key/value profile used by other PCR workbooks.

The `SYBR` sheet has the same field structure as `melt_curve_rfu_results`:

- column 1 is ignored and can be empty.
- column 2 is `Temperature`.
- all following columns are well IDs, such as `A1`, `A2`, and so on.
- values under well columns are RFU readings.

## Payload Contract

Parsed PCR rows store a stable envelope in `raw_excel_rows.payload_json`:

```json
{
  "entity": "pcr",
  "file_kind": "quantification_cq_results",
  "sheet_kind": "cq_results",
  "fields": {
    "well": "A01",
    "fluor": "FAM",
    "target": "N1",
    "content": "Unkn",
    "sample": "S1",
    "cq": 23.4,
    "cq_mean": 23.5,
    "cq_std_dev": 0.1
  },
  "source": {
    "file_id": 1,
    "sheet_name": "0",
    "row_no": 3,
    "header_row_no": 2
  }
}
```

Parsed `amplification_sybr` rows use:

```json
{
  "entity": "pcr",
  "file_kind": "quantification_amplification_results",
  "sheet_kind": "amplification_sybr",
  "fields": {
    "cycle": 1,
    "rfu_by_well": {
      "A1": 10.5,
      "A2": 20.0
    }
  },
  "source": {
    "file_id": 1,
    "sheet_name": "SYBR",
    "row_no": 2,
    "header_row_no": 1
  }
}
```

Parsed `melt_curve_sybr` rows use:

```json
{
  "entity": "pcr",
  "file_kind": "melt_curve_rfu_results",
  "sheet_kind": "melt_curve_sybr",
  "fields": {
    "temperature": 65.0,
    "rfu_by_well": {
      "A1": 100.0,
      "A2": 200.0
    }
  },
  "source": {
    "file_id": 1,
    "sheet_name": "SYBR",
    "row_no": 2,
    "header_row_no": 1
  }
}
```

Parsed `melt_curve_derivative_sybr` rows use the same field structure with
`file_kind` set to `melt_curve_derivative_results` and `sheet_kind` set to
`melt_curve_derivative_sybr`.

## Parsing Behavior

- Header detection scans the configured first rows and does not require the
  header to be row 1.
- Header and metadata rows are marked as `SKIPPED_METADATA`.
- Run Information rows are parsed as `PARSED_OK` key/value payloads.
- Empty rows after the header are marked as `SKIPPED_METADATA`.
- Valid data rows are marked as `PARSED_OK`.
- Row-level conversion errors, such as invalid `cq`, are marked as
  `PARSE_ERROR`.
- Invalid SYBR RFU values are marked as row-level `PARSE_ERROR`.
- Invalid melt curve temperatures are marked as row-level `PARSE_ERROR`.
- Missing required headers mark the sheet rows as `SCHEMA_MISMATCH`.
- Unknown PCR filenames are marked as `SKIPPED_FILE_PROFILE`.
- Unknown sheets inside known PCR files are marked as `SKIPPED_SHEET_PROFILE`.

## Pending PCR Format Backlog

The following PCR Excel formats are known but not yet implemented. They should
remain explicit `SKIPPED_FILE_PROFILE` cases until their parser profiles are
added.

| Format filename suffix | Priority |
| --- | --- |
| `Run Information.xlsx` | Medium |
| `Melt Curve Peak Results.xlsx` | Low |
| `End Point Results.xlsx` | Very low |
| `Gene Expression Result - Bar Chart.xlsx` | Very low |
| `Melt Curve Plate View Results.xlsx` | Very low |
| `Melt Curve Summary.xlsx` | Very low |
| `Quantification Plate View Results.xlsx` | Very low |
| `Quantification Summary.xlsx` | Very low |
| `Standard Curve Results.xlsx` | Very low |

## Extension Path

To add a new PCR Excel format:

1. Add a sanitized example to this document.
2. Add a new `FileProfile` with filename patterns.
3. Add one or more `SheetProfile` entries with required headers and aliases.
4. Add row parser tests using generated dummy workbooks.
5. Keep all parsed output in `raw_excel_rows.payload_json` until normalized
   domain tables are explicitly planned.
