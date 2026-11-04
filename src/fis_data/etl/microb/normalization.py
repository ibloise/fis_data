"""Set-oriented normalization of Microb staging evidence."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable
from datetime import date, datetime
from math import ceil
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection, Engine

from ...json_utils import loads_json
from ...schema import (
    ctl_materialization_item,
    microb_analysis_result, microb_determination, microb_entity_lineage,
    microb_isolate, microb_isolate_observation, microb_isolate_st_link,
    microb_normalization_issue,
    microb_patient, microb_sample, microb_sample_analysis,
    microb_sample_observation, microb_susceptibility_result,
    pcr_microb_well_validation, pcr_sil_culture_classification,
    pcr_sil_culture_marker_summary, pcr_sil_isolate_marker_summary,
    pcr_sil_link_candidate, pcr_sil_organism_marker_summary,
    pcr_sil_result_link, pcr_sil_retrospective_evidence,
    ref_microb_catalog, ref_microb_catalog_entry, stg_microb_antibiogram,
    stg_microb_technique, stg_st_record,
)

BATCH_SIZE = 5_000
UPSERT_BATCH_SIZE = 2_000
STAGES = (
    "patients", "samples", "sample analyses", "analysis results", "isolates",
    "observations and determinations", "susceptibilities", "lineage and issues",
)
CATALOGS: dict[str, tuple[str, ...]] = {
    # The SIL uses these two catalog namespaces interchangeably in some exports.
    # Prefer the semantic column's own catalog and use the other as a fallback.
    "origin_id": ("procedencias", "servicon", "procedencias_extra", "servicios_extra"),
    "service_id": ("servicon", "procedencias", "servicios_extra", "procedencias_extra"),
    "specimen_type_id": ("muestras",), "specimen_location_id": ("localizacion",),
    "analysis_id": ("analisis", "analisis_sero"),
    "microorganism_id": ("microorg",), "observation_id": ("observacion", "observamic"),
    "technique_id": ("tecnicas",),
    "qualitative_result_id": ("rescualt",),
    "quantitative_result_id": ("rescuant",),
    "antibiotic_id": ("antibiotico",), "cmi_id": ("cmis",),
    "interpretation_id": ("resantib",),
}


def _text(value: Any) -> str | None:
    value = str(value).strip() if value is not None else ""
    return value or None


def _catalog_label(value: Any) -> str | None:
    value = _text(value)
    if value is None:
        return None
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(value.casefold().split())


def _datetime(raw_date: Any, raw_time: Any = None) -> datetime | None:
    day, clock = _text(raw_date), _text(raw_time)
    if not day:
        return None
    value = f"{day} {clock}" if clock else day
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def _date(value: Any) -> date | None:
    parsed = _datetime(value)
    return parsed.date() if parsed else None


def _invalidate_st_links(conn: Connection, *, reason: str) -> None:
    """Retain ST inputs but require their isolate matches to be rebuilt."""

    conn.execute(delete(microb_isolate_st_link))
    conn.execute(update(stg_st_record).values(
        match_status="PENDING_MICROB_RELINK",
        matched_sample_count=0,
        matched_isolate_count=0,
    ))
    conn.execute(update(ctl_materialization_item).where(
        ctl_materialization_item.c.materializer_name == "st-domain"
    ).values(status="REPROCESS_REQUIRED", error=reason))


class MicrobNormalizer:
    """Load staging rows in clinical-entity batches, preserving provenance."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def rebuild(self) -> None:
        """Truncate derived Microb entities and their dependent PCR audit."""

        # Keep the dependent PCR/Microb validation explicit instead of using
        # CASCADE: it becomes stale when microb_sample IDs are rebuilt.
        tables = (
            pcr_sil_culture_marker_summary,
            pcr_sil_isolate_marker_summary,
            pcr_sil_organism_marker_summary,
            pcr_sil_result_link,
            pcr_sil_link_candidate,
            pcr_sil_retrospective_evidence,
            pcr_sil_culture_classification,
            pcr_microb_well_validation,
            microb_susceptibility_result,
            microb_isolate_observation,
            microb_sample_observation,
            microb_determination,
            microb_entity_lineage,
            microb_normalization_issue,
            microb_isolate_st_link,
            microb_isolate,
            microb_analysis_result,
            microb_sample_analysis,
            microb_sample,
            microb_patient,
        )
        table_names = ", ".join(table.name for table in tables)
        with self.engine.begin() as conn:
            _invalidate_st_links(conn, reason="Microb rebuild invalidated ST isolate links.")
            conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY"))

    def batch_count(self, *, file_id: int | None = None) -> int:
        with self.engine.connect() as conn:
            query = select(func.count()).select_from(stg_microb_technique)
            if file_id is not None:
                query = query.where(stg_microb_technique.c.file_id == file_id)
            return ceil(int(conn.execute(query).scalar_one()) / BATCH_SIZE)

    def normalize(
        self, *, file_id: int | None = None,
        on_stage: Callable[[str], None] | None = None,
    ) -> dict[str, int]:
        catalogs, hospital_links = self._catalogs()
        counts = {"techniques": 0, "susceptibilities": 0, "issues": 0}
        last_id = 0
        st_links_invalidated = False
        while True:
            batch = self._fetch_batch(file_id=file_id, last_id=last_id)
            if not batch:
                return counts
            last_id = int(batch[-1]["stg_microb_technique_id"])
            with self.engine.begin() as conn:
                if not st_links_invalidated:
                    _invalidate_st_links(
                        conn,
                        reason="Microb normalization changed isolate matching inputs.",
                    )
                    st_links_invalidated = True
                stats = self._normalize_batch(
                    conn, batch, catalogs, hospital_links, on_stage
                )
            for key, value in stats.items():
                counts[key] += value

    def _fetch_batch(self, *, file_id: int | None, last_id: int) -> list[dict[str, Any]]:
        query = select(stg_microb_technique).where(
            stg_microb_technique.c.stg_microb_technique_id > last_id
        )
        if file_id is not None:
            query = query.where(stg_microb_technique.c.file_id == file_id)
        query = query.order_by(stg_microb_technique.c.stg_microb_technique_id).limit(BATCH_SIZE)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(query).mappings()]

    def _catalogs(self) -> tuple[dict[str, dict[str, int]], dict[tuple[str, str], int]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(
                    ref_microb_catalog.c.catalog_name,
                    ref_microb_catalog_entry.c.code,
                    ref_microb_catalog_entry.c.description,
                    ref_microb_catalog_entry.c.raw_fields_json,
                    ref_microb_catalog_entry.c.ref_microb_catalog_entry_id,
                )
                .join(ref_microb_catalog_entry, ref_microb_catalog_entry.c.ref_microb_catalog_id == ref_microb_catalog.c.ref_microb_catalog_id)
                .where(ref_microb_catalog.c.is_current.is_(True))
            ).all()
        catalogs: dict[str, dict[str, int]] = {}
        hospital_ids: dict[str, int] = {}
        for name, code, description, _raw_fields_json, entry_id in rows:
            catalogs.setdefault(str(name), {})[str(code).strip()] = int(entry_id)
            if name == "hospitales":
                label = _catalog_label(description)
                if label:
                    hospital_ids[label] = int(entry_id)

        hospital_links: dict[tuple[str, str], int] = {}
        hospital_field_indexes = {
            "procedencias_extra": 5,
            "servicios_extra": 3,
        }
        for name, code, _description, raw_fields_json, _entry_id in rows:
            field_index = hospital_field_indexes.get(str(name))
            if field_index is None:
                continue
            raw_fields = loads_json(raw_fields_json)
            label = _catalog_label(
                raw_fields[field_index] if len(raw_fields) > field_index else None
            )
            hospital_id = hospital_ids.get(label) if label else None
            if hospital_id is not None:
                hospital_links[(str(name), str(code).strip())] = hospital_id
        return catalogs, hospital_links

    def _normalize_batch(
        self,
        conn: Connection,
        rows: list[dict[str, Any]],
        catalogs,
        hospital_links: dict[tuple[str, str], int],
        progress,
    ) -> dict[str, int]:
        issues: list[dict[str, Any]] = []
        patient_rows: list[dict[str, Any]] = []
        for row in rows:
            nhc = _text(row["nhc"])
            if not nhc:
                self._issue(issues, row, "nhc", None, "ERROR", "NHC nulo")
                continue
            patient_rows.append({"source_id": row["source_id"], "nhc": nhc, "afi": _text(row["afi"]), "sex": _text(row["sexo"]), "birth_date": _date(row["fec_nacimiento_raw"]), "first_name": _text(row["nombre"]), "surname1": _text(row["apellido1"]), "surname2": _text(row["apellido2"])})
        patients = self._upsert(conn, microb_patient, patient_rows, ("source_id", "nhc"), "patient_id")
        self._progress(progress, "patients")

        sample_rows: list[dict[str, Any]] = []
        for row in rows:
            number = _text(row["num_muestra"])
            if not number:
                self._issue(issues, row, "num_muestra", None, "ERROR", "Número de muestra nulo")
                continue
            origin_code = _text(row["cod_procedencia"])
            service_code = _text(row["cod_servicio"])
            sample_rows.append({"source_id": row["source_id"], "patient_id": patients.get((row["source_id"], _text(row["nhc"]))), "sample_number": number, "received_at": _datetime(row["fec_recep_raw"], row["hora_recep_raw"]), "entered_at": _datetime(row["fec_entrada_raw"], row["hora_entrada_raw"]), "origin_id": self._catalog(catalogs, "origin_id", origin_code, row, issues), "service_id": self._catalog(catalogs, "service_id", service_code, row, issues), "hospital_id": self._hospital_id(hospital_links, origin_code, service_code), "specimen_type_id": self._catalog(catalogs, "specimen_type_id", row["cod_muestra"], row, issues), "specimen_location_id": self._catalog(catalogs, "specimen_location_id", row["cod_localizacion"], row, issues), "floor": _text(row["planta"]), "room": _text(row["habitacion"]), "box": _text(row["box"])})
        samples = self._upsert(conn, microb_sample, sample_rows, ("source_id", "sample_number"), "sample_id")
        self._progress(progress, "samples")

        analysis_rows: list[dict[str, Any]] = []
        analysis_kinds: dict[tuple[int, str], str | None] = {}
        for row in rows:
            sample_id = samples.get((row["source_id"], _text(row["num_muestra"])))
            code = _text(row["cod_analisis"])
            if not sample_id or not code:
                if sample_id and not code:
                    self._issue(issues, row, "cod_analisis", None, "ERROR", "Código de análisis nulo")
                continue
            catalog_id, catalog_name = self._catalog_match(
                catalogs, "analysis_id", code, row, issues
            )
            analysis_kind = {
                "analisis": "BACTERIOLOGICAL",
                "analisis_sero": "SEROLOGICAL",
            }.get(catalog_name)
            analysis_kinds[(sample_id, code)] = analysis_kind
            analysis_rows.append({"sample_id": sample_id, "analysis_id": catalog_id, "analysis_code": code, "analysis_kind": analysis_kind, "box_id": _text(row["box"]), "resulted_at": _datetime(row["fec_resultado_raw"], row["hora_resultado_raw"])})
        analyses = self._upsert(conn, microb_sample_analysis, analysis_rows, ("sample_id", "analysis_code"), "sample_analysis_id")
        self._progress(progress, "sample analyses")

        result_rows: list[dict[str, Any]] = []
        for row in rows:
            sample_id = samples.get((row["source_id"], _text(row["num_muestra"])))
            analysis_id = analyses.get((sample_id, _text(row["cod_analisis"]))) if sample_id else None
            code = _text(row["cod_resultado"])
            analysis_kind = analysis_kinds.get((sample_id, _text(row["cod_analisis"])))
            if not analysis_id:
                continue
            if not code:
                if self._requires_result_code(analysis_kind):
                    self._issue(issues, row, "cod_resultado", None, "ERROR", "Código de resultado nulo")
                continue
            result_rows.append({"sample_analysis_id": analysis_id, "result_code_id": self._analysis_result(catalogs, analysis_kind, code, row, issues), "result_code": code})
        results = self._upsert(conn, microb_analysis_result, result_rows, ("sample_analysis_id", "result_code"), "analysis_result_id")
        self._progress(progress, "analysis results")

        isolate_rows: list[dict[str, Any]] = []
        technique_isolates: dict[int, int] = {}
        for row in rows:
            sample_id = samples.get((row["source_id"], _text(row["num_muestra"])))
            analysis_code = _text(row["cod_analisis"])
            analysis_id = analyses.get((sample_id, analysis_code)) if sample_id else None
            result_id = results.get((analysis_id, _text(row["cod_resultado"]))) if analysis_id else None
            microorganism_code = _text(row["cod_microorganismo"])
            if (
                analysis_kinds.get((sample_id, analysis_code)) != "BACTERIOLOGICAL"
                or not result_id
                or not microorganism_code
            ):
                continue
            number = _text(row["aislamiento"]) or "1"
            isolate_rows.append({"analysis_result_id": result_id, "isolate_number": number, "microorganism_id": self._catalog(catalogs, "microorganism_id", microorganism_code, row, issues), "microorganism_code": microorganism_code})
        isolates = self._upsert(conn, microb_isolate, isolate_rows, ("analysis_result_id", "isolate_number"), "isolate_id")
        for row in rows:
            sample_id = samples.get((row["source_id"], _text(row["num_muestra"])))
            analysis_code = _text(row["cod_analisis"])
            analysis_id = analyses.get((sample_id, analysis_code)) if sample_id else None
            result_id = results.get((analysis_id, _text(row["cod_resultado"]))) if analysis_id else None
            if (
                analysis_kinds.get((sample_id, analysis_code)) == "BACTERIOLOGICAL"
                and result_id
                and _text(row["cod_microorganismo"])
            ):
                technique_isolates[int(row["stg_microb_technique_id"])] = isolates[(result_id, _text(row["aislamiento"]) or "1")]
        self._progress(progress, "isolates")

        sample_observations, isolate_observations, determinations = [], [], []
        for row in rows:
            sample_id = samples.get((row["source_id"], _text(row["num_muestra"])))
            isolate_id = technique_isolates.get(int(row["stg_microb_technique_id"]))
            if sample_id:
                for code in (_text(row["cod_obs"]), _text(row["cod_observaciones"]), _text(row["cod_observaciones_2"])):
                    if code:
                        sample_observations.append({"sample_id": sample_id, "observation_id": self._observation(catalogs, code, row, issues), "observation_code": code})
            if isolate_id and _text(row["cod_obs_2"]):
                code = _text(row["cod_obs_2"])
                isolate_observations.append({"isolate_id": isolate_id, "observation_id": self._observation(catalogs, code, row, issues), "observation_code": code})
            if sample_id and _text(row["cod_analisis"]) and _text(row["cod_tecnica"]):
                analysis_id = analyses.get((sample_id, _text(row["cod_analisis"])))
                if analysis_id:
                    quantitative_code = _text(row["cod_result_cuantitativo"])
                    quantitative_id, quantitative_literal = self._quantitative_result(
                        catalogs, quantitative_code
                    )
                    determinations.append({"sample_analysis_id": analysis_id, "technique_id": self._catalog(catalogs, "technique_id", row["cod_tecnica"], row, issues), "technique_code": _text(row["cod_tecnica"]), "qualitative_result_id": self._catalog(catalogs, "qualitative_result_id", row["cod_result_cualitativo"], row, issues), "qualitative_result_code": _text(row["cod_result_cualitativo"]), "quantitative_result_id": quantitative_id, "quantitative_result": quantitative_code, "quantitative_result_literal": quantitative_literal})
        self._upsert(conn, microb_sample_observation, sample_observations, ("sample_id", "observation_code"), "sample_observation_id")
        self._upsert(conn, microb_isolate_observation, isolate_observations, ("isolate_id", "observation_code"), "isolate_observation_id")
        self._upsert(conn, microb_determination, determinations, ("sample_analysis_id", "technique_code"), "determination_id")
        self._progress(progress, "observations and determinations")

        technique_ids = list(technique_isolates)
        antibiograms = conn.execute(select(stg_microb_antibiogram).where(stg_microb_antibiogram.c.stg_microb_technique_id.in_(technique_ids))).mappings().all() if technique_ids else []
        susceptibility_rows = []
        by_technique = {int(row["stg_microb_technique_id"]): row for row in rows}
        for antibiotic in antibiograms:
            technique = by_technique[int(antibiotic["stg_microb_technique_id"])]
            susceptibility_rows.append({"isolate_id": technique_isolates[int(antibiotic["stg_microb_technique_id"])], "antibiotic_id": self._catalog(catalogs, "antibiotic_id", antibiotic["cod_atb"], technique, issues), "antibiotic_code": antibiotic["cod_atb"], "cmi_id": self._catalog(catalogs, "cmi_id", antibiotic["cod_cmi"], technique, issues), "cmi_code": _text(antibiotic["cod_cmi"]), "interpretation_id": self._catalog(catalogs, "interpretation_id", antibiotic["cod_int"], technique, issues), "interpretation_code": _text(antibiotic["cod_int"]), "cmi_description": _text(antibiotic["des_cmi"]), "stg_microb_antibiogram_id": antibiotic["stg_microb_antibiogram_id"]})
        self._upsert(conn, microb_susceptibility_result, susceptibility_rows, ("isolate_id", "stg_microb_antibiogram_id"), "susceptibility_result_id")
        self._progress(progress, "susceptibilities")

        lineage = []
        for row in rows:
            stg_id = int(row["stg_microb_technique_id"])
            context = {key: row[key] for key in ("source_id", "run_id", "file_id", "line_id")}
            sample_id = samples.get((row["source_id"], _text(row["num_muestra"])))
            analysis_id = analyses.get((sample_id, _text(row["cod_analisis"]))) if sample_id else None
            result_id = results.get((analysis_id, _text(row["cod_resultado"]))) if analysis_id else None
            ids = (
                ("patient", patients.get((row["source_id"], _text(row["nhc"])))),
                ("sample", sample_id),
                ("sample_analysis", analysis_id),
                ("analysis_result", result_id),
                ("isolate", technique_isolates.get(stg_id)),
            )
            lineage.extend({"entity_type": kind, "entity_id": entity_id, **context, "staging_table": "stg_microb_technique", "staging_id": stg_id} for kind, entity_id in ids if entity_id is not None)
        self._upsert(conn, microb_entity_lineage, lineage, ("entity_type", "entity_id", "staging_table", "staging_id"), "lineage_id")
        self._upsert(conn, microb_normalization_issue, issues, ("staging_table", "staging_id", "field_name", "code"), "issue_id")
        self._progress(progress, "lineage and issues")
        return {"techniques": len(rows), "susceptibilities": len(susceptibility_rows), "issues": len(issues)}

    def _catalog(self, catalogs, field, value, row, issues) -> int | None:
        entry_id, _ = self._catalog_match(catalogs, field, value, row, issues)
        return entry_id

    @staticmethod
    def _hospital_id(
        hospital_links: dict[tuple[str, str], int],
        origin_code: str | None,
        service_code: str | None,
    ) -> int | None:
        for catalog_name, code in (
            ("procedencias_extra", origin_code),
            ("servicios_extra", service_code),
            ("servicios_extra", origin_code),
            ("procedencias_extra", service_code),
        ):
            if code is not None:
                hospital_id = hospital_links.get((catalog_name, code))
                if hospital_id is not None:
                    return hospital_id
        return None

    def _catalog_match(self, catalogs, field, value, row, issues) -> tuple[int | None, str | None]:
        code = _text(value)
        if not code:
            return None, None
        for name in CATALOGS[field]:
            entry = catalogs.get(name, {}).get(code)
            if entry is not None:
                return entry, name
        self._issue(issues, row, field, code, "WARNING", f"Código no encontrado en catálogo: {field}")
        return None, None

    def _analysis_result(self, catalogs, analysis_kind, code, row, issues) -> int | None:
        catalog_names = {
            "BACTERIOLOGICAL": ("resultados", "rescualt"),
            "SEROLOGICAL": ("rescualt", "resultados"),
        }.get(analysis_kind)
        if catalog_names is None:
            self._issue(issues, row, "cod_resultado", code, "ERROR", "Análisis sin clase de catálogo")
            return None
        catalog_name, incompatible_catalog_name = catalog_names
        entry_id = catalogs.get(catalog_name, {}).get(code)
        if entry_id is not None:
            return entry_id
        if catalogs.get(incompatible_catalog_name, {}).get(code) is not None:
            self._issue(
                issues,
                row,
                "cod_resultado",
                code,
                "WARNING",
                f"Resultado incompatible: análisis {analysis_kind} con código de catálogo {incompatible_catalog_name}",
            )
            return None
        self._issue(issues, row, "cod_resultado", code, "WARNING", f"Código no encontrado en catálogo: {catalog_name}")
        return None

    def _observation(self, catalogs, code: str | None, row, issues) -> int | None:
        """Resolve an observation when possible, otherwise retain it as free text."""

        code = _text(code)
        if not code:
            return None
        for catalog_name in CATALOGS["observation_id"]:
            entry_id = catalogs.get(catalog_name, {}).get(code)
            if entry_id is not None:
                return entry_id
        self._issue(
            issues,
            row,
            "observation_id",
            code,
            "INFO",
            "Observación no catalogada conservada como texto libre",
        )
        return None

    @staticmethod
    def _quantitative_result(catalogs, code: str | None) -> tuple[int | None, str | None]:
        """Resolve a quantitative code or retain an uncatalogued literal."""

        if not code:
            return None, None
        entry_id = catalogs.get("rescuant", {}).get(code)
        return (entry_id, None) if entry_id is not None else (None, code)

    @staticmethod
    def _requires_result_code(analysis_kind: str | None) -> bool:
        """Only serological analyses may omit cod_resultado.

        They express their clinical value through qualitative and/or
        quantitative determination fields instead.
        """

        return analysis_kind != "SEROLOGICAL"

    @staticmethod
    def _issue(issues, row, field, code, severity, message) -> None:
        issues.append({"source_id": row["source_id"], "run_id": row["run_id"], "file_id": row["file_id"], "line_id": row["line_id"], "staging_table": "stg_microb_technique", "staging_id": int(row["stg_microb_technique_id"]), "field_name": field, "code": code or "", "severity": severity, "message": message})

    @staticmethod
    def _progress(callback, stage: str) -> None:
        if callback is not None:
            callback(stage)

    @staticmethod
    def _upsert(conn: Connection, table, rows: Iterable[dict[str, Any]], keys: tuple[str, ...], id_name: str) -> dict[tuple[Any, ...], int]:
        unique = {tuple(row[key] for key in keys): row for row in rows}
        if not unique:
            return {}
        payload = list(unique.values())
        resolved: dict[tuple[Any, ...], int] = {}
        for offset in range(0, len(payload), UPSERT_BATCH_SIZE):
            statement = insert(table).values(payload[offset : offset + UPSERT_BATCH_SIZE])
            updates = {
                column.name: statement.excluded[column.name]
                for column in table.columns
                if not column.primary_key and column.name not in keys
            }
            statement = statement.on_conflict_do_update(
                index_elements=keys, set_=updates
            ).returning(table.c[id_name], *[table.c[key] for key in keys])
            resolved.update(
                {
                    tuple(row[key] for key in keys): int(row[id_name])
                    for row in conn.execute(statement).mappings()
                }
            )
        return resolved
