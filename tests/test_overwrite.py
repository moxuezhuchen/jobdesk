"""M6 测试: core/overwrite.py — 覆盖策略纯逻辑。"""

import pytest
from jobdesk_app.core.overwrite import (
    decide_overwrite,
    OverwriteDecision,
    OverwriteResult,
)


class TestOverwritePolicy:
    def test_target_not_exists_allows(self):
        r = decide_overwrite(same_batch=True, size_same=None, policy="deny_cross_batch")
        assert r.decision == OverwriteDecision.allow

    def test_same_batch_same_size_skips(self):
        r = decide_overwrite(same_batch=True, size_same=True, policy="deny_cross_batch")
        assert r.decision == OverwriteDecision.skip

    def test_same_batch_different_size_requires_flag(self):
        r = decide_overwrite(same_batch=True, size_same=False, policy="deny_cross_batch")
        assert r.decision == OverwriteDecision.require_overwrite_flag

    def test_cross_batch_refuses(self):
        r = decide_overwrite(same_batch=False, size_same=False, policy="deny_cross_batch")
        assert r.decision == OverwriteDecision.refuse

    def test_cross_batch_same_size_refuses(self):
        r = decide_overwrite(same_batch=False, size_same=True, policy="deny_cross_batch")
        assert r.decision == OverwriteDecision.refuse

    def test_overwrite_policy_allows_any(self):
        r = decide_overwrite(same_batch=False, size_same=False, policy="overwrite")
        assert r.decision == OverwriteDecision.allow

    def test_reason_is_readable(self):
        r = decide_overwrite(same_batch=False, size_same=False, policy="deny_cross_batch")
        assert len(r.reason) > 0
        assert "拒绝" in r.reason
