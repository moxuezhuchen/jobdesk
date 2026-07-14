from __future__ import annotations

from collections.abc import Iterable


def validate_server_id_change(existing_ids: Iterable[str], old_id: str | None, new_id: str) -> str | None:
    candidate = new_id.strip()
    if not candidate:
        return "Server ID is required"
    normalized_existing = {sid.strip() for sid in existing_ids if sid.strip()}
    if old_id is not None and candidate == old_id:
        return None
    if candidate in normalized_existing:
        return f"Server ID already exists: {candidate}"
    return None


def build_scheduler_fields(form, dlg, sched: dict, language: str) -> dict:
    """Add scheduler resource widgets to a server dialog form; return widget dict."""
    from PySide6.QtWidgets import QComboBox, QLineEdit, QSpinBox

    from ..i18n import tr

    type_combo = QComboBox()
    type_combo.addItems(["nohup", "slurm", "pbs"])
    ti = type_combo.findText(str(sched.get("type", "nohup")))
    if ti >= 0:
        type_combo.setCurrentIndex(ti)
    cpus = QSpinBox()
    cpus.setRange(1, 4096)
    cpus.setValue(int(sched.get("default_cpus", 1)))
    mem = QSpinBox()
    mem.setRange(128, 4194304)
    mem.setValue(int(sched.get("default_memory_mb", 2048)))
    wall = QSpinBox()
    wall.setRange(1, 1051200)
    wall.setValue(int(sched.get("default_walltime_minutes", 1440)))
    partition = QLineEdit(str(sched.get("default_partition", "")))
    account = QLineEdit(str(sched.get("default_account", "")))
    widgets: dict = {"type": type_combo, "cpus": cpus, "mem": mem, "wall": wall,
                     "partition": partition, "account": account}

    def _toggle(*_):
        hpc = type_combo.currentText() != "nohup"
        for w in (partition, account, wall):
            w.setEnabled(hpc)
    type_combo.currentTextChanged.connect(_toggle)
    _toggle()

    form.addRow(tr("Scheduler:", language), type_combo)
    form.addRow("CPUs:", cpus)
    form.addRow(tr("Memory(MB):", language), mem)
    form.addRow(tr("Walltime:", language), wall)
    form.addRow(tr("Partition/Queue:", language), partition)
    form.addRow(tr("Account:", language), account)
    return widgets


def scheduler_dict(widgets: dict, existing: dict | None = None) -> dict:
    """Read scheduler widgets into a config dict, preserving hidden keys (gpus, extra_directives)."""
    result = dict(existing or {})
    result.update({
        "type": widgets["type"].currentText(),
        "default_cpus": widgets["cpus"].value(),
        "default_memory_mb": widgets["mem"].value(),
        "default_walltime_minutes": widgets["wall"].value(),
        "default_partition": widgets["partition"].text().strip(),
        "default_account": widgets["account"].text().strip(),
    })
    return result


def build_external_tools_fields(form, tools: dict, language: str) -> dict:
    from PySide6.QtWidgets import QComboBox, QLineEdit

    from ..i18n import tr

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
    terminal_path = QLineEdit(str(tools.get("terminal_path", "")))
    terminal_path.setPlaceholderText("Path to terminal executable")
    form.addRow(tr("Terminal:", language), provider)
    form.addRow(tr("Terminal Path:", language), terminal_path)
    form.addRow(tr("SSH Alias:", language), ssh_alias)
    form.addRow(tr("PuTTY Session:", language), putty_session)
    return {
        "terminal_provider": provider,
        "ssh_alias": ssh_alias,
        "putty_session": putty_session,
        "terminal_path": terminal_path,
    }


def external_tools_dict(widgets: dict, existing: dict | None = None) -> dict:
    result = dict(existing or {})
    result.update({
        "terminal_provider": widgets["terminal_provider"].currentText(),
        "ssh_alias": widgets["ssh_alias"].text().strip(),
        "putty_session": widgets["putty_session"].text().strip(),
        "terminal_path": widgets["terminal_path"].text().strip(),
    })
    return result


def build_ssh_access_fields(form, access: dict, language: str) -> dict:
    from PySide6.QtWidgets import QLineEdit

    from ..i18n import tr

    config_alias = QLineEdit(str(access.get("config_alias", "")))
    config_alias.setPlaceholderText("OpenSSH config alias")
    proxy_command = QLineEdit(str(access.get("proxy_command", "")))
    proxy_command.setPlaceholderText("ssh -W %h:%p gateway")
    proxy_jump = QLineEdit(str(access.get("proxy_jump", "")))
    proxy_jump.setPlaceholderText("gateway")
    form.addRow(tr("SSH Config Alias:", language), config_alias)
    form.addRow(tr("ProxyCommand:", language), proxy_command)
    form.addRow(tr("ProxyJump:", language), proxy_jump)
    return {
        "config_alias": config_alias,
        "proxy_command": proxy_command,
        "proxy_jump": proxy_jump,
    }


def ssh_access_dict(widgets: dict, existing: dict | None = None) -> dict:
    result = dict(existing or {})
    result.update({
        "config_alias": widgets["config_alias"].text().strip(),
        "proxy_command": widgets["proxy_command"].text().strip(),
        "proxy_jump": widgets["proxy_jump"].text().strip(),
    })
    return result
