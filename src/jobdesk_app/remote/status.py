"""远程任务状态标记文件读取模块。

只读：读取远程 .jobdesk_status、.jobdesk_exit_code、.jobdesk_submit.log。
不生成这些文件，不修改远程状态。
"""

import base64
import binascii
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass, field

from .ssh import SSHClientWrapper


@dataclass
class RemoteTaskStatusSnapshot:
    """远程任务目录中的 JobDesk 状态标记快照。

    所有字段代表远程文件的读取结果。文件不存在不是错误，exists 字段为 False。
    """

    task_id: str
    remote_job_dir: str
    status_marker: str = ""
    exit_code: int | None = None
    submit_log_tail: str = ""
    marker_exists: bool = False
    exit_code_exists: bool = False
    log_exists: bool = False
    warnings: list[str] = field(default_factory=list)


def read_remote_task_status(
    ssh: SSHClientWrapper,
    task_id: str,
    remote_job_dir: str,
    log_tail_lines: int = 50,
) -> RemoteTaskStatusSnapshot:
    """读取远程单个任务目录中的 JobDesk 状态标记文件。

    所有文件路径使用 shlex.quote 安全转义。
    文件不存在不会抛异常。

    Args:
        ssh: 已连接的 SSHClientWrapper。
        task_id: 任务 ID。
        remote_job_dir: 远程任务工作目录。
        log_tail_lines: submit log 读取的最大行数。

    Returns:
        RemoteTaskStatusSnapshot 实例。
    """
    snapshot = RemoteTaskStatusSnapshot(task_id=task_id, remote_job_dir=remote_job_dir)

    dir_q = shlex.quote(remote_job_dir)

    # .jobdesk_status
    status_path = f"{dir_q}/.jobdesk_status"
    try:
        r = ssh.run(
            f"if test -f {status_path}; then printf '__JD_FOUND__\\n'; cat {status_path}; else printf '__JD_MISSING__\\n'; fi",
            timeout=10,
        )
        found, content = _parse_envelope(r.stdout)
        if found is True:
            if r.exit_code != 0:
                snapshot.marker_exists = False
                snapshot.warnings.append(f"读取 .jobdesk_status 失败 (exit_code={r.exit_code})")
            else:
                snapshot.marker_exists = True
                snapshot.status_marker = content.strip()
        elif found is False:
            snapshot.marker_exists = False
        else:
            snapshot.marker_exists = False
            snapshot.warnings.append(f"读取 .jobdesk_status 无效响应: {r.stdout[:100]!r}")
    except Exception as e:
        snapshot.warnings.append(f"读取 .jobdesk_status 失败: {e}")

    # .jobdesk_exit_code
    exit_code_path = f"{dir_q}/.jobdesk_exit_code"
    try:
        r = ssh.run(
            f"if test -f {exit_code_path}; then printf '__JD_FOUND__\\n'; cat {exit_code_path}; else printf '__JD_MISSING__\\n'; fi",
            timeout=10,
        )
        found, content = _parse_envelope(r.stdout)
        if found is True:
            if r.exit_code != 0:
                snapshot.exit_code_exists = False
                snapshot.warnings.append(f"读取 .jobdesk_exit_code 失败 (exit_code={r.exit_code})")
            else:
                snapshot.exit_code_exists = True
                try:
                    snapshot.exit_code = int(content.strip())
                except ValueError:
                    snapshot.warnings.append(
                        f"exit_code 文件内容不是有效整数: {content.strip()!r}"
                    )
        elif found is False:
            snapshot.exit_code_exists = False
        else:
            snapshot.exit_code_exists = False
            snapshot.warnings.append(f"读取 .jobdesk_exit_code 无效响应: {r.stdout[:100]!r}")
    except Exception as e:
        snapshot.warnings.append(f"读取 .jobdesk_exit_code 失败: {e}")

    # .jobdesk_submit.log (tail)
    log_path = f"{dir_q}/.jobdesk_submit.log"
    try:
        r = ssh.run(
            f"if test -f {log_path}; then printf '__JD_FOUND__\\n'; tail -n {log_tail_lines} {log_path} 2>/dev/null; else printf '__JD_MISSING__\\n'; fi",
            timeout=15,
        )
        found, content = _parse_envelope(r.stdout)
        if found is True:
            if r.exit_code != 0:
                snapshot.log_exists = False
                snapshot.warnings.append(f"读取 .jobdesk_submit.log 失败 (exit_code={r.exit_code})")
            else:
                snapshot.log_exists = True
                snapshot.submit_log_tail = content
        elif found is False:
            snapshot.log_exists = False
        else:
            snapshot.log_exists = False
            snapshot.warnings.append(f"读取 .jobdesk_submit.log 无效响应: {r.stdout[:100]!r}")
    except Exception as e:
        snapshot.warnings.append(f"读取 .jobdesk_submit.log 失败: {e}")

    return snapshot


def _parse_envelope(stdout: str) -> tuple[bool | None, str]:
    """Parse the envelope protocol: first line is __JD_FOUND__ or __JD_MISSING__.

    Returns:
        (True, content) if found, (False, "") if missing, (None, "") if invalid envelope.
    """
    first_nl = stdout.find("\n")
    if first_nl == -1:
        first_line = stdout.strip()
        rest = ""
    else:
        first_line = stdout[:first_nl].strip()
        rest = stdout[first_nl + 1:]
    if first_line == "__JD_FOUND__":
        return True, rest
    if first_line == "__JD_MISSING__":
        return False, ""
    return None, ""


# ---------------------------------------------------------------------------
# 批量读取
# ---------------------------------------------------------------------------

# 批量脚本的输出协议：
#   ##JD-BEGIN <key> F\n<base64-on-one-line>\n##JD-END <key>\n   - 文件存在
#   ##JD-BEGIN <key> M\n##JD-END <key>\n                         - 文件缺失
# base64 用 `tr -d '\n'` 压成单行，避免与协议标记冲突；空文件 base64 为空字符串。
# 整个脚本结束时输出 ##JD-DONE 作为完整性标记。

_BATCH_BEGIN_RE = re.compile(r"^##JD-BEGIN (\S+) ([FME])$")
_BATCH_END_RE = re.compile(r"^##JD-END (\S+)$")
_BATCH_DONE_MARK = "##JD-DONE"

_BATCH_PROLOGUE = (
    "set +e\n"
    "_jd_tmp=$(mktemp)\n"
    "encode_block() {\n"
    "  key=$1; path=$2; n=$3\n"
    '  if [ -f "$path" ]; then\n'
    '    if [ "$n" -gt 0 ]; then\n'
    '      tail -n "$n" -- "$path" > "$_jd_tmp" 2>/dev/null\n'
    "    else\n"
    '      cat -- "$path" > "$_jd_tmp" 2>/dev/null\n'
    "    fi\n"
    '    if [ $? -eq 0 ]; then\n'
    "      printf '##JD-BEGIN %s F\\n' \"$key\"\n"
    '      base64 "$_jd_tmp" | tr -d \'\\n\'\n'
    "      printf '\\n##JD-END %s\\n' \"$key\"\n"
    "    else\n"
    "      printf '##JD-BEGIN %s E\\n##JD-END %s\\n' \"$key\" \"$key\"\n"
    "    fi\n"
    "  else\n"
    "    printf '##JD-BEGIN %s M\\n##JD-END %s\\n' \"$key\" \"$key\"\n"
    "  fi\n"
    "}\n"
)


def read_remote_task_statuses_batch(
    ssh: SSHClientWrapper,
    tasks: Iterable[tuple[str, str]],
    log_tail_lines: int = 50,
    timeout: int | None = None,
    extra_files: list[tuple[str, str, int]] | None = None,
    extra_out: dict[str, bytes | None] | None = None,
) -> dict[str, RemoteTaskStatusSnapshot]:
    """批量读取多个任务的远程状态文件（一条 SSH 命令完成）。

    相比循环调用 :func:`read_remote_task_status`，对 N 个任务把 3N 次远程命令
    降为 1 次。文件内容用 base64 编码后传输，避免与协议标记冲突；空文件、
    缺失文件能正确区分。

    Args:
        ssh: 已连接的 SSHClientWrapper。
        tasks: ``(task_id, remote_job_dir)`` 序列。``remote_job_dir`` 为空
            的条目会被跳过（视为无远程目录），仍会在返回字典中得到一个
            空快照。
        log_tail_lines: ``.jobdesk_submit.log`` tail 行数。
        timeout: SSH 超时（秒）；默认按任务数自适应。

    Returns:
        ``{task_id: RemoteTaskStatusSnapshot}`` 字典；输入的每个 ``task_id``
        都对应一项条目。

    Notes:
        - 所有路径使用 ``shlex.quote`` 转义，安全应对含空格/特殊字符的目录。
        - 整体 SSH 命令失败时，返回的所有快照都会带有相应 warning。
        - 单个 ``cat``/``tail`` 失败会被识别为 read error（``E`` 标记），
          对应 snapshot 的 exists 字段为 False，并在 warnings 中记录。
    """
    items = list(tasks)
    snapshots: dict[str, RemoteTaskStatusSnapshot] = {}
    pending: list[tuple[int, str, str]] = []

    for idx, (task_id, remote_job_dir) in enumerate(items):
        snap = RemoteTaskStatusSnapshot(
            task_id=task_id,
            remote_job_dir=remote_job_dir or "",
        )
        snapshots[task_id] = snap
        if remote_job_dir:
            pending.append((idx, task_id, remote_job_dir))

    extra = list(extra_files or [])
    if not pending and not extra:
        return snapshots

    script = _build_batch_script(pending, log_tail_lines, extra)
    effective_timeout = (
        timeout if timeout is not None else max(30, 5 + (len(pending) + len(extra)) // 4)
    )

    try:
        result = ssh.run(script, timeout=effective_timeout)
    except Exception as exc:
        for _idx, task_id, _ in pending:
            snapshots[task_id].warnings.append(f"批量读取远程状态失败: {exc}")
        if extra_out is not None:
            for key, _p, _t in extra:
                extra_out[key] = None
        return snapshots

    blocks = _parse_batch_output(result.stdout)

    if _BATCH_DONE_MARK not in result.stdout:
        for _idx, task_id, _ in pending:
            snapshots[task_id].warnings.append("批量读取远程状态：未收到结束标记")

    for idx, task_id, remote_job_dir in pending:
        snap = snapshots[task_id]
        _apply_batch_block(
            snap,
            blocks.get(f"T{idx}:S"),
            field="status",
            label=f"{remote_job_dir}/.jobdesk_status",
        )
        _apply_batch_block(
            snap,
            blocks.get(f"T{idx}:E"),
            field="exit_code",
            label=f"{remote_job_dir}/.jobdesk_exit_code",
        )
        _apply_batch_block(
            snap,
            blocks.get(f"T{idx}:L"),
            field="log",
            label=f"{remote_job_dir}/.jobdesk_submit.log",
        )

    if extra_out is not None:
        for key, _p, _t in extra:
            block = blocks.get(key)
            extra_out[key] = block[1] if block else None

    return snapshots


def _build_batch_script(
    pending: list[tuple[int, str, str]],
    log_tail_lines: int,
    extra_files: list[tuple[str, str, int]] | None = None,
) -> str:
    """根据待查询的任务列表（及可选的额外文件）构造批量脚本。"""
    lines = [_BATCH_PROLOGUE.rstrip("\n")]
    for idx, _task_id, remote_job_dir in pending:
        d = shlex.quote(remote_job_dir)
        lines.append(f"encode_block 'T{idx}:S' {d}/.jobdesk_status 0")
        lines.append(f"encode_block 'T{idx}:E' {d}/.jobdesk_exit_code 0")
        lines.append(
            f"encode_block 'T{idx}:L' {d}/.jobdesk_submit.log {int(log_tail_lines)}"
        )
    for key, path, tail in (extra_files or []):
        lines.append(f"encode_block {shlex.quote(key)} {shlex.quote(path)} {int(tail)}")
    lines.append("rm -f \"$_jd_tmp\"")
    lines.append(f"printf '{_BATCH_DONE_MARK}\\n'")
    return "\n".join(lines)


def _parse_batch_output(stdout: str) -> dict[str, tuple[str, bytes | None]]:
    """解析批量脚本输出。

    Returns:
        ``{key: (kind, decoded_bytes)}``：
        - ``kind="F"``，``decoded_bytes`` 为文件内容（空文件为 ``b""``）；
          无法 base64 解码时 ``decoded_bytes`` 为 ``None``。
        - ``kind="M"``，``decoded_bytes`` 为 ``None``。
        - ``kind="E"``，``decoded_bytes`` 为 ``None``（读取错误）。
    """
    blocks: dict[str, tuple[str, bytes | None]] = {}
    current_key: str | None = None
    current_kind: str | None = None
    current_lines: list[str] = []

    for line in stdout.splitlines():
        m_begin = _BATCH_BEGIN_RE.match(line)
        if m_begin:
            current_key = m_begin.group(1)
            current_kind = m_begin.group(2)
            current_lines = []
            continue
        m_end = _BATCH_END_RE.match(line)
        if m_end and current_key == m_end.group(1):
            end_key = m_end.group(1)
            if current_kind == "M" or current_kind == "E":
                blocks[end_key] = (current_kind, None)
            else:
                joined = "".join(current_lines).strip()
                if not joined:
                    blocks[end_key] = ("F", b"")
                else:
                    try:
                        decoded = base64.b64decode(joined, validate=True)
                        blocks[end_key] = ("F", decoded)
                    except (binascii.Error, ValueError):
                        blocks[end_key] = ("F", None)
            current_key = None
            current_kind = None
            current_lines = []
            continue
        if current_key is not None:
            current_lines.append(line)

    return blocks


def _apply_batch_block(
    snap: RemoteTaskStatusSnapshot,
    block: tuple[str, bytes | None] | None,
    *,
    field: str,
    label: str,
) -> None:
    """把单个解析后的块应用到 snapshot 的相应字段。"""
    if block is None:
        snap.warnings.append(f"批量读取 {label} 缺失结果")
        return
    kind, data = block
    if kind == "M":
        # 文件不存在不是错误
        return
    if kind == "E":
        # 文件存在但读取失败
        snap.warnings.append(f"读取 {label} 失败 (read error)")
        return
    if kind == "F" and data is None:
        snap.warnings.append(f"读取 {label} base64 解码失败")
        return
    # kind == "F", data is bytes (possibly empty)
    assert data is not None
    if field == "status":
        snap.marker_exists = True
        snap.status_marker = data.decode("utf-8", errors="replace").strip()
    elif field == "exit_code":
        snap.exit_code_exists = True
        text = data.decode("utf-8", errors="replace").strip()
        try:
            snap.exit_code = int(text)
        except ValueError:
            snap.warnings.append(f"exit_code 文件内容不是有效整数: {text!r}")
    elif field == "log":
        snap.log_exists = True
        snap.submit_log_tail = data.decode("utf-8", errors="replace")
