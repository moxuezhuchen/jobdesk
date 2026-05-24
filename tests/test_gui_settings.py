from pathlib import Path

import pytest
import yaml

from jobdesk_app.services.gui_settings import GuiSettings, GuiSettingsStore


def test_gui_settings_store_roundtrip(tmp_path):
    path = tmp_path / "gui_settings.yaml"
    store = GuiSettingsStore(path)
    settings = GuiSettings(
        default_local_folder=str(tmp_path / "inputs"),
        default_remote_dir="/scratch/jobs",
        default_server_id="s1",
        auto_connect=False,
        overwrite_policy="overwrite",
        command_template="g16 {name}",
        max_parallel=8,
        batch_size=20,
        language="zh",
        column_widths={"files.local": [220, 80, 140], "files.remote": [240, 80, 140, 90]},
    )

    store.save(settings)

    assert store.load() == settings


def test_gui_settings_defaults(tmp_path):
    settings = GuiSettingsStore(tmp_path / "missing.yaml").load()

    assert settings.default_remote_dir == "/tmp"
    assert settings.auto_connect is True
    assert settings.command_template == "bash {name}"
    assert settings.max_parallel == 4
    assert settings.batch_size == 0
    assert settings.language == "en"
    assert settings.column_widths == {}
    assert settings.software_profiles["ConfFlow"]["input_extensions"] == ".xyz"
    assert settings.software_profiles["ConfFlow"]["command_template"] == "confflow {name}"


def test_existing_profiles_get_confflow_merged_without_overwriting_custom(tmp_path):
    """Old config with only Gaussian/ORCA should gain ConfFlow on load."""
    path = tmp_path / "gui_settings.yaml"
    path.write_text(yaml.safe_dump({
        "software_profiles": {
            "Gaussian": {
                "input_extensions": ".gjf",
                "command_template": "my_g16 {name}",
                "download_patterns": "*.log",
            },
            "ORCA": {
                "input_extensions": ".inp",
                "command_template": "orca {name} > {basename}.out",
                "download_patterns": "*.out,*.gbw",
            },
        },
    }), encoding="utf-8")

    settings = GuiSettingsStore(path).load()

    # ConfFlow was added
    assert "ConfFlow" in settings.software_profiles
    assert settings.software_profiles["ConfFlow"]["input_extensions"] == ".xyz"
    # Gaussian custom values preserved
    assert settings.software_profiles["Gaussian"]["command_template"] == "my_g16 {name}"
    assert settings.software_profiles["Gaussian"]["download_patterns"] == "*.log"


def test_existing_profiles_with_confflow_not_overwritten(tmp_path):
    """If user already has ConfFlow with custom settings, they stay."""
    path = tmp_path / "gui_settings.yaml"
    path.write_text(yaml.safe_dump({
        "software_profiles": {
            "Gaussian": {
                "input_extensions": ".gjf,.com",
                "command_template": "g16 {name}",
                "download_patterns": "*.log,*.chk",
            },
            "ConfFlow": {
                "input_extensions": ".xyz",
                "command_template": "confflow {name} --custom",
                "download_patterns": "*.txt",
            },
        },
    }), encoding="utf-8")

    settings = GuiSettingsStore(path).load()

    assert settings.software_profiles["ConfFlow"]["command_template"] == "confflow {name} --custom"
    assert settings.software_profiles["ConfFlow"]["download_patterns"] == "*.txt"


def test_save_replace_failure_keeps_existing_settings(tmp_path, monkeypatch):
    path = tmp_path / "gui_settings.yaml"
    path.write_text("existing: true\n", encoding="utf-8")
    store = GuiSettingsStore(path)

    def fail_replace(self, target):
        raise RuntimeError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="replace failed"):
        store.save(GuiSettings())

    assert path.read_text(encoding="utf-8") == "existing: true\n"



def test_old_config_with_auto_refresh_disabled_is_ignored(tmp_path):
    """Old YAML with auto_refresh_enabled: false must be silently tolerated."""
    path = tmp_path / "gui_settings.yaml"
    path.write_text(yaml.safe_dump({
        "auto_refresh_enabled": False,
        "auto_download_enabled": False,
    }), encoding="utf-8")

    settings = GuiSettingsStore(path).load()

    # Fields no longer exist; load must not crash
    assert not hasattr(settings, "auto_refresh_enabled")
    assert not hasattr(settings, "auto_download_enabled")


def test_save_does_not_write_auto_refresh_keys(tmp_path):
    """Saved config must not contain the deprecated toggle keys."""
    path = tmp_path / "gui_settings.yaml"
    store = GuiSettingsStore(path)
    store.save(GuiSettings())

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "auto_refresh_enabled" not in saved
    assert "auto_download_enabled" not in saved



def test_gui_settings_has_no_auto_refresh_or_auto_download_fields():
    """B4: auto_refresh_enabled and auto_download_enabled must not exist on GuiSettings."""
    assert not hasattr(GuiSettings(), "auto_refresh_enabled")
    assert not hasattr(GuiSettings(), "auto_download_enabled")
