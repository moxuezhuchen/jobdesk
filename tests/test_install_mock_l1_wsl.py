"""Unit tests for ``scripts/install_mock_l1_wsl.py`` safety guards.

These tests verify the structural safety guarantees without actually
running WSL or touching the real ``/opt/g16``. We import the script
as a module via ``importlib.util`` so that ``probe_wrapper`` and
``main`` can be invoked with monkeypatched subprocess / filesystem
state.

Phase 6 (mock-g16-deployed-to-real-g16-path) is the failure mode these
guards exist to prevent. The mock install at ``l1.exe`` must refuse
to run when the upstream ``g16`` wrapper is JOBDESK_MOCK-tainted.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "install_mock_l1_wsl.py"


def _load_module():
    """Load ``install_mock_l1_wsl.py`` as a fresh module each test."""
    spec = importlib.util.spec_from_file_location("_install_mock_l1_wsl", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"could not load spec from {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mod():
    return _load_module()


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    """Keep every ``main()`` invocation out of the developer's real HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout.encode(), stderr=stderr.encode(),
    )


# ---------------------------------------------------------------------------
# probe_wrapper
# ---------------------------------------------------------------------------


class TestProbeWrapper:
    def test_returns_binary_when_marker_absent(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(0, "BINARY"))
        assert mod.probe_wrapper() == "BINARY"

    def test_returns_mock_when_marker_present(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(0, "MOCK"))
        assert mod.probe_wrapper() == "MOCK"

    def test_returns_shell_when_shell_script(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(0, "SHELL"))
        assert mod.probe_wrapper() == "SHELL"

    def test_returns_missing_when_blank(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(0, ""))
        assert mod.probe_wrapper() == "MISSING"

    def test_returns_error_when_unreadable(self, mod, monkeypatch):
        # A WSL/permission failure must fail closed, never look like MISSING.
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(2, "", "UNREADABLE"))
        assert mod.probe_wrapper() == "ERROR"

    def test_returns_error_when_wsl_probe_fails_with_stdout(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(2, "MISSING", "wsl unavailable"))
        assert mod.probe_wrapper() == "ERROR"


class TestProbeL1:
    """Cover the second-layer safety probe for /opt/g16/l1.exe."""

    def test_returns_mock_when_l1_already_tainted(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(0, "MOCK"))
        assert mod.probe_l1() == "MOCK"

    def test_returns_real_when_l1_is_large(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(0, "REAL"))
        assert mod.probe_l1() == "REAL"

    def test_returns_small_for_mock_sized_l1(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(0, "SMALL"))
        assert mod.probe_l1() == "SMALL"

    def test_returns_shell_when_l1_is_anomalous_script(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(0, "SHELL"))
        assert mod.probe_l1() == "SHELL"

    def test_returns_missing_when_l1_does_not_exist(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(0, "MISSING"))
        assert mod.probe_l1() == "MISSING"

    def test_returns_symlink_for_dangling_l1_link(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(0, "SYMLINK"))
        assert mod.probe_l1() == "SYMLINK"

    def test_returns_error_when_l1_probe_fails(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(2, "", "UNREADABLE"))
        assert mod.probe_l1() == "ERROR"

    def test_size_floor_is_one_mib(self, mod):
        # Documented invariant: the floor is exactly 1 MiB. Changing this
        # value must be a conscious decision ? pin it.
        assert mod.L1_SIZE_FLOOR == 1_048_576


class TestProbeBackup:
    @pytest.mark.parametrize(
        "status",
        [
            "SAFE",
            "MISSING",
            "SYMLINK",
            "NOT_REGULAR",
            "SMALL",
            "SCRIPT",
            "MOCK",
            "NON_ELF",
            "MANIFEST_MISSING",
            "MANIFEST_INVALID",
            "HASH_MISMATCH",
        ],
    )
    def test_returns_known_backup_status(self, mod, monkeypatch, status):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(0, status))
        assert mod.probe_backup() == status

    def test_fails_closed_when_backup_probe_errors(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "stream", lambda *a, **k: _completed(2, "", "denied"))
        assert mod.probe_backup() == "ERROR"


# ---------------------------------------------------------------------------
# main(): safety gates around the install path
# ---------------------------------------------------------------------------


class TestMainSafety:
    """Verify the wrapper-state-driven refusal / warn gates in main()."""

    def _patch_source(self, mod, monkeypatch, exists: bool):
        monkeypatch.setattr(mod.Path, "exists", lambda self: exists)
        # Path("scripts/mock-gaussian/mock_l1_exe") must also resolve; fake it.
        monkeypatch.setattr(mod, "SOURCE", MagicMock(spec=Path))
        mod.SOURCE.exists.return_value = exists
        mod.SOURCE.read_bytes.return_value = b"#!/bin/sh\necho mock\n"

    def _patch_l1(self, mod, monkeypatch, kind: str = "SMALL"):
        """Default the l1.exe probe to SMALL (mock-sized) so install proceeds."""
        monkeypatch.setattr(mod, "probe_l1", lambda: kind)

    def _block_wsl(self, mod, monkeypatch, expected_hash: str | None = None):
        """Replace subprocess.run for the post-operation verify step.
        with a no-op so the test never actually shells out to WSL.

        ``stream`` is usually mocked separately. The inline verification call
        receives a digest matching the fake SOURCE (or an explicitly supplied
        restore digest), so tests exercise the successful audit path without
        touching WSL.
        """
        monkeypatch.setattr(mod, "probe_backup", lambda: "SAFE")

        def _fake_run(*args, **kwargs):
            digest = expected_hash
            if digest is None:
                digest = hashlib.sha256(mod.SOURCE.read_bytes()).hexdigest()
            return _completed(0, f"verified /opt/g16/l1.exe size=15 sha256={digest}")
        monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    @pytest.mark.parametrize(
        "status",
        [
            "MISSING",
            "SYMLINK",
            "NOT_REGULAR",
            "SMALL",
            "SCRIPT",
            "MOCK",
            "NON_ELF",
            "MANIFEST_MISSING",
            "MANIFEST_INVALID",
            "HASH_MISMATCH",
            "ERROR",
        ],
    )
    def test_restore_rejects_unsafe_backup_before_mutation(
        self,
        mod,
        monkeypatch,
        capsys,
        status,
    ):
        operation = MagicMock()
        verify = MagicMock()
        monkeypatch.setattr(mod, "probe_backup", lambda: status)
        monkeypatch.setattr(mod, "stream", operation)
        monkeypatch.setattr(mod.subprocess, "run", verify)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py", "--restore", "--yes"])

        assert mod.main() == 8
        operation.assert_not_called()
        verify.assert_not_called()
        error = capsys.readouterr().err
        assert status in error
        assert "was not modified" in error

    def test_restore_safe_backup_uses_validated_atomic_operation(self, mod, monkeypatch):
        digest = "a" * 64
        monkeypatch.setattr(mod, "probe_backup", MagicMock(return_value="SAFE"))
        operation = MagicMock(
            return_value=_completed(
                0,
                "restored /opt/g16/l1.exe from /opt/g16/l1.exe.real "
                f"(31457280 bytes) sha256={digest}",
            )
        )
        monkeypatch.setattr(mod, "stream", operation)
        self._block_wsl(mod, monkeypatch, expected_hash=digest)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py", "--restore"])

        assert mod.main() == 0
        operation.assert_called_once_with(mod.REMOTE_RESTORE_PY, None)

    def test_refuses_to_install_when_wrapper_is_mock(self, mod, monkeypatch, capsys):
        # Critical Phase 6 guard: MOCK wrapper => refuse to install mock l1.exe.
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "MOCK")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0)))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch)
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 3
        # The stream (install) must NOT have been called.
        mod.stream.assert_not_called()
        captured = capsys.readouterr()
        assert "REFUSING" in captured.err
        assert "JOBDESK_MOCK" in captured.err

    def test_refuses_when_wrapper_probe_fails_closed(self, mod, monkeypatch, capsys):
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "ERROR")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0)))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch)
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 6
        mod.stream.assert_not_called()
        assert "unable to safely probe" in capsys.readouterr().err

    def test_refuses_when_l1_probe_fails_closed(self, mod, monkeypatch, capsys):
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "probe_l1", lambda: "ERROR")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0)))
        self._patch_source(mod, monkeypatch, exists=True)
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 7
        mod.stream.assert_not_called()
        assert "unable to safely probe" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "backup_status",
        ["SYMLINK", "NOT_REGULAR", "SMALL", "SCRIPT", "MOCK", "NON_ELF", "ERROR"],
    )
    def test_install_refuses_existing_untrusted_backup_before_remote_mutation(
        self, mod, monkeypatch, capsys, backup_status
    ):
        operation = MagicMock(return_value=_completed(0))
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "probe_l1", lambda: "SMALL")
        monkeypatch.setattr(mod, "probe_backup", lambda: backup_status)
        monkeypatch.setattr(mod, "stream", operation)
        self._patch_source(mod, monkeypatch, exists=True)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 8
        operation.assert_not_called()
        assert backup_status in capsys.readouterr().err

    @pytest.mark.parametrize("backup_status", ["MANIFEST_MISSING", "MANIFEST_INVALID", "HASH_MISMATCH"])
    def test_install_allows_remote_safe_manifest_repair(self, mod, monkeypatch, capsys, backup_status):
        operation = MagicMock(return_value=_completed(0, "mock installed"))
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "probe_l1", lambda: "SMALL")
        monkeypatch.setattr(mod, "probe_backup", lambda: backup_status)
        monkeypatch.setattr(mod, "stream", operation)
        self._patch_source(mod, monkeypatch, exists=True)
        expected = hashlib.sha256(mod.SOURCE.read_bytes()).hexdigest()
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: _completed(0, f"verified /opt/g16/l1.exe size=15 sha256={expected}"),
        )
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 0
        operation.assert_called_once_with(mod.REMOTE_INSTALL_PY, mod.SOURCE.read_bytes())
        output = capsys.readouterr().err
        assert backup_status in output
        assert "safe repair" in output.lower()

    @pytest.mark.parametrize("backup_status", ["MISSING", "SAFE"])
    def test_install_keeps_missing_and_safe_backup_paths(self, mod, monkeypatch, backup_status):
        operation = MagicMock(return_value=_completed(0, "mock installed"))
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "probe_l1", lambda: "SMALL")
        monkeypatch.setattr(mod, "probe_backup", lambda: backup_status)
        monkeypatch.setattr(mod, "stream", operation)
        self._patch_source(mod, monkeypatch, exists=True)
        expected = hashlib.sha256(mod.SOURCE.read_bytes()).hexdigest()
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda *args, **kwargs: _completed(0, f"verified /opt/g16/l1.exe size=15 sha256={expected}"),
        )
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 0
        operation.assert_called_once()

    def test_yes_overrides_mock_wrapper_refusal(self, mod, monkeypatch, capsys):
        # --yes must WARN but proceed past the MOCK guard.
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "MOCK")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "mock installed at /opt/g16/l1.exe (15 bytes)")))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch)
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py", "--yes"])

        assert mod.main() == 0
        # Stream was called (install proceeded).
        mod.stream.assert_called_once()
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "--yes" in captured.err

    def test_warns_on_shell_wrapper_but_proceeds(self, mod, monkeypatch, capsys):
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "SHELL")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "mock installed at /opt/g16/l1.exe (15 bytes)")))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch)
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 0
        mod.stream.assert_called_once()
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "shell script" in captured.err or "Phase 8C" in captured.err

    def test_warns_on_missing_wrapper_but_proceeds(self, mod, monkeypatch, capsys):
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "MISSING")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "mock installed at /opt/g16/l1.exe (15 bytes)")))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch)
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 0
        mod.stream.assert_called_once()
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "missing" in captured.err.lower()

    def test_silent_when_wrapper_is_binary(self, mod, monkeypatch, capsys):
        # The happy path: upstream g16 is a real ELF binary, no warning expected.
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "mock installed at /opt/g16/l1.exe (15 bytes)")))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch)
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 0
        mod.stream.assert_called_once()
        captured = capsys.readouterr()
        # No warning on the BINARY path.
        assert "WARNING" not in captured.err
        assert "REFUSING" not in captured.err

    def test_exits_nonzero_when_source_missing(self, mod, monkeypatch, capsys):
        # SOURCE not on disk => exit 1, no stream call.
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "stream", MagicMock())
        self._patch_source(mod, monkeypatch, exists=False)
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 1
        mod.stream.assert_not_called()
        captured = capsys.readouterr()
        assert "missing source" in captured.err

    def test_restore_path_skips_wrapper_probe(self, mod, monkeypatch):
        # --restore must NOT probe the wrapper (we're going back, not forward).
        probe_called = MagicMock(return_value="BINARY")
        monkeypatch.setattr(mod, "probe_wrapper", probe_called)
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "restored /opt/g16/l1.exe from /opt/g16/l1.exe.real (12345 bytes) sha256=" + "0" * 64)))
        self._block_wsl(mod, monkeypatch, expected_hash="0" * 64)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py", "--restore"])

        assert mod.main() == 0
        probe_called.assert_not_called()

    def test_dry_run_does_not_call_stream(self, mod, monkeypatch, capsys):
        # --dry-run must NEVER touch WSL.
        probe_called = MagicMock(return_value="MOCK")
        monkeypatch.setattr(mod, "probe_wrapper", probe_called)
        stream_called = MagicMock()
        monkeypatch.setattr(mod, "stream", stream_called)
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch)
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py", "--dry-run"])

        assert mod.main() == 0
        probe_called.assert_not_called()
        stream_called.assert_not_called()
        captured = capsys.readouterr()
        assert "dry-run" in captured.out

    def test_propagates_wsl_install_failure(self, mod, monkeypatch, capsys):
        # If WSL install fails (non-zero exit), main() must return that code.
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(7, "", "boom")))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch)
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 7
        captured = capsys.readouterr()
        assert "exit=7" in captured.err

    def test_propagates_post_install_verification_failure(self, mod, monkeypatch, capsys):
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "mock installed")))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch)
        monkeypatch.setattr(mod, "probe_backup", lambda: "SAFE")

        # ``stream`` is mocked for the install; this call is the independent
        # post-install verification command.
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _completed(9, "", "missing"))
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 9
        assert "verification failed" in capsys.readouterr().err

    def test_install_audit_uses_verified_remote_checksum(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "mock installed")))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch)
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 0
        import json
        entry = json.loads((Path.home() / ".jobdesk-mock-l1.log").read_text(encoding="utf-8"))
        expected = hashlib.sha256(mod.SOURCE.read_bytes()).hexdigest()
        assert entry["size"] == 15
        assert entry["sha256"] == expected
        assert entry["sha256_source"] == "wsl-installed-destination"

    def test_install_checksum_mismatch_fails_before_audit(self, mod, monkeypatch, capsys):
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "mock installed")))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch)
        self._block_wsl(mod, monkeypatch, expected_hash="f" * 64)
        audit_called = MagicMock()
        monkeypatch.setattr(mod, "audit_log", audit_called)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 1
        assert "expected destination checksum" in capsys.readouterr().err
        audit_called.assert_not_called()


# ---------------------------------------------------------------------------
# Static guarantees
# ---------------------------------------------------------------------------


    # --- /opt/g16/l1.exe second-layer safety probe -----------------------

    def test_refuses_when_l1_already_mock_tainted(self, mod, monkeypatch, capsys):
        # If l1.exe already carries the JOBDESK_MOCK marker (a prior install
        # that wasn't restored), refuse to re-overwrite — exit code 4.
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0)))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch, kind="MOCK")
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 4
        mod.stream.assert_not_called()
        captured = capsys.readouterr()
        assert "REFUSING" in captured.err
        assert "l1.exe" in captured.err

    def test_refuses_dangling_l1_symlink_without_yes_override(self, mod, monkeypatch, capsys):
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        operation = MagicMock()
        monkeypatch.setattr(mod, "stream", operation)
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch, kind="SYMLINK")
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py", "--yes"])

        assert mod.main() != 0
        operation.assert_not_called()
        captured = capsys.readouterr()
        assert "REFUSING" in captured.err
        assert "symbolic link" in captured.err

    def test_yes_overrides_l1_already_mock(self, mod, monkeypatch, capsys):
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "mock installed at /opt/g16/l1.exe (15 bytes)")))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch, kind="MOCK")
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py", "--yes"])

        assert mod.main() == 0
        mod.stream.assert_called_once()
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "already-mock" in captured.err

    def test_refuses_when_l1_is_real_sized_binary(self, mod, monkeypatch, capsys):
        # If l1.exe is >= L1_SIZE_FLOOR it is almost certainly the real 31 MiB
        # binary; refuse to clobber without explicit --yes — exit code 5.
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0)))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch, kind="REAL")
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 5
        mod.stream.assert_not_called()
        captured = capsys.readouterr()
        assert "REFUSING" in captured.err
        assert "real Gaussian" in captured.err

    def test_yes_overrides_real_l1(self, mod, monkeypatch, capsys):
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "mock installed at /opt/g16/l1.exe (15 bytes)")))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch, kind="REAL")
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py", "--yes"])

        assert mod.main() == 0
        mod.stream.assert_called_once()
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "real-sized" in captured.err

    def test_warns_when_l1_is_anomalous_shell(self, mod, monkeypatch, capsys):
        # l1.exe is a #!/bin/sh script but not JOBDESK_MOCK-tagged — anomalous,
        # since real l1.exe is ELF. Warn but proceed.
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "mock installed at /opt/g16/l1.exe (15 bytes)")))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch, kind="SHELL")
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 0
        mod.stream.assert_called_once()
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "anomalous" in captured.err.lower() or "shell script" in captured.err.lower()

    def test_info_logged_when_l1_missing_for_first_install(self, mod, monkeypatch, capsys):
        # Fresh install path: no l1.exe yet. Should log INFO, not WARNING.
        monkeypatch.setattr(mod, "probe_wrapper", lambda: "BINARY")
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "mock installed at /opt/g16/l1.exe (15 bytes)")))
        self._patch_source(mod, monkeypatch, exists=True)
        self._patch_l1(mod, monkeypatch, kind="MISSING")
        self._block_wsl(mod, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py"])

        assert mod.main() == 0
        mod.stream.assert_called_once()
        captured = capsys.readouterr()
        assert "INFO" in captured.err
        assert "WARNING" not in captured.err or "WARNING" not in captured.err.split("INFO")[0]

    def test_restore_path_skips_l1_probe(self, mod, monkeypatch):
        # --restore must NOT probe l1.exe (we're going back, not forward).
        probe_called = MagicMock(return_value="BINARY")
        l1_called = MagicMock(return_value="REAL")
        monkeypatch.setattr(mod, "probe_wrapper", probe_called)
        monkeypatch.setattr(mod, "probe_l1", l1_called)
        monkeypatch.setattr(mod, "stream", MagicMock(return_value=_completed(0, "restored /opt/g16/l1.exe from /opt/g16/l1.exe.real (12345 bytes) sha256=" + "0" * 64)))
        self._block_wsl(mod, monkeypatch, expected_hash="0" * 64)
        monkeypatch.setattr(sys, "argv", ["install_mock_l1_wsl.py", "--restore"])

        assert mod.main() == 0
        probe_called.assert_not_called()
        l1_called.assert_not_called()


class TestStaticSafety:
    """Source-level checks: the script must never write to /opt/g16/g16."""

    @pytest.mark.parametrize(
        "script_name",
        ["REMOTE_INSTALL_PY", "REMOTE_PROBE_BACKUP_PY", "REMOTE_RESTORE_PY"],
    )
    def test_embedded_remote_scripts_compile(self, script_name):
        mod = _load_module()
        compile(getattr(mod, script_name), script_name, "exec")

    def test_script_never_targets_g16_for_write(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8")
        # No bare redirection or write to /opt/g16/g16 ??must only target /opt/g16/l1.exe.
        assert "/opt/g16/g16" in text  # present for probing only
        # Critically, REMOTE_INSTALL_PY writes to /opt/g16/l1.exe, NOT to g16.
        install_block = text.split("REMOTE_INSTALL_PY", 1)[1].split('"""', 2)[1]
        assert "/opt/g16/l1.exe" in install_block
        assert "tempfile.mkstemp" in install_block
        assert "os.fsync" in install_block
        assert "os.replace(tmp_path, dest_path)" in install_block
        assert "dest_path.write_text" not in install_block
        # The install script must NOT contain any reference to the g16 wrapper path.
        assert "/opt/g16/g16" not in install_block

    def test_script_refuses_mock_with_exit_3(self):
        # MOCK wrapper => exit code 3 (distinct from source-missing=1 and WSL-fail=N).
        text = SCRIPT_PATH.read_text(encoding="utf-8")
        assert "return 3" in text

    def test_yes_flag_only_overrides_safety_prompt(self):
        text = SCRIPT_PATH.read_text(encoding="utf-8")
        assert "--yes" in text
        assert "args.yes" in text
        # The --yes branch must be inside the MOCK guard, not a global bypass.
        # We assert it's gated by `if wrapper_kind == "MOCK"`.
        assert 'wrapper_kind == "MOCK"' in text
        assert "if not args.yes" in text

    def test_mock_sentinel_is_4kb_read(self):
        # probe reads up to 4096 bytes; if a multi-line MOCK sentinel is added
        # later, the read window must still cover it.
        text = SCRIPT_PATH.read_text(encoding="utf-8")
        assert "read(4096)" in text

    def test_restore_validates_before_atomic_replace_and_never_unlinks_live_dest(self):
        mod = _load_module()
        restore = mod.REMOTE_RESTORE_PY
        assert "backup_path.is_symlink()" in restore
        assert "stat.S_ISREG" in restore
        assert "1048576" in restore
        assert "JOBDESK_MOCK" in restore
        assert "startswith(b'\\x7fELF')" in restore
        assert "manifest_path" in restore
        assert "metadata.get('mode') != stat.S_IMODE(backup_stat.st_mode)" in restore
        assert "metadata.get('sha256') != digest" in restore
        assert restore.index("metadata.get('sha256')") < restore.index("os.replace(tmp_path, dest_path)")
        assert "dest_path.unlink" not in restore

    def test_remote_probe_reports_dangling_destination_symlink(self, mod, monkeypatch, capsys):
        destination = Path("/isolated-test/l1.bin")
        script = mod.REMOTE_PROBE_L1_PY.replace("'/opt/g16/l1.exe'", repr(str(destination)))
        original_is_symlink = Path.is_symlink
        original_exists = Path.exists
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda path: path == destination or original_is_symlink(path),
        )
        monkeypatch.setattr(
            Path,
            "exists",
            lambda path: False if path == destination else original_exists(path),
        )

        with pytest.raises(SystemExit) as exc_info:
            exec(compile(script, "REMOTE_PROBE_L1_PY", "exec"), {})

        assert exc_info.value.code == 0
        assert capsys.readouterr().out.strip() == "SYMLINK"

    def test_remote_install_refuses_dangling_destination_symlink(self, mod, monkeypatch, tmp_path):
        destination = tmp_path / "l1.bin"
        backup = tmp_path / "l1.bin.real"
        manifest = tmp_path / "l1.bin.real.jobdesk.json"
        script = (
            mod.REMOTE_INSTALL_PY.replace("'/opt/g16/l1.exe'", repr(str(destination)))
            .replace("'/opt/g16/l1.exe.real'", repr(str(backup)))
            .replace("'/opt/g16/l1.exe.real.jobdesk.json'", repr(str(manifest)))
        )
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda path: path == destination or original_is_symlink(path),
        )
        monkeypatch.setattr(sys, "stdin", io.StringIO(base64.b64encode(b"mock").decode("ascii")))

        with pytest.raises(SystemExit) as exc_info:
            exec(compile(script, "REMOTE_INSTALL_PY", "exec"), {})

        assert exc_info.value.code == 3
        assert not destination.exists()
        assert not backup.exists()

    def test_staging_permissions_and_directory_sync_precede_and_follow_replaces(self):
        mod = _load_module()
        install = mod.REMOTE_INSTALL_PY
        restore = mod.REMOTE_RESTORE_PY

        assert install.index("os.fchmod(tmp_file.fileno(), desired_mode)") < install.index(
            "os.fsync(tmp_file.fileno())"
        )
        assert install.index("os.replace(tmp_path, dest_path)") < install.index("sync_parent(dest_path)")
        assert install.index("os.replace(backup_tmp, backup_path)") < install.index("sync_parent(backup_path)")
        assert install.index("os.replace(manifest_tmp, manifest_path)") < install.index("sync_parent(manifest_path)")
        assert "if os.name != 'posix':" in install
        assert "directory_fd = os.open(str(path.parent), os.O_RDONLY)" in install

        assert restore.index("os.chmod(tmp_path, stat.S_IMODE(backup_stat.st_mode))") < restore.index(
            "os.fsync(tmp_file.fileno())"
        )
        assert restore.index("copied_digest =") < restore.index("os.replace(tmp_path, dest_path)")
        assert restore.index("os.replace(tmp_path, dest_path)") < restore.index("sync_parent(dest_path)")

    @pytest.mark.parametrize("failure", ["write", "verify", "replace"])
    def test_install_failures_leave_live_destination_intact(self, mod, monkeypatch, tmp_path, failure):
        """The install staging file must never truncate l1.exe before replace."""
        destination = tmp_path / "l1.bin"
        backup = tmp_path / "l1.bin.real"
        manifest = tmp_path / "l1.bin.real.jobdesk.json"
        original = b"\x7fELF" + b"x" * (1_048_576 - 4)
        source = b"#!/bin/sh\n# JOBDESK_MOCK\necho replacement\n"
        destination.write_bytes(original)
        script = (
            mod.REMOTE_INSTALL_PY.replace("'/opt/g16/l1.exe'", repr(str(destination)))
            .replace("'/opt/g16/l1.exe.real'", repr(str(backup)))
            .replace("'/opt/g16/l1.exe.real.jobdesk.json'", repr(str(manifest)))
        )
        monkeypatch.setattr(os, "fchmod", lambda _fd, _mode: None)
        monkeypatch.setattr(sys, "stdin", io.StringIO(base64.b64encode(source).decode("ascii")))

        if failure == "write":
            fsync_calls = 0

            def _fail_mock_fsync(_fd):
                nonlocal fsync_calls
                fsync_calls += 1
                if fsync_calls == 3:  # backup, manifest, then mock staging file
                    raise OSError("injected fsync")

            monkeypatch.setattr(os, "fsync", _fail_mock_fsync)
            expected = OSError
        elif failure == "verify":
            original_read_bytes = Path.read_bytes

            def _tamper_temp(path, *args, **kwargs):
                if path.name.startswith(".l1.exe.install-"):
                    return b"tampered"
                return original_read_bytes(path, *args, **kwargs)

            monkeypatch.setattr(Path, "read_bytes", _tamper_temp)
            expected = SystemExit
        else:
            original_replace = os.replace

            def _fail_replace(src, dst):
                if Path(src).name.startswith(".l1.exe.install-"):
                    raise OSError("injected replace")
                return original_replace(src, dst)

            monkeypatch.setattr(os, "replace", _fail_replace)
            expected = OSError

        with pytest.raises(expected):
            exec(compile(script, "REMOTE_INSTALL_PY", "exec"), {})

        assert destination.read_bytes() == original
        assert backup.read_bytes() == original
        assert not list(tmp_path.glob(".l1.exe.install-*"))

    @pytest.mark.parametrize("existing", ["partial", "manifest-missing", "manifest-invalid"])
    def test_existing_untrusted_backup_refuses_to_overwrite_live_destination(
        self, mod, monkeypatch, tmp_path, existing
    ):
        """A leftover backup artifact is never silently trusted on a later install."""
        destination = tmp_path / "l1.bin"
        backup = tmp_path / "l1.bin.real"
        manifest = tmp_path / "l1.bin.real.jobdesk.json"
        original = b"\x7fELF" + b"x" * (1_048_576 - 4)
        source = b"#!/bin/sh\n# JOBDESK_MOCK\necho replacement\n"
        destination.write_bytes(original)
        if existing == "partial":
            backup.write_bytes(b"partial backup")
        else:
            backup.write_bytes(original)
            if existing == "manifest-invalid":
                manifest.write_text("not json", encoding="utf-8")
        script = (
            mod.REMOTE_INSTALL_PY.replace("'/opt/g16/l1.exe'", repr(str(destination)))
            .replace("'/opt/g16/l1.exe.real'", repr(str(backup)))
            .replace("'/opt/g16/l1.exe.real.jobdesk.json'", repr(str(manifest)))
        )
        monkeypatch.setattr(os, "fchmod", lambda _fd, _mode: None)
        monkeypatch.setattr(sys, "stdin", io.StringIO(base64.b64encode(source).decode("ascii")))

        if existing == "partial":
            with pytest.raises(SystemExit) as exc_info:
                exec(compile(script, "REMOTE_INSTALL_PY", "exec"), {})
            assert exc_info.value.code == 3
            assert destination.read_bytes() == original
        else:
            exec(compile(script, "REMOTE_INSTALL_PY", "exec"), {})
            assert manifest.is_file()
            assert destination.read_bytes() == source

    def test_partial_backup_copy_never_publishes_or_truncates_live_destination(self, mod, monkeypatch, tmp_path):
        destination = tmp_path / "l1.bin"
        backup = tmp_path / "l1.bin.real"
        manifest = tmp_path / "l1.bin.real.jobdesk.json"
        original = b"\x7fELF" + b"x" * (1_048_576 - 4)
        destination.write_bytes(original)
        script = (
            mod.REMOTE_INSTALL_PY.replace("'/opt/g16/l1.exe'", repr(str(destination)))
            .replace("'/opt/g16/l1.exe.real'", repr(str(backup)))
            .replace("'/opt/g16/l1.exe.real.jobdesk.json'", repr(str(manifest)))
        )

        def _partial_copy(_source, target, *args, **kwargs):
            Path(target).write_bytes(b"partial backup")
            raise OSError("injected backup copy failure")

        monkeypatch.setattr(os, "fchmod", lambda _fd, _mode: None)
        monkeypatch.setattr(shutil, "copy2", _partial_copy)
        monkeypatch.setattr(sys, "stdin", io.StringIO(base64.b64encode(b"mock").decode("ascii")))
        with pytest.raises(OSError):
            exec(compile(script, "REMOTE_INSTALL_PY", "exec"), {})

        assert destination.read_bytes() == original
        assert not backup.exists()
        assert not list(tmp_path.glob(".l1.exe.backup-*"))

    def test_manifest_publish_failure_leaves_destination_and_is_safely_repaired(
        self, mod, monkeypatch, tmp_path
    ):
        destination = tmp_path / "l1.bin"
        backup = tmp_path / "l1.bin.real"
        manifest = tmp_path / "l1.bin.real.jobdesk.json"
        original = b"\x7fELF" + b"x" * (1_048_576 - 4)
        source = b"#!/bin/sh\n# JOBDESK_MOCK\necho replacement\n"
        destination.write_bytes(original)
        script = (
            mod.REMOTE_INSTALL_PY.replace("'/opt/g16/l1.exe'", repr(str(destination)))
            .replace("'/opt/g16/l1.exe.real'", repr(str(backup)))
            .replace("'/opt/g16/l1.exe.real.jobdesk.json'", repr(str(manifest)))
        )
        original_replace = os.replace

        def _fail_manifest_replace(src, target):
            if Path(target) == manifest:
                raise OSError("injected manifest publish failure")
            return original_replace(src, target)

        monkeypatch.setattr(os, "fchmod", lambda _fd, _mode: None)
        monkeypatch.setattr(os, "replace", _fail_manifest_replace)
        monkeypatch.setattr(sys, "stdin", io.StringIO(base64.b64encode(source).decode("ascii")))
        with pytest.raises(OSError):
            exec(compile(script, "REMOTE_INSTALL_PY", "exec"), {})

        assert destination.read_bytes() == original
        assert backup.read_bytes() == original
        assert not manifest.exists()

        monkeypatch.setattr(os, "replace", original_replace)
        monkeypatch.setattr(sys, "stdin", io.StringIO(base64.b64encode(source).decode("ascii")))
        exec(compile(script, "REMOTE_INSTALL_PY", "exec"), {})
        assert manifest.is_file()
        assert destination.read_bytes() == source

    def test_trusted_old_backup_cannot_overwrite_new_real_destination(self, mod, monkeypatch, tmp_path):
        """A valid old backup is not authority to replace a newer Gaussian binary."""
        destination = tmp_path / "l1.bin"
        backup = tmp_path / "l1.bin.real"
        manifest = tmp_path / "l1.bin.real.jobdesk.json"
        old_real = b"\x7fELF" + b"old" * 349_524
        new_real = b"\x7fELF" + b"new" * 349_524
        backup.write_bytes(old_real)
        destination.write_bytes(new_real)
        backup_stat = backup.stat()
        manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "source": str(destination),
                    "backup": str(backup),
                    "size": backup_stat.st_size,
                    "mode": stat.S_IMODE(backup_stat.st_mode),
                    "sha256": hashlib.sha256(old_real).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        script = (
            mod.REMOTE_INSTALL_PY.replace("'/opt/g16/l1.exe'", repr(str(destination)))
            .replace("'/opt/g16/l1.exe.real'", repr(str(backup)))
            .replace("'/opt/g16/l1.exe.real.jobdesk.json'", repr(str(manifest)))
        )
        monkeypatch.setattr(os, "fchmod", lambda _fd, _mode: None)
        monkeypatch.setattr(sys, "stdin", io.StringIO(base64.b64encode(b"mock").decode("ascii")))

        with pytest.raises(SystemExit) as exc_info:
            exec(compile(script, "REMOTE_INSTALL_PY", "exec"), {})

        assert exc_info.value.code == 3
        assert destination.read_bytes() == new_real

    def test_matching_real_destination_repairs_missing_manifest_before_install(self, mod, monkeypatch, tmp_path):
        destination = tmp_path / "l1.bin"
        backup = tmp_path / "l1.bin.real"
        manifest = tmp_path / "l1.bin.real.jobdesk.json"
        real = b"\x7fELF" + b"x" * (1_048_576 - 4)
        destination.write_bytes(real)
        backup.write_bytes(real)
        script = (
            mod.REMOTE_INSTALL_PY.replace("'/opt/g16/l1.exe'", repr(str(destination)))
            .replace("'/opt/g16/l1.exe.real'", repr(str(backup)))
            .replace("'/opt/g16/l1.exe.real.jobdesk.json'", repr(str(manifest)))
        )
        monkeypatch.setattr(os, "fchmod", lambda _fd, _mode: None)
        monkeypatch.setattr(sys, "stdin", io.StringIO(base64.b64encode(b"mock").decode("ascii")))

        exec(compile(script, "REMOTE_INSTALL_PY", "exec"), {})

        assert manifest.is_file()
        assert destination.read_bytes() == b"mock"

    @pytest.mark.parametrize("destination_state", ["mock", "missing", "digest-mismatch", "manifest-symlink"])
    def test_manifest_is_not_repaired_without_an_exact_authentic_destination(
        self, mod, monkeypatch, tmp_path, destination_state
    ):
        destination = tmp_path / "l1.bin"
        backup = tmp_path / "l1.bin.real"
        manifest = tmp_path / "l1.bin.real.jobdesk.json"
        real = b"\x7fELF" + b"x" * (1_048_576 - 4)
        backup.write_bytes(real)
        if destination_state == "mock":
            destination.write_bytes(b"#!/bin/sh\n# JOBDESK_MOCK\n")
        elif destination_state == "digest-mismatch":
            destination.write_bytes(b"\x7fELF" + b"y" * (1_048_576 - 4))
        elif destination_state == "manifest-symlink":
            destination.write_bytes(real)
            original_is_symlink = Path.is_symlink
            monkeypatch.setattr(Path, "is_symlink", lambda path: path == manifest or original_is_symlink(path))
        script = (
            mod.REMOTE_INSTALL_PY.replace("'/opt/g16/l1.exe'", repr(str(destination)))
            .replace("'/opt/g16/l1.exe.real'", repr(str(backup)))
            .replace("'/opt/g16/l1.exe.real.jobdesk.json'", repr(str(manifest)))
        )
        monkeypatch.setattr(os, "fchmod", lambda _fd, _mode: None)
        monkeypatch.setattr(sys, "stdin", io.StringIO(base64.b64encode(b"replacement").decode("ascii")))

        with pytest.raises(SystemExit) as exc_info:
            exec(compile(script, "REMOTE_INSTALL_PY", "exec"), {})

        assert exc_info.value.code == 3
        if destination_state == "missing":
            assert not destination.exists()
        elif destination_state == "mock":
            assert destination.read_bytes().startswith(b"#!/bin/sh")
        elif destination_state == "digest-mismatch":
            assert destination.read_bytes().endswith(b"y")
        else:
            assert destination.read_bytes() == real


# ---------------------------------------------------------------------------
# audit_log: every install/restore must leave a trace in ~/.jobdesk-mock-l1.log
# ---------------------------------------------------------------------------


class TestAuditLog:
    def test_install_writes_audit_line_with_sha256(self, mod, monkeypatch, tmp_path):
        # Redirect HOME so we don't pollute the user's real ~/.jobdesk-mock-l1.log.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        mod.audit_log("install", "/opt/g16/l1.exe", 42)

        log = tmp_path / ".jobdesk-mock-l1.log"
        assert log.exists(), "audit log file was not created"
        line = log.read_text(encoding="utf-8").strip()
        # Must be valid JSON.
        import json
        entry = json.loads(line)
        assert entry["action"] == "install"
        assert entry["dest"] == "/opt/g16/l1.exe"
        assert entry["size"] == 42
        assert "ts" in entry
        assert "sha256" in entry
        assert len(entry["sha256"]) == 64  # sha256 hex digest length

    def test_restore_writes_audit_line_without_throwing(self, mod, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        mod.audit_log("restore", "/opt/g16/l1.exe", 0)

        log = tmp_path / ".jobdesk-mock-l1.log"
        assert log.exists()
        line = log.read_text(encoding="utf-8").strip()
        import json
        entry = json.loads(line)
        assert entry["action"] == "restore"
        assert "ts" in entry
        assert ".last-restored.sha256" not in line
        assert entry["hash_error"] == "remote restore checksum unavailable"

    def test_restore_audit_records_remote_checksum(self, mod, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        digest = "a" * 64
        mod.audit_log("restore", "/opt/g16/l1.exe", 123, sha256=digest)

        import json
        entry = json.loads((tmp_path / ".jobdesk-mock-l1.log").read_text(encoding="utf-8"))
        assert entry["sha256"] == digest
        assert entry["sha256_source"] == "wsl-restored-destination"

    def test_audit_log_swallows_os_errors(self, mod, monkeypatch, tmp_path):
        # If HOME points at an unwritable path, audit_log must not raise —
        # the install must succeed even if logging fails.
        # On Windows: deny write to the log file's parent directory.
        log_dir = tmp_path / "no-write"
        log_dir.mkdir()
        log_file = log_dir / ".jobdesk-mock-l1.log"
        log_file.write_text("")  # create it
        log_file.chmod(0o444)
        monkeypatch.setattr(mod.Path, "expanduser", lambda p: log_file if "jobdesk" in str(p) else tmp_path / p)

        # Must not raise (audit failures are non-fatal).
        try:
            mod.audit_log("install", "/opt/g16/l1.exe", 1)
        except OSError as exc:
            pytest.fail(f"audit_log raised OSError (must swallow): {exc}")
