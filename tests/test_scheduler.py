"""Tests for scheduler adapters."""
from unittest.mock import MagicMock

import pytest

from jobdesk_app.config.schema import SchedulerConfig, ServerConfig
from jobdesk_app.remote.scheduler import (
    JobState,
    NohupAdapter,
    PBSAdapter,
    ResourceSpec,
    SlurmAdapter,
    make_adapter,
)


def _ssh(stdout="", exit_code=0):
    m = MagicMock()
    m.run.return_value = MagicMock(stdout=stdout, exit_code=exit_code, stderr="")
    return m


class TestResourceSpec:
    def test_walltime_hms(self):
        r = ResourceSpec(walltime_minutes=90)
        assert r.walltime_hms() == "01:30:00"

    def test_from_dict(self):
        r = ResourceSpec.from_dict({"cpus": 8, "memory_mb": 16384, "partition": "gpu"})
        assert r.cpus == 8
        assert r.memory_mb == 16384
        assert r.partition == "gpu"


class TestNohupAdapter:
    def test_submit_returns_pid(self):
        ssh = _ssh(stdout="12345")
        adapter = NohupAdapter()
        job_id = adapter.submit(ssh, "/tmp/run.sh", ResourceSpec())
        assert job_id == "12345"

    def test_poll_alive(self):
        ssh = _ssh(stdout="alive")
        assert NohupAdapter().poll(ssh, "123") == JobState.running

    def test_poll_dead(self):
        ssh = _ssh(stdout="dead")
        assert NohupAdapter().poll(ssh, "123") == JobState.completed

    def test_cancel_sends_kill(self):
        """TERM succeeds: process dies after grace period."""
        ssh = MagicMock()
        ssh.run.side_effect = [
            MagicMock(stdout="", exit_code=0, stderr=""),    # kill -TERM
            MagicMock(stdout="dead", exit_code=0, stderr=""),  # sleep+kill -0
        ]
        NohupAdapter().cancel(ssh, "123")
        assert ssh.run.call_count == 2
        assert "TERM" in ssh.run.call_args_list[0][0][0]
        # Verification uses group-priority: kill -0 -- -<pid>
        check_cmd = ssh.run.call_args_list[1][0][0]
        assert "kill -0 -- -" in check_cmd

    def test_cancel_escalates_to_sigkill(self):
        """TERM fails, KILL succeeds. Both checks use group-priority."""
        ssh = MagicMock()
        ssh.run.side_effect = [
            MagicMock(stdout="", exit_code=0, stderr=""),      # kill -TERM
            MagicMock(stdout="alive", exit_code=0, stderr=""),  # still alive
            MagicMock(stdout="", exit_code=0, stderr=""),      # kill -KILL
            MagicMock(stdout="dead", exit_code=0, stderr=""),  # now dead
        ]
        NohupAdapter().cancel(ssh, "123")
        assert ssh.run.call_count == 4
        assert "KILL" in ssh.run.call_args_list[2][0][0]
        # Both check commands use group-priority
        assert "kill -0 -- -" in ssh.run.call_args_list[1][0][0]
        assert "kill -0 -- -" in ssh.run.call_args_list[3][0][0]

    def test_cancel_raises_if_still_alive_after_kill(self):
        """TERM fails, KILL fails → RuntimeError."""
        ssh = MagicMock()
        ssh.run.side_effect = [
            MagicMock(stdout="", exit_code=0, stderr=""),
            MagicMock(stdout="alive", exit_code=0, stderr=""),
            MagicMock(stdout="", exit_code=0, stderr=""),
            MagicMock(stdout="alive", exit_code=0, stderr=""),
        ]
        import pytest
        with pytest.raises(RuntimeError, match="SIGKILL"):
            NohupAdapter().cancel(ssh, "123")

    def test_submit_uses_setsid(self):
        """submit command must include setsid for PGID consistency."""
        ssh = _ssh(stdout="12345")
        NohupAdapter().submit(ssh, "/tmp/run.sh", ResourceSpec())
        cmd = ssh.run.call_args[0][0]
        assert "setsid" in cmd


class TestSlurmAdapter:
    def test_submit_parses_job_id(self):
        ssh = _ssh(stdout="Submitted batch job 98765")
        adapter = SlurmAdapter()
        assert adapter.submit(ssh, "/tmp/job.sh", ResourceSpec()) == "98765"

    def test_poll_running(self):
        ssh = _ssh(stdout="RUNNING")
        assert SlurmAdapter().poll(ssh, "123") == JobState.running

    def test_poll_pending(self):
        ssh = _ssh(stdout="PENDING")
        assert SlurmAdapter().poll(ssh, "123") == JobState.pending

    def test_poll_completed_when_not_in_queue(self):
        ssh = _ssh(stdout="DONE")
        assert SlurmAdapter().poll(ssh, "123") == JobState.completed

    def test_poll_failed(self):
        ssh = _ssh(stdout="FAILED")
        assert SlurmAdapter().poll(ssh, "123") == JobState.failed

    def test_cancel_calls_scancel(self):
        ssh = _ssh()
        SlurmAdapter().cancel(ssh, "123")
        assert "scancel" in ssh.run.call_args[0][0]

    def test_build_header_basic(self):
        r = ResourceSpec(cpus=8, memory_mb=16384, walltime_minutes=120, partition="cpu")
        header = SlurmAdapter.build_header(r, "test_job")
        header_str = "\n".join(header)
        assert "#SBATCH --cpus-per-task=8" in header_str
        assert "#SBATCH --mem=16384M" in header_str
        assert "#SBATCH --time=02:00:00" in header_str
        assert "#SBATCH --partition=cpu" in header_str

    def test_build_header_with_gpu(self):
        r = ResourceSpec(gpus=2)
        header = "\n".join(SlurmAdapter.build_header(r))
        assert "#SBATCH --gres=gpu:2" in header

    def test_build_header_no_partition_when_empty(self):
        r = ResourceSpec(partition="")
        header = "\n".join(SlurmAdapter.build_header(r))
        assert "--partition" not in header

    def test_build_header_extra_directives(self):
        r = ResourceSpec(extra_directives=["--qos=high", "--mail-type=END"])
        header = "\n".join(SlurmAdapter.build_header(r))
        assert "#SBATCH --qos=high" in header
        assert "#SBATCH --mail-type=END" in header


class TestPBSAdapter:
    def test_submit_parses_job_id(self):
        ssh = _ssh(stdout="54321.cluster.example.com")
        assert PBSAdapter().submit(ssh, "/tmp/job.sh", ResourceSpec()) == "54321"

    def test_poll_running(self):
        ssh = _ssh(stdout="job_state = R")
        assert PBSAdapter().poll(ssh, "123") == JobState.running

    def test_poll_queued(self):
        ssh = _ssh(stdout="job_state = Q")
        assert PBSAdapter().poll(ssh, "123") == JobState.pending

    def test_poll_completed(self):
        ssh = _ssh(stdout="DONE")
        assert PBSAdapter().poll(ssh, "123") == JobState.completed

    def test_cancel_calls_qdel(self):
        ssh = _ssh()
        PBSAdapter().cancel(ssh, "123")
        assert "qdel" in ssh.run.call_args[0][0]

    def test_build_header_basic(self):
        r = ResourceSpec(cpus=4, memory_mb=8192, walltime_minutes=60, partition="batch")
        header = "\n".join(PBSAdapter.build_header(r))
        assert "#PBS -l nodes=1:ppn=4" in header
        assert "#PBS -l mem=8192mb" in header
        assert "#PBS -l walltime=01:00:00" in header
        assert "#PBS -q batch" in header


class TestMakeAdapter:
    def test_nohup_default(self):
        assert isinstance(make_adapter("nohup"), NohupAdapter)
        assert isinstance(make_adapter(""), NohupAdapter)

    def test_slurm(self):
        assert isinstance(make_adapter("slurm"), SlurmAdapter)
        assert isinstance(make_adapter("sbatch"), SlurmAdapter)

    def test_pbs(self):
        assert isinstance(make_adapter("pbs"), PBSAdapter)
        assert isinstance(make_adapter("torque"), PBSAdapter)

    def test_unknown_scheduler_type_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown scheduler type"):
            make_adapter("slrum")


class TestSchedulerConfig:
    def test_server_config_default_scheduler(self):
        cfg = ServerConfig(host="h", username="u")
        assert cfg.scheduler.type == "nohup"
        assert cfg.scheduler.default_cpus == 1

    def test_server_config_slurm_scheduler(self):
        cfg = ServerConfig(
            host="h", username="u",
            scheduler=SchedulerConfig(
                type="slurm",
                default_partition="cpu",
                default_cpus=8,
                default_memory_mb=16384,
                default_walltime_minutes=1440,
            ),
        )
        assert cfg.scheduler.type == "slurm"
        assert cfg.scheduler.default_partition == "cpu"
        assert cfg.scheduler.default_cpus == 8

    def test_scheduler_config_rejects_unknown_type(self):
        with pytest.raises(ValueError, match="scheduler.type"):
            SchedulerConfig(type="slrum")

    def test_scheduler_config_from_yaml(self):
        import tempfile
        from pathlib import Path

        from jobdesk_app.config.servers import load_servers
        content = """
servers:
  hpc1:
    host: hpc.example.com
    username: user
    scheduler:
      type: slurm
      default_partition: cpu
      default_account: mylab
      default_walltime_minutes: 2880
      default_cpus: 16
      default_memory_mb: 32768
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(content)
            path = f.name
        try:
            cfg = load_servers(path)
            s = cfg.servers["hpc1"]
            assert s.scheduler.type == "slurm"
            assert s.scheduler.default_partition == "cpu"
            assert s.scheduler.default_cpus == 16
        finally:
            Path(path).unlink()



class TestCancellationTruthfulness:
    """Cancellation must fail when the remote command reports failure."""

    def test_slurm_cancel_raises_on_nonzero_exit(self):
        ssh = _ssh(stdout="", exit_code=1)
        ssh.run.return_value.stderr = "scancel: error: Invalid job id"
        with pytest.raises(RuntimeError, match="scancel failed"):
            SlurmAdapter().cancel(ssh, "99999")

    def test_pbs_cancel_raises_on_nonzero_exit(self):
        ssh = _ssh(stdout="", exit_code=1)
        ssh.run.return_value.stderr = "qdel: Unknown Job Id"
        with pytest.raises(RuntimeError, match="qdel failed"):
            PBSAdapter().cancel(ssh, "99999")

    def test_nohup_cancel_raises_when_process_still_alive(self):
        ssh = MagicMock()
        # TERM → still alive → KILL → still alive → RuntimeError
        ssh.run.side_effect = [
            MagicMock(stdout="", exit_code=0, stderr=""),      # kill -TERM
            MagicMock(stdout="alive", exit_code=0, stderr=""),  # check after TERM
            MagicMock(stdout="", exit_code=0, stderr=""),      # kill -KILL
            MagicMock(stdout="alive", exit_code=0, stderr=""),  # check after KILL
        ]
        with pytest.raises(RuntimeError, match="SIGKILL"):
            NohupAdapter().cancel(ssh, "12345")

    def test_slurm_cancel_succeeds_on_zero_exit(self):
        ssh = _ssh(stdout="", exit_code=0)
        SlurmAdapter().cancel(ssh, "123")  # should not raise

    def test_nohup_cancel_succeeds_when_process_dead(self):
        ssh = MagicMock()
        ssh.run.side_effect = [
            MagicMock(stdout="", exit_code=0, stderr=""),
            MagicMock(stdout="dead", exit_code=0, stderr=""),
        ]
        NohupAdapter().cancel(ssh, "123")  # should not raise



class TestSchedulerResourceValidation:
    def test_scheduler_config_rejects_non_positive_resources(self):
        with pytest.raises(ValueError):
            SchedulerConfig(default_cpus=0)
        with pytest.raises(ValueError):
            SchedulerConfig(default_walltime_minutes=0)
        with pytest.raises(ValueError):
            SchedulerConfig(default_memory_mb=0)

    def test_resource_spec_rejects_newline_in_text_fields(self):
        with pytest.raises(ValueError, match="control characters"):
            ResourceSpec(extra_directives=["--qos=high\nrm -rf /"])
        with pytest.raises(ValueError, match="control characters"):
            ResourceSpec(partition="cpu\nmalicious")
        with pytest.raises(ValueError, match="control characters"):
            ResourceSpec(account="acct\x00x")
