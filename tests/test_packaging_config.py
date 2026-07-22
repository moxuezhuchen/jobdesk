import tomllib
from pathlib import Path


def test_gui_resources_are_declared_as_package_data():
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert "gui/resources/*.svg" in config["tool"]["setuptools"]["package-data"]["jobdesk_app"]


def test_pyinstaller_bundle_includes_gui_resources():
    spec = Path("packaging/pyinstaller/jobdesk-gui.spec").read_text(encoding="utf-8")

    assert 'gui" / "resources' in spec
    assert "jobdesk_app/gui/resources" in spec


def test_jobdesk_gui_is_gui_script_not_console_script():
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    console_scripts = config.get("project", {}).get("scripts", {})
    gui_scripts = config.get("project", {}).get("gui-scripts", {})

    assert "jobdesk-gui" not in console_scripts, "jobdesk-gui must not be a console script"
    assert "jobdesk-gui" in gui_scripts, "jobdesk-gui must be a gui-script"
    assert "jobdesk" in console_scripts, "jobdesk CLI must remain a console script"


def test_jobdesk_gui_ps1_does_not_use_python_m():
    ps1 = Path("scripts/jobdesk_gui.ps1").read_text(encoding="utf-8")
    active_lines = [ln for ln in ps1.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert not any("python -m jobdesk_app.gui.app" in ln for ln in active_lines)
    assert "jobdesk-gui" in ps1


def test_license_uses_spdx_expression_not_table():
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    license_val = config["project"]["license"]
    # Must be a plain SPDX string, not a table like {file = "LICENSE"}
    assert isinstance(license_val, str), f"license should be SPDX string, got {type(license_val)}"
    assert license_val == "Apache-2.0"


def test_package_extra_includes_pyinstaller():
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    package_extra = config["project"]["optional-dependencies"]["package"]

    assert any(requirement.startswith("pyinstaller") for requirement in package_extra)


def test_chem_extra_bounds_confflow_to_supported_major():
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    chem_extra = config["project"]["optional-dependencies"]["chem"]

    assert "confflow>=1.4.0,<2.0" in chem_extra


def test_source_distribution_manifest_includes_public_support_files():
    manifest = Path("MANIFEST.in").read_text(encoding="utf-8")

    required_lines = {
        "include CHANGELOG.md",
        "include CONTRIBUTING.md",
        "include SECURITY.md",
        "recursive-include docs *.md",
        "recursive-include examples *.md *.gjf *.inp",
        "recursive-include scripts *.ps1",
        "recursive-include packaging *.md *.spec *.py *.manifest",
    }

    assert required_lines <= set(manifest.splitlines())
