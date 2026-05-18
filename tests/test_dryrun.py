"""M6 测试: core/dryrun.py — DryRunAction / DryRunPlan。"""

from jobdesk_app.core.dryrun import DryRunAction, DryRunPlan, RiskLevel


class TestDryRunAction:
    def test_create_action(self):
        a = DryRunAction(
            action_type="upload_file",
            target="/remote/file.txt",
            description="上传 file.txt",
            would_modify_remote=True,
            risk_level=RiskLevel.low,
        )
        assert a.action_type == "upload_file"
        assert a.target == "/remote/file.txt"
        assert a.would_modify_remote is True
        assert a.would_modify_local is False
        assert a.risk_level == RiskLevel.low


class TestDryRunPlan:
    def test_empty_plan(self):
        p = DryRunPlan(title="测试计划")
        assert p.action_count == 0
        assert p.has_risks is False

    def test_plan_with_actions(self):
        actions = [
            DryRunAction("upload", "/r/f.txt", "upload", would_modify_remote=True),
            DryRunAction("overwrite", "/r/f.txt", "would overwrite",
                         would_modify_remote=True, risk_level=RiskLevel.medium),
        ]
        p = DryRunPlan(title="提交计划", actions=actions)
        assert p.action_count == 2
        assert p.has_risks is True

    def test_plan_warnings(self):
        p = DryRunPlan(title="警告测试", warnings=["w1", "w2"])
        assert len(p.warnings) == 2
