# JobDesk User Guide

JobDesk 是 Windows 本地的科研计算工作台，通过 SSH 管理远程 Linux 服务器上的
Gaussian/ORCA 等计算任务。

无需创建 project.yaml。所有操作以当前本地目录 + 远端目录为中心。

## 基本工作流

1. 在 Settings 页配置服务器连接
2. 在 Files 页连接服务器，浏览远端目录
3. 选择远端文件（如 `.gjf`、`.inp`），设置命令模板（如 `g16 {name}`）
4. 点 Run → 自动生成 run 记录并提交到远端执行
5. Runs 页自动刷新状态、下载结果（通过 SSH tail -f 实时监听）
6. Results 页查看分析输出

## Submitting workflows (Phase 2 + Phase 10)

The Submit page drives **everything** workflow-related — there is no
longer a separate ConfFlow Builder / Input Builder / wizard. The page
embeds a single unified editor and the same Submit button routes the
graph through either the legacy confflow path or the new `dag` path,
depending on the topology you have drawn.

> **Phase 2.0 update:** the Submit page has been retired and replaced
> by two cooperating surfaces — a sidebar **Workflow** page (preset
> browser + save) and the modal **SubmitDialog** (auto-detected Single
> vs Workflow mode). See [Submitting calculations](#submitting-calculations-phase-20)
> for the dual-entry walkthrough. The text below is kept for reference
> until the next doc pass retires it.

### The unified editor

The editor is the **WorkflowGraphEditor** (`gui/nodegraph/`). The
legacy "ConfFlow Builder" tab (the Phase 14A `workflow_widget.py` /
`input_builder_widget.py` / `input_source_panel.py` triplet) was
retired in Phase 10.6 — the Submit page is the only place you build
workflows now.

You build a workflow visually:

1. Drag node kinds from the **node library** (left panel) onto the
   canvas. Available kinds: `XYZ_FILE` (input sentinel), `CONF_GEN`,
   `PRE_OPT`, `OPT`, `REFINE`, `SINGLE_POINT`, `FREQUENCY`, `TS`,
   `ADVANCED`, `OUTPUT` (terminal sentinel).
2. Hover a node in the library to see its **port names** in the
   tooltip — both incoming and outgoing ports are listed so you know
   what each kind can connect to.
3. Wire ports by dragging from a node's output to another node's
   input. The editor validates the wiring live (cycle detection,
   required-input checks, port-type compatibility).
4. Click a node to open the **properties panel** (right side). It
   shows the node's parameters plus a list of its **incoming edges**
   — i.e. which upstream steps feed into this one.
5. The page renders a live YAML preview as you edit.

### DAG mode (Phase 10)

The editor supports DAGs, not just linear chains:

- **Fan-out** — one node's output connects to multiple downstream
  nodes' inputs. For example, an `OPT` step that feeds both a
  `FREQUENCY` step and a `SINGLE_POINT` step.
- **Fan-in** — multiple upstream nodes' outputs converge into a
  single downstream node's **STRUCTURES** input. (Fan-in is only
  allowed on `STRUCTURES`-typed ports; the `STRUCTURE`-typed port
  on calc/confgen nodes accepts exactly one predecessor.)

To build a DAG:

1. Draw the parallel branches in the editor (e.g. Generate →
   {Optimize → Frequency, Optimize → SinglePoint}).
2. Drag a second wire from the upstream output to the second
   downstream input (fan-out). For fan-in, drag multiple wires
   into a `STRUCTURES` port.
3. The graph is acyclic by construction; the editor rejects cycles
   with a red issue marker before you can submit.

### Submit to Remote: linear vs DAG auto-detection

The Submit page inspects the per-step `inputs` arrays before
serialising the workflow:

- **Linear graph** (every step has empty `inputs` — i.e. the editor
  produced the Phase 1.6 / 14B chain shape) → uses the legacy
  `confflow` command path. The YAML is written to `workflow.yaml`
  next to the first XYZ and shipped to the remote server; the
  confflow engine runs the steps in declaration order.
- **DAG graph** (at least one step has a non-empty `inputs` list —
  i.e. you have fan-out / fan-in) → uses the new `kind="dag"` path.
  The same `workflow.yaml` is written but now contains per-step
  `inputs: [...]` arrays, and `RunSpec.workflow_kind` is
  `WorkflowKind.dag`. The confflow engine reads `StepConfig.inputs`
  via `graphlib.TopologicalSorter` (Phase 3 of the confflow engine)
  and walks the DAG accordingly. The remote command template is
  identical to the linear case — only the YAML shape differs.

The page's `_detect_payload_kind()` helper flips `kind` from
`"confflow"` to `"dag"` automatically; you don't need to choose
yourself.

### Workflow round-trip

The editor ↔ YAML bridge is faithful both ways:

- **Forward** (editor → YAML): `to_workflow_spec(graph)` writes a
  `WorkflowGraphPayload` with one dict per step. Each dict carries
  `name`, `type`, `params`, and an `inputs: [...]` list reflecting
  the fan-in / fan-out topology. The bridge sorts incoming step
  names by topological order so the YAML is byte-identical across
  runs.
- **Reverse** (YAML → editor): `from_workflow_spec(payload)` rebuilds
  a `NodeGraph` from the same per-step dict list, wires back the
  fan-in / fan-out edges from each step's `inputs` field, and
  injects a single `XYZ_FILE` sentinel at the front and a single
  `OUTPUT` sentinel at the end. Every "leaf" step (no outgoing calc
  edges) feeds the `OUTPUT` sentinel — for an N-sink DAG every sink
  gets attached.

This means a workflow saved from the editor loads back into the
editor with the same DAG topology (modulo UUID node-ids and
canvas positions, which are preserved when saving the template).

### Language toggle

The i18n toggle (zh-CN / en) on the Submit page and on the Runs /
Results page is unchanged from Phase 14B. Switch the language from
the Settings page; the editor and the activity log both pick up the
new strings on next render. See `src/jobdesk_app/gui/i18n.py` for
the catalogue.

### Runs / Results page — detail pane

The Runs / Results page has a per-run **detail pane** (right side)
that opens when you click a run in the list. It carries:

- **Analysis table** with named columns: Task, File, Program,
  Energy, Gibbs, ZPE, Imag. Freq., Diagnosis. (Column names are
  defined as module-level constants on `runs_results_page`; rows
  are built by a `_placeholder_analysis_row` helper so missing
  data renders as `—` instead of empty cells.)
- **Activity log** with a chronological feed of submit / refresh /
  download / analyse events for the run.
- **Downloaded files** list — the SFTP-pulled outputs under
  `results/<run_id>/<task_id>/`.
- **Remote-side work dir** path and the rendered command that was
  run.

### Related design notes

`docs/PHASE10_NODEGRAPH_DAG_PLAN.md` records the design intent for
Phase 10 (multi-port edges, fan-in / fan-out, `inputs: [...]`
list, `WorkflowKind.dag` separation). The end-to-end behaviour is
covered by `tests/test_nodegraph/` (bridge + properties + library
drag) and `tests/test_submit_use_case.py` (the `dag` path), and
re-exercised by `scripts/smoke_confflow_dag_round_trip.py` against
the real vendored confflow engine.

## 重要文件

```text
%APPDATA%/JobDesk/
  servers.yaml          # 服务器配置
  gui_settings.yaml     # GUI 设置（下载模式、并发等）
  runs/                 # 全局 run 记录
    <run_id>/
      run.json          # run 元数据
      manifest.tsv      # 任务清单与状态
```

本地工作目录（workspace）：
```text
<workspace>/
  results/
    <run_id>/
      <task_id>/        # 下载的输出文件
      analysis_preview.tsv
```

## Run ID 格式

Run ID 格式为 `YYMMDD-NNN`（如 `260519-001`），每天从 001 开始递增。

## 服务器配置

`%APPDATA%/JobDesk/servers.yaml`:

```yaml
servers:
  wcm:
    host: example.com
    port: 22
    username: user
    auth_method: key
    key_path: ~/.ssh/id_ed25519
    env_init_scripts:
      - /opt/g16/bsd/g16.profile
```

`env_init_scripts` 声明任务执行前需要 source 的脚本。

## 下载模式

Settings 页可按软件配置自动下载的文件模式：
- Gaussian: `*.log,*.chk`（默认）
- ORCA: `*.out,*.gbw`（默认）

系统根据命令模板自动识别软件类型。

## 状态自动更新

JobDesk 使用 SSH `tail -f` 监听远端 `events.log`：
- 任务开始运行 → 状态自动变为"运行中"
- 任务完成 → 自动刷新 + 下载 → 状态变为"已下载"
- 如需手动刷新：右键 run → "刷新状态"

## 命令模板变量

| 变量 | 含义 | 示例 |
|------|------|------|
| `{name}` | 文件名 | `mol.gjf` |
| `{stem}` | 不含扩展名 | `mol` |
| `{path}` | 完整路径 | `/root/uma/mol.gjf` |
| `{dir}` | 所在目录 | `/root/uma` |

所有变量自动 shell 转义。

## CLI 命令

```powershell
# 文件操作
jobdesk files list-remote <server_id> <remote_path>
jobdesk files upload <server_id> <local_path> <remote_path>
jobdesk files download <server_id> <remote_path> <local_path>

# 运行管理
jobdesk run create <workspace> --server <id> --remote-dir <path> --command "g16 {name}" --files <f1> <f2>
jobdesk run list <workspace>
jobdesk run submit <workspace> <run_id>
jobdesk run refresh <workspace> <run_id>
jobdesk run download <workspace> <run_id>
jobdesk run cancel <workspace> <run_id>
jobdesk run delete <workspace> <run_id>
jobdesk run retry <workspace> <run_id>
```

## 故障恢复

- 网络断开：monitor 自动重连，下次连接成功时补齐状态
- 应用关闭期间任务完成：重启后首次激活 Runs 页时自动检测
- 下载失败：状态保持 `remote_completed`，可手动右键刷新重试
## Open the Current Remote Directory in an External Terminal

Files provides `Open Terminal Here` beside the remote path. JobDesk opens an
external terminal and starts the shell in the current remote directory shown in
the remote path field:

```text
<current remote directory>
```

Windows Terminal uses OpenSSH. JobDesk opens a new tab in the most recently
used Windows Terminal window, or opens a new window if no Terminal window is
available. For best results, configure an alias in `~/.ssh/config` and set
`external_tools.ssh_alias` in `servers.yaml`.

PuTTY uses a saved session. Configure the session in PuTTY first, then set
`external_tools.terminal_provider: putty` and
`external_tools.putty_session: <session name>` in `servers.yaml`. If JobDesk
cannot find `putty.exe`, set `external_tools.terminal_path` to the executable
path.

JobDesk does not save SSH passwords and does not pass passwords on the command
line. Use key authentication, `ssh-agent`, Pageant, or an interactive prompt.

Example:

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
      terminal_path: ""
```

`ssh_access.config_alias` is used by JobDesk's own SSH/SFTP connections.
`external_tools.ssh_alias` is used when opening an external terminal. They can
be the same alias, but they are separate so a user can keep runtime transfers
on Paramiko settings while opening a terminal with a different saved profile.
If a cluster requires a jump host, prefer OpenSSH config. For Paramiko runtime
connections, set `ssh_access.proxy_command`, for example
`ssh -W %h:%p login-node`, or set `ssh_access.proxy_jump` for OpenSSH-style
single-hop or comma-separated jump hosts.

## Submitting calculations (Phase 2.0)

There are two ways to submit a calculation in JobDesk 2.0:

### Option 1 — Files page (recommended for one-off submissions)

1. Switch to the **Files** page.
2. Select one or more input files (`.xyz`, `.gjf`, `.inp`).
3. Click **[🚀 Submit]**. A modal opens.
4. Choose **Single** (Gaussian/ORCA direct run) or **Workflow** (preset-based multi-step). The dialog disables Single automatically when any `.xyz` is selected.
5. Pick a preset if Workflow, set charge / multiplicity / server, click **Submit ▶**.

### Option 2 — Workflow page (use a saved preset)

1. Switch to the **Workflow** page.
2. Browse built-in or user presets in the dropdown. Edit, save, or rename.
3. Click **[Use this preset for submit]** to switch to Files and open the Submit dialog with that preset pre-selected.

If the dialog opens with no files selected yet (either because you
arrived via the Workflow page or the Runs empty-state **Go to Submit**
button), the dialog shows an amber hint and disables **Submit ▶** until
you pick at least one input file. You can still browse presets and
server / charge / multiplicity while the dialog waits for files — the
mode radios are locked into Workflow-only in that state.

User presets live under `%APPDATA%/JobDesk/method_presets/<name>.yaml`
and are plain confflow YAML. The `MethodPresetStore` ships nine built-in
presets (see [Method Preset Store](#method-preset-store) in the
architecture notes); lookup precedence is **user > built-in**, matching
`analysis_profiles.py`.
