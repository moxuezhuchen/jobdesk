import tomllib
from pathlib import Path


def test_gui_resources_are_declared_as_package_data():
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert "gui/resources/*.svg" in config["tool"]["setuptools"]["package-data"]["jobdesk_app"]


def test_pyinstaller_bundle_includes_gui_resources():
    spec = Path("packaging/pyinstaller/jobdesk-gui.spec").read_text(encoding="utf-8")

    assert "gui\" / \"resources" in spec
    assert "jobdesk_app/gui/resources" in spec
