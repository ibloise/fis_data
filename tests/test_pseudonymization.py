from click.testing import CliRunner
from sqlalchemy import create_engine, insert, select

from fis_data.cli import pseudonymize
from fis_data.etl.pseudonymization import PseudonymizationRepository
from fis_data.schema import (
    ctl_source,
    microb_patient,
    microb_sample,
    pcr_derivation_run,
    pcr_microb_well_validation,
    pcr_sample,
    pcr_sample_result,
    pcr_sample_scope_gate,
    pseudonym_patient_registry,
    pseudonym_sample_registry,
    pseudonymization_audit,
    create_schema,
)


def _add_valid_fact(conn, source_id: int, nhc: str, sample_number: str,
                    *, gate_status: str | None = None) -> None:
    patient_id = conn.execute(insert(microb_patient).values(
        source_id=source_id, nhc=nhc
    )).inserted_primary_key[0]
    microb_sample_id = conn.execute(insert(microb_sample).values(
        source_id=source_id, patient_id=patient_id, sample_number=sample_number
    )).inserted_primary_key[0]
    derivation_id = conn.execute(select(
        pcr_derivation_run.c.derivation_id
    ).where(pcr_derivation_run.c.is_current.is_(True))).scalar_one_or_none()
    if derivation_id is None:
        derivation_id = conn.execute(insert(pcr_derivation_run).values(
            algorithm_version="test", rule_version="test", parameters_json="{}",
            status="SUCCESS", is_current=True,
        )).inserted_primary_key[0]
    pcr_sample_id = conn.execute(insert(pcr_sample).values(
        sample_key=f"PCR-{derivation_id}-{sample_number}"
    )).inserted_primary_key[0]
    conn.execute(insert(pcr_sample_result).values(
        derivation_id=derivation_id, pcr_sample_id=pcr_sample_id,
        pcr_category="PCR_NEGATIVE", decision_source="SPECIFIC", details_json="{}",
    ))
    conn.execute(insert(pcr_microb_well_validation).values(
        derivation_id=derivation_id, pcr_well_id=pcr_sample_id,
        pcr_sample_id=pcr_sample_id, microb_sample_id=microb_sample_id,
        validation_status="MATCH_ELIGIBLE", details_json="{}",
    ))
    if gate_status is not None:
        conn.execute(insert(pcr_sample_scope_gate).values(
            derivation_id=derivation_id, pcr_sample_id=pcr_sample_id,
            gate_status=gate_status, details_json="{}",
        ))


def test_pseudonyms_survive_reprocessing_and_assign_new_numbers(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pseudonyms.sqlite'}")
    create_schema(engine)
    with engine.begin() as conn:
        source_id = conn.execute(insert(ctl_source).values(
            source_name="MICROBIOLOGY", source_type="test"
        )).inserted_primary_key[0]
        _add_valid_fact(conn, source_id, "00123", "000045")

    repository = PseudonymizationRepository(engine)
    progress = []
    assert repository.assign_normalized_microb(
        on_progress=lambda entity, completed, total: progress.append(
            (entity, completed, total)
        )
    ) == {"patients_added": 1, "samples_added": 1}
    assert progress == [
        ("patients", 0, 1), ("patients", 1, 1),
        ("samples", 0, 1), ("samples", 1, 1),
    ]
    assert repository.assign_normalized_microb() == {"patients_added": 0, "samples_added": 0}

    with engine.begin() as conn:
        _add_valid_fact(conn, source_id, "00124", "000046")

    assert repository.assign_normalized_microb() == {"patients_added": 1, "samples_added": 1}
    with engine.connect() as conn:
        patients = dict(conn.execute(select(
            pseudonym_patient_registry.c.source_identifier,
            pseudonym_patient_registry.c.pseudonym,
        )).all())
        samples = dict(conn.execute(select(
            pseudonym_sample_registry.c.source_identifier,
            pseudonym_sample_registry.c.pseudonym,
        )).all())
    assert patients == {"00123": "P-0001", "00124": "P-0002"}
    assert samples == {"000045": "S-00001", "000046": "S-00002"}


def test_same_identifier_across_sources_gets_one_pseudonym(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'global-keys.sqlite'}")
    create_schema(engine)
    with engine.begin() as conn:
        source_ids = [
            conn.execute(insert(ctl_source).values(
                source_name=source_name, source_type="test"
            )).inserted_primary_key[0]
            for source_name in ("PCR", "MICROBIOLOGY")
        ]
        for index, source_id in enumerate(source_ids):
            _add_valid_fact(conn, source_id, "00123", f"000045-{index}")

    result = PseudonymizationRepository(engine).assign_normalized_microb()

    assert result == {"patients_added": 1, "samples_added": 2}
    with engine.connect() as conn:
        patients = conn.execute(select(
            pseudonym_patient_registry.c.source_identifier,
            pseudonym_patient_registry.c.pseudonym,
        )).all()
        samples = conn.execute(select(
            pseudonym_sample_registry.c.source_identifier,
            pseudonym_sample_registry.c.pseudonym,
        )).all()
    assert patients == [("00123", "P-0001")]
    assert samples == [("000045-0", "S-00001"), ("000045-1", "S-00002")]


def test_only_current_integrated_qc_eligible_facts_are_pseudonymized(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'eligible-only.sqlite'}")
    create_schema(engine)
    with engine.begin() as conn:
        source_id = conn.execute(insert(ctl_source).values(
            source_name="MICROBIOLOGY", source_type="test"
        )).inserted_primary_key[0]
        _add_valid_fact(conn, source_id, "VALID-NHC", "VALID-SAMPLE")
        patient_id = conn.execute(insert(microb_patient).values(
            source_id=source_id, nhc="NOT-IN-VALID-FACTS"
        )).inserted_primary_key[0]
        conn.execute(insert(microb_sample).values(
            source_id=source_id, patient_id=patient_id,
            sample_number="NOT-IN-VALID-FACTS",
        ))
        _add_valid_fact(
            conn, source_id, "BLOCKED-NHC", "BLOCKED-SAMPLE",
            gate_status="LEGACY_SCREENING_BLOCKED",
        )

    result = PseudonymizationRepository(engine).assign_normalized_microb()

    assert result == {"patients_added": 1, "samples_added": 1}
    with engine.connect() as conn:
        patients = conn.execute(select(
            pseudonym_patient_registry.c.source_identifier
        )).scalars().all()
        samples = conn.execute(select(
            pseudonym_sample_registry.c.source_identifier
        )).scalars().all()
    assert patients == ["VALID-NHC"]
    assert samples == ["VALID-SAMPLE"]


def test_legacy_registry_import_is_idempotent_and_advances_counters(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-import.sqlite'}")
    create_schema(engine)
    with engine.begin() as conn:
        source_id = conn.execute(insert(ctl_source).values(
            source_name="MICROBIOLOGY", source_type="test"
        )).inserted_primary_key[0]
        _add_valid_fact(conn, source_id, "P-00123", "S-00045")
    repository = PseudonymizationRepository(engine)
    legacy_patients = [{"source_id": "P-00123", "pseudonym": "P-0007"}]
    legacy_samples = [{
        "source_id": "S-00045", "patient_source_id": "P-00123",
        "pseudonym": "S-00042",
    }]
    assert repository.import_legacy_registry(
        source_name="MICROBIOLOGY", patients=legacy_patients, samples=legacy_samples
    ) == {"patients_imported": 1, "samples_imported": 1}
    assert repository.import_legacy_registry(
        source_name="MICROBIOLOGY", patients=legacy_patients, samples=legacy_samples
    ) == {"patients_imported": 0, "samples_imported": 0}
    with engine.begin() as conn:
        _add_valid_fact(conn, source_id, "P-00999", "S-00999")
    repository.assign_normalized_microb()
    with engine.connect() as conn:
        patient_map = dict(conn.execute(select(
            pseudonym_patient_registry.c.source_identifier,
            pseudonym_patient_registry.c.pseudonym,
        )).all())
        sample_map = dict(conn.execute(select(
            pseudonym_sample_registry.c.source_identifier,
            pseudonym_sample_registry.c.pseudonym,
        )).all())
    assert patient_map == {"P-00123": "P-0007", "P-00999": "P-0008"}
    assert sample_map == {"S-00045": "S-00042", "S-00999": "S-00043"}


def test_destroy_requires_reason_and_preserves_count_only_audit(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'destroy.sqlite'}")
    create_schema(engine)
    with engine.begin() as conn:
        source_id = conn.execute(insert(ctl_source).values(
            source_name="MICROBIOLOGY", source_type="test"
        )).inserted_primary_key[0]
        _add_valid_fact(conn, source_id, "123", "S-123")
    repository = PseudonymizationRepository(engine)
    repository.assign_normalized_microb()
    assert repository.destroy_registry(reason="Approved test reset") == {
        "patients_destroyed": 1, "samples_destroyed": 1
    }
    with engine.connect() as conn:
        actions = conn.execute(select(
            pseudonymization_audit.c.action,
            pseudonymization_audit.c.patient_count,
        ).order_by(pseudonymization_audit.c.audit_id)).all()
    assert actions == [("ASSIGN", 1), ("DESTROY", 1)]


def test_pseudonymize_cli_shows_count_only_progress(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pseudonym-progress.sqlite'}")
    create_schema(engine)
    with engine.begin() as conn:
        source_id = conn.execute(insert(ctl_source).values(
            source_name="MICROBIOLOGY", source_type="test"
        )).inserted_primary_key[0]
        _add_valid_fact(conn, source_id, "00123", "000045")
    monkeypatch.setattr("fis_data.cli._engine_for_db_url", lambda _url: engine)

    result = CliRunner().invoke(pseudonymize, [])

    assert result.exit_code == 0, result.output
    assert "Assigning patients pseudonyms" in result.output
    assert "Assigning samples pseudonyms" in result.output
    assert "1/1" in result.output
    assert "00123" not in result.output
    assert "000045" not in result.output
    assert '"patients_added": 1' in result.output
    assert '"samples_added": 1' in result.output
