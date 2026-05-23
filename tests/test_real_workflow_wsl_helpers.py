import pytest

from tests.integration.test_real_workflow_wsl import _remote_tmp_for_cleanup
from tests.integration.test_real_workflow_orca_wsl import _safe_remote_tmp


def test_remote_tmp_cleanup_accepts_expected_test_path():
    assert _remote_tmp_for_cleanup("/tmp/jobdesk_test") == "/tmp/jobdesk_test"
    assert _safe_remote_tmp("/tmp/jobdesk_orca_test") == "/tmp/jobdesk_orca_test"


@pytest.mark.parametrize(
    "remote_tmp",
    [
        "/tmp/jobdesk_test; rm -rf /",
        "/tmp/jobdesk_test $(touch /tmp/unexpected)",
        "/tmp/not_jobdesk",
    ],
)
def test_remote_tmp_cleanup_rejects_shell_or_out_of_scope_paths(remote_tmp):
    with pytest.raises(ValueError):
        _remote_tmp_for_cleanup(remote_tmp)
    with pytest.raises(ValueError):
        _safe_remote_tmp(remote_tmp)
