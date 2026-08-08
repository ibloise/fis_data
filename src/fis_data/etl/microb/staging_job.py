"""Materialize parsed Microb exports into normalized staging tables."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import Engine

from ..contracts import JobContext, JobResult
from .repository import MicrobStagingRepository


@dataclass(frozen=True)
class MicrobStagingStats:
    file_id: int
    entity_name: str
    techniques_inserted: int
    antibiograms_inserted: int
    payload_rows_read: int
    payload_invalid: int


class MicrobStagingJob:
    """Replace Microb staging rows from already parsed raw text payloads."""

    job_name = "microb-staging"

    def __init__(self, engine: Engine) -> None:
        self.repository = MicrobStagingRepository(engine)

    def run(self, *, ctx: JobContext) -> JobResult:
        try:
            techniques: list[dict[str, Any]] = []
            antibiograms_by_line: dict[int, list[dict[str, Any]]] = {}
            invalid = 0
            rows = self.repository.parsed_rows(
                entity_name=ctx.entity_name, file_id=ctx.file_id
            )
            for row in rows:
                try:
                    payload = json.loads(str(row["payload_json"]))
                except json.JSONDecodeError:
                    invalid += 1
                    continue
                if not isinstance(payload, dict):
                    invalid += 1
                    continue
                line_id = int(row["line_id"])
                techniques.append(_technique_row(row, payload))
                antibiograms_by_line[line_id] = _antibiograms(row, payload)
            techniques_inserted, antibiograms_inserted = self.repository.replace_file(
                file_id=ctx.file_id,
                techniques=techniques,
                antibiograms_by_line=antibiograms_by_line,
            )
            return JobResult.success(
                job=self.job_name,
                ctx=ctx,
                payload=MicrobStagingStats(
                    file_id=ctx.file_id,
                    entity_name=ctx.entity_name,
                    techniques_inserted=techniques_inserted,
                    antibiograms_inserted=antibiograms_inserted,
                    payload_rows_read=len(rows),
                    payload_invalid=invalid,
                ),
            )
        except Exception as exc:
            return JobResult.failure(job=self.job_name, ctx=ctx, error=str(exc))


def _value(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _technique_row(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "nhc": "nºhistoria", "afi": "afi", "sexo": "Sexo",
        "fec_nacimiento_raw": "Fec.Nacimiento", "nombre": "Nombre",
        "apellido1": "Apellido1", "apellido2": "Apellido2",
        "num_muestra": "NºMuestra", "aislamiento": "Aislamiento",
        "cod_muestra": "Cod.Muestra", "cod_localizacion": "Cod.Localizacion",
        "cod_procedencia": "Cod.Procedencia", "cod_servicio": "Cod.Servicio",
        "planta": "Planta", "habitacion": "Habitacion", "box": "Box",
        "fec_recep_raw": "Fec.Recep.", "hora_recep_raw": "Hora Recep.",
        "fec_entrada_raw": "Fec.Entrada", "hora_entrada_raw": "Hora Entrada",
        "fec_resultado_raw": "Fec.Resultado", "hora_resultado_raw": "Hora Resultado",
        "cod_sig": "Cod.Sig.", "cod_analisis": "Cod.Analisis",
        "cod_resultado": "Cod.Resultado", "cod_microorganismo": "Cod.Microorganismo",
        "cod_recuento": "Cod.Recuento", "cod_result_cualitativo": "Cod.Result.Cualitativo",
        "cod_result_cuantitativo": "Cod.Result.Cuantitativo", "cod_tecnica": "Cod.Tecnica",
        "cod_obs": "Cod.Obs", "cod_obs_2": "Cod.Obs__2",
        "cod_observaciones": "Cod.Observaciones",
        "cod_observaciones_2": "Cod.Observaciones__2",
        "cod_diagnostico": "Cod.Diagnostico", "cod_recr": "Cod.RECR",
        "cod_firma": "Cod.Firma",
    }
    return {
        "source_id": int(row["source_id"]), "run_id": int(row["run_id"]),
        "file_id": int(row["file_id"]), "line_id": int(row["line_id"]),
        "line_no": int(row["line_no"]),
        **{column: _value(payload, key) for column, key in fields.items()},
    }


def _antibiograms(row: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    extra = payload.get("extra")
    if not isinstance(extra, list):
        return []
    records = []
    for sequence, item in enumerate(extra, start=1):
        if not isinstance(item, dict) or not _value(item, "Cod.Atb"):
            continue
        records.append(
            {
                "file_id": int(row["file_id"]), "line_id": int(row["line_id"]),
                "seq_no": sequence, "cod_atb": _value(item, "Cod.Atb"),
                "cod_cmi": _value(item, "Cod.CMI"), "cod_int": _value(item, "Cod.INT"),
                "des_cmi": _value(item, "Des.CMI"),
            }
        )
    return records
