import json

from jobdesk_app.services.confflow_results import format_summary, load_summary


def test_load_and_format_confflow_run_summary(tmp_path):
    path = tmp_path / "run_summary.json"
    path.write_text(json.dumps({
        "initial_conformers": 12,
        "final_conformers": 3,
        "total_duration_seconds": 42.5,
        "step_status_counts": {"completed": 4},
        "lowest_conformer": {"cid": "water_0001", "energy": -76.4},
    }), encoding="utf-8")

    summary = load_summary(path)
    text = format_summary(summary)

    assert summary.initial_conformers == 12
    assert summary.final_conformers == 3
    assert "Final conformers: 3" in text
    assert "water_0001" in text
