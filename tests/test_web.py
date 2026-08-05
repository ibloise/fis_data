from __future__ import annotations

from fis_data.web import create_app
from fis_data.web.app import _curve_series


def test_web_application_exposes_pcr_review_contract() -> None:
    app = create_app(database_url="sqlite:///:memory:")

    paths = app.openapi()["paths"]

    assert "/api/health" in paths
    assert "/api/pcr/runs" in paths
    assert "/api/pcr/runs/{run_id}/filters" in paths
    assert "/api/pcr/runs/{run_id}/curves" in paths
    assert "/api/pcr/qc" in paths
    assert "/api/pcr/qc/{run_id}/{target}/evidence" in paths


def test_curve_series_uses_sample_target_for_a_run_and_target_for_one_sample() -> None:
    rows = [
        {
            "pcr_well_id": 1,
            "well": "A01",
            "sample_name": "S1",
            "target": "KPC",
            "cq": 22.0,
            "axis_value": 1.0,
            "rfu": 10.0,
        },
        {
            "pcr_well_id": 2,
            "well": "B01",
            "sample_name": "S2",
            "target": "KPC",
            "cq": 23.0,
            "axis_value": 1.0,
            "rfu": 12.0,
        },
    ]

    run_series = _curve_series(rows, group_by_sample=True)
    assert len(run_series) == 2
    assert run_series[0]["points"] == [[1.0, 10.0]]

    selected_sample_series = _curve_series(rows[:1], group_by_sample=False)
    assert len(selected_sample_series) == 1
    assert selected_sample_series[0]["target"] == "KPC"
