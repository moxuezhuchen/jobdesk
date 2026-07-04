"""测试 core/lifecycle.py - TaskStatus 枚举。"""

from jobdesk_app.core.lifecycle import TaskStatus


class TestTaskStatus:
    def test_enum_values_unique(self):
        values = [s.value for s in TaskStatus]
        assert len(values) == len(set(values))

    def test_value_equals_name(self):
        for status in TaskStatus:
            assert status.value == status.name

    def test_expected_states_exist(self):
        expected = {
            "local_ready",
            "uploaded",
            "submitting",
            "uncertain",
            "submitted",
            "running",
            "remote_completed",
            "downloaded",
            "analyzed",
            "failed",
            "cancelled",
        }
        assert {s.value for s in TaskStatus} == expected
