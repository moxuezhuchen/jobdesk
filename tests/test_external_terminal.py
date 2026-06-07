from pathlib import Path

from jobdesk_app.config.schema import ServerConfig
from jobdesk_app.services.external_terminal import (
    TerminalLaunch,
    build_cd_command,
    build_terminal_launch,
)


def test_build_cd_command_quotes_remote_path():
    assert build_cd_command("/tmp/job desk/run 1") == "cd '/tmp/job desk/run 1'"


def test_windows_terminal_uses_ssh_alias_when_available(tmp_path):
    server = ServerConfig(
        server_id="hpc",
        host="cluster.example.edu",
        username="chemist",
        external_tools={"ssh_alias": "cluster-a"},
    )

    launch = build_terminal_launch(server, "/tmp/jobdesk/run-a", temp_dir=tmp_path)

    assert isinstance(launch, TerminalLaunch)
    assert launch.executable == "wt"
    assert "ssh" in launch.args
    assert "cluster-a" in launch.args
    joined = " ".join(launch.args)
    assert "cd /tmp/jobdesk/run-a" in joined
    assert launch.user_visible_command.startswith("wt ")


def test_windows_terminal_falls_back_to_user_host_and_port(tmp_path):
    server = ServerConfig(
        server_id="hpc",
        host="cluster.example.edu",
        username="chemist",
        port=2200,
    )

    launch = build_terminal_launch(server, "/tmp/run", temp_dir=tmp_path)

    joined = " ".join(launch.args)
    assert "chemist@cluster.example.edu" in joined
    assert "-p 2200" in joined


def test_putty_requires_saved_session(tmp_path):
    server = ServerConfig(
        server_id="hpc",
        host="cluster.example.edu",
        username="chemist",
        external_tools={"terminal_provider": "putty"},
    )

    try:
        build_terminal_launch(server, "/tmp/run", temp_dir=tmp_path)
    except ValueError as exc:
        assert "PuTTY saved session" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_putty_uses_command_file_for_remote_cd(tmp_path):
    server = ServerConfig(
        server_id="hpc",
        host="cluster.example.edu",
        username="chemist",
        external_tools={
            "terminal_provider": "putty",
            "putty_session": "cluster-a-putty",
        },
    )

    launch = build_terminal_launch(server, "/tmp/job desk/run-a", temp_dir=tmp_path)

    assert launch.executable == "putty.exe"
    assert launch.args[:3] == ["-load", "cluster-a-putty", "-t"]
    assert "-m" in launch.args
    command_file = Path(launch.args[launch.args.index("-m") + 1])
    assert command_file.exists()
    assert command_file.read_text(encoding="utf-8") == (
        "cd '/tmp/job desk/run-a'\n"
        "exec ${SHELL:-/bin/sh} -l\n"
    )
