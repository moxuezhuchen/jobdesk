# JobDesk v0.6 Refactor — `file_transfer_page.py` 终极拆分

## 目标

彻底解决 `file_transfer_page.py` 单文件 1743 行、122 个 method 的 God Class 问题。

**验收标准**：
- `file_transfer_page.py` ≤ 900 行（-50%）
- 不引入任何运行时 regression
- ruff ✅，462 tests ✅，架构边界 ✅

---

## 前置条件

```bash
git log --oneline -5    # 确认在 2928ff3 (或更新)
python -m ruff check .   # 全绿
python -m pytest tests/test_architecture_boundaries.py tests/test_gui_behavior/test_file_transfer_page.py -q   # 全绿
```

任何失败 → **中止 task，报告主会话**。

---

## 策略：Composition + Coordinator

不采用 mixin（因为共享 `self._xxx` 状态难解耦）。改用**组合**：把状态机逻辑抽到独立 `Coordinator` / `Runner` 对象，page 仅做 Qt UI 编排和事件转发。

### 状态分块（基于现状的 122 个方法）

| 域 | 方法数 | 行数 | 协调器 |
|---|---|---|---|
| Connection | 7 | ~150 | `ConnectionsCoordinator` |
| Local 导航 | 7 | ~120 | `LocalNavigator` |
| Remote Editor | 6 | ~130 | `RemoteEditSessionManager` |
| Transfer Run | 11 | ~250 | `TransferRunner` |
| 文件操作（mkdir/mv/rm/rename） | 11 | ~250 | `FileOperations` |
| UI Builder (__init__/事件 wiring) | 20 | ~400 | 留在 page |
| Selection / Menu / 输入 | 25 | ~200 | 留在 page |
| Header / Status / Setting | 14 | ~250 | 留在 page |

**预估最终 page 行数**：400 (UI builder) + 200 (selection/menu) + 250 (header/status) + ~50 (delegate) ≈ **900 行**。

---

## Task 1: `ConnectionsCoordinator` (低风险，单测友好)

**新文件**: `src/jobdesk_app/gui/pages/file_transfer_connections.py` (~200 行)

### 1.1 接口

```python
from typing import Protocol
from pathlib import Path

class ConnectionStatusCallback(Protocol):
    def __call__(self, message: str) -> None: ...

class ServersAvailableCallback(Protocol):
    def __call__(self, servers: dict) -> None: ...

class ConnectionsCoordinator:
    """Owns server list + active SSH/SFTP connection state.

    Independent of Qt widgets except via callback hooks. All FileTransferService
    lifecycle lives here, not in the page.
    """

    def __init__(
        self,
        *,
        settings_loader: Callable[[], GuiSettings],
        log_cb: Callable[[str], None],
        status_cb: Callable[[str], None],
        create_ssh: Callable,
        create_sftp: Callable,
    ) -> None: ...

    @property
    def servers(self) -> dict: ...

    @property
    def connected_server_id(self) -> str | None: ...

    @property
    def service(self) -> FileTransferService | None: ...

    def load_servers(self) -> None:
        """Re-read ``servers.yaml`` and dispatch to listeners."""

    def connect(self, server_id: str, current_dir: str) -> str | None:
        """Open SFTP session. Returns validated remote_dir or None on failure."""

    def teardown(self) -> None:
        """Stop polling, close service, remember current dir."""
```

### 1.2 抽取方法 (从 page 到 coordinator)

| Page 方法 | 迁入方式 |
|---|---|
| `_load_servers` | 整体迁移 → `load_servers` |
| `_load_servers_inner` | 私有 helper 内嵌到 `load_servers` |
| `_auto_connect_selected_server` | 重写为 `try_auto_connect(server_id)`，page 只需调 + 转发 UI 反馈 |
| `_remember_current_remote_dir` | 私有 helper，迁入 |
| `_connect` | 重写为 `connect(server_id)` 返回 status / 异常 → page 用 try/except |
| `_close_service_async` | 迁入 `teardown`，简化 |
| `_current_run_tasks` | page 自己读 `state`，迁入反而绕弯，留在 page 但实现挪到 coordinator 的内部查询 |
| `_import_sample_servers_yaml` | **不迁**（涉及 page 的 settings store UI 反馈，单独处理） |

### 1.3 设计要点

- **callback 协议**用 `Protocol`：`Callable[[str], None]` 足够，关键是要签名可静态推导以过 mypy。
- Coordinator **不持有任何 QWidget**，只持有 callable 引用。
- `connect()` 不抛异常，返回 `(success: bool, remote_dir: str | None, error: str | None)` 让 page 自己决定 UI 反馈。
- page 保留 `_refresh_btn` / `_no_server_hint` 等 Qt 对象引用；状态变化用 Signal 而非直接调用 callback（避免 callback 在 worker 线程触发）。

### 1.4 验收

```bash
python -m ruff check src/jobdesk_app/gui/pages/file_transfer_connections.py
python -m pytest tests/test_gui_behavior/test_file_transfer_page.py -q
```

- ✅ file_transfer_page.py：1743 → ~1580 行（-160）
- ✅ `connections.py` 独立单测可行（mock 掉 create_ssh/sftp 即可）
- ✅ 所有现有测试通过
- ✅ ruff ✅，mypy 不报新增错

### 1.5 Commit

```
refactor(file_transfer): extract ConnectionsCoordinator

Moves server list + SFTP lifecycle out of FileTransferPage into a
Qt-free coordinator. Page now delegates connect/teardown to it.
```

---

## Task 2: `LocalNavigator` (低风险，纯本地)

**新文件**: `src/jobdesk_app/gui/pages/file_transfer_local_navigator.py` (~150 行)

### 2.1 接口

```python
class LocalNavigator:
    """Local-side filesystem navigation state -- independent of remote."""

    def __init__(
        self,
        *,
        root_provider: Callable[[], Path],   # current_project_root
        hide_dot_provider: Callable[[], bool],
        settings: GuiSettings,
        log_cb: Callable[[str], None],
    ) -> None: ...

    @property
    def last_poll_snapshot(self) -> dict: ...

    @property
    def background_workers(self) -> list: ...   # 注入 worker 列表

    def choose_local_folder(self) -> Path | None: ...   # opens QFileDialog, returns new path
    def apply_default_local_folder(self) -> Path: ...
    def save_last_local_folder(self, path: Path) -> None: ...
    def refresh_now(self, on_rows_loaded: Callable[[list[list[str]]], None]) -> None: ...
    def refresh_async(self, on_rows_loaded: Callable[[list[list[str]]], None]) -> None: ...
    def teardown(self) -> None: ...   # 停 polling timer
```

### 2.2 抽取方法 (从 page 到 coordinator)

| Page 方法 | 迁入方式 |
|---|---|
| `_choose_local_folder` | 迁入 → `choose_local_folder` |
| `_apply_default_local_folder` | 迁入 → `apply_default_local_folder` |
| `_save_last_local_folder` | 迁入 → `save_last_local_folder` |
| `_check_local_changes` | 迁入 → `check_local_changes`，page 仅暴露一个 timer 触发它 |
| `_refresh_local` | 迁入 → `refresh_now` (callback 给 page 喂 rows) |
| `_refresh_local_async` | 迁入 → `refresh_async` |
| `_refresh_local_after_navigation` | 重写为薄方法调 `refresh_async` |
| `_load_local_rows` | **不迁**（直接操作 `self.local_table`，page 自己的事） |

### 2.3 设计要点

- `_local_poll_timer` 留在 page（Qt timer 不能跨对象），但 `check_local_changes` 内部逻辑迁入 navigator。
- page 在 `__init__` 创建 timer，connect 到 `self._navigator.check_local_changes()` + 一个 page-level 的 rows 回调。
- `refresh_now` 调用 `_load_local_rows` 路径 → 必须由 page 注入 callback 才能 wire。

### 2.4 验收

- ✅ file_transfer_page.py：~1580 → ~1450 行
- ✅ `local_navigator.py` 独立单测可行（提供 root_provider 和 hide_dot_provider mock）
- ✅ 所有现有测试通过
- ✅ ruff ✅

### 2.5 Commit

```
refactor(file_transfer): extract LocalNavigator

Moves local-side filesystem state (current_project_root, hide_dot,
poll snapshot, async refresh) into a Qt-free navigator. Timer stays
in the page; check callback is injected.
```

---

## Task 3: `RemoteEditSessionManager` (中风险，异步 + 状态)

**新文件**: `src/jobdesk_app/gui/pages/file_transfer_remote_edit.py` (~200 行)

### 3.1 接口

```python
@dataclass
class RemoteEditOutcome:
    success: bool
    error: str | None = None

class RemoteEditSessionManager:
    """Tracks open remote files being edited locally + auto-uploads on save."""

    def __init__(
        self,
        *,
        service_provider: Callable[[], FileTransferService | None],
        remote_target_for_local: Callable[[Path], str],
        settings_provider: Callable[[], GuiSettings],
        on_status: Callable[[str], None],
        on_error: Callable[[str, str], None],
        on_refresh_remote: Callable[[], None],
    ) -> None: ...

    @property
    def dirty_sessions(self) -> list[_RemoteEditSession]: ...
    def has_dirty(self) -> bool: ...

    def open_remote_file(self, remote_path: str, parent: QWidget) -> bool:
        """Download to temp + launch configured editor. Returns True if opened."""

    def register_session(self, remote_path: str, local_path: Path) -> None: ...
    def tick(self) -> None:
        """Called by page's poll timer. Detects dirty sessions and uploads."""
    def upload_session(self, session: _RemoteEditSession, signature: str | None) -> None: ...
    def teardown(self) -> None:
        """Returns list of unsaved sessions for warning dialog."""
```

### 3.2 抽取方法

| Page 方法 | 迁入方式 |
|---|---|
| `_open_remote_file_in_editor` | 迁入 `open_remote_file` |
| `_open_in_text_editor` | 迁入 `open_remote_file` 内嵌 |
| `_register_remote_edit_session` | 迁入 `register_session` |
| `_check_remote_edit_sessions` | 迁入 `tick` |
| `_upload_remote_edit_session` | 迁入 `upload_session` |
| `_dirty_remote_edit_sessions` | 迁入 `dirty_sessions` |

### 3.3 设计要点

- `_remote_edit_timer` 留在 page（同 `_local_poll_timer`），connect 到 `tick`。
- `service_provider` 是 callable，每次调用取最新 `_service`（避免 page 关闭后 manager 持有 stale ref）。
- `teardown()` 返回 `_RemoteEditSession` 列表，page 把它转 `self._error_cb` 用户警告。

### 3.4 验收

- ✅ file_transfer_page.py：~1450 → ~1320 行
- ✅ ruff ✅，所有现有 tests 通过
- ⚠️ 注意：`_open_remote_file_in_editor` 涉及 QMessageBox / QWidget parent，迁入时 page 注入 parent

### 3.5 Commit

```
refactor(file_transfer): extract RemoteEditSessionManager

Moves open-in-editor + remote edit dirty-tracking into a manager.
Page-level timers (Qt) remain in the page, but all session state
and async upload logic moves out.
```

---

## Task 4: `TransferRunner` (中风险，进度回调 + worker 生命周期)

**新文件**: `src/jobdesk_app/gui/pages/file_transfer_runner.py` (~280 行)

### 4.1 接口

```python
class TransferRunner:
    """Manages upload/download/preview operations + progress reporting.

    Owns the Qt progress bar (passed in) and worker lifecycle list.
    """

    def __init__(
        self,
        *,
        progress_bar: QProgressBar,
        service_provider: Callable[[], FileTransferService | None],
        language_provider: Callable[[], str],
        worker_registry: list,           # _background_workers shared list
        on_status: Callable[[str], None],
        on_error: Callable[[str, str], None],
        on_refresh_local: Callable[[], None],
        on_refresh_remote: Callable[[], None],
    ) -> None: ...

    def upload_selected(self, local_path: Path, remote_target: str) -> None: ...
    def download_selected(self, remote_path: str, local_base: Path) -> None: ...
    def upload_dropped_local_paths(self, paths: list[str], remote_dir: str) -> None: ...
    def download_dropped_remote_paths(self, paths: list[str], local_base: Path) -> None: ...
    def preview_remote(self, remote_path: str, parent: QWidget) -> None: ...
    def start_worker(self, run_fn, label: str, on_done_refresh: Callable[[], None]) -> None: ...
    def teardown(self) -> None: ...
```

### 4.2 抽取方法

| Page 方法 | 迁入方式 |
|---|---|
| `_download_selected` | 迁入 `download_selected` |
| `_upload_selected` | 迁入 `upload_selected` |
| `_start_transfer_worker` | 迁入 `start_worker` (page 调它传 run_fn) |
| `_upload_dropped_local_paths` | 迁入 `upload_dropped_local_paths` |
| `_download_dropped_remote_paths` | 迁入 `download_dropped_remote_paths` |
| `_preview_remote` | 迁入 `preview_remote` |
| `_refresh_remote` | **不迁**（依赖 _remote_list state，留 page）|
| `_refresh_remote_path` | **不迁**（同）|
| `_fallback_remote_dirs` | **不迁**（依赖 _gui_settings + page state）|
| `_on_remote_entries_loaded` | **不迁**（直接操作 tables）|
| `_on_remote_list_error` | **不迁**（同）|

### 4.3 设计要点

- `_keep_worker` 工具迁入（用于 BackgroundWorker lifecycle 管理）。
- `start_worker` 内部用 `start_context_worker` / `start_tracked_worker`，callback 全部 forward 到 page 给的 hooks。
- `progress_bar.setFormat` 的格式字符串（`{label}: {done // 1024}K / ...`）迁入。

### 4.4 验收

- ✅ file_transfer_page.py：~1320 → ~1180 行
- ✅ ruff ✅，tests 通过
- ✅ 进度回调正确（人工跑 `qapp` 启动验证，可选）

### 4.5 Commit

```
refactor(file_transfer): extract TransferRunner

Moves upload/download/preview state and progress callback wiring
into a runner that owns the QProgressBar.
```

---

## Task 5: `FileOperations` (低-中风险，纯函数为主)

**新文件**: `src/jobdesk_app/gui/pages/file_transfer_operations.py` (~280 行)

### 5.1 接口

```python
class FileOperations:
    """Local and remote file operations: mkdir, mv, rm, rename.

    Stateless beyond the page callbacks (status_cb, error_cb).
    """

    def __init__(
        self,
        *,
        service_provider: Callable[[], FileTransferService | None],
        local_root_provider: Callable[[], Path],
        language: str,
        on_status: Callable[[str], None],
        on_error: Callable[[str, str], None],
        on_refresh_local: Callable[[], None],
        on_refresh_remote: Callable[[], None],
        prompt_new_name: Callable[[str, str, str], tuple[str, bool]],   # title, label, default
        prompt_new_folder: Callable[[str, str], tuple[str, bool]],
        ask_confirm: Callable[[str, str], bool],                          # title, body
    ) -> None: ...

    # Local
    def copy_dropped_local_paths(self, paths: list[str]) -> None: ...
    def move_local_paths_into_directory(self, paths: list[str], target_dir_text: str) -> None: ...
    def mkdir_local(self) -> None: ...
    def new_file_local(self, parent: QWidget) -> None: ...
    def rename_local(self, local_path: Path) -> None: ...

    # Remote
    def move_remote_paths_into_directory(self, paths: list[str], target_dir_text: str) -> None: ...
    def new_file_remote(self, parent: QWidget, remote_dir: str) -> None: ...
    def mkdir_remote(self, remote_dir: str) -> None: ...
    def rename_remote(self, remote_path: str) -> None: ...
    def delete_remote(self, remote_paths: list[str], current_dir: str) -> None: ...

    @staticmethod
    def validate_rename_name(name: str, error_cb: Callable[[str, str], None]) -> str | None: ...
```

### 5.2 抽取方法

| Page 方法 | 迁入方式 |
|---|---|
| `_copy_dropped_local_paths` | 迁入 `copy_dropped_local_paths` |
| `_move_local_paths_into_directory` | 迁入 `move_local_paths_into_directory` |
| `_move_remote_paths_into_directory` | 迁入 `move_remote_paths_into_directory` |
| `_mkdir_local` | 迁入 `mkdir_local` |
| `_new_file_local` | 迁入 `new_file_local` |
| `_new_file_remote` | 迁入 `new_file_remote` (page 注入 remote_dir) |
| `_mkdir_remote` | 迁入 `mkdir_remote` (page 注入 remote_dir) |
| `_rename_local` | 迁入 `rename_local` (page 注入 local_path) |
| `_rename_remote` | 迁入 `rename_remote` (page 注入 remote_path) |
| `_delete_remote` | 迁入 `delete_remote` |
| `_rename_name` | 静态工具迁入 `validate_rename_name` |
| `_delete_local` | 迁入 `delete_local` (新方法) |

### 5.3 设计要点

- 所有 `QInputDialog.exec()` / `QMessageBox.question` 全部通过 callable 注入，让 manager 单元可测不依赖 GUI。
- `validate_rename_name` 纯函数，可以移到 `file_transfer_helpers.py`，不强制放在 manager。
- 不要重写算法逻辑，只迁位置。

### 5.4 验收

- ✅ file_transfer_page.py：~1180 → ~1000 行
- ✅ ruff ✅，tests 通过（特别是 `test_rename_*` 和 `test_delete_*` 系列）

### 5.5 Commit

```
refactor(file_transfer): extract FileOperations

Moves mkdir/mv/rm/rename/copy for both local and remote out of the
page into FileOperations. Dialogs are injected via callbacks so the
class is unit-testable without QApplication.
```

---

## Task 6: 收尾 — 删除 page 中被迁走的 dead methods + 验证

**单一 commit，验证全部成果**

### 6.1 步骤

1. **遍历 file_transfer_page.py**，删除 Task 1-5 已迁走的所有 page 方法（如果之前是 alias 转发，要完全删除）。
2. **简化 `_import_sample_servers_yaml`**：原 page 调 `data = load_existing_servers_data(path)`，是 `file_transfer_config` import；本 task 用 ConnectionsCoordinator 替代 load_servers 调用。
3. **审查 page 的 `__init__`**，删除 page 上已被迁走的 state（如 `_service` 设为 coordinator 内部字段）。
4. 运行：
   ```bash
   python -m ruff check .
   python -m pytest tests/test_architecture_boundaries.py -q
   python -m pytest tests/test_gui_behavior/ -q
   python -m pytest tests/test_run_service.py tests/test_run_repository.py -q
   ```
5. 记录最终行数：
   ```bash
   wc -l src/jobdesk_app/gui/pages/file_transfer_page.py
   wc -l src/jobdesk_app/gui/pages/file_transfer_connections.py
   wc -l src/jobdesk_app/gui/pages/file_transfer_local_navigator.py
   wc -l src/jobdesk_app/gui/pages/file_transfer_remote_edit.py
   wc -l src/jobdesk_app/gui/pages/file_transfer_runner.py
   wc -l src/jobdesk_app/gui/pages/file_transfer_operations.py
   ```

### 6.2 验收

- ✅ `file_transfer_page.py` ≤ **900 行**
- ✅ 5 个 coordinator 文件全部存在，单元测试结构清晰
- ✅ 462 tests 全绿
- ✅ ruff 全绿
- ✅ `test_architecture_boundaries.py` 全绿（特别注意 `test_file_transfer_page_does_not_import_local_path_provider` 之类如果存在）

### 6.3 Commit

```
refactor(file_transfer): tighten page composition

Final pass to remove dead helpers retained as aliases during
migration, simplify __init__ to delegate to coordinators, and
verify the page contains only Qt-UI orchestration logic.

file_transfer_page.py: 1743 -> ~850 lines
```

---

## 提交规范 (每个 task 完成后)

1. 运行：
   ```bash
   python -m ruff check .
   python -m pytest tests/test_architecture_boundaries.py tests/test_gui_behavior/test_file_transfer_page.py -q
   ```
2. **若失败**：立即停止，不要 commit，立即报告主会话。
3. **若通过**：`git add -u` + `git commit -m "..."`，然后 `python -m pytest tests/ -q` 跑全套再报告主会话。
4. 报告内容：
   - 文件变更 + 行数变化
   - ruff + 测试结果
   - commit hash
   - 是否触及 plan 范围外的文件（应该 0）

---

## 注意事项（重要）

### 不要做
- ❌ 不要一次性合并多个 coordinator 抽取到单 commit。每个 task 是独立可回滚的 commit。
- ❌ 不要重写 page 逻辑或修 bug。本 plan 是纯位置迁移。
- ❌ 不要新增依赖（PySide6 已用 / stdlib 足够）。
- ❌ 不要触碰 `file_transfer_widgets.py`、`file_transfer_tables.py`、`file_transfer_helpers.py`、`file_transfer_config.py`（除非是极小的 forward-dep 调整且明确说明）。
- ❌ 不要修改 `_FileTable` / `_RemoteEditSession` 等已被 subagent 抽出的类。
- ❌ 不要新增 class-level state 到 coordinator 后再迁——每个 task 只迁一类状态。

### 必须做
- ✅ 每 task 完成后跑 `python -m ruff check .` 立即确认（不是事后）。
- ✅ 若 page 方法迁走后 page 上仍需保留 forward alias（保持 `_method_name` 调用兼容测试），先保留 alias，下一个 task 末尾删除（**仅当 ruff/test 全绿**）。
- ✅ 命名严格遵循 plan（`ConnectionsCoordinator` / `LocalNavigator` / `RemoteEditSessionManager` / `TransferRunner` / `FileOperations`）。
- ✅ 每个 coordinator **不要 import Qt widgets**（除了 `TransferRunner` 必须持有 `QProgressBar`）。
- ✅ 测试如果发现 bug，**立即报告主会话**，不要就地修。

### 失败处理
- 若 ruff 失败 → 报告。
- 若 pytest 失败 → `git reset HEAD~1` 然后报告。
- 若 mypy 新增错误 → 报告（但本计划未强制要求 mypy，可选）。

---

## 进度检查清单

- [ ] Task 1: ConnectionsCoordinator
- [ ] Task 2: LocalNavigator
- [ ] Task 3: RemoteEditSessionManager
- [ ] Task 4: TransferRunner
- [ ] Task 5: FileOperations
- [ ] Task 6: 收尾 + 最终验证

---

## 显式声明（本计划不涉及的 task）

为了避免 scope creep，下面这些**明确不在本计划**：

- ❌ `file_transfer_widgets.py` / `file_transfer_tables.py` / `file_transfer_helpers.py` / `file_transfer_config.py` 的进一步抽取——subagent 已经做过，不再回头改。
- ❌ `_FileTransferPage` 的 method 重新命名（即使变长）。
- ❌ 添加新功能、修 bug、UI 改进。
- ❌ `runs_results_page.py` / `settings_servers_page.py` / `workflow_page.py`——这些已经有 refactor commit 在 1bcb6f8 之前完成，本计划焦点只在 file_transfer_page。
- ❌ `docs/REFACTOR_PLAN.md` 调整——本计划是 v0.6 新文件。
