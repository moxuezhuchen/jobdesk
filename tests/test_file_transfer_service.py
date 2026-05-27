import inspect
from pathlib import Path

import pytest

from jobdesk_app.core.file_transfer import OverwritePolicy
from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus
from jobdesk_app.remote.errors import RemotePathError
from jobdesk_app.services.file_transfer_service import FileTransferService, ensure_safe_remote_path


class FakeSFTP:
    def __init__(self):
        self.closed = False
        self.uploads = []
        self.downloads = []
        self.deleted = []
        self.renamed = []
        self.created = []
        self.files = {"/remote/a.txt": b"hello\n"}
        self.last_progress_callback = None

    def upload_file(self, local_path, remote_path, overwrite=False, skip_if_same_size=True, dry_run=False, progress_callback=None):
        self.uploads.append((Path(local_path), remote_path, overwrite, skip_if_same_size, dry_run))
        self.last_progress_callback = progress_callback
        return TransferRecord(TransferDirection.upload, str(local_path), remote_path, status=TransferStatus.transferred)

    def download_file(self, remote_path, local_path, overwrite=False, skip_if_same_size=True, dry_run=False, progress_callback=None):
        self.downloads.append((remote_path, Path(local_path), overwrite, skip_if_same_size, dry_run))
        self.last_progress_callback = progress_callback
        return TransferRecord(TransferDirection.download, str(local_path), remote_path, status=TransferStatus.transferred)

    def list_dir_info(self, remote_dir):
        return [remote_dir]

    def is_dir(self, remote_path):
        return False

    def mkdir_p(self, remote_dir):
        self.created.append(remote_dir)

    def remove_file(self, remote_path):
        self.deleted.append(("file", remote_path))

    def remove_dir(self, remote_path):
        self.deleted.append(("dir", remote_path))

    def rename(self, old_path, new_path):
        self.renamed.append((old_path, new_path))

    def exists(self, remote_path):
        return remote_path in self.files

    def read_file_bytes(self, remote_path, max_bytes):
        return self.files[remote_path][:max_bytes]

    def close(self):
        self.closed = True


def test_ensure_safe_remote_path_rejects_relative_backslash_and_parent():
    for value in ("relative/path", "/tmp/../etc", "/tmp\\bad"):
        with pytest.raises(RemotePathError):
            ensure_safe_remote_path(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("//", "/"),
        ("/root/", "/root"),
        ("/root/.", "/root"),
    ],
)
def test_ensure_safe_remote_path_canonicalizes_equivalent_paths(value, expected):
    assert ensure_safe_remote_path(value) == expected


def test_upload_path_maps_overwrite_policy(tmp_path):
    sftp = FakeSFTP()
    service = FileTransferService(lambda: sftp)
    local = tmp_path / "a.txt"
    local.write_text("x", encoding="utf-8")

    rec = service.upload_path(local, "/remote/a.txt", OverwritePolicy.overwrite)

    assert rec.status == TransferStatus.transferred
    assert sftp.uploads[0][2:4] == (True, False)
    assert sftp.closed is True


def test_download_path_maps_skip_policy(tmp_path):
    sftp = FakeSFTP()
    service = FileTransferService(lambda: sftp)

    service.download_path("/remote/a.txt", tmp_path / "a.txt", OverwritePolicy.skip_same_size)

    assert sftp.downloads[0][2:4] == (False, True)


def test_list_remote_default_mode_closes_each_session():
    sessions = []

    def factory():
        sftp = FakeSFTP()
        sessions.append(sftp)
        return sftp

    service = FileTransferService(factory)

    assert service.list_remote("/remote") == ["/remote"]
    assert service.list_remote("/remote") == ["/remote"]
    assert len(sessions) == 2
    assert all(sftp.closed for sftp in sessions)


def test_list_remote_persistent_mode_reuses_session_until_close():
    sessions = []

    def factory():
        sftp = FakeSFTP()
        sessions.append(sftp)
        return sftp

    service = FileTransferService(factory, persistent_session=True)

    service.list_remote("/remote")
    service.list_remote("/remote")

    assert len(sessions) == 1
    assert sessions[0].closed is False

    service.close()

    assert sessions[0].closed is True


def test_persistent_session_is_discarded_after_operation_error():
    class FailingSFTP(FakeSFTP):
        def list_dir_info(self, remote_dir):
            raise RuntimeError("connection lost")

    sessions = []

    def factory():
        sftp = FailingSFTP() if not sessions else FakeSFTP()
        sessions.append(sftp)
        return sftp

    service = FileTransferService(factory, persistent_session=True)

    with pytest.raises(RuntimeError, match="connection lost"):
        service.list_remote("/remote")

    assert sessions[0].closed is True
    assert service.list_remote("/remote") == ["/remote"]
    assert len(sessions) == 2


def test_persistent_session_survives_rename_destination_conflict():
    sessions = []

    def factory():
        sftp = FakeSFTP()
        sftp.files["/remote/b.txt"] = b"existing\n"
        sessions.append(sftp)
        return sftp

    service = FileTransferService(factory, persistent_session=True)

    with pytest.raises(RemotePathError, match="Destination already exists"):
        service.rename_remote("/remote/a.txt", "/remote/b.txt")

    assert sessions[0].closed is False
    assert service.list_remote("/remote") == ["/remote"]
    assert len(sessions) == 1


def test_delete_remote_guards_dangerous_paths():
    service = FileTransferService(lambda: FakeSFTP(), protected_remote_roots=["/remote/work"])

    for value in ("/", "/remote/work"):
        with pytest.raises(RemotePathError):
            service.delete_remote(value)


def test_delete_remote_guards_protected_root_descendants():
    service = FileTransferService(lambda: FakeSFTP(), protected_remote_roots=["/remote/work"])

    with pytest.raises(RemotePathError):
        service.delete_remote("/remote/work/batch_001")


def test_delete_remote_requires_an_explicit_allowed_root():
    service = FileTransferService(lambda: FakeSFTP())

    with pytest.raises(RemotePathError, match="no allowed delete roots"):
        service.delete_remote("/home/xianj/delete_me")


def test_delete_remote_requires_allowed_root_when_configured():
    service = FileTransferService(
        lambda: FakeSFTP(),
        allowed_delete_roots=["/remote/work/batch_001"],
    )

    with pytest.raises(RemotePathError):
        service.delete_remote("/remote/other/batch_001")


def test_delete_remote_allows_descendant_of_allowed_root():
    sftp = FakeSFTP()
    service = FileTransferService(
        lambda: sftp,
        allowed_delete_roots=["/remote/work/batch_001"],
    )

    service.delete_remote("/remote/work/batch_001/t1")

    assert sftp.deleted == [("file", "/remote/work/batch_001/t1")]


def test_mkdir_rename_and_preview_text():
    sftp = FakeSFTP()
    service = FileTransferService(lambda: sftp)

    service.mkdir_remote("/remote/new")
    service.rename_remote("/remote/a.txt", "/remote/b.txt")
    text = service.preview_remote_text("/remote/a.txt")

    assert sftp.created == ["/remote/new"]
    assert sftp.renamed == [("/remote/a.txt", "/remote/b.txt")]
    assert text == "hello\n"

def test_rename_remote_rejects_existing_destination():
    sftp = FakeSFTP()
    sftp.files["/remote/b.txt"] = b"existing\n"
    service = FileTransferService(lambda: sftp)

    with pytest.raises(RemotePathError, match="Destination already exists"):
        service.rename_remote("/remote/a.txt", "/remote/b.txt")

    assert sftp.renamed == []


def test_upload_path_passes_progress_callback(tmp_path):
    sftp = FakeSFTP()
    service = FileTransferService(lambda: sftp)
    local = tmp_path / "a.txt"
    local.write_text("x", encoding="utf-8")

    def cb(done, total):
        return None

    service.upload_path(local, "/remote/a.txt", progress_callback=cb)

    assert sftp.last_progress_callback is cb
    assert sftp.closed is True


def test_download_path_passes_progress_callback(tmp_path):
    sftp = FakeSFTP()
    service = FileTransferService(lambda: sftp)

    def cb(done, total):
        return None

    service.download_path("/remote/a.txt", tmp_path / "a.txt", progress_callback=cb)

    assert sftp.last_progress_callback is cb
    assert sftp.closed is True


@pytest.mark.parametrize(
    "target",
    [
        "/etc/passwd",
        "/home/user/file.txt",
        "/root/uma/file.gjf",
    ],
)
def test_delete_remote_does_not_authorize_current_browsing_directory(target):
    """Browsing a directory does not replace configured deletion roots."""
    sftp = FakeSFTP()
    service = FileTransferService(lambda: sftp)

    with pytest.raises(RemotePathError, match="no allowed delete roots"):
        service.delete_remote(target)


def test_delete_remote_has_no_browsing_directory_authorization_override():
    assert "extra_allowed_roots" not in inspect.signature(FileTransferService.delete_remote).parameters
