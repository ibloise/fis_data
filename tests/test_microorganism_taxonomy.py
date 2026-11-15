"""Study taxonomy regression tests using synthetic in-memory SQLite only."""

import re
from contextlib import closing, contextmanager
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from openpyxl import load_workbook
from sqlalchemy import create_engine, insert, select, text

from fis_data.cli import cli
from fis_data.etl.exports import downstream as exports
from fis_data.etl.microb.taxonomy import (
    load_microorganism_dimension,
    read_dictionary,
    validate_microorganism_coverage,
)
from fis_data.schema import (
    create_views,
    dim_microb_microorganism,
    metadata,
    microb_susceptibility_result,
    pcr_derivation_run,
    pcr_sample,
    pcr_sil_organism_marker_summary,
    ref_microb_catalog,
    ref_microb_catalog_entry,
)


class Capture:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self):
        self.views = {}

    @contextmanager
    def begin(self):
        yield self

    def execute(self, statement):
        sql = str(statement)
        if sql.startswith("CREATE VIEW"):
            self.views[sql.split()[2]] = sql


@pytest.fixture
def study():
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    capture = Capture()
    create_views(capture)
    dictionary = read_dictionary()
    with engine.begin() as conn:
        conn.execute(
            insert(pcr_derivation_run).values(
                derivation_id=1,
                algorithm_version="synthetic",
                rule_version="test",
                parameters_json="{}",
                status="SUCCESS",
                is_current=True,
            )
        )
        conn.execute(
            text(
                "CREATE TABLE v_downstream_sil_pcr_global "
                "(sample, sample_pseudonym, patient_pseudonym)"
            )
        )
        for index, row in enumerate(dictionary, 1):
            conn.execute(
                insert(pcr_sample).values(
                    pcr_sample_id=index,
                    sample_key=f"S{index}",
                )
            )
            conn.execute(
                text(
                    "INSERT INTO v_downstream_sil_pcr_global VALUES "
                    "(:sample, :sample, 'P-synthetic')"
                ),
                {"sample": f"S{index}"},
            )
            conn.execute(
                insert(pcr_sil_organism_marker_summary).values(
                    derivation_id=1,
                    pcr_sample_id=index,
                    microb_sample_id=index,
                    microorganism_code=row["microorganism_code"],
                    oxa_detected=bool(index & 1),
                    vim_detected=bool(index & 2),
                    kpc_detected=bool(index & 4),
                    ndm_detected=bool(index & 8),
                    isolate_count=1,
                    marker_codes_json="[]",
                    details_json="{}",
                )
            )
        # A matched sample without an organism represents a negative culture.
        conn.execute(insert(pcr_sample).values(pcr_sample_id=29, sample_key="S29"))
        conn.execute(
            text(
                "INSERT INTO v_downstream_sil_pcr_global VALUES "
                "('S29', 'S29', 'P-synthetic')"
            )
        )
        conn.execute(text(capture.views["v_pcr_sil_organism_marker_summary"]))
        conn.execute(
            text(
                "CREATE TABLE resolved_organism AS SELECT *, "
                "microb_sample_id AS current_microb_sample_id "
                "FROM pcr_sil_organism_marker_summary"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE st_by_organism AS SELECT pcr_sample_id, "
                "microb_sample_id, microorganism_code, "
                "'synthetic-ST-' || microorganism_code AS st "
                "FROM pcr_sil_organism_marker_summary"
            )
        )
        # Exercise the actual final production SELECT and joins. SQLite lacks
        # LATERAL, so only the original catalog label lookup is stubbed.
        sql = capture.views["v_downstream_sil_pcr_organism"]
        final = "SELECT summary_global.*" + sql.split("SELECT summary_global.*", 1)[1]
        final = final.split("LEFT JOIN LATERAL", 1)[0]
        final += (
            "LEFT JOIN (SELECT NULL AS code, NULL AS description) "
            "AS microorganism ON microorganism.code=organism.microorganism_code"
        )
        final = re.sub(r"::(?:int|text)\b", "", final)
        conn.execute(text("CREATE VIEW v_downstream_sil_pcr_organism AS " + final))
        conn.execute(
            text(
                re.sub(
                    r"::text\b", "", capture.views["v_downstream_sil_organism_profile"]
                )
            )
        )
    yield engine, capture.views
    engine.dispose()


def test_dictionary_exact_codes_species_and_groups():
    rows = read_dictionary()
    mapping = {row["microorganism_code"]: row for row in rows}
    assert set(mapping) == {
        "3001",
        "5886",
        "1000",
        "2003",
        "2009",
        "19026",
        "4003",
        "4001",
        "4002",
        "4005",
        "4006",
        "3002",
        "KA0CEA",
        "KV038C",
        "10002",
        "16002",
        "16005",
        "PN05D3",
        "PM05D1",
        "5001",
        "2030",
        "5002",
        "PG0AA6",
        "mormor",
        "3005",
        "RP05FE",
        "13000",
        "6002",
    }
    assert len(rows) == 28
    assert len({row["scientific_name"] for row in rows}) == 26
    assert len({row["analysis_group_code"] for row in rows}) == 24
    for field in ("scientific_name", "analysis_group_code", "analysis_group_name"):
        assert mapping["3001"][field] == mapping["5886"][field]
        assert mapping["5001"][field] == mapping["2030"][field]
    complex_rows = [mapping[code] for code in ("2003", "2009", "19026")]
    assert {row["analysis_group_name"] for row in complex_rows} == {
        "Enterobacter cloacae complex"
    }
    assert {row["scientific_name"] for row in complex_rows} == {
        "Enterobacter cloacae",
        "Enterobacter hormaechei",
        "Enterobacter roggenkampii",
    }
    assert (
        len(
            {
                mapping[code]["analysis_group_code"]
                for code in ("3001", "3002", "KA0CEA", "KV038C")
            }
        )
        == 4
    )
    assert mapping["2030"]["scientific_name"] == "Proteus mirabilis"


def test_load_is_idempotent_and_reports_catalog_differences(study):
    engine, _ = study
    with engine.begin() as conn:
        catalog_id = conn.execute(
            insert(ref_microb_catalog).values(
                catalog_name="microorg",
                sha256="synthetic",
                source_path="synthetic",
                entry_count=1,
                is_current=True,
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(ref_microb_catalog_entry).values(
                ref_microb_catalog_id=catalog_id,
                code="2030",
                description="Synthetic catalog label",
                aliases_json="[]",
                raw_fields_json="[]",
            )
        )
        conn.execute(
            insert(microb_susceptibility_result).values(
                isolate_id=1,
                antibiotic_code="KPC",
                interpretation_code="N",
                stg_microb_antibiogram_id=1,
            )
        )
        before = conn.execute(select(microb_susceptibility_result)).all()
    first = load_microorganism_dimension(engine)
    with engine.begin() as conn:
        # A rerun restores the versioned labels instead of accumulating rows.
        conn.execute(
            dim_microb_microorganism.update()
            .where(dim_microb_microorganism.c.microorganism_code == "2030")
            .values(scientific_name="incorrect")
        )
    second = load_microorganism_dimension(engine)
    assert first == second
    assert first["catalog_description_differences"] == [
        {
            "microorganism_code": "2030",
            "catalog_name": "Synthetic catalog label",
            "observed_name": "Proteus mirabilis BLEE",
        }
    ]
    with engine.connect() as conn:
        dimension = conn.execute(select(dim_microb_microorganism)).mappings().all()
        assert len(dimension) == 28
        assert (
            next(row for row in dimension if row["microorganism_code"] == "2030")[
                "scientific_name"
            ]
            == "Proteus mirabilis"
        )
        assert conn.execute(select(microb_susceptibility_result)).all() == before
        assert conn.execute(
            select(ref_microb_catalog_entry.c.description)
        ).scalar_one() == ("Synthetic catalog label")


def test_join_retains_rows_nulls_profiles_and_st(study):
    engine, _ = study
    with engine.connect() as conn:
        before = conn.execute(text("SELECT * FROM v_downstream_sil_pcr_organism"))
        before = [dict(row) for row in before.mappings()]
    load_microorganism_dimension(engine)
    with engine.connect() as conn:
        after = conn.execute(text("SELECT * FROM v_downstream_sil_pcr_organism"))
        after = [dict(row) for row in after.mappings()]
        coverage = validate_microorganism_coverage(conn)
    assert len(before) == len(after) == 29
    added = {"microorg_scientific_name", "microorg_group_code", "microorg_group_name"}
    assert [{k: v for k, v in row.items() if k not in added} for row in before] == [
        {k: v for k, v in row.items() if k not in added} for row in after
    ]
    assert all(row["microorg_scientific_name"] for row in after if row["microorg_code"])
    negative = next(row for row in after if row["microorg_code"] is None)
    assert all(negative[field] is None for field in added)
    for code in ("2003", "2009", "19026"):
        organism = next(row for row in after if row["microorg_code"] == code)
        assert organism["microorg_group_name"] == "Enterobacter cloacae complex"
        assert organism["st"] == f"synthetic-ST-{code}"
    assert coverage == {
        "version": "microorganism-normalization-v1",
        "dimension_codes": 28,
        "microorganism_rows": 28,
        "covered_codes": 28,
        "scientific_names": 26,
        "analysis_groups": 24,
        "unmapped_codes": [],
    }


@pytest.mark.parametrize("unknown", ["UNKNOWN", "MORMOR", "ka0cea"])
def test_unknown_codes_remain_in_view_and_fail_export(study, tmp_path, unknown):
    engine, _ = study
    load_microorganism_dimension(engine)
    with engine.begin() as conn:
        conn.execute(
            pcr_sil_organism_marker_summary.update()
            .where(pcr_sil_organism_marker_summary.c.microorganism_code == "mormor")
            .values(microorganism_code=unknown)
        )
        row = (
            conn.execute(
                text(
                    "SELECT * FROM v_downstream_sil_pcr_organism "
                    "WHERE microorg_code=:code"
                ),
                {"code": unknown},
            )
            .mappings()
            .one()
        )
        assert row["microorg_scientific_name"] is None
        with pytest.raises(ValueError, match=unknown):
            validate_microorganism_coverage(conn)
    output = tmp_path / "must-not-exist.xlsx"
    for name in ("paper-analysis", "analytical-core-tables"):
        with pytest.raises(ValueError, match=unknown):
            exports.export_downstream(engine, format_name=name, output_path=output)
        assert not output.exists()


@pytest.mark.parametrize("name", ["paper-analysis", "analytical-core-tables"])
def test_actual_xlsx_columns_and_version(study, tmp_path, monkeypatch, name):
    engine, views = study
    load_microorganism_dimension(engine)
    definition = exports.FORMATS[name]
    # Use the registered organism queries; unrelated workbook sheets need no
    # synthetic PCR/QC fixtures for this regression.
    monkeypatch.setitem(
        exports.FORMATS,
        name,
        exports.ExportFormat(
            name=name,
            description=definition.description,
            sheets=tuple(
                sheet
                for sheet in definition.sheets
                if sheet.name
                in {
                    "SIL PCR organisms",
                    "SIL organism profiles",
                }
            ),
        ),
    )
    path = tmp_path / f"{name}.xlsx"
    counts = exports.export_downstream(engine, format_name=name, output_path=path)
    assert counts == {"SIL PCR organisms": 29, "SIL organism profiles": 28}
    schema = exports.downstream_schema(engine, format_name=name)
    schema_columns = {sheet["name"]: sheet["columns"] for sheet in schema["sheets"]}
    with closing(load_workbook(path, read_only=True)) as workbook:
        for sheet in counts:
            headers = next(workbook[sheet].values)
            assert list(headers) == schema_columns[sheet]
            if name == "paper-analysis":
                position = headers.index("microorg_code")
                assert headers[position : position + 3] == (
                    "microorg_code",
                    "microorg_name",
                    "microorg_group_name",
                )
                assert "microorg_scientific_name" not in headers
                assert "microorg_group_code" not in headers
            else:
                assert {
                    "microorg_scientific_name",
                    "microorg_group_code",
                    "microorg_group_name",
                } <= set(headers)
            rows = list(workbook[sheet].values)[1:]
            hormaechei = next(
                row for row in rows if row[headers.index("microorg_code")] == "2009"
            )
            assert hormaechei[headers.index("microorg_group_name")] == (
                "Enterobacter cloacae complex"
            )
        report = {
            row[0]: row[1]
            for row in list(workbook["Report"].values)[1:]
            if len(row) > 1
        }
        assert report["microorganism_normalization_version"] == (
            "microorganism-normalization-v1"
        )
    assert (
        "organism.microorg_scientific_name" in views["v_downstream_sil_organism_target"]
    )


def test_cli_load_and_validate(study, monkeypatch, tmp_path):
    engine, _ = study
    monkeypatch.setattr("fis_data.cli.load_dotenv", lambda: None)
    monkeypatch.setattr("fis_data.cli._engine_for_db_url", lambda _: engine)
    # Metadata is already created; keep the synthetic production projections.
    monkeypatch.setattr("fis_data.cli.create_schema", lambda _: None)
    result = CliRunner().invoke(cli, ["load-microorganism-dimension"])
    assert result.exit_code == 0, result.output
    assert '"codes_loaded": 28' in result.output
    result = CliRunner().invoke(cli, ["validate-microorganism-normalization"])
    assert result.exit_code == 0, result.output
    assert '"covered_codes": 28' in result.output
    with engine.begin() as conn:
        conn.execute(
            dim_microb_microorganism.delete().where(
                dim_microb_microorganism.c.microorganism_code == "mormor"
            )
        )
    result = CliRunner().invoke(cli, ["validate-microorganism-normalization"])
    assert result.exit_code == 1
    assert "unmapped SIL codes: 'mormor'" in result.output
    output = tmp_path / "rejected.xlsx"
    result = CliRunner().invoke(
        cli,
        [
            "export-downstream",
            "--format",
            "paper-analysis",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 1
    assert "unmapped SIL codes: 'mormor'" in result.output
    assert not output.exists()
