"""测试 core/lifecycle.py - TaskStatus 状态转换。"""

from jobdesk_app.core.lifecycle import TaskStatus, can_transition


class TestTaskStatus:
    def test_valid_transitions(self):
        assert can_transition(TaskStatus.local_ready, TaskStatus.uploaded)
        assert can_transition(TaskStatus.uploaded, TaskStatus.submitted)
        assert can_transition(TaskStatus.submitted, TaskStatus.running)
        assert can_transition(TaskStatus.running, TaskStatus.remote_completed)
        assert can_transition(TaskStatus.running, TaskStatus.failed)
        assert can_transition(TaskStatus.remote_completed, TaskStatus.downloaded)
        assert can_transition(TaskStatus.downloaded, TaskStatus.analyzed)

    def test_invalid_transitions(self):
        assert not can_transition(TaskStatus.local_ready, TaskStatus.running)
        assert not can_transition(TaskStatus.uploaded, TaskStatus.remote_completed)
        assert not can_transition(TaskStatus.analyzed, TaskStatus.local_ready)
        assert not can_transition(TaskStatus.remote_completed, TaskStatus.submitted)

    def test_same_status_remote_completed_allowed(self):
        assert can_transition(TaskStatus.remote_completed, TaskStatus.remote_completed)

    def test_same_status_local_ready_not_allowed(self):
        assert not can_transition(TaskStatus.local_ready, TaskStatus.local_ready)

    def test_failed_is_terminal_cannot_upload(self):
        assert not can_transition(TaskStatus.failed, TaskStatus.uploaded)

    def test_failed_can_be_reached_from_any(self):
        assert can_transition(TaskStatus.running, TaskStatus.failed)
        assert can_transition(TaskStatus.submitted, TaskStatus.failed)

    def test_enum_values_unique(self):
        values = [s.value for s in TaskStatus]
        assert len(values) == len(set(values))

    def test_status_order(self):
        order = [
            TaskStatus.local_ready,
            TaskStatus.uploaded,
            TaskStatus.submitted,
            TaskStatus.running,
            TaskStatus.remote_completed,
            TaskStatus.downloaded,
            TaskStatus.analyzed,
        ]
        for i in range(len(order) - 1):
            assert can_transition(order[i], order[i + 1])
