# 测试 Mock 策略

本文档说明 JobDesk 测试套件中 Mock 的使用原则、边界和常见模式。

## 核心原则

### 1. 按依赖层级选择 Mock 策略

```
┌─────────────────────────────────────────────────────────┐
│  真实外部依赖 (SSH/SFTP/g16)                              │
│  → 集成测试或 smoke 测试，不 mock                          │
├─────────────────────────────────────────────────────────┤
│  网络 I/O (HTTP 请求、Socket)                            │
│  → Mock at client level (unittest.mock)                  │
├─────────────────────────────────────────────────────────┤
│  文件系统操作                                            │
│  → pytest fixture `tmp_path` + mock 特定路径              │
├─────────────────────────────────────────────────────────┤
│  时间相关 (timing, scheduling)                           │
│  → freezegun 或 mock time                               │
├─────────────────────────────────────────────────────────┤
│  业务逻辑                                               │
│  → 真实实现，不 mock                                    │
└─────────────────────────────────────────────────────────┘
```

### 2. Mock 的边界

**应该 Mock：**
- 外部服务调用 (SSH、SFTP、远程 API)
- 文件系统路径 (避免污染真实目录)
- 时间/日期操作
- 随机数生成 (用于测试确定性)
- GUI 组件的子组件 (widget isolation)

**不应该 Mock：**
- 核心业务逻辑和计算
- 数据模型和验证
- 配置解析
- 内部服务间的协作 (用 integration tests 覆盖)

## 常见 Mock 模式

### SSH/SFTP Mock

```python
from unittest.mock import MagicMock, patch

def test_submit_with_mock_ssh(tmp_path):
    mock_ssh = MagicMock()
    mock_ssh.connect.return_value = True
    mock_ssh.exec_command.return_value = (
        MagicMock()  # stdout
        b"job_id=12345\n",
        MagicMock()  # stderr
        b"",
        MagicMock()  # exit status
        0
    )

    with patch("jobdesk_app.remote.ssh.SSHClient", return_value=mock_ssh):
        result = submit_job(...)
        assert result.job_id == "12345"
```

参考：`tests/test_submitter.py`、`tests/test_ssh.py`

### Scheduler Mock

```python
def test_run_with_mock_scheduler():
    mock_scheduler = MagicMock(spec=SlurmAdapter)
    mock_scheduler.submit.return_value = "slurm_job_123"

    service = RunService(..., scheduler=mock_scheduler)
    result = service.submit_run(run_id)

    mock_scheduler.submit.assert_called_once()
```

参考：`tests/test_scheduler.py`

### 文件系统 Mock

```python
def test_manifest_read(tmp_path):
    manifest_path = tmp_path / "manifest.tsv"
    manifest_path.write_text("task_id\tfile\na\tresult.out\n")

    manifest = Manifest.read_from_file(manifest_path)
    assert len(manifest.tasks) == 1
```

使用 `tmp_path` fixture 避免污染真实文件系统。

### 时间 Mock

```python
from datetime import datetime
from freezegun import freeze_time

@freeze_time("2024-01-15 10:30:00")
def test_status_timeout():
    status = compute_status(...)
    assert status.is_stale  # 基于固定时间判断
```

### GUI Widget Mock

```python
import pytest
pytest.importorskip("PySide6")

def test_button_click_signal(qtbot):
    button = QPushButton("Click me")
    qtbot.addWidget(button)

    clicked = []
    button.clicked.connect(lambda: clicked.append(True))

    qtbot.click(button)
    assert clicked
```

## Mock 辅助工具

### `tests/repository_helpers.py`

提供测试专用的 repository 替换函数：

```python
from tests.repository_helpers import replace_tasks_for_test

def test_task_aggregation():
    replace_tasks_for_test(repo, [
        Task(status=TaskStatus.completed, result="good"),
        Task(status=TaskStatus.failed, result="error"),
    ])
    # 测试聚合逻辑
```

### `tests/test_gui_behavior/conftest.py` - `_FakeWorker`

用于模拟 GUI 后台 worker：

```python
from tests.test_gui_behavior.conftest import _FakeWorker

def test_page_with_fake_worker(runs_page, qtbot):
    worker = _FakeWorker(outcome={"errors": []})
    runs_page._background_worker = worker
    # 测试 UI 响应
```

## 集成测试中的 Mock 边界

集成测试 (位于 `tests/integration/`) 主要测试真实外部依赖：

```python
# integration/test_real_submitter.py
def test_real_ssh_submit():
    """真实 SSH 连接，使用真实环境变量配置"""
    host = os.environ.get("JOBDESK_TEST_SSH_HOST")
    if not host:
        pytest.skip("No SSH test host configured")

    # 不 mock，直接连接
    result = real_submit(host, ...)
```

对于需要部分 mock 的场景，使用条件 mock：

```python
def test_with_partial_mock():
    # Mock 网络层，保留文件操作
    with patch("jobdesk_app.remote.ssh.socket.create_connection"):
        # 测试 SSH 逻辑但不实际连接
```

## 避免的 Anti-Patterns

### 1. 不要 Mock 被测对象本身

```python
# 错误
service = Mock(spec=RunService)
service.create_run.return_value = ...

# 正确
service = RunService(...)  # 真实对象
```

### 2. 不要过度 Mock 配置

```python
# 错误 - 绕过了真实配置解析
config = Mock(config_file="...")

# 正确 - 测试真实配置
config = load_config("tests/fixtures/valid_config.yaml")
```

### 3. 不要在单元测试中 Mock 数据库

使用 `tmp_path` 创建临时 SQLite 数据库：

```python
def test_sqlite_persistence(tmp_path):
    db_path = tmp_path / "test.db"
    repo = RunRepository(db_path)
    repo.save_run(RunRecord(...))
    # 真实 SQLite 操作
```

### 4. 不要使用 Mock 绕过验证

```python
# 错误
validator.validate = Mock(return_value=True)  # 跳过验证

# 正确 - 使用合法测试数据
valid_data = load_fixture("valid_input.gjf")
```

## Mock 与 Fixture 的配合

```python
# conftest.py
@pytest.fixture
def mock_sftp_client():
    client = MagicMock(spec=SFTPClient)
    client.list_dir.return_value = ["file1.out", "file2.out"]
    return client

# test_file_transfer.py
def test_list_remote_files(mock_sftp_client):
    files = list_remote_files("/remote/dir", client=mock_sftp_client)
    assert files == ["file1.out", "file2.out"]
```

## 测试可移植性

为了在不同环境下运行，使用条件 mock：

```python
@pytest.fixture
def ssh_client():
    if os.environ.get("JOBDESK_TEST_USE_REAL_SSH"):
        return RealSSHClient()  # CI 环境
    return MagicMock(spec=SSHClient)  # 本地开发
```

## 参考实现

- `tests/test_submitter.py` - 完整的 mock 与真实混合模式
- `tests/test_run_service.py` - 使用 `tmp_path` 和 mock scheduler
- `tests/test_gui_behavior/test_runs_page.py` - GUI 测试 mock 模式
- `tests/integration/conftest.py` - 集成测试 fixture
