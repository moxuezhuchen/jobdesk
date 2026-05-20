"""M2 测试: analyzer / grouping / outputs。

使用临时文件作为 fixture，不依赖真实 SSH 或远程文件。
"""

import json
import tempfile
from pathlib import Path
from datetime import datetime

import pytest

from jobdesk_app.core.models import BatchMeta, ResultRecord, FailureRecord
from jobdesk_app.core.manifest import TaskRecord
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.analyzer import analyze_tasks, analyze_one_task
from jobdesk_app.core.outputs import (
    write_final_results_tsv,
    write_failures_tsv,
    write_summary_json,
    read_final_results_tsv,
    _FINAL_RESULTS_COLUMNS,
    _FAILURES_COLUMNS,
)
from jobdesk_app.config.schema import (
    ExtractResult,
    ExtractStrategy,
    ExtractType,
)

# ---- helpers ----------------------------------------------------------------


def _make_task(task_id: str, group_key: str | None = None) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        batch_id="b1",
        task_files=[f"inputs/{task_id}.gjf"],
        remote_job_dir=f"/remote/{task_id}",
        remote_task_files=[f"{task_id}.gjf"],
        rendered_command="cmd",
        group_key=group_key,
    )


def _make_extract_rules(extract_results: list[dict] | None = None) -> list:
    if not extract_results:
        return []
    from jobdesk_app.config.schema import ExtractResult
    return [ExtractResult(**r) for r in extract_results]


def _write_file(file_dir: Path, filename: str, content: str) -> Path:
    file_dir.mkdir(parents=True, exist_ok=True)
    fpath = file_dir / filename
    fpath.write_text(content, encoding="utf-8")
    return fpath


# ---- M2.0 batch_id 微秒 ----------------------------------------------------


class TestBatchIdMicroseconds:
    def test_batch_id_has_microseconds(self):
        import time
        b1 = BatchMeta(project_name="p", max_parallel=4, remote_batch_dir="/r")
        time.sleep(0.001)
        b2 = BatchMeta(project_name="p", max_parallel=4, remote_batch_dir="/r")
        assert b1.batch_id != b2.batch_id
        assert len(b1.batch_id) == 22
        parts = b1.batch_id.split("_")
        assert len(parts) == 3  # YYYYMMDD, HHMMSS, ffffff
        assert len(parts[2]) == 6


# ---- 1. extract 为空 -------------------------------------------------------


class TestEmptyExtract:
    def test_empty_extract_no_error(self):
        cfg = _make_extract_rules([])
        tasks = [_make_task("t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            results, failures = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert results == []
            assert failures == []


# ---- 2. 单任务单文件单字段 -------------------------------------------------


class TestSingleExtract:
    def test_single_float(self):
        cfg = _make_extract_rules([
            {
                "name": "energy",
                "source_glob": "output.log",
                "regex": r"Energy:\s+(?P<value>-?[\d.]+)",
                "strategy": "last",
                "type": "float",
                "unit": "hartree",
            }
        ])
        tasks = [_make_task("mol_001")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "mol_001"
            _write_file(task_dir, "output.log", "Energy: -150.12345\n")
            results, failures = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert len(results) == 1
            assert len(failures) == 0
            r = results[0]
            assert r.task_id == "mol_001"
            assert r.field_name == "energy"
            assert isinstance(r.value, float)
            assert abs(r.value - (-150.12345)) < 1e-8
            assert r.value_type == "float"
            assert r.unit == "hartree"

    def test_single_int(self):
        cfg = _make_extract_rules([
            {
                "name": "count",
                "source_glob": "output.log",
                "regex": r"Count:\s+(?P<value>\d+)",
                "strategy": "first",
                "type": "int",
            }
        ])
        tasks = [_make_task("t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "t1"
            _write_file(task_dir, "output.log", "Count: 42\n")
            results, failures = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert len(results) == 1
            assert results[0].value == 42
            assert results[0].value_type == "int"

    def test_single_str(self):
        cfg = _make_extract_rules([
            {
                "name": "status",
                "source_glob": "output.log",
                "regex": r"Status:\s+(?P<value>\S+)",
                "strategy": "first",
                "type": "str",
            }
        ])
        tasks = [_make_task("t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "t1"
            _write_file(task_dir, "output.log", "Status: completed\n")
            results, failures = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert results[0].value == "completed"
            assert results[0].value_type == "str"


# ---- 3. first / last / all 策略 ---------------------------------------


class TestStrategyFirstLastAll:
    def _make_cfg(self, strategy: str) -> list:
        return _make_extract_rules([
            {
                "name": "energy",
                "source_glob": "output.log",
                "regex": r"Energy:\s+(?P<value>-?[\d.]+)",
                "strategy": strategy,
                "type": "float",
            }
        ])

    def test_strategy_first(self):
        cfg = self._make_cfg("first")
        tasks = [_make_task("t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "t1"
            _write_file(task_dir, "output.log", "Energy: 1.0\nEnergy: 2.0\nEnergy: 3.0\n")
            results, _ = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert len(results) == 1
            assert abs(results[0].value - 1.0) < 1e-8

    def test_strategy_last(self):
        cfg = self._make_cfg("last")
        tasks = [_make_task("t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "t1"
            _write_file(task_dir, "output.log", "Energy: 1.0\nEnergy: 2.0\nEnergy: 3.0\n")
            results, _ = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert len(results) == 1
            assert abs(results[0].value - 3.0) < 1e-8

    def test_strategy_all(self):
        cfg = self._make_cfg("all")
        tasks = [_make_task("t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "t1"
            _write_file(task_dir, "output.log", "Energy: 1.0\nEnergy: 2.0\nEnergy: 3.0\n")
            results, _ = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert len(results) == 3
            assert abs(results[0].value - 1.0) < 1e-8
            assert abs(results[1].value - 2.0) < 1e-8
            assert abs(results[2].value - 3.0) < 1e-8
            assert results[0].result_id == "energy_0"
            assert results[1].result_id == "energy_1"
            assert results[2].result_id == "energy_2"


# ---- 4. 多字段提取 --------------------------------------------------


class TestMultiField:
    def test_two_fields(self):
        cfg = _make_extract_rules([
            {"name": "energy", "source_glob": "out.log", "regex": r"E=\s*(?P<value>-?[\d.]+)", "strategy": "last", "type": "float"},
            {"name": "freq", "source_glob": "out.log", "regex": r"Freq:\s*(?P<value>-?[\d.]+)", "strategy": "first", "type": "float"},
        ])
        tasks = [_make_task("t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "t1"
            _write_file(task_dir, "out.log", "E= -150.5\nFreq: 1234.5\n")
            results, failures = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert len(results) == 2
            assert len(failures) == 0
            fields = {r.field_name: r.value for r in results}
            assert abs(fields["energy"] - (-150.5)) < 1e-8
            assert abs(fields["freq"] - 1234.5) < 1e-8


# ---- 5. 多 source 文件 --------------------------------------------------


class TestMultiSourceFiles:
    def test_glob_matches_multiple_files(self):
        cfg = _make_extract_rules([
            {
                "name": "energy",
                "source_glob": "*.log",
                "regex": r"E=\s*(?P<value>-?[\d.]+)",
                "strategy": "all",
                "type": "float",
            }
        ])
        tasks = [_make_task("t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "t1"
            _write_file(task_dir, "step1.log", "E= -150.0\n")
            _write_file(task_dir, "step2.log", "E= -150.5\n")
            results, _ = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert len(results) == 2


# ---- 6. 缺失 source 文件 --------------------------------------------------


class TestMissingSourceFile:
    def test_missing_file_generates_failure(self):
        cfg = _make_extract_rules([
            {"name": "e", "source_glob": "missing.log", "regex": r"(?P<value>\d+)", "strategy": "first", "type": "float"},
        ])
        tasks = [_make_task("t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "t1"
            task_dir.mkdir(parents=True, exist_ok=True)
            results, failures = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert results == []
            assert len(failures) == 1
            assert failures[0].stage == "analysis"
            assert "未找到" in failures[0].reason


# ---- 7. regex 无匹配 --------------------------------------------------


class TestRegexNoMatch:
    def test_no_match_generates_failure(self):
        cfg = _make_extract_rules([
            {"name": "e", "source_glob": "*.log", "regex": r"NOT_FOUND:(?P<value>\d+)", "strategy": "first", "type": "int"},
        ])
        tasks = [_make_task("t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "t1"
            _write_file(task_dir, "out.log", "some other content\n")
            results, failures = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert results == []
            assert len(failures) == 1


# ---- 8. 类型转换失败 --------------------------------------------------


class TestTypeConversionFailure:
    def test_float_convert_fails(self):
        cfg = _make_extract_rules([
            {"name": "e", "source_glob": "*.log", "regex": r"E=\s*(?P<value>\S+)", "strategy": "first", "type": "float"},
        ])
        tasks = [_make_task("t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "t1"
            _write_file(task_dir, "out.log", "E= not_a_number\n")
            results, failures = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert results == []
            assert len(failures) == 1
            assert "类型转换" in failures[0].reason


# ---- 9. 单任务分析失败不中断 --------------------------------------------------


class TestGracefulDegradation:
    def test_one_fails_others_succeed(self):
        cfg = _make_extract_rules([
            {"name": "e", "source_glob": "*.log", "regex": r"E=\s*(?P<value>-?[\d.]+)", "strategy": "last", "type": "float"},
        ])
        tasks = [_make_task("t1"), _make_task("t2"), _make_task("t3")]
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "b1" / "t1").mkdir(parents=True, exist_ok=True)
            _write_file(base / "b1" / "t2", "output.log", "E= -100.0\n")
            _write_file(base / "b1" / "t3", "output.log", "E= -200.0\n")
            (base / "b1" / "t1").mkdir(parents=True, exist_ok=True)  # no file

            results, failures = analyze_tasks(cfg, tasks, base, "b1")
            assert len(results) == 2
            assert len(failures) == 1
            assert failures[0].task_id == "t1"


# ---- 10. grouping --------------------------------------------------


class TestOutputs:
    def test_write_final_results_tsv_fields(self):
        results = [
            ResultRecord(
                task_id="t1", batch_id="b1", group_key="g", result_id="e_0",
                source_file="f.log", field_name="energy", value=-150.0,
                value_type="float", unit="hartree",
                is_best_for_task=True, relative_group=0.0, relative_global=50.0,
            )
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "final_results.tsv"
            write_final_results_tsv(results, path)
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 2
            header_cols = lines[0].split("\t")
            assert header_cols == _FINAL_RESULTS_COLUMNS

    def test_write_final_results_tsv_roundtrip(self):
        results = [
            ResultRecord(
                task_id="t1", batch_id="b1", group_key="g", result_id="e_0",
                source_file="f.log", field_name="energy", value=-150.123,
                value_type="float", unit="hartree",
                is_best_for_task=True, relative_group=0.0, relative_global=50.0,
            ),
            ResultRecord(
                task_id="t2", batch_id="b1", group_key=None, result_id="e_0",
                source_file="f2.log", field_name="energy", value=-140.0,
                value_type="float", unit="hartree",
                is_best_for_task=False, relative_group=None, relative_global=None,
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "final_results.tsv"
            write_final_results_tsv(results, path)
            loaded = read_final_results_tsv(path)
            assert len(loaded) == 2
            assert loaded[0].task_id == "t1"
            assert abs(loaded[0].value - (-150.123)) < 1e-8
            assert loaded[0].value_type == "float"
            assert loaded[0].unit == "hartree"
            assert loaded[0].is_best_for_task is True
            assert loaded[0].relative_group == 0.0
            assert loaded[0].relative_global == 50.0
            assert loaded[1].group_key is None
            assert loaded[1].relative_group is None

    def test_write_failures_tsv(self):
        failures = [
            FailureRecord(task_id="t1", batch_id="b1", stage="analysis", reason="no match", source_file="f.log", context="ctx"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "failures.tsv"
            write_failures_tsv(failures, path)
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 2
            assert lines[0].split("\t") == _FAILURES_COLUMNS

    def test_write_summary_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "summary.json"
            write_summary_json("b1", 10, 8, 12, 2, 3, path)
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["batch_id"] == "b1"
            assert data["task_count"] == 10
            assert data["analyzed_task_count"] == 8
            assert data["result_count"] == 12
            assert data["failure_count"] == 2
            assert data["group_count"] == 3
            assert "generated_at" in data

    def test_outputs_create_parent_dirs(self):
        results = [
            ResultRecord(task_id="t1", batch_id="b1", source_file="f", field_name="e", value=1.0, value_type="float"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "deep" / "nested" / "final_results.tsv"
            write_final_results_tsv(results, path)
            assert path.exists()

    def test_write_final_results_column_order_fixed(self):
        results = [
            ResultRecord(task_id="t1", batch_id="b1", source_file="f", field_name="e", value=1.0, value_type="float"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "fr.tsv"
            write_final_results_tsv(results, path)
            header = path.read_text(encoding="utf-8").split("\n")[0].split("\t")
            assert header == _FINAL_RESULTS_COLUMNS


# ---- 12. UTF-8 和非 ASCII --------------------------------------------------


class TestUtf8Support:
    def test_chinese_in_file_content(self):
        cfg = _make_extract_rules([
            {"name": "结果", "source_glob": "输出.log",
             "regex": r"能量:\s*(?P<value>-?[\d.]+)", "strategy": "first", "type": "float", "unit": "哈特里"},
        ])
        tasks = [_make_task("任务1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "任务1"
            _write_file(task_dir, "输出.log", "能量: -150.5\n")
            results, _ = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert len(results) == 1
            assert results[0].field_name == "结果"
            assert results[0].task_id == "任务1"
            assert results[0].unit == "哈特里"


# ---- 13. empty results 不写死字段 ---------------------------------------------


class TestNoFieldNameHardcoding:
    def test_arbitrary_field_name(self):
        cfg = _make_extract_rules([
            {"name": "dipole", "source_glob": "*.log",
             "regex": r"Dipole:\s*(?P<value>-?[\d.]+)", "strategy": "last", "type": "float"},
        ])
        tasks = [_make_task("t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "b1" / "t1"
            _write_file(task_dir, "out.log", "Dipole: 2.5\n")
            results, _ = analyze_tasks(cfg, tasks, Path(tmpdir), "b1")
            assert results[0].field_name == "dipole"
            assert abs(results[0].value - 2.5) < 1e-8


# ---- 14. 集成测试: analyze → group → relative 写回 ------------------------------


