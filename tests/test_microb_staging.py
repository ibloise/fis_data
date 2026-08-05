from __future__ import annotations

import json

from sqlalchemy import create_engine, insert, select

from fis_data.etl.contracts import JobContext
from fis_data.etl.microb_staging_job import MicrobStagingJob
from fis_data.schema import (
    create_schema,
    ctl_etl_run,
    ctl_file_registry,
    ctl_source,
    raw_text_lines,
    stg_microb_antibiogram,
    stg_microb_technique,
)


def test_microb_staging_materializes_techniques_and_antibiograms(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'microb.sqlite'}", future=True)
    create_schema(engine)
    with engine.begin() as conn:
        source_id = conn.execute(
            insert(ctl_source).values(source_name="microb", source_type="MICROB")
        ).lastrowid
        run_id = conn.execute(
            insert(ctl_etl_run).values(pipeline_name="test", status="SUCCESS")
        ).lastrowid
        file_id = conn.execute(
            insert(ctl_file_registry).values(
                source_id=source_id,
                source_name="microb",
                storage_path="microb.txt",
                file_format="text",
                sha256="a" * 64,
            )
        ).lastrowid
        conn.execute(
            insert(raw_text_lines).values(
                source_id=source_id,
                run_id=run_id,
                file_id=file_id,
                entity_name="MICROB",
                line_no=3,
                raw_line="raw",
                raw_line_sha256="b" * 64,
                is_header=False,
                parse_status="PARSED_OK",
                payload_json=json.dumps(
                    {
                        "nºhistoria": "123",
                        "afi": "AFI-1",
                        "NºMuestra": "M-1",
                        "Cod.Microorganismo": "E_COLI",
                        "extra": [
                            {
                                "Cod.Atb": "AMC",
                                "Cod.CMI": "1",
                                "Cod.INT": "S",
                                "Des.CMI": "<=1",
                            }
                        ],
                    }
                ),
            )
        )

    result = MicrobStagingJob(engine).run(
        ctx=JobContext(entity_name="microb", file_id=file_id, run_id=run_id)
    )

    assert result.ok
    assert result.metrics["techniques_inserted"] == 1
    assert result.metrics["antibiograms_inserted"] == 1
    with engine.connect() as conn:
        technique = conn.execute(
            select(
                stg_microb_technique.c.nhc,
                stg_microb_technique.c.afi,
                stg_microb_technique.c.num_muestra,
                stg_microb_technique.c.cod_microorganismo,
            )
        ).one()
        antibiogram = conn.execute(
            select(
                stg_microb_antibiogram.c.cod_atb,
                stg_microb_antibiogram.c.cod_int,
            )
        ).one()
    assert technique == ("123", "AFI-1", "M-1", "E_COLI")
    assert antibiogram == ("AMC", "S")
