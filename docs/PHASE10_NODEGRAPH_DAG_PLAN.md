# Phase 10 — NodeGraph DAG 收官 + 收尾清理

> 范围:把 `gui/nodegraph/` 拉齐到 `confflow/workflow/engine.py` 已经支持的能力,然后做收尾清理。
> 起点:`a0f62ed` (Phase 1.6 bridge) + `26afe31` (Phase 2 submit page)。
> 引擎侧(`run_workflow` + `dag.py` + `StepConfig.inputs/outputs`)在 `1dff20f` 已经能跑 fan-out / fan-in;
> 本阶段只是让 **编辑器** 和 **提交路径** 跟上,并把 `gui/pages/` / `gui/widgets/` 里残留的 legacy widget 拆掉。

## 总览

| Phase | 目标 | 估时 | 风险 |
|---|---|---|---|
| **10.1** Bridge 多 successor 支持 (fan-out) | `to_workflow_spec` 接受 1→N 边,把多 successor 写进 `step.inputs` 数组 | 0.5d | 低 |
| **10.2** Editor 允许多条入/出边 | 放宽 `_assert_linear_topology` 在 model 层的限制;`canvas.py` 不再禁止拖多线 | 0.5d | 中 |
| **10.3** Library 增加多 INPUT_PORT / OUTPUT_PORT 节点 | 给 `CONF_GEN` (output) 和 `OPT` (input) 之外的端口拓扑暴露成可连线 | 0.5d | 中 |
| **10.4** Bridge 反向 (`from_workflow_spec`) 把 `inputs: [...]` 还原为多入边 | DAG 模板加载必须保留并行结构,不能塌缩成链 | 0.5d | 中 |
| **10.5** Submit 流程支持 `confflow_kind="dag"` | `submit_payload` 已经预留字段,本阶段把它实际接通 | 0.5d | 低 |
| **10.6** 删除 legacy widget (`CalculationWidget` / `InputBuilderWidget` / `WorkflowWidget`) | Phase 2 已经不再使用,本阶段彻底删除 | 0.5d | 低 |
| **10.7** Documentation pass + smoke | `docs/USER_GUIDE.md` 更新,confflow 反向端到端 smoke 跑通 | 1d | 低 |

合计 ~4 人天。

---

## Phase 10.1 — Bridge fan-out

**问题**: `spec_bridge._assert_linear_topology` 显式拒绝 `len(out_edges) > 1`;Phase 1.6 的提交路径完全跑不出 DAG。

**改动**:

1. `src/jobdesk_app/gui/nodegraph/spec_bridge.py`
   - 把 `_assert_linear_topology` 改成 `_assert_well_formed`,只检:
     - 没有自环 / 环(`CYCLE_DETECTED` 已经在 `graph.validate` 里检了)
     - `XYZ_FILE` 没有入边
     - `OUTPUT` 没有出边
     - 每个 calc/confgen 节点的每个 `STRUCTURE` 输入端口最多 1 条入边;`STRUCTURES` 入端口允许任意多条
   - `_build_step_dict(graph, node, step_name)` 返回的 dict 加 `inputs: [upstream_step_names]`,
     按上游节点的 step 顺序(线性 order)填;根步骤空 list,等价于"消费全局 input_xyz"
   - `to_yaml()` 通过 `WorkflowGraphPayload.to_yaml` 写出的 `steps:` 自动包含 `inputs`

2. `src/jobdesk_app/gui/nodegraph/__init__.py`
   - `from_workflow_spec` 反向逻辑(见 Phase 10.4)也一并从这一步就开公共信号
   - 加 `WorkflowGraphPayload` 上的 `to_yaml()` 注释说明 `inputs:` 现在存在

3. `tests/test_nodegraph/test_spec_bridge.py`
   - 新增 fixture: 两条并行的 PRE_OPT 分别从同一个 CONF_GEN 取输入 → 一个后续 SINGLE_POINT
   - 新增断言: `step["inputs"]` 数组精确匹配上游名字
   - 现有 linear 测试保持不变(回归)

**验收**:

```bash
pytest tests/test_nodegraph/test_spec_bridge.py -p no:cacheprovider
# expected: 13 原有 + 5 fan-out 新增,全部绿
```

---

## Phase 10.2 — Editor 允许多条入/出边

**问题**: `model.NodeGraph.validate()` 已经支持 `STRUCTURES` 出端口扇出到多个 `STRUCTURE` 入端口,
但 `canvas.GraphScene.add_edge` 可能禁止同对端口二次连接;另外 `library.refresh_visibility`
对 calc 节点"已经 1 个入边就 hide 后续 PRE_OPT"也可能抑制用户连线。

**改动**:

1. `src/jobdesk_app/gui/nodegraph/model.py`
   - 确认 `NodeGraph.add_edge` 对**不同** dst 端口的多条 src→dst_pair 边是允许的
   - `validate` 已经支持 fan-out/fan-in (Phase 1 的 `INVALID_PORT_TYPE` 例外规则就是为这个写的),无改动
   - `NodeGraph.topological_order()` 已经支持任意 DAG,无改动

2. `src/jobdesk_app/gui/nodegraph/canvas.py`
   - 确认 `GraphScene.add_edge` 对 `(src_node, src_port, dst_node, dst_port)` 重复检测只对**完全相同**的四元组去重
   - 现有代码看起来 OK;如果有问题改这里

3. `src/jobdesk_app/gui/nodegraph/library.py`
   - `refresh_visibility` 不要因为"已经有入边"而 disable 后续 PRE_OPT 节点;改成只检查 OUTPUT 节点的去重
   - 加 `RefreshPolicy.has_input_port(node.kind, port_name)` 让 library 在 fan-in 场景下保留按钮

4. `tests/test_nodegraph/test_model.py`
   - 加 1 个 fixture:CONF_GEN (STRUCTURES) → PRE_OPT_1 + PRE_OPT_2 → OPT_FINAL
   - 断言 `graph.validate()` 返回 0 error,且 `topological_order` 给出 [CONF_GEN, PRE_OPT_1, PRE_OPT_2, OPT_FINAL] 的一个拓扑序

**验收**:

```bash
pytest tests/test_nodegraph -p no:cacheprovider
# expected: 51 (含 1 skipped) 全部绿
```

---

## Phase 10.3 — Library 多端口暴露

**问题**: 当前 `library._buttons` 给每种 kind 只显示一个按钮;fan-in 需要给 calc 节点"再加一个上游入口"的能力,
但已经存在的 calc 节点本身已经定义了 input ports,所以这一步主要工作是**教用户在 fan-out/fan-in 后画布不再拒绝连线**,
库面板本身不需要增加新 kind。

**改动**:

1. `src/jobdesk_app/gui/nodegraph/library.py`
   - 在 `_tooltip_text` 给 `CONF_GEN` 加 "Output: STRUCTURES (fan-out to multiple OPTs / SPs)",
     给 `PRE_OPT/OPT/SINGLE_POINT/FREQUENCY/TS/REFINE` 加 "Input: STRUCTURE",
     给 `OUTPUT` 加 "Aggregate all upstream paths into workflow.yaml terminator"
   - `refresh_visibility` 在 fan-out 场景下仍然 hide 重复的 `OUTPUT` (1 个工作流最多 1 个)
   - 在 `_apply_filter` 加一个 "Show all" 切换,把 `refresh_visibility` 临时关掉,方便用户画复杂图

2. `src/jobdesk_app/gui/nodegraph/properties.py`
   - 当一个节点被多条边指向时,properties panel 列出 "Inputs: step1, step2 (incoming edges = 2)"

3. `src/jobdesk_app/gui/i18n.py`
   - 给新文案加 ZH 条目

4. `tests/test_nodegraph/test_library.py` (新建或扩充)
   - `test_refresh_visibility_does_not_hide_calc_when_fanout`
   - `test_tooltip_mentions_fan_out_for_confgen`

**验收**:

```bash
pytest tests/test_nodegraph -p no:cacheprovider
# expected: 51+2 = 53 (含 1 skipped) 全部绿
```

---

## Phase 10.4 — `from_workflow_spec` 还原多入边

**问题**: 当前的 `from_workflow_spec` 走 `_make_linear_edge` 把所有步骤串成单链;fan-out 模板加载回来就塌缩了。

**改动**:

1. `src/jobdesk_app/gui/nodegraph/spec_bridge.py`
   - `from_workflow_spec` 不再调用 `_make_linear_edge`
   - 改成读 `step["inputs"]`,如果非空则按名字查上游节点,用真实 `(src_node, src_port, dst_node, dst_port)`
     调 `graph.add_edge`;如果空,沿用 "XYZ_FILE → first step" 的根规则
   - 末端仍然 inject 一个 `OUTPUT` 哨兵并把所有**叶节点**(没有出边的 step)接到它

2. `tests/test_nodegraph/test_spec_bridge.py`
   - `test_from_workflow_spec_round_trips_fanout`:用 `to_workflow_spec` 出一个 fan-out 图,再 `from_workflow_spec` 还原,
     断言 `graph.validate()` 0 error 且 `topological_order` 包含正确的关系

**验收**:

```bash
pytest tests/test_nodegraph/test_spec_bridge.py -p no:cacheprovider
# expected: 18 (含原有 13 + 5 fan-out) 全部绿
```

---

## Phase 10.5 — Submit 流程接通 `confflow_kind="dag"`

**问题**: `submit_payload.SubmitKind` 已经有 `"single" | "confflow"`,但 submit 路径写死 kind 是 `"confflow"` (Phase 2)。
同时 `submit_use_case._build_confflow_specs` 走 `WorkflowSpec.from_form(...)` 重建 spec,绕开了 DAG 拓扑。

**改动**:

1. `src/jobdesk_app/gui/pages/submit_page.py`
   - `_on_submit_clicked` 通过 `to_workflow_spec` 时,检查 `payload.steps` 里有没有任意 `step["inputs"]` 非空
     - 如果全空 → `kind = "confflow"` (linear,Phase 14B 习惯)
     - 否则 → `kind = "dag"`
   - 把 kind 放到 `SubmitPayload.kind`

2. `src/jobdesk_app/services/submit_use_case.py`
   - `_build_confflow_specs` 拆成 `_build_linear_specs(payload)` 和 `_build_dag_specs(payload)` 两条路径
   - DAG 路径直接拿 `payload.dag_yaml` (在 Phase 10.4 里补) 写到 `workflow.yaml`,不走 `WorkflowSpec.from_form`
   - linear 路径行为不变,继续走 `WorkflowSpec.from_form` 保持向后兼容

3. `src/jobdesk_app/core/submit_payload.py`
   - `SubmitPayload` 加可选 `dag_yaml: str | None = None`
   - `WorkflowFields` 字段保留(`work_dir_name / steps / advanced_options`)向后兼容老 wizard path

4. `tests/test_submit_use_case.py`
   - 新增: `test_dag_payload_routes_to_dag_path` — 构造一个 `SubmitPayload(kind="dag", dag_yaml="steps:\n  - name: opt\n    inputs: []\n")`,
     断言 use case 直接把这个 yaml 写到 `first_xyz.parent / workflow.yaml`,不再二次 `from_form`

**验收**:

```bash
pytest tests/test_submit_use_case.py tests/test_submit_page.py -p no:cacheprovider
# expected: 11 + 22 = 33 全部绿 (包括预存的 confflow_kind 测试)
```

---

## Phase 10.6 — 删除 legacy widget

**改动**:

1. `src/jobdesk_app/gui/widgets/calculation_widget.py` → 删除文件
2. `src/jobdesk_app/gui/widgets/input_builder_widget.py` → 删除文件
3. `src/jobdesk_app/gui/widgets/workflow_widget.py` → 删除文件
4. `src/jobdesk_app/gui/widgets/__init__.py` → 清理 `__all__` 和不再需要的 re-export
5. `src/jobdesk_app/gui/pages/submit_page.py` → 删掉顶部 `# noqa: F401` 的 3 行 legacy import
6. `tests/` → grep `CalculationWidget|InputBuilderWidget|WorkflowWidget`,
   找到老测试并删除(它们已经在 Phase 14B 被 `_on_submit_requested` 替换)
7. `docs/USER_GUIDE.md` → 删掉"Build input file wizard"和"Confflow wizard"小节

**风险**: `submit_payload.WorkflowFields` 还引用 wizard 风格的 `steps: list[str]`;
Phase 10.5 之后 linear 路径仍在用,删除 wizard widget 不会影响。

**验收**:

```bash
pytest tests/ -p no:cacheprovider --ignore=tests/test_confflow_full_suite
# expected: 1267+ 全绿 (与 commit a0f62ed 时代的 baseline 持平)
```

---

## Phase 10.7 — Documentation + smoke

**改动**:

1. `docs/USER_GUIDE.md`
   - "Build workflow" 一节重写为 nodegraph 截图(占位)+ 文字描述
   - "DAG workflows" 一节新增,展示 fan-out 案例
   - "Saving and loading templates" 一节写实(Phase 1.2-1.5 已经实现 `save` / `load`)

2. `docs/PHASE10_NODEGRAPH_DAG.md` (本文件 — 已写)
   - 链接到 `confflow/docs/DAG-EXECUTION.md`

3. Smoke test
   - 写 `scripts/smoke_nodegraph_dag_roundtrip.py`:
     1. 用 `to_workflow_spec` 出一个 4-节点 fan-out 图
     2. `to_yaml()` 写到临时 `workflow.yaml`
     3. `from_workflow_spec` 还原图
     4. 二次 `to_yaml()`,assert 字节一致
   - 这一步只在 dev 机器跑;CI 跑 `pytest` 就够

**验收**:

```bash
python scripts/smoke_nodegraph_dag_roundtrip.py
# expected: "Round-trip byte-identical: True" + exit 0
```

---

## 整体回归

每个 Phase 提交后跑一次:

```bash
pytest tests/ -p no:cacheprovider --ignore=tests/test_confflow_full_suite -q
```

预期最终态: **1268+ passed, 7 skipped, 1 failure** (pre-existing
`test_confflow_kind_builds_single_spec_and_writes_yaml`,Phase 2 已经验证过无关)。

如果该测试在 Phase 10.5 重写 `submit_use_case` 时被顺便修了,记一笔;
否则显式给它打 `pytest.mark.xfail(reason="Windows path quirk, see #NNN")` 防止 CI 红。

---

## 不在 Phase 10 范围

- 撤销 `WorkflowGraphEditor` 嵌入的 `QMainWindow` 父化(Phase 2 的 subagent 已经 OK,没必要在 Phase 10 折腾)
- 让编辑器直接预览 confflow 日志 / 实时连线统计 — 那是 Phase 11 (可视化)
- 把 confflow 引擎反过来跑在本地(去掉 `sftp + slurm` 远端,纯本机) — 那是 Phase 12 (单机 confflow)
- 高 DPI / 暗色主题 / 多语言全翻译 — 那是 Phase 13 (polish)

## 与 Gaussian-16 / WSL 的关系

Phase 10 **完全不动** `/opt/g16/`、`smoke_confflow_real_g16*.py`、`install_mock_l1_wsl.py`。
所有新增测试都用 `pytest` 在 dev 机器跑(无需 WSL / g16)。
真机 g16 验证留给 Phase 11 的 smoke 阶段。