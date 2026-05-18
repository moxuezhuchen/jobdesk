"""Projects 页面 — 打开项目、显示基本信息、配置 RuntimeBinding。"""

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QLabel, QFileDialog, QHeaderView, QTableWidgetItem,
    QDialog, QFormLayout, QComboBox, QLineEdit, QSpinBox,
    QDialogButtonBox, QMessageBox,
)

from ...services.project_service import create_project_context
from ...services.project_wizard import (
    WizardDiscoverySpec,
    WizardProfileSpec,
    WizardProjectSpec,
    create_project_from_wizard,
)
from ...config.runtime import RuntimeBindingStore
from ...config.servers import load_servers, get_default_servers_path
from ...config.schema import RuntimeBinding
from ..table_models import display_dict_as_table


def build_project_info(ctx, binding_store) -> dict[str, str]:
    profiles = list(ctx.project_config.execution_profiles.keys())
    binding_lines = []
    for ep_name in profiles:
        b = binding_store.get_binding(ctx.project_id, ep_name)
        if b:
            binding_lines.append(f"{ep_name}: bound to {b.server_id} ({b.remote_work_dir})")
        else:
            binding_lines.append(f"{ep_name}: NOT BOUND")
    return {
        "Project Name": ctx.project_name,
        "Project ID": ctx.project_id,
        "Execution Profiles": ", ".join(profiles) if profiles else "(none)",
        "Binding Status": "\n".join(binding_lines) if binding_lines else "(none)",
        "Local Input Dir": str(ctx.local_input_dir),
        "Local Result Dir": str(ctx.local_result_dir),
    }


class _BindingDialog(QDialog):
    """极简 RuntimeBinding 配置对话框。"""

    def __init__(self, project_id: str, profiles: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Configure Runtime Binding - {project_id}")
        self.setMinimumWidth(450)
        layout = QFormLayout(self)

        self._profile_combo = QComboBox()
        self._profile_combo.addItems(profiles)
        layout.addRow("Execution Profile:", self._profile_combo)

        self._server_combo = QComboBox()
        try:
            servers = load_servers(get_default_servers_path())
            for sid in sorted(servers.servers.keys()):
                srv = servers.servers[sid]
                self._server_combo.addItem(f"{sid} ({srv.host})", sid)
        except Exception:
            self._server_combo.addItem("(no servers loaded)", "")
        layout.addRow("Server:", self._server_combo)

        self._remote_dir = QLineEdit()
        self._remote_dir.setPlaceholderText("/home/user/project/g16")
        layout.addRow("Remote Work Dir:", self._remote_dir)

        self._max_parallel = QSpinBox()
        self._max_parallel.setRange(0, 9999)
        self._max_parallel.setSpecialValueText("(use profile default)")
        self._max_parallel.setValue(0)
        layout.addRow("Max Parallel:", self._max_parallel)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def get_result(self) -> tuple[str, str, str, int | None]:
        profile = self._profile_combo.currentText()
        server = self._server_combo.currentData()
        remote_dir = self._remote_dir.text().strip()
        mp = self._max_parallel.value()
        return profile, server, remote_dir, (mp if mp > 0 else None)


class ProjectWizardDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New JobDesk Project")
        self.setMinimumWidth(560)
        layout = QFormLayout(self)

        root_row = QHBoxLayout()
        self._project_root = QLineEdit()
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_project_root)
        root_row.addWidget(self._project_root, 1)
        root_row.addWidget(browse_btn)
        layout.addRow("Project Directory:", root_row)

        self._project_id = QLineEdit()
        self._project_id.setPlaceholderText("my_project")
        layout.addRow("Project ID:", self._project_id)

        self._project_name = QLineEdit()
        self._project_name.setPlaceholderText("My Project")
        layout.addRow("Project Name:", self._project_name)

        self._profile_name = QLineEdit("shell")
        layout.addRow("Execution Profile:", self._profile_name)

        self._entry_glob = QLineEdit("*.sh")
        layout.addRow("Entry Glob:", self._entry_glob)

        self._command = QLineEdit("bash {entry_name}")
        layout.addRow("Command:", self._command)

        self._max_parallel = QSpinBox()
        self._max_parallel.setRange(1, 9999)
        self._max_parallel.setValue(4)
        layout.addRow("Max Parallel:", self._max_parallel)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def _browse_project_root(self):
        proj_dir = QFileDialog.getExistingDirectory(self, "Select New Project Directory")
        if proj_dir:
            self._project_root.setText(proj_dir)

    def get_spec(self) -> WizardProjectSpec:
        profile_name = self._profile_name.text().strip()
        return WizardProjectSpec(
            project_id=self._project_id.text().strip(),
            project_name=self._project_name.text().strip(),
            project_root=Path(self._project_root.text().strip()),
            discoveries=[
                WizardDiscoverySpec(
                    name=f"{profile_name}_jobs",
                    mode="flat_single",
                    entry_glob=self._entry_glob.text().strip(),
                    execution_profile=profile_name,
                )
            ],
            profiles=[
                WizardProfileSpec(
                    name=profile_name,
                    label=profile_name,
                    command=self._command.text().strip(),
                    max_parallel=self._max_parallel.value(),
                )
            ],
        )


class ProjectsPage(QWidget):
    def __init__(self, state, log_cb, status_cb, on_opened_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        self._on_opened = on_opened_cb
        self._binding_store = RuntimeBindingStore()
        layout = QVBoxLayout(self)

        title = QLabel("Projects")
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        layout.addWidget(title)

        self.info_table = QTableWidget()
        layout.addWidget(self.info_table)

        btn_row = QHBoxLayout()
        new_btn = QPushButton("New Project")
        new_btn.clicked.connect(self._new_project)
        btn_row.addWidget(new_btn)
        open_btn = QPushButton("Open Project")
        open_btn.clicked.connect(self._open_project)
        btn_row.addWidget(open_btn)
        self.bind_btn = QPushButton("Configure Binding")
        self.bind_btn.setEnabled(False)
        self.bind_btn.clicked.connect(self._configure_binding)
        btn_row.addWidget(self.bind_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()

    def _set_project_context(self, proj_dir: Path):
        self._clear_project()

        try:
            ctx = create_project_context(proj_dir)
        except FileNotFoundError as e:
            self._status_cb(f"Error: {e}")
            return None
        except Exception as e:
            self._status_cb(f"Error: {type(e).__name__}: {e}")
            return None

        self.state.current_project_context = ctx
        self.state.current_project_root = proj_dir
        self.bind_btn.setEnabled(True)
        self._refresh_project_info()
        return ctx

    def _clear_project(self):
        self.state.current_project_context = None
        self.state.current_project_root = None
        self.state.current_batch_id = None
        self.state.current_manifest_path = None
        self.info_table.clear()
        self.info_table.setRowCount(0)
        self.info_table.setColumnCount(0)
        self.bind_btn.setEnabled(False)

    def _new_project(self):
        dlg = ProjectWizardDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return

        try:
            result = create_project_from_wizard(dlg.get_spec())
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create project: {e}")
            return

        ctx = self._set_project_context(result.project_root)
        if not ctx:
            return

        self._log(f"Project created: {ctx.project_name} (id={ctx.project_id})")
        self._status_cb(f"Project created: {ctx.project_name}")
        self._on_opened()

    def _open_project(self):
        proj_dir = QFileDialog.getExistingDirectory(
            self, "Select Project Directory (containing project.yaml)"
        )
        if not proj_dir:
            return
        proj_dir = Path(proj_dir)
        if not (proj_dir / "project.yaml").exists():
            self._status_cb(f"Error: project.yaml not found in {proj_dir}")
            return

        self._clear_project()

        ctx = self._set_project_context(proj_dir)
        if not ctx:
            return

        self._log(f"Project opened: {ctx.project_name} (id={ctx.project_id})")
        self._status_cb(f"Project: {ctx.project_name}")

        self._on_opened()

    def _refresh_project_info(self):
        ctx = self.state.current_project_context
        if not ctx:
            return
        display_dict_as_table(self.info_table, build_project_info(ctx, self._binding_store))

    def _configure_binding(self):
        ctx = self.state.current_project_context
        if not ctx: return
        profiles = list(ctx.project_config.execution_profiles.keys())
        if not profiles:
            QMessageBox.warning(self, "No Profiles", "Project has no execution_profiles defined.")
            return

        dlg = _BindingDialog(ctx.project_id, profiles, self)
        if dlg.exec() != QDialog.Accepted:
            return

        profile, server, remote_dir, max_parallel = dlg.get_result()
        if not server:
            self._status_cb("Error: no server selected")
            return
        if not remote_dir:
            self._status_cb("Error: remote_work_dir is required")
            return

        try:
            binding = RuntimeBinding(
                server_id=server,
                remote_work_dir=remote_dir,
                max_parallel=max_parallel,
            )
            self._binding_store.save_binding(ctx.project_id, profile, binding)
            self._log(
                f"Binding saved: {ctx.project_id}/{profile}"
                f" -> server={server}, remote={remote_dir}"
                + (f", max_parallel={max_parallel}" if max_parallel else "")
            )
            self._refresh_project_info()
            self._status_cb(f"Binding saved for {profile}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save binding: {e}")
