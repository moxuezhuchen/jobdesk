"""M7.1 测试: GUI state + helpers。"""

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")


class TestAppState:
    def test_init_empty(self):
        from jobdesk_app.gui.state import AppState
        s = AppState()
        assert s.current_project_root is None
        assert s.current_project_context is None
        assert s.current_batch_id is None
        assert s.current_manifest_path is None
        assert s.last_error is None

    def test_set_and_clear(self):
        from jobdesk_app.gui.state import AppState
        from pathlib import Path
        s = AppState()
        s.current_batch_id = "b1"
        s.current_manifest_path = Path("/tmp/m.tsv")
        assert s.current_batch_id == "b1"
        # clear: simulate project switch
        s.current_batch_id = None
        s.current_manifest_path = None
        assert s.current_batch_id is None


class TestSessionHelper:
    def test_import_session(self):
        from jobdesk_app.gui.session import create_ssh_client, create_sftp_client
        assert callable(create_ssh_client)
        assert callable(create_sftp_client)


class TestProjectsPageHelpers:
    def test_build_project_info_reflects_profile_bindings(self, tmp_path):
        import yaml
        from jobdesk_app.config.schema import RuntimeBinding
        from jobdesk_app.gui.pages.projects_page import build_project_info
        from jobdesk_app.services.project_service import create_project_context

        project_dir = tmp_path / "proj"
        (project_dir / "inputs").mkdir(parents=True)
        (project_dir / "project.yaml").write_text(yaml.safe_dump({
            "project_id": "gui-bindings",
            "project": {"name": "gui_test"},
            "local_paths": {"input_dir": "./inputs"},
            "task_discoveries": [
                {"name": "g16_jobs", "mode": "flat_single", "entry_glob": "*.gjf", "execution_profile": "g16"},
                {"name": "orca_jobs", "mode": "flat_single", "entry_glob": "*.inp", "execution_profile": "orca"},
            ],
            "execution_profiles": {
                "g16": {"label": "G16", "command": "g16 {input_name}"},
                "orca": {"label": "ORCA", "command": "orca {input_name}"},
            },
        }), encoding="utf-8")

        class Store:
            def get_binding(self, project_id, execution_profile):
                if execution_profile == "g16":
                    return RuntimeBinding(server_id="srv1", remote_work_dir="/r/g16")
                return None

        info = build_project_info(create_project_context(project_dir), Store())

        assert info["Project ID"] == "gui-bindings"
        assert "g16: bound to srv1 (/r/g16)" in info["Binding Status"]
        assert "orca: NOT BOUND" in info["Binding Status"]


class TestGuiImports:
    """确保所有页面仍可 import。"""

    def test_all_pages(self):
        from jobdesk_app.gui.pages.servers_page import ServersPage
        from jobdesk_app.gui.pages.projects_page import ProjectsPage
        from jobdesk_app.gui.pages.tasks_page import TasksPage
        from jobdesk_app.gui.pages.results_page import ResultsPage
        from jobdesk_app.gui.main_window import MainWindow
        from jobdesk_app.gui.workers import BackgroundWorker
        from jobdesk_app.gui.table_models import load_tsv_to_table
        # all imports ok
