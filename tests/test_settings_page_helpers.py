from jobdesk_app.gui.pages.settings_page import build_settings_rows, settings_status_summary


def test_build_settings_rows_shows_workspace_and_config_paths(tmp_path):
    rows = build_settings_rows(tmp_path)
    data = dict(rows)

    assert data["workspace"] == str(tmp_path)
    assert data["runs"] == str(tmp_path / ".jobdesk" / "runs")
    assert data["results"] == str(tmp_path / "results")
    assert "servers.yaml" in data["servers_config"]
    assert "run_profiles.yaml" in data["run_profiles"]
    assert "gui_settings.yaml" in data["gui_settings"]


def test_settings_status_summary():
    assert settings_status_summary("s1", "/tmp/jobs", True) == "Auto connect to s1 at /tmp/jobs"
    assert settings_status_summary("", "/tmp", False) == "Auto connect disabled"
