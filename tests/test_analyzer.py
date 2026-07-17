"""P2c: analyzer caches source-file reads across rules within a task."""

from pathlib import Path
from types import SimpleNamespace

from jobdesk_app.config.schema import ExtractResult, ExtractType
from jobdesk_app.core.analyzer import analyze_tasks


def test_analyzer_reads_each_file_once_per_task(tmp_path, monkeypatch):
    results_base = tmp_path / "results"
    task_dir = results_base / "batch1" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "out.log").write_text("E= 1.0\nF= 2.0\n", encoding="utf-8")

    rules = [
        ExtractResult(name="energy", source_glob="*.log", regex=r"E= (?P<value>[\d.]+)", type=ExtractType.float),
        ExtractResult(name="force", source_glob="*.log", regex=r"F= (?P<value>[\d.]+)", type=ExtractType.float),
    ]
    task = SimpleNamespace(task_id="t1", group_key=None)

    reads: list[str] = []
    orig_read_text = Path.read_text

    def counting_read_text(self, *args, **kwargs):
        reads.append(str(self))
        return orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    results, failures = analyze_tasks(rules, [task], results_base, "batch1")

    assert not failures
    assert len(results) == 2  # both rules matched
    assert len([r for r in reads if r.endswith("out.log")]) == 1  # read once despite two rules
