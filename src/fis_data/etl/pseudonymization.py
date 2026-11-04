"""Persistent pseudonym registries for paper exports."""

from __future__ import annotations

import re
from collections.abc import Callable

from sqlalchemy import delete, insert, inspect, select, text, update
from sqlalchemy.engine import Engine

from ..schema import (
    microb_patient,
    microb_sample,
    pcr_derivation_run,
    pcr_microb_well_validation,
    pcr_sample_result,
    pcr_sample_scope_gate,
    pseudonym_sample_registry,
    pseudonym_patient_registry,
    pseudonymization_audit,
    pseudonymization_counter,
)


class PseudonymizationRepository:
    """Assign stable numeric pseudonyms without tying them to rebuildable IDs."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def assign_normalized_microb(
        self,
        *,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, int]:
        """Register identifiers used by current integrated, eligible PCR facts."""

        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text(
                    "LOCK TABLE pseudonymization_counter, "
                    "pseudonym_patient_registry, pseudonym_sample_registry "
                    "IN SHARE ROW EXCLUSIVE MODE"
                ))
            audit_id = conn.execute(insert(pseudonymization_audit).values(
                action="ASSIGN", patient_count=0, sample_count=0,
            )).inserted_primary_key[0]
            valid_facts = conn.execute(
                select(
                    microb_sample.c.source_id,
                    microb_patient.c.nhc,
                    microb_sample.c.sample_number,
                )
                .select_from(
                    pcr_sample_result
                    .join(
                        pcr_derivation_run,
                        pcr_derivation_run.c.derivation_id
                        == pcr_sample_result.c.derivation_id,
                    )
                    .join(
                        pcr_microb_well_validation,
                        (pcr_microb_well_validation.c.derivation_id
                         == pcr_sample_result.c.derivation_id)
                        & (pcr_microb_well_validation.c.pcr_sample_id
                           == pcr_sample_result.c.pcr_sample_id),
                    )
                    .join(
                        microb_sample,
                        microb_sample.c.sample_id
                        == pcr_microb_well_validation.c.microb_sample_id,
                    )
                    .outerjoin(
                        microb_patient,
                        microb_patient.c.patient_id == microb_sample.c.patient_id,
                    )
                    .outerjoin(
                        pcr_sample_scope_gate,
                        (pcr_sample_scope_gate.c.derivation_id
                         == pcr_sample_result.c.derivation_id)
                        & (pcr_sample_scope_gate.c.pcr_sample_id
                           == pcr_sample_result.c.pcr_sample_id),
                    )
                )
                .where(
                    pcr_derivation_run.c.status == "SUCCESS",
                    pcr_derivation_run.c.is_current.is_(True),
                    pcr_microb_well_validation.c.validation_status == "MATCH_ELIGIBLE",
                    (pcr_sample_scope_gate.c.gate_status.is_(None))
                    | (pcr_sample_scope_gate.c.gate_status != "LEGACY_SCREENING_BLOCKED"),
                )
                .distinct()
                .order_by(microb_sample.c.source_id, microb_patient.c.nhc,
                          microb_sample.c.sample_number)
            ).all()
            patients = [
                (row.source_id, row.nhc)
                for row in valid_facts
                if row.nhc is not None and str(row.nhc).strip()
            ]
            samples = [
                (row.source_id, row.sample_number)
                for row in valid_facts
                if row.sample_number is not None and str(row.sample_number).strip()
            ]
            patient_count = self._assign_kind(
                conn, "PATIENT", patients, pseudonym_patient_registry, "P",
                progress_name="patients", on_progress=on_progress,
            )
            sample_count = self._assign_kind(
                conn, "SAMPLE", samples, pseudonym_sample_registry, "S",
                progress_name="samples", on_progress=on_progress,
            )
            conn.execute(update(pseudonymization_audit).where(
                pseudonymization_audit.c.audit_id == audit_id
            ).values(patient_count=patient_count, sample_count=sample_count))
        return {"patients_added": patient_count, "samples_added": sample_count}

    def import_legacy_registry(
        self, *, source_name: str, patients: list[dict[str, str]],
        samples: list[dict[str, str | None]],
    ) -> dict[str, int]:
        """Import validated rows from the former paper SQLite registry."""

        source_name = source_name.strip()
        if not source_name:
            raise ValueError("source_name is required")
        patient_by_id = {
            str(row["source_id"]).strip(): row
            for row in patients
        }
        if len(patient_by_id) != len(patients):
            raise ValueError("The legacy patient registry contains duplicate identifiers.")
        patient_pseudonyms = [str(row["pseudonym"]).strip() for row in patients]
        if len(set(patient_pseudonyms)) != len(patient_pseudonyms):
            raise ValueError("The legacy patient registry contains duplicate pseudonyms.")
        sample_ids = [str(row["source_id"]).strip() for row in samples]
        sample_pseudonyms = [str(row["pseudonym"]).strip() for row in samples]
        if len(set(sample_ids)) != len(sample_ids):
            raise ValueError("The legacy sample registry contains duplicate identifiers.")
        if len(set(sample_pseudonyms)) != len(sample_pseudonyms):
            raise ValueError("The legacy sample registry contains duplicate pseudonyms.")
        if any(not value for value in patient_pseudonyms + sample_pseudonyms):
            raise ValueError("The legacy registry contains an empty pseudonym.")
        if any(
            row.get("patient_source_id")
            and str(row["patient_source_id"]).strip() not in patient_by_id
            for row in samples
        ):
            raise ValueError("A legacy sample refers to a patient absent from its patient registry.")

        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text(
                    "LOCK TABLE pseudonymization_counter, "
                    "pseudonym_patient_registry, pseudonym_sample_registry "
                    "IN SHARE ROW EXCLUSIVE MODE"
                ))
            source_id = conn.execute(text(
                "SELECT source_id FROM ctl_source WHERE source_name=:source_name"
            ), {"source_name": source_name}).scalar_one_or_none()
            if source_id is None:
                raise ValueError(f"Unknown source name: {source_name}")
            inserted_patients = self._import_kind(
                conn, patients, pseudonym_patient_registry, int(source_id)
            )
            inserted_samples = self._import_kind(
                conn, samples, pseudonym_sample_registry, int(source_id)
            )
            self._advance_counter(conn, "PATIENT", pseudonym_patient_registry, "P")
            self._advance_counter(conn, "SAMPLE", pseudonym_sample_registry, "S")
            conn.execute(insert(pseudonymization_audit).values(
                action="IMPORT", patient_count=inserted_patients,
                sample_count=inserted_samples, reason=f"SQLite registry import: {source_name}",
            ))
        return {"patients_imported": inserted_patients, "samples_imported": inserted_samples}

    @staticmethod
    def _import_kind(conn, rows, table, destination_source_id: int) -> int:
        existing_by_identifier = dict(conn.execute(select(
            table.c.source_identifier, table.c.pseudonym
        )).all())
        existing_pseudonyms = set(conn.execute(select(table.c.pseudonym)).scalars())
        inserted = 0
        for row in rows:
            identifier = str(row["source_id"]).strip()
            pseudonym = str(row["pseudonym"]).strip()
            old = existing_by_identifier.get(identifier)
            if old is not None:
                if old != pseudonym:
                    raise ValueError(f"Identifier {identifier!r} already has a different pseudonym.")
                continue
            if pseudonym in existing_pseudonyms:
                raise ValueError(f"Pseudonym {pseudonym!r} already belongs to another identifier.")
            conn.execute(insert(table).values(
                source_id=destination_source_id,
                source_identifier=identifier,
                pseudonym=pseudonym,
            ))
            existing_by_identifier[identifier] = pseudonym
            existing_pseudonyms.add(pseudonym)
            inserted += 1
        return inserted

    def globalize_registry_keys(self, *, reason: str) -> dict[str, int]:
        """Merge source-scoped duplicate keys, retaining the oldest mapping."""

        reason = reason.strip()
        if not reason:
            raise ValueError("A non-empty reason is required to globalize pseudonyms.")
        if self.engine.dialect.name != "postgresql":
            raise ValueError("Globalizing an existing registry requires PostgreSQL.")

        specs = (
            (pseudonym_patient_registry, "uq_pseudonym_patient_identifier"),
            (pseudonym_sample_registry, "uq_pseudonym_sample_identifier"),
        )
        removed = {"patients": 0, "samples": 0}
        with self.engine.begin() as conn:
            conn.execute(text(
                "LOCK TABLE pseudonymization_counter, "
                "pseudonym_patient_registry, pseudonym_sample_registry "
                "IN SHARE ROW EXCLUSIVE MODE"
            ))
            for (table, global_constraint), label in zip(
                specs, ("patients", "samples"), strict=True
            ):
                constraints = inspect(conn).get_unique_constraints(table.name)
                has_global_key = any(
                    tuple(item.get("column_names") or ()) == ("source_identifier",)
                    for item in constraints
                )
                scoped_constraints = [
                    item["name"]
                    for item in constraints
                    if set(item.get("column_names") or ())
                    == {"source_id", "source_identifier"}
                ]
                if not has_global_key:
                    deleted = conn.execute(text(f"""
                        DELETE FROM {table.name} AS duplicate
                        USING {table.name} AS retained
                        WHERE duplicate.source_identifier=retained.source_identifier
                          AND duplicate.registry_id>retained.registry_id
                    """)).rowcount
                    removed[label] = max(0, int(deleted or 0))
                for constraint_name in scoped_constraints:
                    quoted_name = conn.dialect.identifier_preparer.quote(
                        constraint_name
                    )
                    conn.execute(text(
                        f"ALTER TABLE {table.name} DROP CONSTRAINT {quoted_name}"
                    ))
                if not has_global_key:
                    conn.execute(text(
                        f"ALTER TABLE {table.name} ADD CONSTRAINT "
                        f"{global_constraint} UNIQUE (source_identifier)"
                    ))
            conn.execute(insert(pseudonymization_audit).values(
                action="GLOBALIZE",
                patient_count=removed["patients"],
                sample_count=removed["samples"],
                reason=reason,
            ))
        return removed

    @staticmethod
    def _advance_counter(conn, kind: str, table, prefix: str) -> None:
        values = conn.execute(select(table.c.pseudonym)).scalars().all()
        maximum = max((
            int(match.group(1))
            for value in values
            if (match := re.fullmatch(rf"{prefix}-(\d+)", value))
        ), default=0)
        current = conn.execute(select(pseudonymization_counter.c.last_number).where(
            pseudonymization_counter.c.entity_type == kind
        )).scalar_one_or_none()
        if current is None:
            conn.execute(insert(pseudonymization_counter).values(
                entity_type=kind, last_number=maximum
            ))
        elif int(current) < maximum:
            conn.execute(update(pseudonymization_counter).where(
                pseudonymization_counter.c.entity_type == kind
            ).values(last_number=maximum))

    @staticmethod
    def _assign_kind(
        conn,
        kind,
        rows,
        table,
        prefix: str,
        *,
        progress_name: str,
        on_progress: Callable[[str, int, int], None] | None,
    ) -> int:
        source_by_identifier: dict[str, int] = {}
        for row in rows:
            identifier = str(row[1]).strip() if row[1] is not None else ""
            if identifier:
                source_by_identifier.setdefault(identifier, int(row[0]))
        identifiers = sorted(source_by_identifier)
        if not identifiers:
            return 0
        existing = set(conn.execute(
            select(table.c.source_identifier)
        ).tuples())
        existing_identifiers = {row[0] for row in existing}
        missing = [identifier for identifier in identifiers if identifier not in existing_identifiers]
        if not missing:
            return 0
        total = len(missing)
        if on_progress is not None:
            on_progress(progress_name, 0, total)
        counter = conn.execute(select(pseudonymization_counter.c.last_number).where(
            pseudonymization_counter.c.entity_type == kind
        )).scalar_one_or_none()
        registered_numbers = [
            int(match.group(1))
            for pseudonym in conn.execute(select(table.c.pseudonym)).scalars()
            if (match := re.fullmatch(rf"{prefix}-(\d+)", pseudonym))
        ]
        counter = max([int(counter or 0), *registered_numbers])
        if conn.execute(select(pseudonymization_counter.c.entity_type).where(
            pseudonymization_counter.c.entity_type == kind
        )).scalar_one_or_none() is None:
            conn.execute(insert(pseudonymization_counter).values(
                entity_type=kind, last_number=counter
            ))
        next_number = int(counter)
        width = 4 if kind == "PATIENT" else 5
        for completed, identifier in enumerate(missing, start=1):
            next_number += 1
            conn.execute(insert(table).values(
                source_id=source_by_identifier[identifier],
                source_identifier=identifier,
                pseudonym=f"{prefix}-{next_number:0{width}d}",
            ))
            if on_progress is not None:
                on_progress(progress_name, completed, total)
        conn.execute(update(pseudonymization_counter).where(
            pseudonymization_counter.c.entity_type == kind
        ).values(last_number=next_number))
        return len(missing)

    def destroy_registry(self, *, reason: str) -> dict[str, int]:
        """Explicitly destroy mappings while retaining a count-only audit event."""

        reason = reason.strip()
        if not reason:
            raise ValueError("A non-empty reason is required to destroy pseudonyms.")
        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text(
                    "LOCK TABLE pseudonymization_counter, "
                    "pseudonym_patient_registry, pseudonym_sample_registry "
                    "IN SHARE ROW EXCLUSIVE MODE"
                ))
            patients = int(conn.execute(select(
                text("COUNT(*)")
            ).select_from(pseudonym_patient_registry)).scalar_one())
            samples = int(conn.execute(select(
                text("COUNT(*)")
            ).select_from(pseudonym_sample_registry)).scalar_one())
            conn.execute(delete(pseudonym_patient_registry))
            conn.execute(delete(pseudonym_sample_registry))
            conn.execute(delete(pseudonymization_counter))
            conn.execute(insert(pseudonymization_audit).values(
                action="DESTROY", patient_count=patients,
                sample_count=samples, reason=reason,
            ))
        return {"patients_destroyed": patients, "samples_destroyed": samples}
