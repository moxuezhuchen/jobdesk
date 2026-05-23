from pathlib import Path

import pytest

from jobdesk_app.services.run_profiles import RunProfileStore


def test_run_profile_store_saves_and_loads_last_profile(tmp_path):
    store = RunProfileStore(tmp_path / "profiles.yaml")

    store.save_last(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="g16 {name}",
        max_parallel=4,
        download_patterns=["*.log"],
    )

    profile = store.load_last("s1", "/remote/jobs")

    assert profile is not None
    assert profile.command_template == "g16 {name}"
    assert profile.max_parallel == 4
    assert profile.download_patterns == ["*.log"]


def test_run_profile_save_replace_failure_keeps_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "profiles.yaml"
    path.write_text("profiles: {}\n", encoding="utf-8")
    store = RunProfileStore(path)

    def fail_replace(self, target):
        raise RuntimeError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="replace failed"):
        store.save_last("s1", "/remote/jobs", "g16 {name}", 4, ["*.log"])

    assert path.read_text(encoding="utf-8") == "profiles: {}\n"
