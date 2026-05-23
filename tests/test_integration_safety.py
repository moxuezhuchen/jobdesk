from unittest.mock import MagicMock

import pytest

from tests.integration._remote_safety import cleanup_remote_test_dir


def test_cleanup_remote_test_dir_quotes_isolated_descendant():
    ssh = MagicMock()

    cleanup_remote_test_dir(ssh, "/tmp/jobdesk_test/run with space", "/tmp/jobdesk_test")

    ssh.run.assert_called_once_with("rm -rf -- '/tmp/jobdesk_test/run with space'", check=True)


@pytest.mark.parametrize(
    ("target", "root"),
    [
        ("/tmp/jobdesk_test", "/tmp/jobdesk_test"),
        ("/tmp/other/job", "/tmp/jobdesk_test"),
        ("/tmp/jobdesk_test/job", "/tmp"),
    ],
)
def test_cleanup_remote_test_dir_rejects_unsafe_targets(target, root):
    with pytest.raises(ValueError):
        cleanup_remote_test_dir(MagicMock(), target, root)
