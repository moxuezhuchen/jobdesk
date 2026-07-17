from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import pytest

from jobdesk_app.services.session_pool import SessionPool


@dataclass
class Config:
    name: str


class FakeSSH:
    def __init__(self, *, alive: bool = True, connect_error: Exception | None = None):
        self.alive = alive
        self.connect_error = connect_error
        self.connected = 0
        self.closed = 0

    def connect(self) -> None:
        self.connected += 1
        if self.connect_error is not None:
            raise self.connect_error

    def is_alive(self) -> bool:
        return self.alive

    def close(self) -> None:
        self.closed += 1


class FakeSFTP:
    def __init__(self, *, alive: bool = True, create_error: Exception | None = None):
        self.alive = alive
        self.create_error = create_error
        self.closed = 0

    def is_alive(self) -> bool:
        return self.alive

    def close(self) -> None:
        self.closed += 1


class Factory:
    def __init__(
        self,
        ssh_clients: list[FakeSSH] | None = None,
        sftp_clients: list[FakeSFTP] | None = None,
    ):
        self.ssh_clients = list(ssh_clients or [])
        self.sftp_clients = list(sftp_clients or [])
        self.created_ssh: list[FakeSSH] = []
        self.created_sftp: list[FakeSFTP] = []
        self.sftp_error: Exception | None = None

    def ssh(self, _config: Config) -> FakeSSH:
        client = self.ssh_clients.pop(0) if self.ssh_clients else FakeSSH()
        self.created_ssh.append(client)
        return client

    def sftp(self, _ssh: FakeSSH) -> FakeSFTP:
        if self.sftp_error is not None:
            raise self.sftp_error
        client = self.sftp_clients.pop(0) if self.sftp_clients else FakeSFTP()
        self.created_sftp.append(client)
        return client


def make_pool(factory: Factory | None = None) -> tuple[SessionPool, Factory]:
    factory = factory or Factory()
    return SessionPool(factory.ssh, factory.sftp), factory


def test_ssh_only_lease_does_not_create_sftp() -> None:
    factory = Factory()
    factory.sftp_error = RuntimeError("sftp unavailable")
    pool, _ = make_pool(factory)

    with pool.lease("a", Config("a"), need_sftp=False) as lease:
        assert lease.ssh is factory.created_ssh[0]
        assert lease.sftp is None

    assert factory.created_sftp == []


def test_ssh_only_session_is_upgraded_when_sftp_is_later_required() -> None:
    pool, factory = make_pool()

    with pool.lease("a", Config("a"), need_sftp=False) as ssh_only:
        ssh = ssh_only.ssh
        assert ssh_only.sftp is None

    with pool.lease("a", Config("a"), need_sftp=True) as with_sftp:
        assert with_sftp.ssh is ssh
        assert with_sftp.sftp is factory.created_sftp[0]

    assert len(factory.created_ssh) == 1
    assert len(factory.created_sftp) == 1


def test_failed_sftp_upgrade_closes_invalid_client() -> None:
    invalid_sftp = FakeSFTP(alive=False)
    pool, _ = make_pool(Factory(sftp_clients=[invalid_sftp]))

    with pool.lease("a", Config("a"), need_sftp=False):
        pass

    with pytest.raises(RuntimeError, match="live SFTP"):
        with pool.lease("a", Config("a"), need_sftp=True):
            pass

    assert invalid_sftp.closed == 1


def test_same_server_leases_serialize() -> None:
    pool, _ = make_pool()
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        with pool.lease("a", Config("a")):
            first_entered.set()
            assert release_first.wait(2)

    def second() -> None:
        assert first_entered.wait(2)
        with pool.lease("a", Config("a")):
            second_entered.set()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    assert first_entered.wait(2)
    time.sleep(0.05)
    assert not second_entered.is_set()
    release_first.set()
    for thread in threads:
        thread.join(2)
    assert second_entered.is_set()


def test_different_server_leases_can_overlap() -> None:
    pool, _ = make_pool()
    both_entered = threading.Barrier(2, timeout=2)
    errors: list[BaseException] = []

    def use(server_id: str) -> None:
        try:
            with pool.lease(server_id, Config(server_id)):
                both_entered.wait()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=use, args=(server,)) for server in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    assert errors == []


def test_close_is_nonblocking_and_active_clients_close_on_release() -> None:
    pool, factory = make_pool()
    lease = pool.lease("a", Config("a")).__enter__()
    started = time.monotonic()
    pool.close()
    assert time.monotonic() - started < 0.2
    assert factory.created_ssh[0].closed == 0
    assert factory.created_sftp[0].closed == 0
    lease.release()
    assert factory.created_sftp[0].closed == 1
    assert factory.created_ssh[0].closed == 1


def test_close_closes_idle_clients_and_rejects_new_leases() -> None:
    pool, factory = make_pool()
    with pool.lease("a", Config("a")):
        pass
    pool.close()
    assert factory.created_sftp[0].closed == 1
    assert factory.created_ssh[0].closed == 1
    with pytest.raises(RuntimeError, match="closing"):
        with pool.lease("b", Config("b")):
            pass


def test_dead_session_is_replaced_before_yield() -> None:
    dead = FakeSSH(alive=False)
    live = FakeSSH()
    pool, factory = make_pool(Factory([dead, live]))
    with pool.lease("a", Config("a")) as lease:
        assert lease.ssh is live
    assert dead.closed == 1
    assert factory.created_sftp[0].closed == 1


def test_dead_sftp_is_replaced_before_reused_session_is_yielded() -> None:
    old_ssh = FakeSSH()
    dead_sftp = FakeSFTP()
    new_ssh = FakeSSH()
    live_sftp = FakeSFTP()
    pool, _ = make_pool(Factory([old_ssh, new_ssh], [dead_sftp, live_sftp]))

    with pool.lease("a", Config("a")) as first:
        assert first.sftp is dead_sftp
    dead_sftp.alive = False

    with pool.lease("a", Config("a")) as replacement:
        assert replacement.ssh is new_ssh
        assert replacement.sftp is live_sftp

    assert dead_sftp.closed == 1
    assert old_ssh.closed == 1


def test_repeated_dead_sessions_fail_finitely_and_do_not_leak_server_mutex() -> None:
    dead_clients = [FakeSSH(alive=False), FakeSSH(alive=False)]
    live = FakeSSH()
    pool, factory = make_pool(Factory([*dead_clients, live]))
    with pytest.raises(RuntimeError, match="live session"):
        with pool.lease("a", Config("a")):
            pass
    assert [client.closed for client in dead_clients] == [1, 1]
    assert [client.closed for client in factory.created_sftp] == [1, 1]
    with pool.lease("a", Config("a")) as lease:
        assert lease.ssh is live


def test_release_and_context_exit_are_idempotent() -> None:
    pool, factory = make_pool()
    handle = pool.lease("a", Config("a"))
    lease = handle.__enter__()
    lease.release()
    lease.release()
    handle.__exit__(None, None, None)
    pool.close()
    assert factory.created_sftp[0].closed == 1
    assert factory.created_ssh[0].closed == 1


@pytest.mark.parametrize("failure", ["connect", "sftp"])
def test_creation_failure_closes_owned_clients_and_does_not_poison_pool(failure: str) -> None:
    bad = FakeSSH(connect_error=RuntimeError("connect")) if failure == "connect" else FakeSSH()
    factory = Factory([bad, FakeSSH()])
    if failure == "sftp":
        factory.sftp_error = RuntimeError("sftp")
    pool, _ = make_pool(factory)
    with pytest.raises(RuntimeError, match=failure):
        with pool.lease("a", Config("a")):
            pass
    factory.sftp_error = None
    with pool.lease("a", Config("a")):
        pass
    assert bad.closed == 1


def test_changed_config_replaces_idle_session() -> None:
    pool, factory = make_pool()
    with pool.lease("a", Config("old")) as old:
        old_ssh = old.ssh
    with pool.lease("a", Config("new")) as new:
        assert new.ssh is not old_ssh
    assert factory.created_sftp[0].closed == 1
    assert old_ssh.closed == 1


def test_mutating_reused_config_object_replaces_idle_session() -> None:
    pool, factory = make_pool()
    config = Config("old")
    with pool.lease("a", config) as old:
        old_ssh = old.ssh
    config.name = "new"
    with pool.lease("a", config) as new:
        assert new.ssh is not old_ssh
    assert factory.created_sftp[0].closed == 1
    assert old_ssh.closed == 1


# --- Tests for acquire() context manager ---


def test_acquire_basic_usage() -> None:
    pool, factory = make_pool()
    with pool.acquire("a", Config("a")) as lease:
        assert lease.ssh is factory.created_ssh[0]
        assert lease.sftp is factory.created_sftp[0]
    assert factory.created_sftp[0].closed == 0
    assert factory.created_ssh[0].closed == 0
    pool.close()
    assert factory.created_sftp[0].closed == 1
    assert factory.created_ssh[0].closed == 1


def test_acquire_without_sftp() -> None:
    pool, factory = make_pool()
    with pool.acquire("a", Config("a"), need_sftp=False) as lease:
        assert lease.ssh is factory.created_ssh[0]
        assert lease.sftp is None
    assert factory.created_sftp == []
    assert factory.created_ssh[0].closed == 0
    pool.close()
    assert factory.created_ssh[0].closed == 1


def test_acquire_releases_on_exception() -> None:
    pool, factory = make_pool()
    with pytest.raises(RuntimeError, match="test"):
        with pool.acquire("a", Config("a")) as _lease:
            raise RuntimeError("test")
    assert factory.created_sftp[0].closed == 0
    assert factory.created_ssh[0].closed == 0
    pool.close()
    assert factory.created_sftp[0].closed == 1
    assert factory.created_ssh[0].closed == 1


def test_acquire_allows_reuse_after_exception() -> None:
    pool, factory = make_pool()
    with pytest.raises(RuntimeError):
        with pool.acquire("a", Config("a")) as _lease:
            raise RuntimeError("test")
    with pool.acquire("a", Config("a")) as lease:
        assert lease.ssh is factory.created_ssh[0]
        assert lease.sftp is factory.created_sftp[0]


def test_acquire_and_lease_compatibility() -> None:
    pool, factory = make_pool()
    with pool.acquire("a", Config("a")) as lease1:
        ssh1 = lease1.ssh
    with pool.lease("a", Config("a")) as lease2:
        assert lease2.ssh is ssh1
        assert lease2.sftp is factory.created_sftp[0]
    assert factory.created_ssh[0].closed == 0
    pool.close()
    assert factory.created_ssh[0].closed == 1


def test_acquire_creates_new_session_when_config_changes() -> None:
    pool, factory = make_pool()
    with pool.acquire("a", Config("old")) as old:
        old_ssh = old.ssh
    with pool.acquire("a", Config("new")) as new:
        assert new.ssh is not old_ssh
    assert factory.created_sftp[0].closed == 1
    assert old_ssh.closed == 1


def test_acquire_idempotent_release() -> None:
    pool, factory = make_pool()
    lease = pool.acquire("a", Config("a"))
    handle = lease.__enter__()
    handle.release()
    handle.release()
    lease.__exit__(None, None, None)
    pool.close()
    assert factory.created_sftp[0].closed == 1
    assert factory.created_ssh[0].closed == 1
