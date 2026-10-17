from __future__ import annotations

from datetime import datetime
from math import log10

from openpyxl import load_workbook
import pytest
from sqlalchemy import create_engine, insert, text

from fis_data.etl.exports import export_downstream, format_names
from fis_data.etl.exports.downstream import _xlsx_value
from fis_data.schema import (
    create_views,
    create_schema,
    ctl_source,
    pcr_derivation_run,
    pcr_run,
    pcr_run_analysis,
    pcr_run_qc,
    pcr_result_selection,
    pcr_target_qc,
)


def test_sil_organism_view_keeps_samples_without_an_organism_summary() -> None:
    class CapturingConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement) -> None:
            self.statements.append(str(statement))

    class CapturingTransaction:
        def __init__(self, connection: CapturingConnection) -> None:
            self.connection = connection

        def __enter__(self) -> CapturingConnection:
            return self.connection

        def __exit__(self, *_: object) -> bool:
            return False

    class CapturingEngine:
        dialect = type("Dialect", (), {"name": "postgresql"})()

        def __init__(self, connection: CapturingConnection) -> None:
            self.connection = connection

        def begin(self) -> CapturingTransaction:
            return CapturingTransaction(self.connection)

    connection = CapturingConnection()
    create_views(CapturingEngine(connection))
    statement = next(
        sql for sql in connection.statements
        if sql.startswith("CREATE VIEW v_downstream_sil_pcr_organism")
    )

    assert "LEFT JOIN pcr_sil_organism_marker_summary AS organism" in statement
    # The inner reconciliation CTE filters its own derivation, while the
    # outer LEFT JOIN must still preserve samples without an organism.
    outer_select = statement.split("SELECT summary_global.*", 1)[1]
    assert "WHERE organism.derivation_id" not in outer_select
    assert "COALESCE(organism.oxa_detected, FALSE)::int" in statement


def test_pcr_qc_error_export_contains_unpromoted_samples_and_other_context(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'qc-errors.sqlite'}", future=True)
    create_schema(engine)
    with engine.begin() as conn:
        source_id = conn.execute(
            insert(ctl_source).values(source_name="PCR", source_type="test")
        ).inserted_primary_key[0]
        derivation_id = conn.execute(
            insert(pcr_derivation_run).values(
                algorithm_version="test", rule_version="1.0", parameters_json="{}",
                status="SUCCESS", is_current=True,
            )
        ).inserted_primary_key[0]
        run_id = conn.execute(
            insert(pcr_run).values(
                source_id=source_id, run_name="screening-qc-error",
                run_name_normalized="screening-qc-error",
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(pcr_run_analysis).values(
                derivation_id=derivation_id, pcr_run_id=run_id,
                run_class="PANEL", run_type="SPECIFIC",
                run_started=datetime(2026, 1, 1), episode_date_key="2026-01-01",
                canonical_targets_json="[]", review_reason=None,
            )
        )
        conn.execute(
            insert(pcr_run_qc).values(
                derivation_id=derivation_id, pcr_run_id=run_id,
                qc_status="INVALID", valid_targets=0, invalid_targets=4,
                warning_count=0,
            )
        )
        conn.execute(
            insert(pcr_target_qc).values(
                derivation_id=derivation_id, pcr_run_id=run_id,
                canonical_target="KPC", qc_status="INVALID", is_valid=False,
                positive_controls=1, negative_controls=1,
                warnings_json="[]", details_json='{"failures": ["NEGATIVE_CONTROL_POSITIVE"]}',
            )
        )
        promoted_run_id = conn.execute(
            insert(pcr_run).values(
                source_id=source_id, run_name="specific-promoted",
                run_name_normalized="specific-promoted",
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(pcr_run_analysis).values(
                derivation_id=derivation_id, pcr_run_id=promoted_run_id,
                run_class="PANEL", run_type="SPECIFIC",
                run_started=datetime(2026, 1, 2), episode_date_key="2026-01-02",
                canonical_targets_json="[]", review_reason=None,
            )
        )
        conn.execute(
            text(
                """
                CREATE VIEW v_pcr_run_qc AS
                SELECT qc.*, run.run_name
                FROM pcr_run_qc AS qc
                JOIN pcr_run AS run ON run.pcr_run_id=qc.pcr_run_id
                """
            )
        )
        for statement in (
            """
            INSERT INTO pcr_sample (pcr_sample_id, sample_key)
            VALUES (101, 'S-1')
            """,
            """
            INSERT INTO pcr_well (
                pcr_well_id, pcr_run_id, well, well_normalized, sample_name,
                content, file_id, raw_excel_row_id, run_id
            ) VALUES (201, 1, 'A01', 'A01', 'S-1', 'Unkn', 1, 1, 1)
            """,
            """
            INSERT INTO pcr_well_classification (
                derivation_id, pcr_well_id, pcr_sample_id, raw_sample_name,
                normalized_name, well_role, rule_key, content_evidence,
                review_reason
            ) VALUES (1, 201, 101, 'S-1', 'S-1', 'REAL_SAMPLE',
                      'test', 'Unkn', NULL)
            """,
            """
            INSERT INTO pcr_interpretation (
                derivation_id, pcr_run_id, pcr_well_id, pcr_cq_result_id,
                source_target, canonical_target, call, cq, peak_temperature,
                peak_deriv_rfu, peaks_json, metrics_json, error
            ) VALUES (1, 1, 201, 1, 'Screening', 'KPC', 'INDETERMINATE',
                      31.2, 88.1, 145.0, '[{"temperature": 88.1}]',
                      '{"melt_status": "MELTING_INCOMPATIBLE"}',
                      'MELTING_INCOMPATIBLE')
            """,
            """
            INSERT INTO pcr_interpretation (
                derivation_id, pcr_run_id, pcr_well_id, pcr_cq_result_id,
                source_target, canonical_target, call, cq, peak_temperature,
                peak_deriv_rfu, peaks_json, metrics_json, error
            ) VALUES (1, 1, 201, 2, '16S', '16S', 'POSITIVE',
                      20.0, NULL, NULL, '[]', '{}', NULL)
            """,
        ):
            conn.execute(text(statement))
        promoted_well_id = conn.execute(
            text(
                """
                INSERT INTO pcr_well (
                    pcr_run_id, well, well_normalized, sample_name, content,
                    file_id, raw_excel_row_id, run_id
                ) VALUES (:run_id, 'A01', 'A01', 'S-1', 'Unkn', 1, 2, 1)
                RETURNING pcr_well_id
                """
            ),
            {"run_id": promoted_run_id},
        ).scalar_one()
        promoted_interpretation_id = conn.execute(
            text(
                """
                INSERT INTO pcr_interpretation (
                    derivation_id, pcr_run_id, pcr_well_id, pcr_cq_result_id,
                    source_target, canonical_target, call, cq, peak_temperature,
                    peak_deriv_rfu, peaks_json, metrics_json, error
                ) VALUES (
                    :derivation_id, :run_id, :well_id, 3, 'KPC', 'KPC',
                    'NEGATIVE', 25.0, 88.0, 160.0, '[]', '{}', NULL
                )
                RETURNING interpretation_id
                """
            ),
            {
                "derivation_id": derivation_id,
                "run_id": promoted_run_id,
                "well_id": promoted_well_id,
            },
        ).scalar_one()
        conn.execute(
            insert(pcr_result_selection).values(
                derivation_id=derivation_id, pcr_sample_id=101,
                run_type="SPECIFIC", canonical_target="KPC",
                preferred_interpretation_id=promoted_interpretation_id,
                occurrence_count=2, relation_kind="HISTORICAL_REPEAT",
                concordance="CONCORDANT", needs_review=False, details_json="{}",
            )
        )
        conn.execute(
            text(
                """
                CREATE VIEW v_pcr_unpromoted_sample_errors AS
                SELECT 'S-1' AS sample_key, 101 AS pcr_sample_id,
                       'specific' AS semantic_type, 'KPC' AS canonical_target,
                       1 AS pcr_run_id, 'screening-qc-error' AS run_name,
                       'PANEL' AS run_class, '2026-01-01' AS run_started,
                       NULL AS interpretation_errors,
                       '{"failures": ["NEGATIVE_CONTROL_POSITIVE"]}' AS target_qc_errors,
                       NULL AS run_error, NULL AS control_evidence_json,
                       'Invalid QC' AS error
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE VIEW v_pcr_effective_result AS
                SELECT CAST(NULL AS TEXT) AS sample_key,
                       CAST(NULL AS TEXT) AS run_type,
                       CAST(NULL AS TEXT) AS canonical_target,
                       CAST(NULL AS TEXT) AS call,
                       CAST(NULL AS FLOAT) AS cq,
                       CAST(NULL AS FLOAT) AS peak_temperature,
                       CAST(NULL AS INTEGER) AS pcr_run_id,
                       CAST(NULL AS TEXT) AS concordance,
                       CAST(NULL AS BOOLEAN) AS needs_review,
                       CAST(NULL AS BOOLEAN) AS has_override
                WHERE 0
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE VIEW v_pcr_target_qc_evidence AS
                SELECT CAST(NULL AS INTEGER) AS pcr_run_id,
                       CAST(NULL AS TEXT) AS canonical_target,
                       CAST(NULL AS BOOLEAN) AS is_valid,
                       CAST(NULL AS TEXT) AS well_role,
                       CAST(NULL AS TEXT) AS well
                WHERE 0
                """
            )
        )

    output_path = tmp_path / "qc-errors.xlsx"
    counts = export_downstream(
        engine, format_name="pcr-qc-errors", output_path=output_path
    )

    assert "pcr-qc-errors" in format_names()
    assert counts == {
        "QC runs": 1,
        "QC targets": 1,
        "Affected sample targets": 1,
        "Affected raw results": 1,
        "Promoted after QC failure": 1,
        "QC control evidence": 0,
    }
    workbook = load_workbook(output_path, read_only=True, data_only=True)
    assert workbook.sheetnames == [
        "Report", "QC runs", "QC targets", "Affected sample targets",
        "Affected raw results", "Promoted after QC failure",
        "QC control evidence",
    ]
    rows = list(workbook["Affected sample targets"].values)
    status_column = rows[0].index("other_context_status")
    context_column = rows[0].index("other_context")
    assert rows[1][context_column] == "screening"
    assert rows[1][status_column] == "NOT_PERFORMED"
    qc_target_rows = list(workbook["QC targets"].values)
    assert qc_target_rows[1][
        qc_target_rows[0].index("promotion_coverage_status")
    ] == "ALL_PROMOTED"
    assert qc_target_rows[1][
        qc_target_rows[0].index("promotion_coverage_json")
    ] == ('{"real_sample_count":1,"promoted_sample_count":1,'
          '"unpromoted_sample_count":0}')
    raw_rows = list(workbook["Affected raw results"].values)
    assert raw_rows[1][raw_rows[0].index("raw_cq")] == 31.2
    assert raw_rows[1][raw_rows[0].index("raw_peak_temperature")] == 88.1
    assert raw_rows[1][raw_rows[0].index("raw_reference_16s_cq")] == 20
    assert raw_rows[1][raw_rows[0].index("raw_delta_cq")] == pytest.approx(
        log10(2**11.2)
    )
    assert raw_rows[1][raw_rows[0].index("raw_delta_cq_status")] == (
        "RAW_REFERENCE_16S_QC_INVALID"
    )
    recovered_rows = list(workbook["Promoted after QC failure"].values)
    assert recovered_rows[1][recovered_rows[0].index("source_run_name")] == (
        "screening-qc-error"
    )
    assert recovered_rows[1][recovered_rows[0].index("promoted_run_name")] == (
        "specific-promoted"
    )


def test_xlsx_value_serializes_native_json() -> None:
    assert _xlsx_value({"failures": ["NEGATIVE_CONTROL_POSITIVE"]}) == (
        '{"failures": ["NEGATIVE_CONTROL_POSITIVE"]}'
    )
