"""M4 测试: remote/sftp.py — SFTP 上传/下载 mock 测试。

使用 fake SFTP client（Flask-like dict store），不连接真实服务器。
"""

import io
import stat as statlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus
from jobdesk_app.remote.errors import RemotePathError
from jobdesk_app.remote.sftp import SFTPClientWrapper, _validate_remote_path

# ---- fake SFTP client --------------------------------------------------


class FakeSFTPFile:
    def __init__(self, content: bytes):
        self._buf = io.BytesIO(content)

    def read(self, size: int = -1):
        return self._buf.read(size)

    def write(self, data: bytes):
        self._buf.write(data)

    def close(self):
        pass

    def settimeout(self, t):
        pass


class FakeSFTPClient:
    """内存中的伪 SFTP client，模拟 paramiko SFTPClient 接口。"""

    def __init__(self):
        self._files: dict[str, bytes] = {}
        self._attrs: dict[str, "FakeStat"] = {}
        self._closed = False

    def put(self, local_path: str, remote_path: str, confirm: bool = True, callback=None):
        content = Path(local_path).read_bytes()
        self._files[remote_path] = content
        self._attrs[remote_path] = FakeStat.from_bytes(content)
        if callback:
            callback(len(content), len(content))
        return MagicMock()

    def get(self, remote_path: str, local_path: str, callback=None):
        data = self._files.get(remote_path)
        if data is None:
            raise FileNotFoundError(remote_path)
        Path(local_path).write_bytes(data)
        if callback:
            callback(len(data), len(data))

    def open(self, remote_path: str, mode: str = "rb"):
        """Fake file-like object for reading/writing remote files."""
        fake = self
        class _FakeFile:
            def __init__(self):
                self._buf = b""
            def write(self, data: bytes):
                self._buf += data
            def read(self, size: int = -1):
                return fake._files.get(remote_path, b"")[:size] if size > 0 else fake._files.get(remote_path, b"")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                if "w" in mode:
                    fake._files[remote_path] = self._buf
                    fake._attrs[remote_path] = FakeStat.from_bytes(self._buf)
        return _FakeFile()

    def stat(self, remote_path: str):
        if remote_path in self._attrs:
            return self._attrs[remote_path]
        raise FileNotFoundError(remote_path)

    def mkdir(self, remote_path: str):
        self._attrs[remote_path] = FakeStat(is_dir=True)

    def listdir(self, remote_path: str):
        prefix = remote_path.rstrip("/") + "/" if remote_path != "/" else "/"
        names = set()
        for p in self._attrs:
            if p == prefix.rstrip("/"):
                continue
            if p.startswith(prefix):
                rest = p[len(prefix):]
                name = rest.split("/")[0]
                names.add(name)
        return sorted(names)

    def listdir_attr(self, remote_path: str):
        prefix = remote_path.rstrip("/") + "/" if remote_path != "/" else "/"
        entries = {}
        for p, attr in self._attrs.items():
            if p == prefix.rstrip("/"):
                continue
            if p.startswith(prefix):
                rest = p[len(prefix):]
                name = rest.split("/")[0]
                if name not in entries:
                    child_path = prefix + name
                    child_attr = self._attrs.get(child_path, self._attrs.get(child_path.rstrip("/"), FakeStat()))
                    entry = MagicMock()
                    entry.filename = name
                    entry.st_mode = child_attr.st_mode
                    entry.st_size = child_attr.st_size
                    entries[name] = entry
        return list(entries.values())

    def isdir(self, remote_path: str):
        return self._attrs.get(remote_path, FakeStat()).is_dir()

    def close(self):
        self._closed = True


class FakeStat:
    def __init__(self, st_size: int = 0, is_dir: bool = False):
        self.st_size = st_size
        self.st_mode = statlib.S_IFDIR | 0o755 if is_dir else statlib.S_IFREG | 0o644

    def is_dir(self):
        return statlib.S_ISDIR(self.st_mode)

    @classmethod
    def from_bytes(cls, data: bytes):
        return cls(st_size=len(data))


# ---- helpers -----------------------------------------------------------


@pytest.fixture
def fake_sftp():
    return SFTPClientWrapper(FakeSFTPClient())


# ---- remote path validation --------------------------------------------


class TestRemotePathValidation:
    def test_valid_path(self):
        assert _validate_remote_path("/home/user/file.txt") == "/home/user/file.txt"

    def test_reject_backslash(self):
        with pytest.raises(RemotePathError, match="反斜杠"):
            _validate_remote_path("C:\\Users\\file.txt")

    def test_reject_mixed(self):
        with pytest.raises(RemotePathError):
            _validate_remote_path("/home/user\\file.txt")


# ---- upload ------------------------------------------------------------


class TestUploadFile:
    def test_upload_success(self, fake_sftp):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("hello world")
            tmp = f.name
        try:
            rec = fake_sftp.upload_file(Path(tmp), "/remote/test.txt")
            assert rec.status == TransferStatus.transferred
            assert fake_sftp.exists("/remote/test.txt")
            st = fake_sftp.stat("/remote/test.txt")
            assert st.st_size > 0
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_upload_creates_remote_parent_dirs(self, fake_sftp):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("data")
            tmp = f.name
        try:
            rec = fake_sftp.upload_file(Path(tmp), "/a/b/c/out.txt")
            assert rec.status == TransferStatus.transferred
            assert fake_sftp.exists("/a/b/c/out.txt")
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_upload_dry_run_no_write(self, fake_sftp):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("data")
            tmp = f.name
        try:
            rec = fake_sftp.upload_file(Path(tmp), "/remote/dry.txt", dry_run=True)
            assert rec.status == TransferStatus.planned
            assert rec.dry_run is True
            assert not fake_sftp.exists("/remote/dry.txt")
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_upload_skip_same_size(self, fake_sftp):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("data")
            tmp = f.name
        try:
            rec1 = fake_sftp.upload_file(Path(tmp), "/remote/same.txt")
            assert rec1.status == TransferStatus.transferred
            rec2 = fake_sftp.upload_file(Path(tmp), "/remote/same.txt", skip_if_same_size=True)
            assert rec2.status == TransferStatus.skipped
            assert "大小相同" in rec2.reason
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_upload_exists_overwrite_false(self, fake_sftp):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("content1")
            tmp1 = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("different_content")
            tmp2 = f.name
        try:
            fake_sftp.upload_file(Path(tmp1), "/remote/f.txt")
            rec = fake_sftp.upload_file(Path(tmp2), "/remote/f.txt", overwrite=False)
            assert rec.status == TransferStatus.failed
            assert "overwrite=False" in rec.reason
        finally:
            Path(tmp1).unlink(missing_ok=True)
            Path(tmp2).unlink(missing_ok=True)

    def test_upload_overwrite_true(self, fake_sftp):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("old")
            tmp1 = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("new_content")
            tmp2 = f.name
        try:
            fake_sftp.upload_file(Path(tmp1), "/remote/ow.txt")
            rec = fake_sftp.upload_file(Path(tmp2), "/remote/ow.txt", overwrite=True)
            assert rec.status == TransferStatus.transferred
        finally:
            Path(tmp1).unlink(missing_ok=True)
            Path(tmp2).unlink(missing_ok=True)

    def test_upload_local_not_found(self, fake_sftp):
        rec = fake_sftp.upload_file(Path("/nonexistent/file.txt"), "/remote/f.txt")
        assert rec.status == TransferStatus.failed
        assert "文件不存在" in rec.reason

    def test_upload_text_normalizes_crlf(self, fake_sftp):
        """Text files have CRLF/CR normalized to LF on upload."""
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".gjf", delete=False) as f:
            f.write(b"line1\r\nline2\rline3\n")
            tmp = f.name
        try:
            rec = fake_sftp.upload_file(Path(tmp), "/remote/mol.gjf")
            assert rec.status == TransferStatus.transferred
            content = fake_sftp._sftp._files["/remote/mol.gjf"]
            assert content == b"line1\nline2\nline3\n"
            assert b"\r" not in content
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_upload_large_text_streaming_normalizes_crlf(self, fake_sftp, monkeypatch):
        """Large text files use streaming normalization (not full read_bytes)."""
        import jobdesk_app.remote.sftp as sftp_mod
        # Use a tiny chunk size to exercise multi-chunk path
        monkeypatch.setattr(sftp_mod, "_CRLF_CHUNK_SIZE", 16)
        # Build content with CRLF split across chunk boundary
        content = b"A" * 15 + b"\r\n" + b"B" * 15 + b"\r" + b"C" * 5
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".log", delete=False) as f:
            f.write(content)
            tmp = f.name
        try:
            rec = fake_sftp.upload_file(Path(tmp), "/remote/big.log")
            assert rec.status == TransferStatus.transferred
            uploaded = fake_sftp._sftp._files["/remote/big.log"]
            assert b"\r" not in uploaded
            # Verify content correctness
            expected = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            assert uploaded == expected
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_upload_binary_no_crlf_normalization(self, fake_sftp):
        """Binary files (.bin) are uploaded verbatim without CRLF normalization."""
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".bin", delete=False) as f:
            f.write(b"\r\n\x00\r\n")
            tmp = f.name
        try:
            rec = fake_sftp.upload_file(Path(tmp), "/remote/data.bin")
            assert rec.status == TransferStatus.transferred
            content = fake_sftp._sftp._files["/remote/data.bin"]
            assert content == b"\r\n\x00\r\n"  # unchanged
        finally:
            Path(tmp).unlink(missing_ok=True)


# ---- download ----------------------------------------------------------


class TestDownloadFile:
    def test_download_success(self, fake_sftp):
        fake_sftp._sftp._files["/remote/dl.txt"] = b"hello download"
        fake_sftp._sftp._attrs["/remote/dl.txt"] = FakeStat(st_size=14)
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "dl.txt"
            rec = fake_sftp.download_file("/remote/dl.txt", local)
            assert rec.status == TransferStatus.transferred
            assert local.read_bytes() == b"hello download"

    def test_download_creates_local_parent_dirs(self, fake_sftp):
        fake_sftp._sftp._files["/remote/d.txt"] = b"x"
        fake_sftp._sftp._attrs["/remote/d.txt"] = FakeStat(st_size=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "a" / "b" / "d.txt"
            rec = fake_sftp.download_file("/remote/d.txt", local)
            assert rec.status == TransferStatus.transferred
            assert local.read_bytes() == b"x"

    def test_download_dry_run_no_write(self, fake_sftp):
        fake_sftp._sftp._files["/remote/dry.txt"] = b"yyy"
        fake_sftp._sftp._attrs["/remote/dry.txt"] = FakeStat(st_size=3)
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "dry.txt"
            rec = fake_sftp.download_file("/remote/dry.txt", local, dry_run=True)
            assert rec.status == TransferStatus.planned
            assert rec.dry_run is True
            assert not local.exists()

    def test_download_skip_same_size(self, fake_sftp):
        fake_sftp._sftp._files["/remote/s.txt"] = b"abc"
        fake_sftp._sftp._attrs["/remote/s.txt"] = FakeStat(st_size=3)
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "s.txt"
            local.write_bytes(b"abc")
            rec = fake_sftp.download_file("/remote/s.txt", local, skip_if_same_size=True)
            assert rec.status == TransferStatus.skipped

    def test_download_exists_overwrite_false(self, fake_sftp):
        fake_sftp._sftp._files["/remote/d2.txt"] = b"abcdef"
        fake_sftp._sftp._attrs["/remote/d2.txt"] = FakeStat(st_size=6)
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "d2.txt"
            local.write_bytes(b"xyz")  # different size
            rec = fake_sftp.download_file("/remote/d2.txt", local, overwrite=False)
            assert rec.status == TransferStatus.failed

    def test_download_overwrite_true(self, fake_sftp):
        fake_sftp._sftp._files["/remote/d3.txt"] = b"newnew"
        fake_sftp._sftp._attrs["/remote/d3.txt"] = FakeStat(st_size=6)
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "d3.txt"
            local.write_bytes(b"old")
            rec = fake_sftp.download_file("/remote/d3.txt", local, overwrite=True)
            assert rec.status == TransferStatus.transferred
            assert local.read_bytes() == b"newnew"

    def test_download_failure_does_not_replace_existing_local_file(self, fake_sftp, monkeypatch, tmp_path):
        fake_sftp._sftp._files["/remote/d4.txt"] = b"new contents"
        fake_sftp._sftp._attrs["/remote/d4.txt"] = FakeStat(st_size=12)
        local = tmp_path / "d4.txt"
        local.write_bytes(b"old")

        def interrupted_get(remote_path, local_path, callback=None):
            Path(local_path).write_bytes(b"partial")
            raise OSError("connection lost")

        monkeypatch.setattr(fake_sftp._sftp, "get", interrupted_get)

        with pytest.raises(OSError, match="connection lost"):
            fake_sftp.download_file("/remote/d4.txt", local, overwrite=True)

        assert local.read_bytes() == b"old"

    def test_download_remote_not_found(self, fake_sftp):
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "nf.txt"
            rec = fake_sftp.download_file("/remote/nonexistent.txt", local)
            assert rec.status == TransferStatus.failed
            assert "远程文件不存在" in rec.reason


# ---- mkdir_p / exists / stat -------------------------------------------


class TestSFTPOperations:
    def test_mkdir_p(self, fake_sftp):
        fake_sftp.mkdir_p("/a/b/c")
        assert fake_sftp.exists("/a/b/c")

    def test_exists(self, fake_sftp):
        fake_sftp.mkdir_p("/x")
        assert fake_sftp.exists("/x")
        assert not fake_sftp.exists("/nonexistent")

    def test_stat_on_dir(self, fake_sftp):
        fake_sftp.mkdir_p("/mydir")
        st = fake_sftp.stat("/mydir")
        assert st is not None
        assert st.is_dir()


# ---- batch upload/download ---------------------------------------------


class TestBatchTransfer:
    def test_upload_many(self, fake_sftp):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("a")
            f1 = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("bb")
            f2 = f.name
        try:
            records = fake_sftp.upload_many([
                (Path(f1), "/remote/f1.txt"),
                (Path(f2), "/remote/f2.txt"),
            ])
            assert len(records) == 2
            assert all(r.status == TransferStatus.transferred for r in records)
        finally:
            Path(f1).unlink(missing_ok=True)
            Path(f2).unlink(missing_ok=True)

    def test_download_many(self, fake_sftp):
        fake_sftp._sftp._files["/a.txt"] = b"x"
        fake_sftp._sftp._attrs["/a.txt"] = FakeStat(st_size=1)
        fake_sftp._sftp._files["/b.txt"] = b"y"
        fake_sftp._sftp._attrs["/b.txt"] = FakeStat(st_size=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            records = fake_sftp.download_many([
                ("/a.txt", Path(tmpdir) / "a.txt"),
                ("/b.txt", Path(tmpdir) / "b.txt"),
            ])
            assert len(records) == 2
            assert all(r.status == TransferStatus.transferred for r in records)


# ---- directory upload/download -----------------------------------------


class TestDirTransfer:
    def test_upload_dir(self, fake_sftp):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "a.txt").write_text("a", encoding="utf-8")
            (base / "sub").mkdir()
            (base / "sub" / "b.txt").write_text("bb", encoding="utf-8")
            (base / "skip.log").write_text("log", encoding="utf-8")

            records = fake_sftp.upload_dir(
                base, "/remote/base",
                include_globs=["*.txt"],
                exclude_globs=["*.log"],
            )
            assert len(records) == 2
            paths = {r.remote_path for r in records}
            assert "/remote/base/a.txt" in paths
            assert "/remote/base/sub/b.txt" in paths
            assert fake_sftp.exists("/remote/base/a.txt")

    def test_upload_dir_preserves_structure(self, fake_sftp):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "sub" / "deep").mkdir(parents=True)
            (base / "sub" / "deep" / "file.dat").write_text("d", encoding="utf-8")

            records = fake_sftp.upload_dir(base, "/remote")
            assert len(records) == 1
            assert records[0].remote_path == "/remote/sub/deep/file.dat"

    def test_upload_dir_dry_run(self, fake_sftp):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "f.txt").write_text("x", encoding="utf-8")
            records = fake_sftp.upload_dir(base, "/r", dry_run=True)
            assert len(records) == 1
            assert records[0].dry_run is True
            assert records[0].status == TransferStatus.planned
            assert not fake_sftp.exists("/r/f.txt")

    def test_download_dir(self, fake_sftp):
        fake_sftp.mkdir_p("/remote/dir/sub")
        fake_sftp._sftp._files["/remote/dir/a.txt"] = b"a"
        fake_sftp._sftp._attrs["/remote/dir/a.txt"] = FakeStat(st_size=1)
        fake_sftp._sftp._files["/remote/dir/sub/b.txt"] = b"bb"
        fake_sftp._sftp._attrs["/remote/dir/sub/b.txt"] = FakeStat(st_size=2)
        fake_sftp._sftp._files["/remote/dir/skip.log"] = b"xxx"
        fake_sftp._sftp._attrs["/remote/dir/skip.log"] = FakeStat(st_size=3)

        with tempfile.TemporaryDirectory() as tmpdir:
            records = fake_sftp.download_dir(
                "/remote/dir", Path(tmpdir),
                include_globs=["*.txt"],
                exclude_globs=["*.log"],
            )
            assert len(records) == 2
            assert (Path(tmpdir) / "a.txt").exists()
            assert (Path(tmpdir) / "sub" / "b.txt").exists()

    def test_download_dir_dry_run(self, fake_sftp):
        fake_sftp.mkdir_p("/remote/d2")
        fake_sftp._sftp._files["/remote/d2/f.txt"] = b"x"
        fake_sftp._sftp._attrs["/remote/d2/f.txt"] = FakeStat(st_size=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            records = fake_sftp.download_dir("/remote/d2", Path(tmpdir), dry_run=True)
            assert len(records) == 1
            assert records[0].dry_run is True
            assert records[0].status == TransferStatus.planned

    def test_download_dir_skips_symlinks(self, fake_sftp):
        fake_sftp.mkdir_p("/remote/d3")
        fake_sftp._sftp._files["/remote/d3/real.txt"] = b"x"
        fake_sftp._sftp._attrs["/remote/d3/real.txt"] = FakeStat(st_size=1)
        link = FakeStat(is_dir=True)
        link.st_mode = statlib.S_IFLNK | 0o777  # symlinked dir (potential loop)
        fake_sftp._sftp._attrs["/remote/d3/loop"] = link

        with tempfile.TemporaryDirectory() as tmpdir:
            records = fake_sftp.download_dir("/remote/d3", Path(tmpdir))
            assert len(records) == 1
            assert (Path(tmpdir) / "real.txt").exists()


# ---- TransferRecord ----------------------------------------------------


class TestTransferRecord:
    def test_fields(self):
        rec = TransferRecord(
            direction=TransferDirection.upload,
            local_path="/local/f.txt",
            remote_path="/remote/f.txt",
            size_bytes=100,
            status=TransferStatus.transferred,
            reason="ok",
        )
        assert rec.direction == TransferDirection.upload
        assert rec.local_path == "/local/f.txt"
        assert rec.remote_path == "/remote/f.txt"
        assert rec.size_bytes == 100
        assert rec.status == TransferStatus.transferred
        assert rec.reason == "ok"
        assert rec.dry_run is False


# ---- UTF-8 filenames ---------------------------------------------------


class TestUtf8Filenames:
    def test_utf8_local_filename(self, fake_sftp):
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "测试文件.txt"
            local.write_text("内容", encoding="utf-8")
            rec = fake_sftp.upload_file(local, "/remote/utf8.txt")
            assert rec.status == TransferStatus.transferred

    def test_utf8_remote_path(self, fake_sftp):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("x")
            tmp = f.name
        try:
            rec = fake_sftp.upload_file(Path(tmp), "/remote/中文路径/文件.txt")
            assert rec.status == TransferStatus.transferred
            assert fake_sftp.exists("/remote/中文路径/文件.txt")
        finally:
            Path(tmp).unlink(missing_ok=True)
