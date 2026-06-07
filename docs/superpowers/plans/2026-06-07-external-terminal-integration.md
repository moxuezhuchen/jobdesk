# SSH Access and Startup Responsiveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve JobDesk's SSH usability without a GUI rewrite by adding external terminal launch, SSH config/jump/proxy connection options, and delayed startup work for heavier GUI tasks.

**Architecture:** Keep terminal integration outside the SSH/SFTP runtime path, and keep GUI changes thin. Add typed server configuration for external terminal settings and SSH access options, pure command-building helpers, Paramiko connection argument resolution, and delayed activation for expensive GUI page work. Do not embed a terminal widget, do not store passwords, and do not pass passwords on a command line.

**Tech Stack:** Python 3.11, PySide6, Pydantic v2, Paramiko, Windows `subprocess.Popen`, Windows Terminal `wt`, OpenSSH `ssh`, PuTTY saved sessions.

---

## Scope

In scope:

- Open a terminal for the selected run at `remote_run_dir(record.remote_dir, record.run_id)`.
- Support Windows Terminal/OpenSSH as the default provider.
- Support PuTTY saved sessions with a temporary remote startup command file.
- Add copyable SSH and `cd` commands.
- Support OpenSSH config aliases for runtime SSH/SFTP connections through Paramiko.
- Support single-hop jump/proxy connections through a validated `proxy_command`.
- Delay Runs/Results auto-refresh and monitoring startup until after the page is visible.
- Preserve backward compatibility for existing `servers.yaml` files.

Out of scope:

- Embedded terminal UI.
- Password storage or password command-line passing.
- OTP automation.
- WinSCP integration.
- Task-level directory selection. This first version opens the run directory; task-level launch can be added after the task detail GUI exists.
- Multi-hop interactive terminal orchestration. Complex login chains should live in OpenSSH config, PuTTY sessions, or a configured proxy command.
- Replacing the current Files/Runs/Settings page layout.

## File Structure

- Modify: `src/jobdesk_app/config/schema.py`
  - Add `TerminalProvider` enum, `ExternalToolsConfig`, and `SSHAccessConfig` models.
  - Add `external_tools` and `ssh_access` to `ServerConfig`.

- Create: `src/jobdesk_app/services/external_terminal.py`
  - Pure command builder for Windows Terminal/OpenSSH and PuTTY.
  - Safe POSIX shell quoting for remote directories.
  - Temporary PuTTY startup file creation.
  - Clipboard-friendly command rendering.

- Modify: `src/jobdesk_app/gui/pages/settings_servers_page.py`
  - Add server edit/add fields for terminal provider, SSH alias, and PuTTY session.
  - Preserve hidden external tool keys.

- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
  - Add context-menu actions: `Open Terminal Here`, `Copy SSH Command`, `Copy cd Command`.
  - Keep launching thin: load selected `RunRecord`, resolve server config, call service.
  - Delay activation-time refresh/monitor startup with a single-shot timer to avoid blocking initial navigation.

- Modify: `src/jobdesk_app/remote/ssh.py`
  - Resolve host, user, port, key, and proxy settings from OpenSSH config aliases when configured.
  - Pass a validated `paramiko.ProxyCommand` object when `proxy_command` is configured.

- Modify: `src/jobdesk_app/gui/i18n.py`
  - Add translation strings for new UI actions and settings labels.

- Test: `tests/test_config_loader.py`
  - Validate default and explicit external terminal config parsing.

- Test: `tests/test_external_terminal.py`
  - Validate command generation and PuTTY startup file content.

- Test: `tests/test_remote_ssh.py`
  - Validate SSH config alias lookup and proxy command wiring without real network access.

- Test: `tests/test_settings_servers_page.py`
  - Validate edit/add dialogs save external terminal fields and preserve hidden keys.

- Test: `tests/test_gui_behavior.py`
  - Validate Runs context actions and command launching/copy callbacks without spawning real tools.
  - Validate Runs activation defers heavy refresh/monitor work through a timer.

---

### Task 1: Add Typed External Tool and SSH Access Configuration

**Files:**
- Modify: `src/jobdesk_app/config/schema.py`
- Test: `tests/test_config_loader.py`

- [ ] **Step 1: Write failing config tests**

Add these tests to `tests/test_config_loader.py`:

```python
def test_server_config_external_tools_defaults_to_windows_terminal():
    cfg = ServerConfig(server_id="s1", host="cluster", username="chemist")

    assert cfg.external_tools.terminal_provider == "windows_terminal"
    assert cfg.external_tools.ssh_alias == ""
    assert cfg.external_tools.putty_session == ""


def test_server_config_external_tools_loads_explicit_values():
    cfg = ServerConfig(
        server_id="hpc",
        host="cluster.example.edu",
        username="chemist",
        external_tools={
            "terminal_provider": "putty",
            "ssh_alias": "cluster-a",
            "putty_session": "cluster-a-putty",
        },
    )

    assert cfg.external_tools.terminal_provider == "putty"
    assert cfg.external_tools.ssh_alias == "cluster-a"
    assert cfg.external_tools.putty_session == "cluster-a-putty"


def test_server_config_rejects_unknown_terminal_provider():
    with pytest.raises(Exception):
        ServerConfig(
            server_id="bad",
            host="cluster",
            username="chemist",
            external_tools={"terminal_provider": "unknown"},
        )


def test_server_config_ssh_access_defaults_are_empty():
    cfg = ServerConfig(server_id="s1", host="cluster", username="chemist")

    assert cfg.ssh_access.config_alias == ""
    assert cfg.ssh_access.proxy_command == ""
    assert cfg.ssh_access.proxy_jump == ""


def test_server_config_ssh_access_loads_explicit_values():
    cfg = ServerConfig(
        server_id="hpc",
        host="cluster.example.edu",
        username="chemist",
        ssh_access={
            "config_alias": "cluster-a",
            "proxy_command": "ssh -W %h:%p gateway",
            "proxy_jump": "gateway",
        },
    )

    assert cfg.ssh_access.config_alias == "cluster-a"
    assert cfg.ssh_access.proxy_command == "ssh -W %h:%p gateway"
    assert cfg.ssh_access.proxy_jump == "gateway"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/test_config_loader.py -q --basetemp .pytest_tmp_terminal_config
```

Expected: fail because `ServerConfig.external_tools` and `ServerConfig.ssh_access` do not exist.

- [ ] **Step 3: Implement config model**

Add this near the existing enum definitions in `src/jobdesk_app/config/schema.py`:

```python
class TerminalProvider(str, Enum):
    windows_terminal = "windows_terminal"
    putty = "putty"


class ExternalToolsConfig(BaseModel):
    """External desktop tools associated with one server profile."""

    terminal_provider: TerminalProvider = Field(
        default=TerminalProvider.windows_terminal,
        description="External terminal provider: windows_terminal / putty",
    )
    ssh_alias: str = Field(
        default="",
        description="OpenSSH config alias used by Windows Terminal",
    )
    putty_session: str = Field(
        default="",
        description="PuTTY saved session name",
    )


class SSHAccessConfig(BaseModel):
    """Advanced SSH connection options for Paramiko and OpenSSH interop."""

    config_alias: str = Field(
        default="",
        description="Host alias from ~/.ssh/config used for runtime SSH/SFTP",
    )
    proxy_command: str = Field(
        default="",
        description="ProxyCommand used by Paramiko, for example ssh -W %h:%p gateway",
    )
    proxy_jump: str = Field(
        default="",
        description="Documented jump-host name; runtime uses config_alias or proxy_command",
    )
```

Add these fields to `ServerConfig`:

```python
    external_tools: ExternalToolsConfig = Field(
        default_factory=ExternalToolsConfig,
        description="External terminal and file-browser integration settings",
    )
    ssh_access: SSHAccessConfig = Field(
        default_factory=SSHAccessConfig,
        description="Advanced SSH connection settings",
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```powershell
python -m pytest tests/test_config_loader.py -q --basetemp .pytest_tmp_terminal_config
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/config/schema.py tests/test_config_loader.py
git commit -m "Add external SSH access server config"
```

---

### Task 2: Add Pure External Terminal Command Builder

**Files:**
- Create: `src/jobdesk_app/services/external_terminal.py`
- Test: `tests/test_external_terminal.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_external_terminal.py`:

```python
from pathlib import Path

from jobdesk_app.config.schema import ServerConfig
from jobdesk_app.services.external_terminal import (
    TerminalLaunch,
    build_cd_command,
    build_terminal_launch,
)


def test_build_cd_command_quotes_remote_path():
    assert build_cd_command("/tmp/job desk/run 1") == "cd '/tmp/job desk/run 1'"


def test_windows_terminal_uses_ssh_alias_when_available(tmp_path):
    server = ServerConfig(
        server_id="hpc",
        host="cluster.example.edu",
        username="chemist",
        external_tools={"ssh_alias": "cluster-a"},
    )

    launch = build_terminal_launch(server, "/tmp/jobdesk/run-a", temp_dir=tmp_path)

    assert isinstance(launch, TerminalLaunch)
    assert launch.executable == "wt"
    assert "ssh" in launch.args
    assert "cluster-a" in launch.args
    joined = " ".join(launch.args)
    assert "cd '/tmp/jobdesk/run-a'" in joined
    assert launch.user_visible_command.startswith("wt ")


def test_windows_terminal_falls_back_to_user_host_and_port(tmp_path):
    server = ServerConfig(
        server_id="hpc",
        host="cluster.example.edu",
        username="chemist",
        port=2200,
    )

    launch = build_terminal_launch(server, "/tmp/run", temp_dir=tmp_path)

    joined = " ".join(launch.args)
    assert "chemist@cluster.example.edu" in joined
    assert "-p 2200" in joined


def test_putty_requires_saved_session(tmp_path):
    server = ServerConfig(
        server_id="hpc",
        host="cluster.example.edu",
        username="chemist",
        external_tools={"terminal_provider": "putty"},
    )

    try:
        build_terminal_launch(server, "/tmp/run", temp_dir=tmp_path)
    except ValueError as exc:
        assert "PuTTY saved session" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_putty_uses_command_file_for_remote_cd(tmp_path):
    server = ServerConfig(
        server_id="hpc",
        host="cluster.example.edu",
        username="chemist",
        external_tools={
            "terminal_provider": "putty",
            "putty_session": "cluster-a-putty",
        },
    )

    launch = build_terminal_launch(server, "/tmp/job desk/run-a", temp_dir=tmp_path)

    assert launch.executable == "putty.exe"
    assert launch.args[:3] == ["-load", "cluster-a-putty", "-t"]
    assert "-m" in launch.args
    command_file = Path(launch.args[launch.args.index("-m") + 1])
    assert command_file.exists()
    assert command_file.read_text(encoding="utf-8") == (
        "cd '/tmp/job desk/run-a'\n"
        "exec ${SHELL:-/bin/sh} -l\n"
    )
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/test_external_terminal.py -q --basetemp .pytest_tmp_terminal_service
```

Expected: fail because `jobdesk_app.services.external_terminal` does not exist.

- [ ] **Step 3: Implement service**

Create `src/jobdesk_app/services/external_terminal.py`:

```python
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config.schema import ServerConfig, TerminalProvider


@dataclass(frozen=True)
class TerminalLaunch:
    executable: str
    args: list[str]
    user_visible_command: str


def build_cd_command(remote_dir: str) -> str:
    return f"cd {shlex.quote(remote_dir)}"


def build_remote_shell_command(remote_dir: str) -> str:
    return f"{build_cd_command(remote_dir)} && exec ${{SHELL:-/bin/sh}} -l"


def build_terminal_launch(
    server: ServerConfig,
    remote_dir: str,
    *,
    temp_dir: str | Path,
) -> TerminalLaunch:
    provider = server.external_tools.terminal_provider
    if provider == TerminalProvider.putty:
        return _build_putty_launch(server, remote_dir, temp_dir=Path(temp_dir))
    return _build_windows_terminal_launch(server, remote_dir)


def _ssh_target(server: ServerConfig) -> str:
    alias = server.external_tools.ssh_alias.strip()
    if alias:
        return alias
    return f"{server.username}@{server.host}"


def _build_windows_terminal_launch(server: ServerConfig, remote_dir: str) -> TerminalLaunch:
    remote_command = build_remote_shell_command(remote_dir)
    ssh_parts = ["ssh", "-t", _ssh_target(server)]
    if not server.external_tools.ssh_alias.strip() and server.port != 22:
        ssh_parts.extend(["-p", str(server.port)])
    ssh_parts.append(remote_command)
    powershell_command = " ".join(shlex.quote(part) for part in ssh_parts)
    args = ["new-tab", "powershell", "-NoExit", "-Command", powershell_command]
    return TerminalLaunch(
        executable="wt",
        args=args,
        user_visible_command="wt " + " ".join(shlex.quote(part) for part in args),
    )


def _build_putty_launch(
    server: ServerConfig,
    remote_dir: str,
    *,
    temp_dir: Path,
) -> TerminalLaunch:
    session = server.external_tools.putty_session.strip()
    if not session:
        raise ValueError("PuTTY saved session is required for PuTTY terminal launch")
    temp_dir.mkdir(parents=True, exist_ok=True)
    command_file = temp_dir / f"jobdesk_putty_{server.server_id}.sh"
    command_file.write_text(
        build_cd_command(remote_dir) + "\nexec ${SHELL:-/bin/sh} -l\n",
        encoding="utf-8",
    )
    args = ["-load", session, "-t", "-m", str(command_file)]
    return TerminalLaunch(
        executable="putty.exe",
        args=args,
        user_visible_command="putty.exe " + " ".join(shlex.quote(part) for part in args),
    )


def launch_terminal(launch: TerminalLaunch) -> subprocess.Popen:
    return subprocess.Popen([launch.executable, *launch.args])
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```powershell
python -m pytest tests/test_external_terminal.py -q --basetemp .pytest_tmp_terminal_service
```

Expected: pass.

- [ ] **Step 5: Run type/lint on new service**

Run:

```powershell
python -m ruff check src/jobdesk_app/services/external_terminal.py tests/test_external_terminal.py
python -m mypy src
```

Expected: both pass. If mypy objects to `subprocess.Popen` generic return type, annotate `launch_terminal` as returning `subprocess.Popen[bytes]` or remove the explicit return annotation according to the project's current mypy behavior.

- [ ] **Step 6: Commit**

```powershell
git add src/jobdesk_app/services/external_terminal.py tests/test_external_terminal.py
git commit -m "Build external terminal launch commands"
```

---

### Task 3: Add Server Settings Fields

**Files:**
- Modify: `src/jobdesk_app/gui/pages/settings_servers_page.py`
- Modify: `src/jobdesk_app/gui/i18n.py`
- Test: `tests/test_settings_servers_page.py`

- [ ] **Step 1: Write failing settings-page tests**

Add this test to `tests/test_settings_servers_page.py`:

```python
def test_edit_server_saves_external_terminal_fields(qtbot, tmp_path):
    from PySide6.QtWidgets import QComboBox

    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n"
        "  hpc:\n"
        "    host: cluster\n"
        "    username: chemist\n"
        "    auth_method: key\n"
        "    external_tools:\n"
        "      terminal_provider: windows_terminal\n"
        "      ssh_alias: old-alias\n"
        "      winscp_session: hidden-site\n",
        encoding="utf-8",
    )
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()

    def accept_with_external_tools(dialog):
        provider_combo = next(
            c for c in dialog.findChildren(QComboBox)
            if "windows_terminal" in [c.itemText(i) for i in range(c.count())]
            and "putty" in [c.itemText(i) for i in range(c.count())]
        )
        provider_combo.setCurrentText("putty")
        edits = dialog.findChildren(QLineEdit)
        ssh_alias = next(edit for edit in edits if edit.text() == "old-alias")
        ssh_alias.setText("cluster-a")
        putty_session = next(edit for edit in edits if edit.placeholderText() == "PuTTY saved session")
        putty_session.setText("cluster-a-putty")
        return QDialog.Accepted

    with patch(
        "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
        return_value=settings_store,
    ), patch(
        "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
        return_value=servers_path,
    ), patch(
        "jobdesk_app.gui.pages.settings_servers_page.load_servers",
        side_effect=lambda: load_servers_from_path(servers_path),
    ), patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_external_tools):
        page = SettingsServersPage(MagicMock(), lambda message: None, lambda message: None)
        qtbot.addWidget(page)
        page.server_table.selectRow(0)
        page._edit_server()

    external = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]["hpc"]["external_tools"]
    assert external["terminal_provider"] == "putty"
    assert external["ssh_alias"] == "cluster-a"
    assert external["putty_session"] == "cluster-a-putty"
    assert external["winscp_session"] == "hidden-site"


def test_edit_server_saves_ssh_access_fields(qtbot, tmp_path):
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n"
        "  hpc:\n"
        "    host: cluster\n"
        "    username: chemist\n"
        "    auth_method: key\n"
        "    ssh_access:\n"
        "      config_alias: old-runtime-alias\n"
        "      hidden_ssh_key: preserved\n",
        encoding="utf-8",
    )
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()

    def accept_with_ssh_access(dialog):
        edits = dialog.findChildren(QLineEdit)
        config_alias = next(edit for edit in edits if edit.text() == "old-runtime-alias")
        config_alias.setText("cluster-runtime")
        proxy_command = next(edit for edit in edits if edit.placeholderText() == "ssh -W %h:%p gateway")
        proxy_command.setText("ssh -W %h:%p login-node")
        proxy_jump = next(edit for edit in edits if edit.placeholderText() == "gateway")
        proxy_jump.setText("login-node")
        return QDialog.Accepted

    with patch(
        "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
        return_value=settings_store,
    ), patch(
        "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
        return_value=servers_path,
    ), patch(
        "jobdesk_app.gui.pages.settings_servers_page.load_servers",
        side_effect=lambda: load_servers_from_path(servers_path),
    ), patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_ssh_access):
        page = SettingsServersPage(MagicMock(), lambda message: None, lambda message: None)
        qtbot.addWidget(page)
        page.server_table.selectRow(0)
        page._edit_server()

    ssh_access = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]["hpc"]["ssh_access"]
    assert ssh_access["config_alias"] == "cluster-runtime"
    assert ssh_access["proxy_command"] == "ssh -W %h:%p login-node"
    assert ssh_access["proxy_jump"] == "login-node"
    assert ssh_access["hidden_ssh_key"] == "preserved"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
python -m pytest tests/test_settings_servers_page.py::test_edit_server_saves_external_terminal_fields -q --basetemp .pytest_tmp_terminal_settings
```

Expected: fail because the dialog does not expose terminal or SSH access fields.

- [ ] **Step 3: Add helper methods in settings page**

In `SettingsServersPage`, add:

```python
    def _add_external_tools_fields(self, form, tools: dict) -> dict:
        provider = QComboBox()
        provider.addItems(["windows_terminal", "putty"])
        current = str(tools.get("terminal_provider", "windows_terminal"))
        idx = provider.findText(current)
        if idx >= 0:
            provider.setCurrentIndex(idx)
        ssh_alias = QLineEdit(str(tools.get("ssh_alias", "")))
        ssh_alias.setPlaceholderText("OpenSSH alias")
        putty_session = QLineEdit(str(tools.get("putty_session", "")))
        putty_session.setPlaceholderText("PuTTY saved session")
        form.addRow(tr("Terminal:", self._language), provider)
        form.addRow(tr("SSH Alias:", self._language), ssh_alias)
        form.addRow(tr("PuTTY Session:", self._language), putty_session)
        return {
            "terminal_provider": provider,
            "ssh_alias": ssh_alias,
            "putty_session": putty_session,
        }

    @staticmethod
    def _external_tools_dict(widgets: dict, existing: dict | None = None) -> dict:
        result = dict(existing or {})
        result.update({
            "terminal_provider": widgets["terminal_provider"].currentText(),
            "ssh_alias": widgets["ssh_alias"].text().strip(),
            "putty_session": widgets["putty_session"].text().strip(),
        })
        return result

    def _add_ssh_access_fields(self, form, access: dict) -> dict:
        config_alias = QLineEdit(str(access.get("config_alias", "")))
        config_alias.setPlaceholderText("OpenSSH config alias")
        proxy_command = QLineEdit(str(access.get("proxy_command", "")))
        proxy_command.setPlaceholderText("ssh -W %h:%p gateway")
        proxy_jump = QLineEdit(str(access.get("proxy_jump", "")))
        proxy_jump.setPlaceholderText("gateway")
        form.addRow(tr("SSH Config Alias:", self._language), config_alias)
        form.addRow(tr("ProxyCommand:", self._language), proxy_command)
        form.addRow(tr("ProxyJump:", self._language), proxy_jump)
        return {
            "config_alias": config_alias,
            "proxy_command": proxy_command,
            "proxy_jump": proxy_jump,
        }

    @staticmethod
    def _ssh_access_dict(widgets: dict, existing: dict | None = None) -> dict:
        result = dict(existing or {})
        result.update({
            "config_alias": widgets["config_alias"].text().strip(),
            "proxy_command": widgets["proxy_command"].text().strip(),
            "proxy_jump": widgets["proxy_jump"].text().strip(),
        })
        return result
```

Call `_add_external_tools_fields()` in both `_edit_server()` and `_add_server()` after scheduler fields:

```python
        external_widgets = self._add_external_tools_fields(form, srv.get("external_tools", {}) or {})
        ssh_access_widgets = self._add_ssh_access_fields(form, srv.get("ssh_access", {}) or {})
```

For add-server:

```python
        external_widgets = self._add_external_tools_fields(form, {})
        ssh_access_widgets = self._add_ssh_access_fields(form, {})
```

When saving edit-server:

```python
        existing["external_tools"] = self._external_tools_dict(
            external_widgets,
            srv.get("external_tools", {}) or {},
        )
        existing["ssh_access"] = self._ssh_access_dict(
            ssh_access_widgets,
            srv.get("ssh_access", {}) or {},
        )
```

When saving add-server:

```python
            "external_tools": self._external_tools_dict(external_widgets),
            "ssh_access": self._ssh_access_dict(ssh_access_widgets),
```

- [ ] **Step 4: Add translations**

Add to `ZH` in `src/jobdesk_app/gui/i18n.py`:

```python
    "Terminal:": "\u7ec8\u7aef:",
    "SSH Alias:": "SSH \u522b\u540d:",
    "PuTTY Session:": "PuTTY \u4f1a\u8bdd:",
    "SSH Config Alias:": "SSH \u914d\u7f6e\u522b\u540d:",
    "ProxyCommand:": "ProxyCommand:",
    "ProxyJump:": "ProxyJump:",
```

- [ ] **Step 5: Run settings tests**

Run:

```powershell
python -m pytest tests/test_settings_servers_page.py -q --basetemp .pytest_tmp_terminal_settings
```

Expected: pass.

- [ ] **Step 6: Commit**

```powershell
git add src/jobdesk_app/gui/pages/settings_servers_page.py src/jobdesk_app/gui/i18n.py tests/test_settings_servers_page.py
git commit -m "Expose external terminal settings"
```

---

### Task 4: Add Runtime SSH Config and ProxyCommand Support

**Files:**
- Modify: `src/jobdesk_app/remote/ssh.py`
- Test: `tests/test_remote_ssh.py`

- [ ] **Step 1: Write failing runtime SSH tests**

Add these tests under `TestSSHClientWrapper` in `tests/test_remote_ssh.py`:

```python
    def test_connect_uses_ssh_config_alias_for_runtime_connection(self, tmp_path):
        config_path = tmp_path / "config"
        config_path.write_text(
            "Host cluster-a\n"
            "  HostName cluster.example.edu\n"
            "  User chemist\n"
            "  Port 2200\n"
            "  IdentityFile ~/.ssh/id_cluster\n",
            encoding="utf-8",
        )
        server = ServerConfig(
            server_id="hpc",
            host="ignored.example.edu",
            username="ignored",
            key_path=None,
            ssh_access={"config_alias": "cluster-a"},
        )

        with patch("paramiko.SSHClient") as mock_client_class, \
             patch("jobdesk_app.remote.ssh._ssh_config_path", return_value=config_path), \
             patch("jobdesk_app.remote.ssh.os.path.expanduser", side_effect=lambda value: value.replace("~", "C:/Users/me")), \
             patch.object(MockSSHWrapper, "_resolve_key", return_value=MagicMock(spec=paramiko.PKey)) as resolve_key:
            mock_client_class.return_value = MagicMock()
            MockSSHWrapper(server).connect()

        connect_kwargs = mock_client_class.return_value.connect.call_args.kwargs
        assert connect_kwargs["hostname"] == "cluster.example.edu"
        assert connect_kwargs["username"] == "chemist"
        assert connect_kwargs["port"] == 2200
        resolve_key.assert_called_once_with("C:/Users/me/.ssh/id_cluster")

    def test_connect_uses_proxy_command_when_configured(self):
        server = ServerConfig(
            server_id="hpc",
            host="compute.internal",
            username="chemist",
            key_path=None,
            ssh_access={"proxy_command": "ssh -W %h:%p login-node"},
        )
        proxy = MagicMock()

        with patch("paramiko.SSHClient") as mock_client_class, \
             patch("jobdesk_app.remote.ssh.paramiko.ProxyCommand", return_value=proxy) as proxy_command:
            mock_client_class.return_value = MagicMock()
            MockSSHWrapper(server).connect()

        proxy_command.assert_called_once_with("ssh -W compute.internal:22 login-node")
        connect_kwargs = mock_client_class.return_value.connect.call_args.kwargs
        assert connect_kwargs["sock"] is proxy

    def test_connect_rejects_proxy_jump_without_runtime_proxy_command_or_alias(self):
        server = ServerConfig(
            server_id="hpc",
            host="compute.internal",
            username="chemist",
            key_path=None,
            ssh_access={"proxy_jump": "login-node"},
        )

        with pytest.raises(SSHConnectionError, match="proxy_command"):
            MockSSHWrapper(server).connect()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/test_remote_ssh.py::TestSSHClientWrapper::test_connect_uses_ssh_config_alias_for_runtime_connection tests/test_remote_ssh.py::TestSSHClientWrapper::test_connect_uses_proxy_command_when_configured tests/test_remote_ssh.py::TestSSHClientWrapper::test_connect_rejects_proxy_jump_without_runtime_proxy_command_or_alias -q --basetemp .pytest_tmp_ssh_access
```

Expected: fail because `ssh_access` is not used by `SSHClientWrapper.connect()`.

- [ ] **Step 3: Add SSH config resolution helpers**

Add these helpers near the module-level constants in `src/jobdesk_app/remote/ssh.py`:

```python
def _ssh_config_path() -> Path:
    return Path(os.path.expanduser("~/.ssh/config"))


def _load_ssh_config_lookup(alias: str) -> dict[str, object]:
    path = _ssh_config_path()
    if not alias or not path.is_file():
        return {}
    config = paramiko.SSHConfig()
    with path.open("r", encoding="utf-8") as handle:
        config.parse(handle)
    return config.lookup(alias)


def _first_identity_file(lookup: dict[str, object]) -> str:
    value = lookup.get("identityfile")
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return ""


def _proxy_command_text(template: str, host: str, port: int) -> str:
    return template.replace("%h", host).replace("%p", str(port))
```

- [ ] **Step 4: Apply resolved connection settings in `connect()`**

In `SSHClientWrapper.connect()`, before building `connect_kwargs`, add:

```python
        ssh_lookup = _load_ssh_config_lookup(self._server.ssh_access.config_alias)
        hostname = str(ssh_lookup.get("hostname") or self._server.host)
        username = str(ssh_lookup.get("user") or self._server.username)
        port = int(ssh_lookup.get("port") or self._server.port)
        key_path = self._server.key_path or _first_identity_file(ssh_lookup)
        proxy_command = self._server.ssh_access.proxy_command.strip()
        if self._server.ssh_access.proxy_jump.strip() and not proxy_command and not self._server.ssh_access.config_alias.strip():
            raise SSHConnectionError(
                "proxy_jump requires ssh_access.proxy_command or ssh_access.config_alias",
                host=self._server.host,
                port=self._server.port,
            )
```

Change `connect_kwargs` to use the resolved values:

```python
        connect_kwargs: dict[str, Any] = {
            "hostname": hostname,
            "port": port,
            "username": username,
            "timeout": self._timeout,
            "banner_timeout": self._timeout,
        }
        if proxy_command:
            connect_kwargs["sock"] = paramiko.ProxyCommand(_proxy_command_text(proxy_command, hostname, port))
```

Change key handling from:

```python
            key_path = self._server.key_path
```

to use the already resolved `key_path` local variable:

```python
            if key_path:
                expanded = os.path.expanduser(key_path)
                pkey = self._resolve_key(expanded)
                connect_kwargs["pkey"] = pkey
```

Update error host/port arguments in the three `except` blocks to use `hostname` and `port`.

- [ ] **Step 5: Run targeted SSH tests**

Run:

```powershell
python -m pytest tests/test_remote_ssh.py -q --basetemp .pytest_tmp_ssh_access
```

Expected: pass.

- [ ] **Step 6: Commit**

```powershell
git add src/jobdesk_app/remote/ssh.py tests/test_remote_ssh.py
git commit -m "Support SSH config aliases and proxy commands"
```

---

### Task 5: Add Runs/Results Terminal Actions

**Files:**
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Modify: `src/jobdesk_app/gui/i18n.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write failing GUI tests**

Add these tests under `TestRunsPage` in `tests/test_gui_behavior.py`:

```python
    def test_context_menu_includes_terminal_actions(self, runs_page):
        labels = [label for label, _callback in runs_page._build_context_actions()]

        assert "Open Terminal Here" in labels
        assert "Copy SSH Command" in labels
        assert "Copy cd Command" in labels

    def test_open_terminal_here_launches_selected_run_directory(self, runs_page, tmp_path):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidgetItem

        from jobdesk_app.services.run_service import RunRecord

        record = RunRecord(
            run_id="260607-001",
            server_id="hpc",
            remote_dir="/scratch/jobs",
            command_template="orca {name}",
            max_parallel=1,
            mode="selected_files",
            created_at="now",
            run_dir=tmp_path / "runs" / "260607-001",
            manifest_path=tmp_path / "runs" / "260607-001" / "manifest.tsv",
            batch_path=tmp_path / "runs" / "260607-001" / "batch.json",
        )
        runs_page.table.setRowCount(1)
        item = QTableWidgetItem(record.run_id)
        item.setData(Qt.UserRole, record)
        runs_page.table.setItem(0, 0, item)
        runs_page.table.selectRow(0)

        server = MagicMock()
        servers = MagicMock(servers={"hpc": server})
        launch = MagicMock(user_visible_command="wt new-tab ...")

        with patch("jobdesk_app.gui.pages.runs_results_page.load_servers", return_value=servers), \
             patch("jobdesk_app.gui.pages.runs_results_page.build_terminal_launch", return_value=launch) as build, \
             patch("jobdesk_app.gui.pages.runs_results_page.launch_terminal") as launcher:
            runs_page._open_terminal_here()

        build.assert_called_once()
        assert build.call_args.args[0] is server
        assert build.call_args.args[1] == "/scratch/jobs/.jobdesk_runs/260607-001"
        launcher.assert_called_once_with(launch)

    def test_copy_cd_command_uses_selected_run_directory(self, runs_page, tmp_path):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QTableWidgetItem

        from jobdesk_app.services.run_service import RunRecord

        record = RunRecord(
            run_id="260607-001",
            server_id="hpc",
            remote_dir="/scratch/jobs",
            command_template="orca {name}",
            max_parallel=1,
            mode="selected_files",
            created_at="now",
            run_dir=tmp_path / "runs" / "260607-001",
            manifest_path=tmp_path / "runs" / "260607-001" / "manifest.tsv",
            batch_path=tmp_path / "runs" / "260607-001" / "batch.json",
        )
        runs_page.table.setRowCount(1)
        item = QTableWidgetItem(record.run_id)
        item.setData(Qt.UserRole, record)
        runs_page.table.setItem(0, 0, item)
        runs_page.table.selectRow(0)

        runs_page._copy_cd_command()

        assert QApplication.clipboard().text() == "cd /scratch/jobs/.jobdesk_runs/260607-001"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::TestRunsPage::test_context_menu_includes_terminal_actions tests/test_gui_behavior.py::TestRunsPage::test_open_terminal_here_launches_selected_run_directory tests/test_gui_behavior.py::TestRunsPage::test_copy_cd_command_uses_selected_run_directory -q --basetemp .pytest_tmp_terminal_runs
```

Expected: fail because methods and actions do not exist.

- [ ] **Step 3: Add imports in `runs_results_page.py`**

Add:

```python
import tempfile
```

Add service imports:

```python
from ...services.external_terminal import (
    build_cd_command,
    build_terminal_launch,
    launch_terminal,
)
```

- [ ] **Step 4: Add context menu actions**

Modify `_build_context_actions()` to include:

```python
            (tr("Open Terminal Here", self._language), self._open_terminal_here),
            (tr("Copy SSH Command", self._language), self._copy_ssh_command),
            (tr("Copy cd Command", self._language), self._copy_cd_command),
```

Place them after `Show Paths` or before `Open Results`. Keep existing actions unchanged.

- [ ] **Step 5: Add helper methods**

Add these methods to `RunsResultsPage`:

```python
    def _selected_remote_run_dir(self) -> str | None:
        record = self._selected_record()
        if record is None:
            self._status_cb(tr("Select one run first", self._language))
            return None
        return remote_run_dir(record.remote_dir, record.run_id)

    def _open_terminal_here(self):
        record = self._selected_record()
        if record is None:
            self._status_cb(tr("Select one run first", self._language))
            return
        try:
            server = load_servers().servers[record.server_id]
            launch = build_terminal_launch(
                server,
                remote_run_dir(record.remote_dir, record.run_id),
                temp_dir=Path(tempfile.gettempdir()) / "jobdesk_terminal",
            )
            launch_terminal(launch)
            self._status_cb(tr("Terminal opened", self._language))
        except Exception as exc:
            self._status_cb(tr("Open terminal failed: {e}", self._language, e=exc))

    def _copy_ssh_command(self):
        record = self._selected_record()
        if record is None:
            self._status_cb(tr("Select one run first", self._language))
            return
        try:
            from PySide6.QtWidgets import QApplication
            server = load_servers().servers[record.server_id]
            launch = build_terminal_launch(
                server,
                remote_run_dir(record.remote_dir, record.run_id),
                temp_dir=Path(tempfile.gettempdir()) / "jobdesk_terminal",
            )
            QApplication.clipboard().setText(launch.user_visible_command)
            self._status_cb(tr("SSH command copied", self._language))
        except Exception as exc:
            self._status_cb(tr("Copy SSH command failed: {e}", self._language, e=exc))

    def _copy_cd_command(self):
        remote_dir = self._selected_remote_run_dir()
        if remote_dir is None:
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(build_cd_command(remote_dir))
        self._status_cb(tr("cd command copied", self._language))
```

- [ ] **Step 6: Add translations**

Add to `ZH`:

```python
    "Open Terminal Here": "\u5728\u6b64\u6253\u5f00\u7ec8\u7aef",
    "Copy SSH Command": "\u590d\u5236 SSH \u547d\u4ee4",
    "Copy cd Command": "\u590d\u5236 cd \u547d\u4ee4",
    "Select one run first": "\u8bf7\u5148\u9009\u62e9\u4e00\u4e2a\u8fd0\u884c",
    "Terminal opened": "\u7ec8\u7aef\u5df2\u6253\u5f00",
    "Open terminal failed: {e}": "\u6253\u5f00\u7ec8\u7aef\u5931\u8d25: {e}",
    "SSH command copied": "SSH \u547d\u4ee4\u5df2\u590d\u5236",
    "Copy SSH command failed: {e}": "\u590d\u5236 SSH \u547d\u4ee4\u5931\u8d25: {e}",
    "cd command copied": "cd \u547d\u4ee4\u5df2\u590d\u5236",
```

- [ ] **Step 7: Run targeted GUI tests**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::TestRunsPage -q --basetemp .pytest_tmp_terminal_runs
```

Expected: pass.

- [ ] **Step 8: Commit**

```powershell
git add src/jobdesk_app/gui/pages/runs_results_page.py src/jobdesk_app/gui/i18n.py tests/test_gui_behavior.py
git commit -m "Add run terminal actions"
```

---

### Task 6: Defer Runs/Results Activation Work

**Files:**
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write failing delayed-activation tests**

Add these tests under `TestRunsPage` in `tests/test_gui_behavior.py`:

```python
    def test_on_activated_defers_refresh_and_monitor_start(self, runs_page, qtbot):
        from jobdesk_app.services.gui_settings import GuiSettings

        settings = GuiSettings(auto_refresh_interval=15)
        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as store, \
             patch.object(runs_page, "refresh_run_list") as refresh, \
             patch.object(runs_page, "_start_monitoring") as monitor:
            store.return_value.load.return_value = settings

            runs_page.on_activated()

            refresh.assert_not_called()
            monitor.assert_not_called()
            assert runs_page._refresh_timer.isActive()
            qtbot.waitUntil(lambda: refresh.called and monitor.called, timeout=1000)

    def test_shutdown_stops_pending_activation_timer(self, runs_page):
        runs_page._activation_timer.start(1000)

        runs_page.shutdown()

        assert not runs_page._activation_timer.isActive()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::TestRunsPage::test_on_activated_defers_refresh_and_monitor_start tests/test_gui_behavior.py::TestRunsPage::test_shutdown_stops_pending_activation_timer -q --basetemp .pytest_tmp_activation
```

Expected: fail because `on_activated()` performs refresh/monitor startup immediately and `_activation_timer` does not exist.

- [ ] **Step 3: Add activation timer in `RunsResultsPage.__init__()`**

After `_preview_timer` setup, add:

```python
        self._activation_timer = QTimer(self)
        self._activation_timer.setSingleShot(True)
        self._activation_timer.timeout.connect(self._run_deferred_activation)
```

- [ ] **Step 4: Replace eager activation work**

Replace `on_activated()` with:

```python
    def on_activated(self):
        settings = GuiSettingsStore().load()
        self._language = settings.language
        self._refresh_timer.setInterval(settings.auto_refresh_interval * 1000)
        self._refresh_timer.start()
        self._activation_timer.start(0)
```

Add:

```python
    def _run_deferred_activation(self):
        if self._shutting_down:
            return
        self.refresh_run_list()
        self._start_monitoring()
```

- [ ] **Step 5: Stop timer during shutdown**

In `shutdown()`, after stopping `_preview_timer`, add:

```python
        self._activation_timer.stop()
```

- [ ] **Step 6: Run targeted GUI tests**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::TestRunsPage -q --basetemp .pytest_tmp_activation
```

Expected: pass.

- [ ] **Step 7: Commit**

```powershell
git add src/jobdesk_app/gui/pages/runs_results_page.py tests/test_gui_behavior.py
git commit -m "Defer Runs page activation work"
```

---

### Task 7: Integration Verification and Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/USER_GUIDE.md`

- [ ] **Step 1: Add user documentation**

Add a short section to `docs/USER_GUIDE.md`:

~~~markdown
## Open a Run in an External Terminal

Runs/Results provides `Open Terminal Here` for the selected run. JobDesk opens
an external terminal and starts the shell in the remote run directory:

```text
<remote_dir>/.jobdesk_runs/<run_id>
```

Windows Terminal uses OpenSSH. For best results, configure an alias in
`~/.ssh/config` and set `external_tools.ssh_alias` in `servers.yaml`.

PuTTY uses a saved session. Configure the session in PuTTY first, then set
`external_tools.terminal_provider: putty` and
`external_tools.putty_session: <session name>` in `servers.yaml`.

JobDesk does not save SSH passwords and does not pass passwords on the command
line. Use key authentication, `ssh-agent`, Pageant, or an interactive prompt.
~~~

Add the YAML example:

```yaml
servers:
  hpc:
    host: cluster.example.edu
    port: 22
    username: chemist
    auth_method: key
    ssh_access:
      config_alias: cluster-a
      proxy_command: ""
      proxy_jump: ""
    external_tools:
      terminal_provider: windows_terminal
      ssh_alias: cluster-a
      putty_session: cluster-a-putty
```

Add this paragraph after the YAML example:

~~~markdown
`ssh_access.config_alias` is used by JobDesk's own SSH/SFTP connections.
`external_tools.ssh_alias` is used when opening an external terminal. They can
be the same alias, but they are separate so a user can keep runtime transfers
on Paramiko settings while opening a terminal with a different saved profile.
If a cluster requires a jump host, prefer OpenSSH config. For Paramiko runtime
connections, set `ssh_access.proxy_command`, for example
`ssh -W %h:%p login-node`.
~~~

- [ ] **Step 2: Run focused tests**

Run:

```powershell
python -m pytest tests/test_config_loader.py tests/test_external_terminal.py tests/test_remote_ssh.py tests/test_settings_servers_page.py tests/test_gui_behavior.py::TestRunsPage -q --basetemp .pytest_tmp_terminal_focused
```

Expected: pass.

- [ ] **Step 3: Run static checks**

Run:

```powershell
python -m ruff check .
python -m mypy src
```

Expected: pass.

- [ ] **Step 4: Run full test suite**

Run:

```powershell
python -m pytest tests -q --basetemp .pytest_tmp_terminal_full
```

Expected: pass.

- [ ] **Step 5: Check diff hygiene**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only intended files changed.

- [ ] **Step 6: Commit docs and final fixes**

```powershell
git add README.md docs/USER_GUIDE.md
git commit -m "Document external terminal integration"
```

---

## Manual Smoke Test

Use a server profile with OpenSSH alias:

```yaml
servers:
  wsl:
    host: 127.0.0.1
    port: 22
    username: root
    auth_method: key
    ssh_access:
      config_alias: wsl
      proxy_command: ""
      proxy_jump: ""
    external_tools:
      terminal_provider: windows_terminal
      ssh_alias: wsl
```

Then:

- Start `jobdesk-gui`.
- Open Runs/Results.
- Select an existing run.
- Right-click and choose `Copy cd Command`.
- Verify clipboard equals `cd <remote_dir>/.jobdesk_runs/<run_id>`.
- Right-click and choose `Copy SSH Command`.
- Verify the command contains `wt`, `ssh -t`, the alias, and the remote run directory.
- Right-click and choose `Open Terminal Here`.
- Verify Windows Terminal opens and the remote shell starts in the run directory.

Use a PuTTY saved session:

```yaml
external_tools:
  terminal_provider: putty
  putty_session: cluster-a-putty
```

Then:

- Select the same run.
- Choose `Open Terminal Here`.
- Verify PuTTY opens using the saved session.
- Verify the shell starts in `<remote_dir>/.jobdesk_runs/<run_id>`.

## Risk Notes

- Windows Terminal may not be installed or `wt` may not be on PATH. The GUI should surface the launch exception and leave `Copy SSH Command` available.
- PuTTY may not be installed or `putty.exe` may not be on PATH. This is acceptable for the first version; users can use Windows Terminal or copy commands.
- PuTTY startup command files live under the system temp directory. They contain only `cd` and `exec shell` commands, not secrets.
- Remote paths are shell-quoted before insertion into startup commands.
- Password and OTP automation remain intentionally unsupported.
