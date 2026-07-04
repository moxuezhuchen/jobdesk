"""Task submission and parallel-control module.

Generates JobDesk internal run scripts (.jobdesk_run.sh, launch scripts,
tasks.tsv, batch_control.sh) from a Manifest, uploads them via SSH,
sets permissions, and starts the batch in the background with nohup.

Scheme B: whole-batch submission + batch_control.sh uses xargs -P N
to execute launch scripts in parallel. All task information comes from
the Manifest; input files are not re-discovered.
"""

import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..core.lifecycle import TaskStatus
from ..core.manifest import TaskRecord
from ..core.submit import SubmitMode, SubmitPlan, SubmitResult

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class SubmitCheckpointError(RuntimeError):
    """A durable submission checkpoint could not be persisted."""


def _validate_task_id(task_id: str) -> str:
    if not _TASK_ID_RE.fullmatch(task_id):
        raise ValueError(f"task_id contains unsafe characters: {task_id!r}")
    return task_id


class JobSubmitter:
    """Task submission orchestrator.

    Usage:
        submitter = JobSubmitter(
            tasks=[...],
            ssh=ssh,
            sftp=sftp,
            max_parallel=4,
            remote_batch_dir="/remote/batch_20260511",
            batch_id="20260511_143022_123456",
        )
        plan = submitter.dry_run(SubmitMode.all)
        result = submitter.submit_batch(SubmitMode.all)
    """

    def __init__(
        self,
        ssh=None,     # SSHClientWrapper
        sftp=None,    # SFTPClientWrapper
        max_parallel: int = 1,
        remote_batch_dir: str = "",
        batch_id: str = "",
        control_subdir: str = "_batch",
        env_init_scripts: list[str] | None = None,
        scheduler=None,   # SchedulerAdapter | None
        resources=None,   # ResourceSpec | None
        *,
        tasks: list[TaskRecord] | None = None,
        task_update_callback: Callable[[list[TaskRecord]], None] | None = None,
        remote_started_callback: Callable[[list[str]], None] | None = None,
    ):
        if tasks is None:
            raise ValueError("tasks is required")
        if max_parallel < 1:
            raise ValueError(f"max_parallel must be >= 1, got: {max_parallel}")
        self._tasks = [task.model_copy(deep=True) for task in tasks]
        self._ssh = ssh
        self._sftp = sftp
        self._max_parallel = max_parallel
        self._remote_batch_dir = remote_batch_dir.rstrip("/")
        self._batch_id = batch_id
        self._control_subdir = control_subdir
        self._env_init_scripts: list[str] = list(env_init_scripts or [])
        from .scheduler import NohupAdapter, ResourceSpec
        self._scheduler = scheduler if scheduler is not None else NohupAdapter()
        self._resources = resources if resources is not None else ResourceSpec()
        self._task_update_callback = task_update_callback
        self._remote_started_callback = remote_started_callback

    # ---- task selection -------------------------------------------------------

    def select_tasks(
        self,
        mode: SubmitMode = SubmitMode.all,
        selected_ids: list[str] | None = None,
    ) -> list[TaskRecord]:
        """Select submittable tasks from the Manifest (status must be uploaded)."""
        all_tasks = self._all_tasks()
        uploaded = [t for t in all_tasks if t.status == TaskStatus.uploaded]

        if mode == SubmitMode.all:
            return uploaded
        elif mode == SubmitMode.selected:
            if not selected_ids:
                return []
            sid_set = set(selected_ids)
            return [t for t in uploaded if t.task_id in sid_set]
        elif mode == SubmitMode.unfinished:
            return uploaded

        return []

    # ---- script generation ----------------------------------------------------

    @staticmethod
    def generate_task_runner(task: TaskRecord, env_init_scripts: list[str] | None = None) -> str:
        """Generate .jobdesk_run.sh content for a single task.

        Sources the user's shell environment before running the command
        (to bypass the issue where non-interactive shells do not load
        ~/.bashrc), then sources additional env_init_scripts.
        """
        task_id = _validate_task_id(task.task_id)
        rendered = task.rendered_command
        init_lines = [
            "# JobDesk: load user shell environment",
            "export PS1=\"${PS1:-jobdesk> }\"",
            "set +u",
            "[ -f /etc/profile ] && . /etc/profile 2>/dev/null || true",
            "[ -f \"$HOME/.bash_profile\" ] && . \"$HOME/.bash_profile\" 2>/dev/null || true",
            "[ -f \"$HOME/.profile\" ] && . \"$HOME/.profile\" 2>/dev/null || true",
            "[ -f \"$HOME/.bashrc\" ] && . \"$HOME/.bashrc\" 2>/dev/null || true",
        ]
        for script in (env_init_scripts or []):
            if script:
                init_lines.append(f"[ -f {shlex.quote(script)} ] && . {shlex.quote(script)} 2>/dev/null || true")
        lines = [
            "#!/usr/bin/env bash",
            *init_lines,
            "set +e",
            "echo 'running' > .jobdesk_status",
            f'printf "%s\\n" "RUNNING {task_id}" >> ../_batch/events.log',
            "(",
            f"  {rendered}",
            ") > .jobdesk_submit.log 2>&1",
            "rc=$?",
            "echo \"$rc\" > .jobdesk_exit_code",
            'if [ "$rc" -eq 0 ]; then',
            "  echo 'completed' > .jobdesk_status",
            "else",
            "  echo 'failed' > .jobdesk_status",
            "fi",
            f'printf "%s\\n" "DONE {task_id} $rc" >> ../_batch/events.log',
            'exit "$rc"',
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def generate_launch_script(task_id: str, remote_job_dir: str) -> str:
        """Generate launch_<task_id>.sh for a single task.

        Responsibilities: cd to remote_job_dir and execute .jobdesk_run.sh.
        Paths are safely escaped with shlex.quote.
        """
        dir_q = shlex.quote(remote_job_dir)
        lines = [
            "#!/usr/bin/env bash",
            f"cd {dir_q} || exit 1",
            "bash ./.jobdesk_run.sh",
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def generate_tasks_tsv(tasks: list[TaskRecord], remote_batch_dir: str, control_subdir: str = "_batch") -> str:
        """Generate the _batch/tasks.tsv content."""
        lines = ["task_id\tremote_job_dir\trunner_path"]
        control_dir = f"{remote_batch_dir.rstrip('/')}/{control_subdir}"
        for t in tasks:
            task_id = _validate_task_id(t.task_id)
            if "\t" in t.task_id or "\n" in t.task_id:
                raise ValueError(f"task_id contains invalid characters (tab/newline): {t.task_id!r}")
            if "\t" in t.remote_job_dir or "\n" in t.remote_job_dir:
                raise ValueError(f"remote_job_dir contains invalid characters: {t.remote_job_dir!r}")
            launch_path = f"{control_dir}/launch_{task_id}.sh"
            lines.append(f"{task_id}\t{t.remote_job_dir}\t{launch_path}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def generate_batch_control(
        max_parallel: int,
        remote_batch_dir: str,
        task_count: int,
        control_subdir: str = "_batch",
    ) -> str:
        """Generate _batch/batch_control.sh content."""
        control_dir = f"{remote_batch_dir.rstrip('/')}/{control_subdir}"
        control_dir_q = shlex.quote(control_dir)
        lines = [
            "#!/usr/bin/env bash",
            "set -e",
            "",
            f'MAX_PARALLEL={max_parallel}',
            f'CONTROL_DIR={control_dir_q}',
            "",
            "cd \"$CONTROL_DIR\" || exit 1",
            "",
            "# BATCH_RUNNING marker",
            "echo 'BATCH_RUNNING'",
            f"echo 'batch_id: {shlex.quote(control_dir.rsplit('/', 2)[0].rsplit('/', 1)[-1])}'",
            f"echo 'task_count: {task_count}'",
            f"echo 'max_parallel: {max_parallel}'",
            "echo 'started_at: '\"$(date -Iseconds)\"",
            "",
            "# Extract launch script paths from tasks.tsv (3rd column), skip header",
            "tail -n +2 tasks.tsv | cut -f3 > launch_list.txt",
            "",
            "# Execute: use bash -c 'bash \"$1\"' _ \"{}\" for safe argument passing",
            "batch_rc=0",
            "if command -v xargs > /dev/null 2>&1; then",
            "# NOTE: `|| batch_rc=$?` intentionally captures xargs exit code under set -e",
            "  xargs -r -P \"$MAX_PARALLEL\" -I{} bash -c 'bash \"$1\"' _ \"{}\" < launch_list.txt || batch_rc=$?",
            "else",
            "  echo 'ERROR: xargs not found' >&2",
            "  exit 1",
            "fi",
            "",
            "# Record batch control exit code",
            "echo \"$batch_rc\" > batch_control_exit_code",
            "",
            "# BATCH_FINISHED: only means batch_control.sh finished running",
            "# Each task's success/failure is determined by .jobdesk_status and .jobdesk_exit_code",
            "echo 'BATCH_FINISHED'",
            "echo 'finished_at: '\"$(date -Iseconds)\"",
            "exit \"$batch_rc\"",
            "",
        ]
        return "\n".join(lines)

    def generate_scheduler_script(
        self,
        task: TaskRecord,
        runner_path: str,
    ) -> str:
        """Generate a scheduler-specific job script wrapping the runner.

        For nohup: returns empty string (batch_control.sh handles submission).
        For Slurm/PBS: returns a script with resource directives.
        """
        from .scheduler import NohupAdapter, PBSAdapter, SlurmAdapter
        if isinstance(self._scheduler, NohupAdapter):
            return ""
        task_id = _validate_task_id(task.task_id)
        job_name = f"jd_{task_id[:16]}"
        if isinstance(self._scheduler, SlurmAdapter):
            header = SlurmAdapter.build_header(self._resources, job_name)
        elif isinstance(self._scheduler, PBSAdapter):
            header = PBSAdapter.build_header(self._resources, job_name)
        else:
            return ""
        lines = header + [
            "",
            f"cd {shlex.quote(task.remote_job_dir)} || exit 1",
            f"bash {shlex.quote(runner_path)}",
        ]
        return "\n".join(lines) + "\n"

    # ---- submission flow ------------------------------------------------------

    def prepare_plan(
        self,
        mode: SubmitMode = SubmitMode.all,
        selected_ids: list[str] | None = None,
    ) -> SubmitPlan:
        """Prepare a submission plan (dry-run, no side effects)."""
        tasks = self.select_tasks(mode, selected_ids)
        control_dir = f"{self._remote_batch_dir}/{self._control_subdir}"

        generated = []
        for t in tasks:
            generated.append(f"{t.remote_job_dir}/.jobdesk_run.sh")
            generated.append(f"{control_dir}/launch_{t.task_id}.sh")
        generated.append(f"{control_dir}/tasks.tsv")
        generated.append(f"{control_dir}/batch_control.sh")

        cd_cmd = shlex.quote(control_dir)
        control_command = (
            f"cd {cd_cmd} && nohup setsid bash './batch_control.sh'"
            " > './batch_control.nohup.log' 2>&1 & echo $!"
        )

        return SubmitPlan(
            batch_id=self._batch_id,
            max_parallel=self._max_parallel,
            task_count=len(tasks),
            selected_task_ids=[t.task_id for t in tasks],
            remote_batch_dir=self._remote_batch_dir,
            generated_files=sorted(generated),
            control_command=control_command,
            dry_run=True,
        )

    def dry_run(
        self,
        mode: SubmitMode = SubmitMode.all,
        selected_ids: list[str] | None = None,
    ) -> SubmitPlan:
        """Dry-run: return submission plan without any side effects."""
        return self.prepare_plan(mode, selected_ids)

    def submit_batch(
        self,
        mode: SubmitMode = SubmitMode.all,
        selected_ids: list[str] | None = None,
    ) -> SubmitResult:
        """Submit executable tasks and persist confirmed remote results durably.

        When later tasks fail, results of previously confirmed tasks are
        retained; tasks whose remote startup status is ambiguous are
        persisted as ``uncertain`` so the caller can safely verify.
        """
        tasks = self.select_tasks(mode, selected_ids)
        control_dir = f"{self._remote_batch_dir}/{self._control_subdir}"

        result = SubmitResult(
            batch_id=self._batch_id,
            submitted_task_count=0,
            remote_batch_dir=self._remote_batch_dir,
            control_script_path=f"{control_dir}/batch_control.sh",
            control_log_path=f"{control_dir}/batch_control.nohup.log",
            control_nohup_log_path=f"{control_dir}/batch_control.nohup.log",
        )

        if not tasks:
            result.errors.append("no tasks available to submit (all tasks must be in uploaded status)")
            return result

        from .scheduler import NohupAdapter
        if isinstance(self._scheduler, NohupAdapter):
            return self._submit_nohup(tasks, result, control_dir)
        else:
            return self._submit_scheduler(tasks, result)

    def _submit_nohup(self, tasks, result, control_dir):
        """Original nohup batch_control.sh submission path."""
        try:
            runner_contents: dict[str, str] = {}
            launch_contents: dict[str, str] = {}
            for t in tasks:
                runner_contents[t.task_id] = self.generate_task_runner(t, env_init_scripts=self._env_init_scripts)
                launch_contents[t.task_id] = self.generate_launch_script(t.task_id, t.remote_job_dir)
            tasks_tsv_content = self.generate_tasks_tsv(tasks, self._remote_batch_dir, self._control_subdir)
            batch_control_content = self.generate_batch_control(
                self._max_parallel, self._remote_batch_dir, len(tasks), self._control_subdir
            )
            self._sftp.mkdir_p(control_dir)
            for t in tasks:
                self._sftp.mkdir_p(t.remote_job_dir)
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                for t in tasks:
                    p = tmp / f"{t.task_id}_run.sh"
                    p.write_text(runner_contents[t.task_id], encoding="utf-8", newline="\n")
                    self._sftp.upload_file(p, f"{t.remote_job_dir}/.jobdesk_run.sh", overwrite=True)
                for t in tasks:
                    p = tmp / f"launch_{t.task_id}.sh"
                    p.write_text(launch_contents[t.task_id], encoding="utf-8", newline="\n")
                    self._sftp.upload_file(p, f"{control_dir}/launch_{t.task_id}.sh", overwrite=True)
                p = tmp / "tasks.tsv"
                p.write_text(tasks_tsv_content, encoding="utf-8", newline="\n")
                self._sftp.upload_file(p, f"{control_dir}/tasks.tsv", overwrite=True)
                p = tmp / "batch_control.sh"
                p.write_text(batch_control_content, encoding="utf-8", newline="\n")
                self._sftp.upload_file(p, f"{control_dir}/batch_control.sh", overwrite=True)
            script_paths = []
            for t in tasks:
                script_paths.append(shlex.quote(f"{t.remote_job_dir}/.jobdesk_run.sh"))
                script_paths.append(shlex.quote(f"{control_dir}/launch_{t.task_id}.sh"))
            script_paths.append(shlex.quote(f"{control_dir}/batch_control.sh"))
            chmod_result = self._ssh.run("chmod +x " + " ".join(script_paths), timeout=30)
            if chmod_result.exit_code != 0:
                result.errors.append(f"chmod failed: {chmod_result.stderr}")
                return result
            cd_q = shlex.quote(control_dir)
            nohup_cmd = (
                f"cd {cd_q} && nohup setsid bash './batch_control.sh'"
                " > './batch_control.nohup.log' 2>&1 & echo $!"
            )
            result.nohup_command = nohup_cmd
            self._notify_remote_started([task.task_id for task in tasks])
            try:
                ssh_result = self._ssh.run(nohup_cmd, timeout=30)
            except Exception as exc:
                message = f"nohup start failed: {exc}"
                result.errors.append(message)
                self._mark_uncertain(tasks, result, message, "nohup")
                return result
            if ssh_result.exit_code != 0:
                self._mark_uncertain(
                    tasks, result, f"nohup start failed: {ssh_result.stderr}", "nohup"
                )
                result.errors.append(f"nohup start failed: {ssh_result.stderr}")
                return result
            job_id = ssh_result.stdout.strip().splitlines()[-1] if ssh_result.stdout.strip() else ""
            if not job_id:
                result.errors.append("nohup start did not return a remote process id")
                all_tasks = self._all_tasks()
                selected_ids = {task.task_id for task in tasks}
                ambiguous: list[TaskRecord] = []
                claimed_at = datetime.now()
                for task in all_tasks:
                    if task.task_id in selected_ids:
                        task.status = TaskStatus.uncertain
                        task.submitted_at = claimed_at
                        task.scheduler_type = "nohup"
                        task.remote_job_id = None
                        task.error_message = "nohup start did not return a remote process id"
                        ambiguous.append(task.model_copy(deep=True))
                self._notify_task_updates(ambiguous)
                self._persist_tasks(all_tasks, result)
                return result
            return self._mark_submitted(tasks, result, scheduler_type="nohup", remote_job_id=job_id)
        except SubmitCheckpointError:
            raise
        except Exception as e:
            result.errors.append(f"submission error: {e}")
            return result

    def _submit_scheduler(self, tasks, result):
        """Slurm/PBS per-task submission path."""
        updated_ids: list[str] = []
        all_tasks: list[TaskRecord] | None = None
        try:
            import tempfile
            now = datetime.now()
            all_tasks = self._all_tasks()
            for t in tasks:
                self._sftp.mkdir_p(t.remote_job_dir)
                runner = self.generate_task_runner(t, env_init_scripts=self._env_init_scripts)
                runner_remote = f"{t.remote_job_dir}/.jobdesk_run.sh"
                sched_script = self.generate_scheduler_script(t, runner_remote)
                sched_remote = f"{t.remote_job_dir}/.jobdesk_submit.sh"
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp = Path(tmpdir)
                    rp = tmp / "run.sh"
                    rp.write_text(runner, encoding="utf-8", newline="\n")
                    self._sftp.upload_file(rp, runner_remote, overwrite=True)
                    sp = tmp / "submit.sh"
                    sp.write_text(sched_script, encoding="utf-8", newline="\n")
                    self._sftp.upload_file(sp, sched_remote, overwrite=True)
                self._ssh.run(f"chmod +x {shlex.quote(runner_remote)} {shlex.quote(sched_remote)}", timeout=15)
                try:
                    self._notify_remote_started([t.task_id])
                    job_id = self._scheduler.submit(self._ssh, sched_remote, self._resources)
                    if not job_id:
                        raise RuntimeError("scheduler did not return a job id")
                except SubmitCheckpointError:
                    raise
                except Exception as e:
                    result.errors.append(f"task {t.task_id}: submit failed: {e}")
                    for mt in all_tasks:
                        if mt.task_id == t.task_id:
                            mt.status = TaskStatus.uncertain
                            mt.submitted_at = now
                            mt.scheduler_type = _scheduler_type(self._scheduler)
                            mt.remote_job_id = None
                            mt.error_message = f"submit failed: {e}"
                            self._notify_task_updates([mt])
                    continue
                for mt in all_tasks:
                    if mt.task_id == t.task_id:
                        mt.status = TaskStatus.submitted
                        mt.submitted_at = now
                        mt.scheduler_type = _scheduler_type(self._scheduler)
                        mt.remote_job_id = job_id
                        mt.error_message = None
                        self._notify_task_updates([mt])
                updated_ids.append(t.task_id)
        except SubmitCheckpointError:
            raise
        except Exception as e:
            result.errors.append(f"submission error: {e}")
        if all_tasks is not None:
            result.updated_task_ids = updated_ids
            result.submitted_task_count = len(updated_ids)
            self._persist_tasks(all_tasks, result)
        return result

    def _mark_submitted(self, tasks, result, scheduler_type: str = "nohup", remote_job_id: str | None = None):
        now = datetime.now()
        updated_ids: list[str] = []
        all_tasks = self._all_tasks()
        tid_map = {t.task_id: t for t in all_tasks}
        for t in tasks:
            if t.task_id in tid_map:
                tid_map[t.task_id].status = TaskStatus.submitted
                tid_map[t.task_id].submitted_at = now
                tid_map[t.task_id].scheduler_type = scheduler_type
                tid_map[t.task_id].remote_job_id = remote_job_id
                tid_map[t.task_id].error_message = None
                updated_ids.append(t.task_id)
        result.updated_task_ids = updated_ids
        result.submitted_task_count = len(updated_ids)
        self._notify_task_updates([tid_map[task_id] for task_id in updated_ids])
        self._persist_tasks(all_tasks, result)
        return result

    def _notify_task_updates(self, tasks: list[TaskRecord]) -> None:
        if self._task_update_callback is not None and tasks:
            try:
                self._task_update_callback(
                    [task.model_copy(deep=True) for task in tasks]
                )
            except Exception as exc:
                raise SubmitCheckpointError(str(exc)) from exc

    def _notify_remote_started(self, task_ids: list[str]) -> None:
        if self._remote_started_callback is not None:
            try:
                self._remote_started_callback(list(task_ids))
            except Exception as exc:
                raise SubmitCheckpointError(str(exc)) from exc

    def _mark_uncertain(
        self,
        tasks: list[TaskRecord],
        result: SubmitResult,
        error: str,
        scheduler_type: str,
    ) -> None:
        all_tasks = self._all_tasks()
        selected = {task.task_id for task in tasks}
        changed: list[TaskRecord] = []
        submitted_at = datetime.now()
        for task in all_tasks:
            if task.task_id in selected:
                task.status = TaskStatus.uncertain
                task.submitted_at = submitted_at
                task.scheduler_type = scheduler_type
                task.remote_job_id = None
                task.error_message = error
                changed.append(task.model_copy(deep=True))
        self._notify_task_updates(changed)
        self._persist_tasks(all_tasks, result)

    def _all_tasks(self) -> list[TaskRecord]:
        return [task.model_copy(deep=True) for task in self._tasks]

    def _persist_tasks(self, tasks: list[TaskRecord], result: SubmitResult) -> None:
        self._tasks = [task.model_copy(deep=True) for task in tasks]
        result.updated_tasks = [task.model_copy(deep=True) for task in tasks]


def _scheduler_type(scheduler) -> str:
    from .scheduler import PBSAdapter, SlurmAdapter

    if isinstance(scheduler, SlurmAdapter):
        return "slurm"
    if isinstance(scheduler, PBSAdapter):
        return "pbs"
    return "nohup"
