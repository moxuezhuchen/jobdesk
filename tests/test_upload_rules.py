"""M8.5E 测试: core/upload_rules.py — select_upload_files 完整覆盖。"""

import tempfile
from pathlib import Path

import pytest

from jobdesk_app.core.models import TaskPackage
from jobdesk_app.core.upload_rules import select_upload_files
from jobdesk_app.config.schema import TaskFilesUploadConfig, MissingUploadPatternPolicy


def _make_pkg(
    task_id: str = "001",
    files: list[Path] | None = None,
    entry_file: Path | None = None,
    task_dir: Path | None = None,
) -> TaskPackage:
    if files is None:
        files = [Path(f) for f in ["001.inp", "001.xyz", "001.constraint"]]
    if entry_file is None:
        entry_file = files[0]
    return TaskPackage(
        task_id=task_id,
        files=files,
        entry_file=entry_file,
        task_dir=task_dir,
    )


class TestNoConfigSelectsAll:
    def test_none_config_returns_all(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            pkg = _make_pkg(files=[inp / "001.inp"])
            files, names = select_upload_files(pkg, None, inp)
            assert [f.name for f in files] == ["001.inp"]

    def test_empty_include_selects_all(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            for fn in ["001.inp", "001.xyz"]:
                (inp / fn).write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz"])
            cfg = TaskFilesUploadConfig(include=[])
            files, names = select_upload_files(pkg, cfg, inp)
            assert set(f.name for f in files) == {"001.inp", "001.xyz"}


class TestListShorthand:
    def test_list_shorthand_equivalent_to_include(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            for fn in ["001.inp", "001.xyz"]:
                (inp / fn).write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz"])

            # list shorthand via coercion: ["*.inp"] → include=["*.inp"]
            from jobdesk_app.config.schema import UploadConfig
            cfg = UploadConfig(task_files=["*.inp"])
            files, _ = select_upload_files(pkg, cfg.task_files, inp)
            # only 001.inp matches; 001.xyz doesn't
            assert [f.name for f in files] == ["001.inp"]

    def test_list_shorthand_include_only(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            for fn in ["001.inp", "001.xyz"]:
                (inp / fn).write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz"])
            cfg = TaskFilesUploadConfig(include=["*.xyz"], exclude=[], require_entry_file=False)
            files, _ = select_upload_files(pkg, cfg, inp)
            names = {f.name for f in files}
            assert "001.xyz" in names
            # entry_file (.inp) not selected, but require_entry_file=True → error
            with pytest.raises(ValueError, match="entry_file"):
                select_upload_files(pkg, TaskFilesUploadConfig(include=["*.xyz"]), inp)


class TestIncludeExclude:
    def test_include_selects_matching(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            for fn in ["001.inp", "001.xyz", "001.constraint"]:
                (inp / fn).write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz", inp / "001.constraint"])
            cfg = TaskFilesUploadConfig(include=["*.inp", "*.xyz"], require_entry_file=False)
            files, _ = select_upload_files(pkg, cfg, inp)
            assert {f.name for f in files} == {"001.inp", "001.xyz"}

    def test_exclude_removes_files(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            for fn in ["001.inp", "001.xyz", "001.tmp"]:
                (inp / fn).write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz", inp / "001.tmp"])
            cfg = TaskFilesUploadConfig(include=["*"], exclude=["*.tmp"], require_entry_file=False)
            files, _ = select_upload_files(pkg, cfg, inp)
            names = {f.name for f in files}
            assert "001.tmp" not in names
            assert "001.inp" in names

    def test_exclude_zero_match_no_error(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            pkg = _make_pkg(files=[inp / "001.inp"])
            cfg = TaskFilesUploadConfig(include=["*"], exclude=["*.nonexistent"])
            files, _ = select_upload_files(pkg, cfg, inp)
            assert len(files) == 1

    def test_include_and_exclude_together(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            for fn in ["001.inp", "001.xyz", "001.constraint"]:
                (inp / fn).write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz", inp / "001.constraint"])
            cfg = TaskFilesUploadConfig(
                include=["*.inp", "*.xyz", "*.constraint"],
                exclude=["*.constraint"],
                require_entry_file=False,
            )
            files, _ = select_upload_files(pkg, cfg, inp)
            assert {f.name for f in files} == {"001.inp", "001.xyz"}


class TestDirectoryMode:
    def test_directory_mode_relative_to_task_dir(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            task_dir = inp / "001"
            task_dir.mkdir(parents=True)
            (task_dir / "run.sh").write_text("")
            (task_dir / "input.inp").write_text("")
            (task_dir / "coord.xyz").write_text("")
            (task_dir / "old.log").write_text("")

            pkg = _make_pkg(
                task_id="001",
                files=[task_dir / "run.sh", task_dir / "input.inp",
                       task_dir / "coord.xyz", task_dir / "old.log"],
                entry_file=task_dir / "run.sh",
                task_dir=task_dir,
            )
            cfg = TaskFilesUploadConfig(
                include=["run.sh", "*.inp", "*.xyz"],
                exclude=["*.log"],
                require_entry_file=True,
            )
            files, _ = select_upload_files(pkg, cfg, inp)
            names = {f.name for f in files}
            assert names == {"run.sh", "input.inp", "coord.xyz"}
            assert "old.log" not in names


class TestFlatGroupedPatternRelative:
    def test_flat_mode_pattern_relative_to_input_dir(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            (inp / "001.xyz").write_text("")

            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz"])
            # pattern relative to input_dir: "001.inp" matches the file
            cfg = TaskFilesUploadConfig(include=["001.inp", "001.xyz"])
            files, _ = select_upload_files(pkg, cfg, inp)
            assert {f.name for f in files} == {"001.inp", "001.xyz"}


class TestTemplateVariables:
    def test_entry_name_variable(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            (inp / "001.xyz").write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz"])
            cfg = TaskFilesUploadConfig(include=["{entry_name}"])
            files, _ = select_upload_files(pkg, cfg, inp)
            assert len(files) == 1
            assert files[0].name == "001.inp"

    def test_entry_stem_variable(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            (inp / "001.xyz").write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz"])
            cfg = TaskFilesUploadConfig(include=["{entry_stem}.inp"], require_entry_file=False)
            files, _ = select_upload_files(pkg, cfg, inp)
            assert [f.name for f in files] == ["001.inp"]

    def test_stem_alias(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            (inp / "001.xyz").write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz"])
            cfg = TaskFilesUploadConfig(include=["{stem}.xyz"], require_entry_file=False)
            files, _ = select_upload_files(pkg, cfg, inp)
            assert [f.name for f in files] == ["001.xyz"]

    def test_task_id_variable(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            pkg = _make_pkg(task_id="001", files=[inp / "001.inp"])
            cfg = TaskFilesUploadConfig(include=["{task_id}.inp"], require_entry_file=False)
            files, _ = select_upload_files(pkg, cfg, inp)
            assert [f.name for f in files] == ["001.inp"]

    def test_unknown_template_variable_raises(self):
        """未知模板变量在 schema 解析时 fail-fast。"""
        with pytest.raises(Exception, match="unknown"):
            TaskFilesUploadConfig(include=["{unknown}.inp"])


class TestOnMissing:
    def test_include_zero_match_error(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            pkg = _make_pkg(files=[inp / "001.inp"])
            cfg = TaskFilesUploadConfig(include=["*.nonexistent"], on_missing="error")
            with pytest.raises(ValueError, match="均未匹配"):
                select_upload_files(pkg, cfg, inp)

    def test_include_zero_match_warn(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            pkg = _make_pkg(files=[inp / "001.inp"])
            cfg = TaskFilesUploadConfig(include=["*.nonexistent"], on_missing="warn", require_entry_file=False)
            with pytest.warns(UserWarning, match="均未匹配"):
                files, _ = select_upload_files(pkg, cfg, inp)
            assert len(files) == 0

    def test_include_zero_match_ignore(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            pkg = _make_pkg(files=[inp / "001.inp"])
            cfg = TaskFilesUploadConfig(include=["*.nonexistent"], on_missing="ignore", require_entry_file=False)
            files, _ = select_upload_files(pkg, cfg, inp)
            assert len(files) == 0


class TestRequireEntryFile:
    def test_require_entry_true_include_miss_raises(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            (inp / "001.xyz").write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz"])
            cfg = TaskFilesUploadConfig(include=["*.xyz"])  # doesn't match .inp
            with pytest.raises(ValueError, match="entry_file 未进入"):
                select_upload_files(pkg, cfg, inp)

    def test_require_entry_true_excluded_raises(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            (inp / "001.xyz").write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz"])
            cfg = TaskFilesUploadConfig(include=["*"], exclude=["*.inp"])
            with pytest.raises(ValueError, match="entry_file 未进入"):
                select_upload_files(pkg, cfg, inp)

    def test_require_entry_false_allows_missing(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            (inp / "001.inp").write_text("")
            (inp / "001.xyz").write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz"])
            cfg = TaskFilesUploadConfig(include=["*.xyz"], require_entry_file=False)
            files, _ = select_upload_files(pkg, cfg, inp)
            assert [f.name for f in files] == ["001.xyz"]


class TestManifestConsistency:
    def test_task_files_remote_task_files_same_length(self):
        """select_upload_files 返回的两个列表长度必须一致。"""
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            for fn in ["001.inp", "001.xyz"]:
                (inp / fn).write_text("")
            pkg = _make_pkg(files=[inp / "001.inp", inp / "001.xyz"])
            cfg = TaskFilesUploadConfig(include=["*"], require_entry_file=False)
            files, names = select_upload_files(pkg, cfg, inp)
            assert len(files) == len(names)
            for f, n in zip(files, names):
                assert f.name == n


class TestStableOrdering:
    def test_files_returned_in_stable_order(self):
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inputs"
            inp.mkdir()
            fnames = ["c.txt", "a.txt", "b.txt"]
            for fn in fnames:
                (inp / fn).write_text("")
            paths = [inp / fn for fn in fnames]
            pkg = _make_pkg(files=paths)
            cfg = TaskFilesUploadConfig(include=["*"], require_entry_file=False)
            f1, _ = select_upload_files(pkg, cfg, inp)
            f2, _ = select_upload_files(pkg, cfg, inp)
            assert [x.name for x in f1] == [x.name for x in f2]
            assert [x.name for x in f1] == sorted(fnames)
