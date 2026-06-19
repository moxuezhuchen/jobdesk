from pathlib import Path

from jobdesk_app.core.atomic_write import atomic_write_text


def test_atomic_write_retries_transient_replace_permission_error(tmp_path, monkeypatch):
    path = tmp_path / "data.txt"
    path.write_text("old\n", encoding="utf-8")
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(self, target):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("temporary replace lock")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    atomic_write_text(path, "new\n")

    assert attempts == 2
    assert path.read_text(encoding="utf-8") == "new\n"
