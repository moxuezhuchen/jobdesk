# ConfFlow × JobDesk Monorepo Migration RFC（草案 v0.1）

> 状态：**草案**，待评审。
> 来源：`confflow-review-plan.md` §4.2 + `confflow-remediation-plan.md` §6 R-MONO-A/B/C。
> 决策要求：本 RFC 不进入本轮修复验收范围（plan §6 显式排除）；实施前必须先回答 §1 缺失条件。

## 0. 摘要

当前 ConfFlow（Producer，独立 git 仓库）与 JobDesk（Consumer，Windows 端 PySide6 应用）通过 **SSH 隧道 + `confflow --capabilities --json` 协议** 协作。两条契约线（Producer 持有工件文件名、Consumer 持有版本窗口）通过 `tests/test_version_consistency.py` 与 `tests/test_confflow_validation_differential.py` 硬锁。

**本 RFC 探讨**：把 ConfFlow 与 JobDesk 合并到同一 git 仓库是否能简化协作、降低双仓当前成本，并明确在什么条件下可以启动迁移。

**结论先行**：monorepo 是值得探索的方向，但**目前有 4 个未回答的硬阻塞**，不可在本轮修复中直接实施。

## 1. 缺失条件（必须先回答）

### 1.1 远端 CLI 入口

| 问题 | 当前 | monorepo 后 |
|---|---|---|
| `confflow` 命令在 WSL 端如何引用？ | `/usr/local/bin/confflow` 指向 `/opt/ConfFlow/.venv/bin/confflow`，venv 由 `pip install /opt/ConfFlow/dist/confflow-1.4.3-py3-none-any.whl` 装 | 远端没有 wheel 也没有独立的 `Confflow` git 仓库；需要 monorepo 内提供 vendored Confflow Python 包 + 在远端 `PATH` 中放一个 `confflow` shim |

**未回答**：
- 远端 vendored 路径是否进 git（如 `third_party/confflow/`）？还是 git submodule？还是纯 build 时下载？
- `confflow` shim 是 bash 还是 Python entry point？需要用什么 entry 名字（`jobdesk-confflow` vs `confflow`）？

### 1.2 Linux 端依赖

| 依赖 | 状态 |
|---|---|
| `rdkit` | Confflow 仍依赖；WSL 上独立安装 |
| `numpy` | Confflow 仍依赖；WSL 上独立安装 |
| `numba` | Confflow 仍依赖；WSL 上独立安装 |
| Windows 端是否需要这些依赖？ | 否（JobDesk 仅用 Confflow Python API 做 YAML 验证） |

**未回答**：
- monorepo 后远端 `pip install -r requirements-linux.txt` 怎么处理（仍走 wheel？本地 `pip install` vendored confflow）？
- `pyproject.toml` 是否拆 `[project.optional-dependencies.chem]`（Windows 端）+ `[project.optional-dependencies.remote]`（Linux/WSL 端）？还是统一一个 marker？
- Windows `pip install .` 会触发 rdkit/numba 编译吗？需要 `extras_require` 跳过机制

### 1.3 过渡方案

- `JOBDESK_CONFFLOW_EXTERNAL=1`（env 变量）：让 JobDesk 跳过 vendored confflow Python import，直接走 SSH 远端 confflow 命令
- 需要保留双路径至少一个 release 周期（plan §1 R-MONO-C）
- 过渡期间：
  - `pyproject.toml` 仍依赖外部 `confflow>=1.4.3,<2.0`
  - vendored confflow Python 包（可选，用于离线 YAML 验证）通过 `[chem]` extra 引入
- 切到 monorepo 后，`JOBDESK_CONFFLOW_EXTERNAL=1` 必须**默认 False**（vendored 优先），但允许通过 env 切换到外部

**未回答**：
- 过渡期的 release cadence 是怎么排（monorepo tag → PyPI vs 旧 wheel tag → PyPI 二者并存？）
- 回滚策略：发现 monorepo 引入 bug 时，旧 wheel 是否需要保留 N 个 release 的支持窗口？

### 1.4 发布机制替代

当前 ConfFlow 的发布载体：
- `setup.py` cmdclass `BuildPyWithProvenance` → `confflow-1.4.3-py3-none-any.whl`
- `/opt/ConfFlow/dist/` 是构建临时目录
- `pip install /opt/ConfFlow/dist/confflow-1.4.3-py3-none-any.whl` 是 WSL 部署方式
- `v1.4.3` tag 标在 release commit `e47a53e`

monorepo 后发布机制：
- JobDesk 单仓库 → PyPI 上 publish `jobdesk-X.Y.Z` wheel
- Confflow 子包 → 是否仍发到独立 PyPI（`confflow` 项目名）？还是改名（`jobdesk-confflow`）？
- 跨消费者（其他用 confflow 的项目）怎么办？

**未回答**：
- 是否同时 publish 两套 wheel（`jobdesk` + `confflow`）？
- `confflow` PyPI 上传是否维护（与 JobDesk 同 release cadence？）？

## 2. 已知双仓成本（RFC §1.1-1.4 的反向证据）

- **跨仓库 `contract.py` 镜像**（producer 端 + consumer 端各一份）—— plan §4.2 第 1 项
- **CI 协调**（分别 checkout 两个仓库并协调 wheel 安装）—— plan §4.2 第 2 项
- **release hygiene gate**（producer dirty 工作树会影响 consumer 镜像锁）—— plan §3.1.a G0
- **5 镜像同步成本**（pyproject / CI / README / 部署文档 / 离线校验）—— plan §R-H2 第 4 项
- **PR 顺序约束**（producer 先 PR，consumer 后 PR）—— plan §0 全局约束第 4 项

## 3. 已知 monorepo 收益（待量化）

- **contract.py 单一来源**：producer / consumer 共享同一 `contract.py`，plan §R-H2 5 镜像同步消失
- **CI 简化**：单仓库，单 checkout，单 wheel 构建
- **release 协调**：单 tag → 双 wheel 同 release cadence
- **本地协作**：`JobDesk/scripts/dev.sh` 可同时跑 consumer unit test + producer unit test

## 4. 已知 monorepo 风险

- **Windows 构建复杂度上升**：JobDesk 的 `pyinstaller` 包目前只装 Windows 端需要的依赖，monorepo 后 `[project]` 顶级 `dependencies` 必须 **不**包含 rdkit/numba/scipy
- **vendor 体积膨胀**：`third_party/confflow/` 如果做完整 vendor，git 体积涨 5-50 MB（取决于 confflow 历史 commit depth）
- **producer / consumer release 节奏解耦损失**：当前 producer 可独立发 1.4.3 / 1.4.4，consumer 跟进；monorepo 后必须 single release cadence
- **submodule 复杂度**：如果 Confflow 用 submodule 引入，git 操作复杂度上升（`git submodule update --init`）
- **分支策略调整**：必须确认是 `trunk-based` / `gitflow` / 当前的 `feature/phase2-...` 命名规则哪一个

## 5. 评估 RFC（推荐方案）

### 5.1 不推荐：纯 vendor（git subtree）

- 把 Confflow 历史 commit 内嵌到 monorepo `third_party/confflow/`
- **不推荐**：丢失独立 producer release 能力；上游 Confflow 项目仍在 `feature/phase2-workflow-state` 分支演进，merge 冲突成本极高

### 5.2 推荐：git submodule + 单 wheel 发布

- JobDesk 主仓库 + Confflow 作为 submodule（pinned 到 `v1.4.3` 等具体 tag）
- `pip install .[chem,remote]` 触发不同依赖集
- 单一 `pyproject.toml` + 单一 release tag（`jobdesk-X.Y.Z`）
- Confflow 子模块在 monorepo 内**不**独立发 PyPI wheel；通过 `vendored_confflow` Python 包提供 Windows 端验证

**前提**：
- §1.1 / §1.2 / §1.3 / §1.4 全部回答
- plan §R-MONO-A：列出完整"远端部署方案"
- plan §R-MONO-B：列出 Windows / WSL 两端依赖拆分
- plan §R-MONO-C：列出过渡期 env 变量

### 5.3 备选：保留双仓 + 强化契约锁（不迁移）

- 不迁移；加强 `tests/test_version_consistency.py` 与 `tests/test_confflow_validation_differential.py`
- 加 GitHub Actions cross-repo sync check：consumer PR 必须引用 producer commit hash
- **优势**：成本最低，保留 producer 独立 release
- **劣势**：plan §4.2 列的双仓当前成本**不会消失**

## 6. 决策矩阵

| 维度 | 双仓（当前） | Monorepo（推荐 §5.2） | 双仓 + 强化锁（§5.3） |
|---|---|---|---|
| contract 镜像 | 2 份 | 1 份 | 2 份 |
| CI checkout 协调 | 高 | 低 | 中 |
| Release 灵活性 | 高 | 低 | 高 |
| Windows 端依赖复杂度 | 低 | 中 | 低 |
| 团队协作上下文切换 | 高 | 低 | 高 |
| 迁移成本 | 0 | 高 | 低 |
| 风险 | 已暴露 | 新风险 | 已知 |

## 7. 退出 / 回滚计划（如果实施 §5.2）

- monorepo 迁移完成后保留 1 个 release 周期（典型 4-6 周）
- 旧 `feature/confflow-monorepo` 起源分支 (`origin/feature/confflow-monorepo` 已存在) 用作回滚锚点
- producer 独立 release 在迁移后**冻结 1 个周期**

## 8. 待评审项

| # | 项目 | 负责人 | 截止 |
|---|---|---|---|
| 1 | §1.1 远端 CLI 入口方案 | （待定） | （待定） |
| 2 | §1.2 Linux 端依赖拆分 | （待定） | （待定） |
| 3 | §1.3 过渡方案 env 变量细节 | （待定） | （待定） |
| 4 | §1.4 发布机制替代 | （待定） | （待定） |
| 5 | 选 §5.2 还是 §5.3 | （待定） | （待定） |
| 6 | 迁移时间窗 | （待定） | （待定） |

## 9. 链接

- `docs/architecture.md`：当前 JobDesk 架构（monorepo 起点）
- `confflow-review-plan.md` §4.2：本 RFC 的来源
- `confflow-remediation-plan.md` §6：R-MONO-A/B/C 修复代号
- `docs/CONFFLOW_1_4_3_WHEEL_DEPLOYMENT.md`：当前 wheel 部署文档

---

> 本 RFC 不进入本轮修复验收。plan §6 显式："monorepo 是值得探索的方向，但**需先完成 RFC 评估**，不能在本轮修复中直接实施"。