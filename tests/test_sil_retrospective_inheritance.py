"""Run production SQL against synthetic in-memory fixtures only.

SQLite adaptations below translate PostgreSQL date/regex syntax and functions;
the selection, joins, grouping and reconciliation expressions stay unchanged.
No project configuration or external database connection is used.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from fis_data.etl.pcr.analytics.repository import PCRAnalyticsRepository
from fis_data.schema import create_views


class Capture:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self):
        self.statements = []

    @contextmanager
    def begin(self):
        yield self

    def execute(self, statement, parameters=None):
        self.statements.append(str(statement))
        return SimpleNamespace(rowcount=0)


class BoolOr:
    def __init__(self):
        self.value = False

    def step(self, value):
        self.value = self.value or bool(value)

    def finalize(self):
        return int(self.value)


def compatible(sql):
    sql = sql.replace(
        "seeds.current_at - (:max_days * INTERVAL '1 day')",
        "datetime(seeds.current_at, '-' || :max_days || ' days')",
    )
    sql = sql.replace(
        "EXTRACT(DAY FROM (current_sample.current_at - selected.previous_at))::integer",
        "CAST(julianday(current_sample.current_at) - "
        "julianday(selected.previous_at) AS INTEGER)",
    )
    sql = sql.replace(
        "ignored_organism.description ~* :irrelevant_organism_pattern",
        "ignored_organism.description REGEXP :irrelevant_organism_pattern",
    )
    return re.sub(r"::(?:integer|int|text)\b", "", sql)


def cte(sql, name):
    start = sql.index(name + " AS (") + len(name + " AS (")
    depth = 1
    for pos in range(start, len(sql)):
        depth += (sql[pos] == "(") - (sql[pos] == ")")
        if depth == 0:
            return sql[start:pos]
    raise AssertionError("Unterminated CTE")


@pytest.fixture
def model():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.create_aggregate("BOOL_OR", 1, BoolOr)
    db.create_function(
        "jsonb_build_object",
        -1,
        lambda *args: json.dumps(dict(zip(args[::2], args[1::2], strict=True))),
    )
    db.create_function(
        "regexp", 2, lambda pattern, value: bool(re.search(pattern, value, re.I))
    )
    db.executescript("""
        CREATE TABLE pcr_microb_well_validation (derivation_id, pcr_sample_id,
                microb_sample_id, validation_status);
        CREATE TABLE pcr_sample_scope_gate (derivation_id, pcr_sample_id, gate_status);
        CREATE TABLE microb_sample (sample_id, patient_id, received_at, entered_at,
                specimen_type_id, sample_number);
        CREATE TABLE ref_microb_catalog_entry (ref_microb_catalog_entry_id, code,
                description);
        CREATE TABLE microb_sample_analysis (sample_analysis_id, sample_id,
                analysis_code, analysis_kind);
        CREATE TABLE microb_analysis_result (analysis_result_id, sample_analysis_id,
                result_code);
        CREATE TABLE microb_isolate (isolate_id, analysis_result_id,
                microorganism_code, microorganism_id);
        CREATE TABLE microb_susceptibility_result (isolate_id, antibiotic_code,
                interpretation_code);
        CREATE TABLE pcr_sil_culture_classification (derivation_id, microb_sample_id,
                sample_analysis_id, culture_category);
        CREATE TABLE pcr_sil_retrospective_evidence (derivation_id, pcr_sample_id,
                current_microb_sample_id,
            evidence_microb_sample_id, matched_microorganism_code, evidence_status,
                days_difference,
            negative_count, policy_version, reason, details_json);
        CREATE TABLE pcr_sil_link_candidate (derivation_id, pcr_sample_id,
                current_microb_sample_id,
            current_isolate_id, microorganism_code, evidence_microb_sample_id,
                evidence_isolate_id,
            marker_code, candidate_kind, candidate_status, days_difference,
                negative_count,
            policy_version, reason, details_json);
        CREATE TABLE pcr_derivation_run (derivation_id, status, is_current);
        CREATE TABLE pcr_sil_organism_marker_summary (derivation_id, pcr_sample_id,
                microb_sample_id,
            microorganism_code, oxa_detected, kpc_detected, ndm_detected,
                vim_detected, isolate_count);
        CREATE TABLE pcr_sil_culture_marker_summary (derivation_id, pcr_sample_id,
                microb_sample_id,
            oxa_detected, kpc_detected, ndm_detected, vim_detected);
        CREATE TABLE pcr_result_selection (derivation_id, pcr_sample_id, run_type,
                canonical_target, preferred_interpretation_id);
        CREATE TABLE pcr_interpretation (interpretation_id, call);
        CREATE TABLE pcr_sil_result_link (derivation_id, pcr_sample_id, run_type,
                canonical_target,
            candidate_id, link_type, concordance, policy_version, reason, details_json);
        INSERT INTO ref_microb_catalog_entry VALUES (1, '068', 'Rectal');
        INSERT INTO pcr_derivation_run VALUES (1, 'SUCCESS', 1);
        INSERT INTO pcr_microb_well_validation VALUES (1, 1, 100, 'MATCH_ELIGIBLE');
        INSERT INTO pcr_interpretation VALUES (1, 'POSITIVE');
        INSERT INTO pcr_result_selection VALUES
            (1, 1, 'SPECIFIC', 'OXA48', 1), (1, 1, 'SPECIFIC', 'VIM', 1),
            (1, 1, 'SPECIFIC', 'KPC', 1), (1, 1, 'SPECIFIC', 'NDM', 1);
    """)
    capture = Capture()
    PCRAnalyticsRepository(capture).link_sil_results(
        1, policy_version="test", max_days=30, max_negatives=3
    )
    queries = capture.statements
    views_capture = Capture()
    create_views(views_capture)
    views = {
        sql.split()[2]: sql
        for sql in views_capture.statements
        if sql.startswith("CREATE VIEW")
    }
    params = dict(
        derivation_id=1,
        policy_version="test",
        max_days=30,
        max_negatives=3,
        irrelevant_organism_pattern="Candida|Enterococcus|Stenotrophomonas|Acinetobacter",
    )

    def sample(sample_id, date, organisms, patient=1):
        # organisms: (isolate id, exact code, [(marker, interpretation), ...])
        db.execute(
            "INSERT INTO microb_sample VALUES (?, ?, ?, NULL, 1, ?)",
            (sample_id, patient, date, str(sample_id)),
        )
        db.execute(
            "INSERT INTO microb_sample_analysis VALUES (?, ?, "
            "'PMDR', 'BACTERIOLOGICAL')",
            (sample_id, sample_id),
        )
        db.execute(
            "INSERT INTO microb_analysis_result VALUES (?, ?, ?)",
            (sample_id, sample_id, "CO1" if organisms else "NEG"),
        )
        if sample_id == 100:
            db.execute(
                "INSERT INTO pcr_sil_culture_classification VALUES "
                "(1, 100, 100, 'CULTURE_POSITIVE')"
            )
        direct = {}
        for isolate_id, code, markers in organisms:
            db.execute(
                "INSERT INTO microb_isolate VALUES (?, ?, ?, NULL)",
                (isolate_id, sample_id, code),
            )
            db.executemany(
                "INSERT INTO microb_susceptibility_result VALUES (?, ?, ?)",
                [(isolate_id, marker, call) for marker, call in markers],
            )
            flags = direct.setdefault(code, [0, 0, 0, 0])
            for index, marker in enumerate(("OXA", "KPC", "NDM", "VIM")):
                flags[index] |= int((marker, "P") in markers)
        if sample_id == 100:
            db.executemany(
                "INSERT INTO pcr_sil_organism_marker_summary "
                "VALUES (1, 1, 100, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        code,
                        *flags,
                        sum(org_code == code for _, org_code, _ in organisms),
                    )
                    for code, flags in direct.items()
                ],
            )
            db.execute(
                "INSERT INTO pcr_sil_culture_marker_summary VALUES "
                "(1, 1, 100, ?, ?, ?, ?)",
                [int(any(flags[i] for flags in direct.values())) for i in range(4)],
            )

    def run():
        # Classification is seeded above. Execute actual search, empty-result
        # audit, candidate generation and PCR reconciliation SQL.
        for query in queries[1:]:
            db.execute(compatible(query), params)
        organism_sql = views["v_downstream_sil_pcr_organism"]
        resolved = cte(organism_sql, "resolved_organism")
        rows = db.execute(compatible(resolved)).fetchall()
        profiles = {
            row["microorganism_code"]: "".join(
                str(row[key])
                for key in (
                    "oxa_detected",
                    "vim_detected",
                    "kpc_detected",
                    "ndm_detected",
                )
            )
            for row in rows
        }
        global_sql = cte(views["v_downstream_sil_pcr_global"], "retrospective_markers")
        global_rows = db.execute(compatible(global_sql)).fetchall()
        global_profile = (
            None
            if not global_rows
            else "".join(
                str(global_rows[0][key])
                for key in (
                    "oxa_detected",
                    "vim_detected",
                    "kpc_detected",
                    "ndm_detected",
                )
            )
        )
        # Execute the real final projections as well as the aggregation CTEs.
        # Stub unrelated demographic/ST labels, retaining marker expressions.
        projection = organism_sql.split("SELECT summary_global.*", 1)[1].split(
            "FROM v_downstream_sil_pcr_global AS summary_global", 1
        )[0]
        db.execute(
            compatible(
                "CREATE TEMP VIEW v_downstream_sil_pcr_organism AS "
                "WITH resolved_organism AS ("
                + resolved
                + ") SELECT summary_global.*"
                + projection
                + """
            FROM (SELECT 'S1' AS sample_pseudonym, 'P1' AS patient_pseudonym) AS
                summary_global
            CROSS JOIN pcr_sil_organism_marker_summary AS organism
            LEFT JOIN resolved_organism
              ON resolved_organism.pcr_sample_id=organism.pcr_sample_id
             AND resolved_organism.current_microb_sample_id=organism.microb_sample_id
             AND resolved_organism.microorganism_code=organism.microorganism_code
            CROSS JOIN (SELECT 'Synthetic organism' AS description) AS microorganism
            CROSS JOIN (SELECT NULL AS st) AS st_by_organism
        """
            )
        )
        direct_profiles = {
            row["microorganism_code"]: "".join(
                str(row[key])
                for key in (
                    "oxa_detected",
                    "vim_detected",
                    "kpc_detected",
                    "ndm_detected",
                )
            )
            for row in db.execute("SELECT * FROM pcr_sil_organism_marker_summary")
        }
        rows = db.execute("SELECT * FROM v_downstream_sil_pcr_organism").fetchall()
        assert {
            row["microorg_code"]: row["encoded_carba_org"] for row in rows
        } == profiles
        for row in rows:
            code = row["microorg_code"]
            for index, marker in enumerate(("oxa48", "vim", "kpc", "ndm")):
                assert row[f"organism_{marker}"] == int(profiles[code][index])
                assert row[f"direct_organism_{marker}"] == int(
                    direct_profiles[code][index]
                )

        global_view = views["v_downstream_sil_pcr_global"]
        global_resolved = db.execute(
            compatible(
                "WITH matched AS ("
                + cte(global_view, "matched")
                + "), retrospective_markers AS ("
                + global_sql
                + ") "
                + cte(global_view, "resolved")
            )
        ).fetchone()
        for index, key in enumerate(
            ("oxa_detected", "vim_detected", "kpc_detected", "ndm_detected")
        ):
            assert global_resolved[key] == int(
                any(value[index] == "1" for value in profiles.values())
            )
        links = {
            row["canonical_target"]: row["link_type"]
            for row in db.execute("SELECT * FROM pcr_sil_result_link")
        }
        return profiles, global_profile, links

    yield SimpleNamespace(db=db, sample=sample, run=run, params=params, views=views)
    db.close()


def test_inherit_all_matching_codes_but_exclude_absent_organism(model):
    model.sample(100, "2026-01-31 00:00:00", [(1, "K", []), (2, "E", [])])
    model.sample(
        90,
        "2026-01-25 00:00:00",
        [(3, "K", [("OXA", "P")]), (4, "E", [("VIM", "P")]), (5, "T", [("NDM", "P")])],
    )
    profiles, global_profile, links = model.run()
    assert profiles == {"K": "1000", "E": "0100"}
    assert global_profile == "1100"
    assert links == {
        "OXA48": "PRIOR_COMPATIBLE",
        "VIM": "PRIOR_COMPATIBLE",
        "KPC": "NO_EVIDENCE",
        "NDM": "NO_EVIDENCE",
    }
    assert {
        row[0]
        for row in model.db.execute(
            "SELECT current_isolate_id FROM pcr_sil_link_candidate"
        )
    } == {1, 2}


def test_first_compatible_sample_stops_search_for_every_organism(model):
    model.sample(100, "2026-01-31 00:00:00", [(1, "K", []), (2, "E", [])])
    model.sample(90, "2026-01-25 00:00:00", [(3, "K", [("OXA", "P")])])
    model.sample(80, "2026-01-20 00:00:00", [(4, "E", [("VIM", "P")])])
    profiles, global_profile, _ = model.run()
    assert profiles == {"K": "1000", "E": "0000"}
    assert global_profile == "1000"
    evidence = model.db.execute(
        "SELECT evidence_microb_sample_id FROM pcr_sil_retrospective_evidence"
    ).fetchall()
    assert [row[0] for row in evidence] == [90]
    audit = model.db.execute(
        "SELECT current_isolate_id, candidate_status FROM "
        "pcr_sil_link_candidate WHERE microorganism_code='E'"
    ).fetchall()
    assert [tuple(row) for row in audit] == [(2, "NO_EVIDENCE")]


def test_duplicate_isolates_and_multiple_markers_use_positive_dominance(model):
    model.sample(100, "2026-01-31 00:00:00", [(1, "K", []), (2, "K", [])])
    model.sample(
        90,
        "2026-01-25 00:00:00",
        [(3, "K", [("OXA", "N")]), (4, "K", [("OXA", "P"), ("KPC", "P")])],
    )
    profiles, global_profile, _ = model.run()
    assert profiles == {"K": "1010"}
    assert global_profile == "1010"
    accepted = model.db.execute(
        "SELECT DISTINCT current_isolate_id FROM "
        "pcr_sil_link_candidate WHERE candidate_status='ACCEPTED'"
    ).fetchall()
    assert {row[0] for row in accepted} == {1, 2}


@pytest.mark.parametrize("markers", [[], [("OXA", "N")]])
def test_matching_sample_without_positive_marker_stops_search(model, markers):
    model.sample(100, "2026-01-31 00:00:00", [(1, "K", [])])
    model.sample(
        90, "2026-01-25 00:00:00", [(2, "K", markers), (3, "T", [("NDM", "P")])]
    )
    model.sample(80, "2026-01-20 00:00:00", [(4, "K", [("OXA", "P")])])
    profiles, global_profile, links = model.run()
    assert profiles == {"K": "0000"}
    assert global_profile == "0000"
    assert set(links.values()) == {"NO_EVIDENCE"}
    assert (
        model.db.execute(
            "SELECT evidence_microb_sample_id FROM pcr_sil_retrospective_evidence"
        ).fetchone()[0]
        == 90
    )


@pytest.mark.parametrize(
    ("date", "patient"),
    [
        ("2025-12-31 23:59:59", 1),
        ("2026-02-01 00:00:00", 1),
        ("2026-01-25 00:00:00", 2),
    ],
)
def test_incompatible_patient_date_or_window_cannot_supply_evidence(
    model, date, patient
):
    model.sample(100, "2026-01-31 00:00:00", [(1, "K", [])])
    model.sample(90, date, [(2, "K", [("OXA", "P")])], patient=patient)
    profiles, global_profile, _ = model.run()
    assert profiles == {"K": "0000"}
    assert global_profile is None


def test_window_includes_exact_boundary(model):
    model.sample(100, "2026-01-31 00:00:00", [(1, "K", [])])
    model.sample(90, "2026-01-01 00:00:00", [(2, "K", [("OXA", "P")])])
    assert model.run()[:2] == ({"K": "1000"}, "1000")


def test_direct_profile_is_used_when_no_search_applies(model):
    model.sample(100, "2026-01-31 00:00:00", [(1, "K", [("OXA", "P")])])
    profiles, _, links = model.run()
    assert profiles == {"K": "1000"}
    assert links["OXA48"] == "DIRECT"


def test_original_characterized_organism_can_select_the_single_prior_sample(model):
    model.sample(100, "2026-01-31 00:00:00", [(1, "K", [("OXA", "P")]), (2, "E", [])])
    model.sample(90, "2026-01-25 00:00:00", [(3, "K", [("KPC", "P")])])
    model.sample(80, "2026-01-20 00:00:00", [(4, "E", [("VIM", "P")])])
    assert model.run()[:2] == ({"K": "0010", "E": "0000"}, "0010")
    # Historical resolution does not overwrite direct observations.
    assert (
        model.db.execute(
            "SELECT oxa_detected FROM "
            "pcr_sil_organism_marker_summary WHERE microorganism_code='K'"
        ).fetchone()[0]
        == 1
    )


def test_positive_without_exact_current_code_is_a_barrier(model):
    model.sample(100, "2026-01-31 00:00:00", [(1, "K", [])])
    model.sample(90, "2026-01-25 00:00:00", [(2, "T", [("NDM", "P")])])
    model.sample(80, "2026-01-20 00:00:00", [(3, "K", [("OXA", "P")])])
    assert model.run()[:2] == ({"K": "0000"}, None)


def test_negative_limit_still_stops_retrospective_search(model):
    model.params["max_negatives"] = 0
    model.sample(100, "2026-01-31 00:00:00", [(1, "K", [])])
    model.sample(90, "2026-01-25 00:00:00", [])
    model.sample(80, "2026-01-20 00:00:00", [(2, "K", [("OXA", "P")])])
    assert model.run()[:2] == ({"K": "0000"}, None)
