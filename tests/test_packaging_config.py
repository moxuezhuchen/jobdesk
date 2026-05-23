import tomllib
from pathlib import Path


def test_gui_resources_are_declared_as_package_data():
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert "gui/resources/*.svg" in config["tool"]["setuptools"]["package-data"]["jobdesk_app"]


def test_pyinstaller_bundle_includes_gui_resources():
    spec = Path("packaging/pyinstaller/jobdesk-gui.spec").read_text(encoding="utf-8")

    assert "gui\" / \"resources" in spec
    assert "jobdesk_app/gui/resources" in spec



def test_jobdesk_gui_is_gui_script_not_console_script():
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    console_scripts = config.get("project", {}).get("scripts", {})
    gui_scripts = config.get("project", {}).get("gui-scripts", {})

    assert "jobdesk-gui" not in console_scripts, "jobdesk-gui must not be a console script"
    assert "jobdesk-gui" in gui_scripts, "jobdesk-gui must be a gui-script"
    assert "jobdesk" in console_scripts, "jobdesk CLI must remain a console script"
