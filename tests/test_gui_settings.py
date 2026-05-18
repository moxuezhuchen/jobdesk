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
