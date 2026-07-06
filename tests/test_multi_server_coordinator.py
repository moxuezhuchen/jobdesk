"""Tests for :class:`MultiServerCoordinator`.

These tests use ``unittest.mock.MagicMock`` to keep the service-level
coordination logic fully isolated from the real ``RunService`` and SSH
layers. Per-test setup lives in a ``setup()`` factory-style helper rather
than pytest fixtures so each test is self-contained.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jobdesk_app.config.schema import AuthMethod, ServerConfig
from jobdesk_app.services.multi_server_coordinator import (
    CancelResult,
    MultiServerCoordinator,
    RefreshResult,
)
from jobdesk_app.services.run_coordinator import RunOperationOutcome


def _server_config(server_id: str) -> ServerConfig:
    return ServerConfig(
        server_id=server_id,
        host="example.invalid",
        username="user",
        auth_method=AuthMethod.key,
    )


def setup(
    *,
    workspace: Path | None = None,
    server_ids: list[str] | None = None,
    server_lookup: object | None = None,
    session_pool: object | None = None,
    patch_coordinator_for: bool = False,
    runs_dir: Path | None = None,
    tmp_path: Path | None = None,
):
    """Build a fully-mocked environment around ``MultiServerCoordinator``.

    Returns a ``SimpleNamespace`` with the coordinator, the inner
    ``RunCoordinator`` mocks per server (when ``patch_coordinator_for``
    is true), and bookkeeping helpers.

    When ``patch_coordinator_for`` is ``False`` the real
    ``_coordinator_for`` is preserved, so tests that specifically exercise
    lazy creation / caching can do so.
    """
    workspace = workspace or Path("/tmp/jobdesk-msc-test")
    if tmp_path is not None:
        workspace = tmp_path
    if runs_dir is None:
        runs_dir = workspace / "runs"
    server_ids = list(server_ids or [])

    if server_lookup is None:
        lookup_map = {sid: _server_config(sid) for sid in server_ids}

        def _lookup(server_id: str) -> ServerConfig:
            if server_id not in lookup_map:
                raise KeyError(server_id)
            return lookup_map[server_id]

        server_lookup = _lookup

    ssh_factory = MagicMock(name="ssh_factory")
    sftp_factory = MagicMock(name="sftp_factory")

    coordinator = MultiServerCoordinator(
        workspace=workspace,
        server_lookup=server_lookup,
        ssh_factory=ssh_factory,
        sftp_factory=sftp_factory,
        session_pool=session_pool,
        max_workers=4,
        runs_dir=runs_dir,
    )

    coordinator_mocks: dict[str, MagicMock] = {}
    if patch_coordinator_for:
        def _patched_for(server_id: str) -> MagicMock:
            mock = coordinator_mocks.get(server_id)
            if mock is None:
                mock = MagicMock(name=f"rc[{server_id}]")
                coordinator_mocks[server_id] = mock
            coordinator._coordinators[server_id] = mock
            return mock

        coordinator._coordinator_for = _patched_for  # type: ignore[assignment]

    return SimpleNamespace(
        coordinator=coordinator,
        ssh_factory=ssh_factory,
        sftp_factory=sftp_factory,
        coordinator_mocks=coordinator_mocks,
        lookup_map={sid: _server_config(sid) for sid in server_ids},
    )


# ---- 1 ---------------------------------------------------------------------


def test_init_creates_no_coordinators(tmp_path: Path) -> None:
    ctx = setup(tmp_path=tmp_path, server_ids=["a", "b"], patch_coordinator_for=True)

    assert ctx.coordinator._coordinators == {}
    assert ctx.coordinator.list_servers() == []


# ---- 2 ---------------------------------------------------------------------


def test_coordinator_for_lazy_creates(tmp_path: Path) -> None:
    # No mock patch — we exercise the real lazy creation path.
    # Pass real workspace + pre-built RunService-like workspace via tmp_path
    # so the underlying repository is reachable but no SSH is needed.
    ctx = setup(workspace=tmp_path, server_ids=["a"])

    first = ctx.coordinator._coordinator_for("a")

    assert first is not None
    assert "a" in ctx.coordinator.list_servers()


# ---- 3 ---------------------------------------------------------------------


def test_coordinator_for_caches(tmp_path: Path) -> None:
    # No mock patch — real cache semantics matter here.
    ctx = setup(workspace=tmp_path, server_ids=["a", "b"])

    first = ctx.coordinator._coordinator_for("a")
    second = ctx.coordinator._coordinator_for("a")
    third = ctx.coordinator._coordinator_for("b")

    assert first is second
    assert third is not first
    assert sorted(ctx.coordinator.list_servers()) == ["a", "b"]


# ---- 4 ---------------------------------------------------------------------


def test_list_all_runs_empty_servers(tmp_path: Path) -> None:
    ctx = setup(tmp_path=tmp_path, server_ids=[], patch_coordinator_for=True)

    result = ctx.coordinator.list_all_runs()

    assert result == []


# ---- 5 ---------------------------------------------------------------------


def test_refresh_all_no_in_progress(tmp_path: Path) -> None:
    ctx = setup(tmp_path=tmp_path, server_ids=["a", "b"], patch_coordinator_for=True)
    ctx.coordinator._coordinator_for("a")
    ctx.coordinator._coordinator_for("b")
    for server_id in ("a", "b"):
        inner = ctx.coordinator._coordinators[server_id]
        inner.service.list_runs.return_value = [
            SimpleNamespace(
                run_id=f"{server_id}-done",
                server_id=server_id,
                status_summary={"downloaded": 1, "remote_completed": 0},
            )
        ]

    result = ctx.coordinator.refresh_all()

    assert result == {}
    for server_id in ("a", "b"):
        inner = ctx.coordinator._coordinators[server_id]
        inner.refresh.assert_not_called()


# ---- 6 ---------------------------------------------------------------------


def test_cancel_passes_through(tmp_path: Path) -> None:
    ctx = setup(tmp_path=tmp_path, server_ids=["a"], patch_coordinator_for=True)
    inner = ctx.coordinator._coordinator_for("a")
    inner.cancel.return_value = RunOperationOutcome(changed_count=1)

    result = ctx.coordinator.cancel("run-1", "a")

    assert isinstance(result, CancelResult)
    assert result.server_id == "a"
    assert result.run_id == "run-1"
    assert result.outcome is inner.cancel.return_value
    inner.cancel.assert_called_once_with("run-1")


# ---- 7 ---------------------------------------------------------------------


def test_close_no_session_pool_is_safe(tmp_path: Path) -> None:
    ctx = setup(tmp_path=tmp_path, server_ids=[], session_pool=None, patch_coordinator_for=True)

    ctx.coordinator.close()

    assert ctx.coordinator._coordinators == {}


# ---- 8 ---------------------------------------------------------------------


def test_failure_in_one_server_does_not_block_others(tmp_path: Path) -> None:
    def _lookup(server_id: str) -> ServerConfig:
        if server_id == "broken":
            raise RuntimeError("server blew up")
        return _server_config(server_id)

    ctx = setup(
        tmp_path=tmp_path,
        server_ids=["good-1", "broken", "good-2"],
        server_lookup=_lookup,
        patch_coordinator_for=True,
    )
    ctx.coordinator._coordinator_for("good-1")
    ctx.coordinator._coordinator_for("broken")
    ctx.coordinator._coordinator_for("good-2")

    good_records_1 = [
        SimpleNamespace(
            run_id="g1-1",
            server_id="good-1",
            status_summary={"uploaded": 1},
        )
    ]
    good_records_2 = [
        SimpleNamespace(
            run_id="g2-1",
            server_id="good-2",
            status_summary={"uploaded": 1},
        ),
        SimpleNamespace(
            run_id="g2-2",
            server_id="good-2",
            status_summary={"uploaded": 1},
        ),
    ]
    ctx.coordinator._coordinators["good-1"].service.list_runs.return_value = (
        good_records_1
    )
    # 'broken' must never have list_runs called.
    ctx.coordinator._coordinators["good-2"].service.list_runs.return_value = (
        good_records_2
    )

    results = ctx.coordinator.list_all_runs()

    returned_ids = {record.run_id for record in results}
    assert "g1-1" in returned_ids
    assert "g2-1" in returned_ids
    assert "g2-2" in returned_ids
    # The broken server must not appear in any result.
    assert all(
        getattr(record, "server_id", None) != "broken" for record in results
    )


def test_refresh_all_isolates_failure_to_one_server(tmp_path: Path) -> None:
    """``refresh_all`` must continue past a server whose refresh raises."""
    ctx = setup(tmp_path=tmp_path, server_ids=["ok", "boom"], patch_coordinator_for=True)
    ctx.coordinator._coordinator_for("ok")
    ctx.coordinator._coordinator_for("boom")

    ok_record = SimpleNamespace(
        run_id="ok-1",
        server_id="ok",
        status_summary={"running": 1},
    )
    ctx.coordinator._coordinators["ok"].service.list_runs.return_value = [ok_record]
    ctx.coordinator._coordinators["ok"].refresh.return_value = RunOperationOutcome(
        changed_count=2,
    )

    boom_record = SimpleNamespace(
        run_id="boom-1",
        server_id="boom",
        status_summary={"running": 1},
    )
    ctx.coordinator._coordinators["boom"].service.list_runs.return_value = [boom_record]
    ctx.coordinator._coordinators["boom"].refresh.side_effect = RuntimeError("ssn down")

    results = ctx.coordinator.refresh_all()

    # ok server: clean outcome preserved
    assert "ok" in results
    assert isinstance(results["ok"], RefreshResult)
    assert results["ok"].total_changed == 2
    assert results["ok"].errors == []

    # boom server: failure is recorded but does not block the rest
    assert "boom" in results
    assert any("ssn down" in err for err in results["boom"].errors)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
