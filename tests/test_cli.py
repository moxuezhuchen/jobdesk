from jobdesk_app.cli import build_remote_cleanup_commands, main


def test_cli_scan_reports_discovered_tasks(capsys):
    rc = main(["scan", "examples/shell_basic"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "discovered" in out
    assert "shell_jobs" in out


def test_cli_list_batches_handles_empty_project(capsys):
    rc = main(["list-batches", "examples/shell_basic"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "No batches" in out


def test_remote_cleanup_command_is_scoped_to_batch_dirs():
    commands = build_remote_cleanup_commands(
        remote_work_dirs=["/tmp/jobdesk/a", "/tmp/jobdesk/b"],
        batch_id="batch_001",
        dry_run=True,
    )

    assert commands == [
        "test -d /tmp/jobdesk/a/batch_001 && printf '%s\\n' /tmp/jobdesk/a/batch_001 || true",
        "test -d /tmp/jobdesk/b/batch_001 && printf '%s\\n' /tmp/jobdesk/b/batch_001 || true",
    ]
