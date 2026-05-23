from datetime import datetime

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.core.transfer import TransferStatus
from jobdesk_app.gui.pages.file_transfer_page import (
    breadcrumb_parts,
    build_file_button_reasons,
    choose_chunks_to_submit,
    choose_confflow_xyz,
    choose_delete_scope,
    collect_remote_delete_roots,
    connection_status_text,
    default_remote_dir_for_server,
    file_action_labels,
    file_table_headers,
    files_layout_row_counts,
    format_command_preview_rows,
    format_file_size,
    format_modified_time,
    format_queue_summary,
    format_remote_size,
    format_selection_summary,
    local_parent_row,
    local_table_row,
    remote_child_path,
    remote_parent_path,
    remote_parent_row,
    remote_table_row,
    run_button_reason,
    table_resize_mode_name,
)


def test_format_file_size():
    assert format_file_size(None) == ""
    assert format_file_size(12) == "12 B"
    assert format_file_size(2048) == "2.0 KB"
    assert format_file_size(3 * 1024 * 1024) == "3.0 MB"


def test_format_remote_size_hides_directory_size():
    assert format_remote_size(4096, is_dir=True) == ""
    assert format_remote_size(4096, is_dir=False) == "4.0 KB"


def test_format_modified_time():
    assert format_modified_time(None) == ""
    local_time = datetime(2020, 1, 2, 3, 4, 5).timestamp()
    assert format_modified_time(local_time) == "2020-01-02 03:04:05"


def test_build_file_button_reasons():
    reasons = build_file_button_reasons(local_selected=False, remote_selected=True, connected=False)

    assert reasons["upload"] == "Select a local file or folder"
    assert reasons["download"] == "Connect to a server first"
    assert reasons["preview"] == "Connect to a server first"


def test_format_queue_summary_counts_statuses():
    statuses = [TransferStatus.transferred, TransferStatus.failed, TransferStatus.skipped]

    assert format_queue_summary(statuses) == "Queue 1 ok | 1 skip | 1 fail"


def test_collect_remote_delete_roots_from_manifest(tmp_path):
    from jobdesk_app.core.lifecycle import TaskStatus
    from jobdesk_app.core.manifest import Manifest, TaskRecord

    manifest_path = tmp_path / "manifest.tsv"
    Manifest.write(manifest_path, [
        TaskRecord(
            task_id="t1", batch_id="b1", remote_job_dir="/remote/work/b1/t1",
            server_id="s", remote_work_dir="/remote/work",
            status=TaskStatus.submitted,
        ),
        TaskRecord(
            task_id="t2", batch_id="b1", remote_job_dir="/remote/other/b1/t2",
            server_id="s", remote_work_dir="/remote/other",
            status=TaskStatus.submitted,
        ),
    ])

    assert collect_remote_delete_roots(manifest_path) == [
        "/remote/other/b1",
        "/remote/work/b1",
    ]


def test_format_command_preview_rows_for_remote_files():
    rows = format_command_preview_rows(
        remote_paths=["/remote/jobs/a.gjf", "/remote/jobs/b.gjf"],
        remote_dirs=[],
        remote_dir="/remote/jobs",
        command_template="g16 {name}",
        run_mode="selected_files",
        max_preview=5,
    )

    assert rows == [
        "a: cd /remote/jobs && g16 a.gjf",
        "b: cd /remote/jobs && g16 b.gjf",
    ]


def test_run_button_reason_requires_connection_selection_and_command():
    assert run_button_reason(False, 1, "g16 {name}") == "Connect to a server first"
    assert run_button_reason(True, 0, "g16 {name}") == "Select remote files or directories"
    assert run_button_reason(True, 1, "  ") == "Enter a command template"
    assert run_button_reason(True, 1, "g16 {name}") == ""


def test_choose_chunks_to_submit_modes():
    chunks = [["a"], ["b"], ["c"]]

    assert choose_chunks_to_submit(chunks, "create_only") == []
    assert choose_chunks_to_submit(chunks, "first_batch") == [["a"]]
    assert choose_chunks_to_submit(chunks, "all_sequential") == chunks


def test_choose_confflow_xyz_requires_exactly_one_xyz_from_one_pane():
    assert choose_confflow_xyz(["C:/job/water.xyz"], []) == ("local", ["C:/job/water.xyz"], "")
    assert choose_confflow_xyz([], ["/tmp/water.xyz"]) == ("remote", ["/tmp/water.xyz"], "")
    assert choose_confflow_xyz(["C:/job/readme.txt"], ["/tmp/water.xyz"]) == ("remote", ["/tmp/water.xyz"], "")
    assert choose_confflow_xyz(["C:/job/readme.txt"], []) == ("", [], "No .xyz files selected")


def test_choose_confflow_xyz_multiple_from_one_pane():
    """Multi-select from one pane is now supported."""
    result = choose_confflow_xyz(["C:/job/a.xyz", "C:/job/b.xyz"], [])
    assert result == ("local", ["C:/job/a.xyz", "C:/job/b.xyz"], "")

    result = choose_confflow_xyz([], ["/tmp/a.xyz", "/tmp/b.xyz"])
    assert result == ("remote", ["/tmp/a.xyz", "/tmp/b.xyz"], "")


def test_choose_confflow_xyz_both_panes_is_error():
    """XYZ in both panes is an ambiguous error."""
    origin, paths, error = choose_confflow_xyz(
        ["C:/job/water.xyz"], ["/tmp/mol.xyz"]
    )
    assert origin == ""
    assert paths == []
    assert "both" in error.lower() or "ambig" in error.lower()


def test_choose_confflow_xyz_non_xyz_does_not_block():
    """Non-XYZ residual in the same pane as XYZ does not block."""
    result = choose_confflow_xyz(["C:/job/water.xyz", "C:/job/readme.txt"], [])
    assert result == ("local", ["C:/job/water.xyz"], "")

    result = choose_confflow_xyz([], ["/tmp/mol.xyz", "/tmp/notes.txt"])
    assert result == ("remote", ["/tmp/mol.xyz"], "")


def test_choose_delete_scope_prefers_focused_pane():
    assert choose_delete_scope(1, 1, "local") == "local"
    assert choose_delete_scope(1, 1, "remote") == "remote"
    assert choose_delete_scope(1, 0, "") == "local"
    assert choose_delete_scope(0, 1, "") == "remote"
    assert choose_delete_scope(0, 0, "remote") == ""


def test_default_remote_dir_for_server_uses_username_home():
    class Server:
        username = "alice"

    class RootServer:
        username = "root"

    assert default_remote_dir_for_server(Server()) == "/home/alice"
    assert default_remote_dir_for_server(RootServer()) == "/root"


def test_remote_path_navigation_helpers():
    assert remote_child_path("/tmp/jobs", "a") == "/tmp/jobs/a"
    assert remote_child_path("/", "a") == "/a"
    assert remote_parent_path("/tmp/jobs") == "/tmp"
    assert remote_parent_path("/tmp") == "/"
    assert remote_parent_path("/") == "/"
    assert breadcrumb_parts("/tmp/jobs") == [
        ("/", "/"),
        ("tmp", "/tmp"),
        ("jobs", "/tmp/jobs"),
    ]


def test_parent_rows_for_local_and_remote_navigation(tmp_path):
    child = tmp_path / "child"
    child.mkdir()

    assert local_parent_row(child) == ["..", "", "", "dir", str(tmp_path)]
    assert local_parent_row(tmp_path.anchor) is None
    assert remote_parent_row("/tmp/jobs") == ["..", "", "", "", "dir", "/tmp"]
    assert remote_parent_row("/") is None


def test_connection_status_text():
    assert connection_status_text("s1", True, "") == "Connected: s1"
    assert connection_status_text("s1", False, "") == "Connecting: s1"
    assert connection_status_text("s1", False, "boom") == "Connection failed: boom"
    assert connection_status_text("", False, "") == "No server selected"


def test_file_action_labels_are_winscp_like():
    assert file_action_labels() == {
        "up": "Up",
        "home": "Home",
        "refresh_local": "Refresh Local",
        "refresh_remote": "Refresh Remote",
        "upload": "Upload ->",
        "download": "<- Download",
        "mkdir": "New Folder",
        "rename": "Rename",
        "delete": "Delete",
        "preview": "Preview",
    }


def test_file_table_headers_do_not_show_path_column():
    assert file_table_headers("local") == ["name", "size", "modified"]
    assert file_table_headers("remote") == ["name", "size", "modified", "permissions"]


def test_table_rows_keep_hidden_type_and_path():
    assert local_table_row("a.txt", False, "12 B", "C:/x/a.txt") == [
        "a.txt",
        "12 B",
        "",
        "file",
        "C:/x/a.txt",
    ]
    assert remote_table_row("d", True, "", "", "0755", "/tmp/d") == [
        "d",
        "",
        "",
        "0755",
        "dir",
        "/tmp/d",
    ]


def test_files_layout_is_split_into_short_rows():
    assert files_layout_row_counts() == {
        "top_toolbar_rows": 1,
        "action_rows": 1,
        "run_rows": 3,
    }


def test_format_selection_summary():
    assert format_selection_summary(0, 0) == "Local 0 | Remote 0"
    assert format_selection_summary(2, 3) == "Local 2 | Remote 3"


def test_table_resize_mode_is_interactive():
    assert table_resize_mode_name() == "Interactive"



def test_choose_confflow_yaml_remote_yaml_with_remote_xyz():
    """Remote YAML with remote XYZ is valid."""
    from jobdesk_app.gui.pages.file_transfer_page import choose_confflow_yaml
    yaml_path, error = choose_confflow_yaml(
        remote_files=["/tmp/confflow.yaml", "/tmp/mol.xyz"],
        xyz_origin="remote",
    )
    assert yaml_path == "/tmp/confflow.yaml"
    assert error == ""


def test_choose_confflow_yaml_no_remote_yaml_returns_empty():
    from jobdesk_app.gui.pages.file_transfer_page import choose_confflow_yaml
    yaml_path, error = choose_confflow_yaml(
        remote_files=["/tmp/mol.xyz"],
        xyz_origin="remote",
    )
    assert yaml_path == ""
    assert error == ""


def test_choose_confflow_yaml_multiple_remote_yamls_is_error():
    from jobdesk_app.gui.pages.file_transfer_page import choose_confflow_yaml
    yaml_path, error = choose_confflow_yaml(
        remote_files=["/tmp/a.yaml", "/tmp/b.yml"],
        xyz_origin="remote",
    )
    assert yaml_path == ""
    assert "multiple" in error.lower() or "one" in error.lower()


def test_choose_confflow_yaml_remote_yaml_with_local_xyz_rejected():
    from jobdesk_app.gui.pages.file_transfer_page import choose_confflow_yaml
    yaml_path, error = choose_confflow_yaml(
        remote_files=["/tmp/confflow.yaml"],
        xyz_origin="local",
    )
    assert yaml_path == ""
    assert error != ""
