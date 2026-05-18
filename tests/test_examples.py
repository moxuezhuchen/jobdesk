from pathlib import Path

from jobdesk_app.config.loader import load_project


def test_example_projects_load_with_current_schema():
    examples_dir = Path("examples")
    project_files = sorted(examples_dir.glob("*/project.yaml"))
    assert project_files
    for project_file in project_files:
        cfg = load_project(project_file.parent)
        assert cfg.project_id
        assert cfg.task_discoveries
        assert cfg.execution_profiles
