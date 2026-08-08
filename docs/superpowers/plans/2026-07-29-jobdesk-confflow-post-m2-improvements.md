# JobDesk / ConfFlow Post-M2 Architecture Improvements

**Date:** 2026-07-29

## Execution status (2026-08-09 release continuation)

ConfFlow worker-handoff release closure is now at the immutable producer
release `v1.5.3`. The normal merge commit is
`f37759954da2818d777ec4d06f81bd53aeafe6e3`, with parents
`147ebfef884e0339b1ece00164e86f1d8202bf60` and the reviewed candidate
`9fdeb7742c77cb2cf7bfebf63f4f4c8595d2f648`. The annotated tag `v1.5.3`
peels to that merge commit and the GitHub Release assets were downloaded and
verified from the tag. The formal wheel is
`confflow-1.5.3-py3-none-any.whl`, SHA-256
`213eba551b344c7146450fa1135a884e3c00896371507a1edbf2eb18c7c0c5d6`;
`provenance.json` and `attestation.json` bind the wheel to repository
`moxuezhuchen/ConfFlow`, tag `v1.5.3`, and peeled commit `f377599`. The
release `SHA256SUMS` verified all seven downloaded assets. The exact release
inputs are also recorded by dependency-lock SHA-256
`a389b56baeaf75d3567175fd0c7c6282423df04ddf42469160fc8b476a1cd376` and
wheelhouse-manifest SHA-256
`ab3a940525f0230dda58e8087dab2e33c29bee6183940f72aee66c4c999adc7c`.

Remote CI passed at runs `31271946187` (full ConfFlow matrix),
`31271946186` (coverage), and `31271946207` (JobDesk Consumer Contract).
The release workflow `31272089279` completed successfully and published the
seven release assets. The GitHub connector lacked PR-write scope and returned
403; after the exact HEAD, checks, and independent review were revalidated,
the user-authorized normal `--no-ff` merge was created in an isolated
worktree and pushed. The remote push reported that the protected-ref PR rule
was bypassed; this is recorded rather than presented as a connector merge.

JobDesk's consumer reference is being updated to the formal v1.5.3 release
commit and wheel digest. The final exact production venv verified the wheel,
tag, attestation, build cleanliness, dependency lock, and wheelhouse
manifest provenance. The worker-handoff path remains explicitly one-task
(`maxItems=1`) and fail-closed; it never uses `confflow-agent` SQLite. One
real released-v1.5.3 JobDesk control computation then completed through the
supported `nohup` launcher and producer-owned worker on the authorized SSH
server. Its durable state reached revision 6 and `completed`; launcher
metadata recorded `execute_rc=0`, `worker_started=true`, and `worker_rc=0`.
The idempotent resubmit assertion observed `submitted_task_count=0`, the
reconnect/events/status/artifact/download probes advanced without an error,
and the exact attempt root was removed. Evidence is retained at
`C:\tmp\jobdesk-control-release-v153-20260809-a3\evidence.json`; the bundle
explicitly marks the in-memory event/cancel/resume/raw-manifest responses as
not persisted because the harness timed out only while deleting the large
temporary runtime after the attempt root had already been removed. The formal
compatibility counters are now `control_backend_runs=1` and
`legacy_backend_runs=1`; candidate-only, synthetic, and historical samples
remain excluded. A complete measured published compatibility cycle is still
missing. Formal decision: **COMPATIBILITY PERIOD CONTINUES**; Phase F is not
ready.

## Execution status (2026-08-08 continuation)

The current consumer reference is ConfFlow `v1.5.0`, producer commit
`0fff6439a4614ec155959b1d0d3781fc5342d736`, wheel SHA-256
`d9ac87410f1b73b91e19eb740298431663ee5f07bd4ffaeb19779c3a53c2e8dc`, with
stable `v1.4.6` retained only as the exact legacy rollback exception. The
isolated JobDesk continuation is verified at commit
`60d97c7` (code/evidence plus compatibility-preserving parser/schema/test,
request-path/download safety fixes, malformed-identifier regressions, and the current candidate evidence
snapshot); commits after this boundary are documentation-only status/review
updates. The
ConfFlow contract workflow was green at
run `31242460044` for PR #50 before the additional local provenance/schema
parity steps (`cc4a401`, `fd2ea06`), which have not been rerun remotely. The JobDesk
candidate matrix was also executed locally on 2026-08-08 in isolated Python
3.13 environments: the exact stable v1.5.0 and next v1.5.1 wheels passed
capability/build/schema parity and each pinned contract suite reported
`88 passed`. The Windows run used command-presence stubs for Unix-only
capability fields; the matrix has not been published, so its Ubuntu Actions
result is still pending.

Direct v1.5.0 Gaussian/g16 and ORCA probes completed, but they are not JobDesk
SSH compatibility-period samples. After the authorized WSL restart, a real
JobDesk legacy-backend batch (`confflow-batch`, water plus methane) completed
through SSH upload, detached execution, refresh, manifest-driven download, and
summary parsing. Both summaries had `confflow.run_summary.v1` and
`final_conformers=1`; the exact local evidence is under
`C:\tmp\jobdesk_pytest_real_legacy_20260808_b1`, and the remote root
`/tmp/jobdesk_phasef_real_legacy_20260808_b1` was absent after the bounded
cleanup. This counts as one real legacy-backend sample, but it uses the pinned
v1.5.0 producer and is not the separate stable v1.4.6 rollback probe.

A separate isolated stable v1.4.6 rollback probe then completed the same
two-molecule JobDesk legacy path with the exact rollback executable
`/opt/confflow-1.4.6-prod-venv/bin/confflow`; both summaries and manifests
passed, and `/tmp/jobdesk_phasef_real_legacy146_20260808_a1` was absent after
bounded cleanup. This closes the live rollback probe, but not the full Phase F
gate: no real JobDesk control computation/worker handoff, published candidate
CI, or complete measured compatibility period is established yet.

A separate real JobDesk control launcher acceptance also completed capability
negotiation, prepare, input-manifest upload, and `nohup` launcher dispatch over
SSH/SFTP. The producer returned the expected queued state at revision 2 with
two events; launcher metadata and the producer state root were confined to
`/tmp/jobdesk_phasef_real_control_20260808_a1/attempt`, which was absent after
bounded cleanup. This is non-compute evidence only: the pinned producer still
requires an external worker handoff, so no g16/ORCA process was started and it
does not count as a real control computation sample.

A read-only WSL audit confirmed that the installed `confflow-agent` is an
independent queue/AgentStateDB worker, while the pinned control executor does
not enqueue control launch tokens there. Its separate request digest and state
layout cannot be substituted for the JobDesk control request; no agent was
started and no agent state was touched. The worker handoff is therefore a
producer/release-scope prerequisite, not an omitted local command.

The earlier SSH attempt remains recorded as a failed, non-counted attempt. The
pinned producer's `control execute` currently returns a queued launch intent
until an external worker handoff is supplied. Therefore the formal decision
remains **COMPATIBILITY PERIOD CONTINUES**: keep both backends and the v1.4.6
rollback path; do not mark Phase F ready or delete compatibility code. The
2026-08-08 readiness recheck found the Ubuntu-24.04 `ssh.service` stuck in
`deactivating`/`sshd -t` with `rtnl_dumpit`; an isolated 10022 sshd listener
reproduced the same child-process stall before the restart. The temporary
listener and its exact `/tmp/jobdesk_phasef_ssh_20260808_a1` root were stopped
and removed; no `/opt` files or user state were modified.

## 2026-08-08 producer candidate worker-handoff (candidate-only evidence)

The producer-side worker gap was implemented in an isolated, unpublished
ConfFlow candidate. The current candidate commit is
`9a5f213`; its clean
`confflow-1.5.1-py3-none-any.whl` has SHA-256
`7c3bdfda3489fccdbd5b096d0ef170fddcd3988dc1f513740da39bdb782a634e` and
`DIRTY=False` build provenance. It adds the producer-owned
`worker-handoff.schema.json`, the `control_worker` capability flag, and the
`confflow-control-worker` entrypoint. The worker consumes the existing queued
launch token through `ExecutionService`; it does not call `prepare`, enqueue
the legacy agent, or create a second state authority. The candidate contract
also locks the UTF-8 canonical handoff digest profile, owner-private staging,
dedicated-session recovery, and required fixed sidecars before `completed`.

After the mandatory four-line g16 probe, one isolated Ubuntu-24.04 WSL run used
that exact wheel and the `confflow-control-worker` console entrypoint to execute
a real methane Gaussian 16 optimization. The run completed in 10.149 seconds;
producer revisions advanced
`prepared -> queued -> running -> checkpointed -> completed`, and the output
manifest, `methane.txt`, `methanemin.xyz`, workflow summary/stats/state, G16
identity, and file SHA-256 values were captured. The worker returned exactly one
machine-readable JSON line with exit code 0. Evidence is retained at
`C:\tmp\jobdesk-control-worker-real-9a5f213-evidence.json`; the exact remote
attempt root `/tmp/jobdesk-control-worker-real-9a5f213` was absent after
bounded cleanup. No `/opt` file was modified.

This is producer candidate evidence, not a JobDesk compatibility-period sample:
the candidate is not published or pinned by the stable JobDesk consumer, the
JobDesk launcher has not been switched to the candidate worker entrypoint, and
the remote dual-repository CI has not run for this commit. The compatibility
decision therefore remains **COMPATIBILITY PERIOD CONTINUES**; Phase F still
requires a published producer/consumer candidate, remote CI, a real JobDesk
control computation sample, and one complete measured published cycle.

## Execution status (2026-08-01)

The release-closure track in sections 3, 4, and 8.1 is **complete**, with the
original 1.4.4/1.4.5 candidates superseded by the final ConfFlow 1.4.6 hotfix
release. This does **not** mark the separate Route B collaboration-architecture
track in sections 5 and 8.2 complete.

- ConfFlow release commit: `4e9e74a8991338aec0f393182073c8c087b4fa63`
- Annotated tag: `v1.4.6`; tag object
  `6f3b24106308c1b20a78105825b5f8ebbb5d7ec5`; peeled commit `4e9e74a...`
- Final wheel SHA256:
  `7d036a44784d581b5b2fec2443f9cac7a0b2257d08b85c1a1b797bae565f75f5`
- Production venv: `/opt/confflow-1.4.6-prod-venv`; capability schema v4,
  clean build, verified install provenance
- JobDesk consumer commit: `80ce46834aee399c65eeccef5f40dbe59d15a02d`
- Strict-smoke cleanup hardening commits: `492ab35` and `713a9ec`
- Gate A, Gate B, Gate C, and M2-4A: PASS
- M2-4B methane optimization: PASS; relative manifest
  `g16_opt/output.xyz`; Gaussian normal termination
- M2-4B checkpoint opt-to-SP: PASS; `%OldChk`, checkpoint copy, initial-guess
  reuse, both Gaussian steps normally terminated
- Both successful smoke remote directories satisfied the strict cleanup
  postcondition; `/opt/g16/g16` and `/opt/g16/l1.exe` SHA256/stat identities
  were unchanged
- Existing user modifications in `/opt/ConfFlow` and the JobDesk main
  worktree were preserved

Route B status: Phase A (JobDesk compatibility facade), Phase B (frozen
control-protocol RFC/schema), Phase C (`ExecutionService` convergence), and
Phase D (thin `control --json` adapter) are complete on isolated, pushed
branches. Phase E (JobDesk control backend cutover) is implemented and passed
the local full gate plus the real WSL protocol/facade chain on an isolated
producer worktree at the pinned Phase D commit. The WSL acceptance used the
producer lifecycle callback and a synthetic non-computational artifact for
manifest download; no real worker or g16 calculation was run. Phase F has not
been accepted.

Phase C implementation and independent review were completed and pushed to
`origin/codex/execution-service-convergence` at
`64eaf696318f92ac78790cf645e0bffa91949608` (commits `5390f7f` and `64eaf69`).
Phase D was completed and independently accepted at
`1d25594cb15404c984f1dd2bf618f152d486f49d`, pushed to
`origin/codex/control-json-adapter`; the remote HEAD was verified equal to the
local HEAD. The isolated producer environment was refreshed from `.[dev]` for
the Phase D schema and protocol gate.

Phase D boundary and preservation record: the adapter calls the Phase C
`ExecutionService` public methods only; it does not own state transitions,
SQLite/repository access, revisions, events, cancellation/resume policy, or
artifact manifest generation. The four user modifications in `/opt/ConfFlow`
were preserved, no Gaussian installation files were touched, and no real g16
calculation, `main` update, tag change, or release publication was performed.

Phase E implementation is committed on
`codex/control-backend-cutover` at `6235c74` (full commit recorded in the
branch history). The gate covered the full local suite (`1844 passed, 35
skipped, 12 deselected`), Ruff/diff/compile checks, and real WSL SSH/SFTP
probe, upload, idempotent prepare, persisted handle, execute, reconnect,
cursor events, status, cancel, resume, artifacts, and manifest download.

### Route B Phase E mainline merge-ready record (2026-08-01)

Phase E is **mainline merge-ready** on the integration branch
`codex/control-backend-release`, subject to the explicit authorization boundary
below. This record is preparation only: it does not merge JobDesk `main`, does
not start the compatibility period, and does not start Phase F.

- Phase E source HEAD integrated exactly: `16c55097a49e57d2e54bf26ea1c7a71809a7cd5b`
- Integration merge commit: `6bb4793` (full ancestry retains the exact source
  commit); confirmed merge base is live `origin/main`
  `fe54fe2151be3e6ee80b71a9136d560453a1955d`
- Confirmed Phase E fix/regression commit: `f7c9f1d`
- Planned JobDesk main commit at the authorization boundary:
  `fe54fe2151be3e6ee80b71a9136d560453a1955d`
- Stable producer compatibility baseline: released ConfFlow `v1.4.6`
- Control/next producer acceptance pin:
  `1d25594cb15404c984f1dd2bf618f152d486f49d`
- Local legacy compatibility result: legacy facade, CLI, GUI, and service
  regression coverage is included in the green non-integration suite; no
  legacy real worker/compute run was started in this phase
- Real control result: SSH/SFTP against WSL `wsl` with the pinned producer
  passed capability negotiation, prepare, execute, reconnect/events, status,
  checkpoint-bound resume, producer-validated synthetic artifact manifest,
  digest/size-verified download, and cancel. The lifecycle fixture used
  producer callbacks only; `compute_executed=false`, `g16_touched=false`.
- Final local evidence for this integration candidate: Phase E targeted
  `160 passed`; non-integration suite `1835 passed, 25 skipped, 6 deselected`;
  Ruff, GUI offscreen smoke, build, and diff check passed. The CI-mirror
  one-shot environment used Python `3.13.14`, MyPy `2.3.0`, editable JobDesk
  `.[dev]`, and the independently SHA-256-verified ConfFlow `v1.4.6` wheel
  (`7d036a44784d581b5b2fec2443f9cac7a0b2257d08b85c1a1b797bae565f75f5`). The
  exact `python -m mypy src` command passed with no issues in `160` source
  files. The four changed service files also pass isolated MyPy.
- Optional chemistry-runtime diagnostic: installing the declared `rdkit` extra
  makes the upstream RDKit wheel install a `rdkit-stubs` directory whose
  `Chem/rdMolDescriptors.pyi:10` contains invalid syntax. With the same
  Python/MyPy versions this reproduces identically on live `origin/main` and
  this candidate, before project files are checked; `pip check` is otherwise
  clean. This is an upstream optional-stub defect, not a Phase E type error,
  and the mandatory CI type-check environment intentionally does not install
  that optional RDKit distribution.
- PR #3 online Gate at candidate HEAD
  `a1188805db1dd9c4d3f85b7fdc07205ee5175397`: `lint`, `type-check`, `build`,
  and `pyinstaller` succeeded; `test (3.11)`, `test (3.12)`, and `test
  (3.13)` failed specifically at their `Test with coverage` step. The
  candidate reproduced the CI test command locally with the forced
  ConfFlow differential and passed `1851 passed, 30 skipped, 12 deselected`.
  GitHub's public API exposes only exit-code annotations for those failures;
  the log body requires authentication, so no failure detail is inferred.
  The PR remains ready-for-review with `mergeable=true` but
  `mergeable_state=unstable`; no reviews or review comments are present.
- Confirmed CI blocker and minimal fix: the failing coverage command selected
  `tests/integration` for collection even though the marker deselected those
  tests; the no-deps ConfFlow wheel then raised `ModuleNotFoundError: rich`
  during collection. Commit `25c6166` adds `--ignore=tests/integration` to the
  non-integration coverage command. In the same no-deps environment, the
  exact corrected command passed `1832 passed, 28 skipped, 6 deselected` with
  XML coverage output. The fix is CI-only and does not alter the control
  backend or the real WSL acceptance chain.
- After pushing `25c6166` and the separate record commit `8a6131f`, remote
  lint, type-check, build, and PyInstaller succeeded, but all three remote
  coverage jobs still failed after normal test-duration execution. The same
  corrected no-deps command passed on live `origin/main` (`1824 passed, 28
  skipped, 6 deselected`) and on this candidate (`1832 passed, 28 skipped, 6
  deselected`). The remaining blocker is therefore a CI-only failure whose
  assertion/log body is unavailable to the current unauthenticated GitHub
  API/browser session; no further code change is inferred or made.
- Final observed PR #3 run for HEAD `be33ede`: lint, build, and PyInstaller
  succeeded; type-check failed in the external ConfFlow wheel download/install
  step before MyPy; and `test (3.11)`, `test (3.12)`, and `test (3.13)` failed
  in `Test with coverage`. The PR is open and ready-for-review with
  `mergeable=true` and `mergeable_state=unstable`; no reviews or review
  comments are present. Branch-protection required-check details could not be
  read through the unauthenticated API (HTTP 401), so no required-check policy
  is inferred.

#### Compatibility-cycle prerequisites (not started)

- `cycle_started`: **false**; `cycle_start_date`: **not assigned**. A date may
  be filled only after this branch is actually merged to JobDesk `main` and a
  compatible release is published.
- Required before starting the cycle: merge authorization, mainline CI green,
  stable `v1.4.6` and next-producer parity evidence, the legacy/control
  acceptance record above, and a published consumer release with backend choice
  fixed per run.
- Required metrics for the one published cycle: runs by backend, explicit
  unsupported-protocol fallback count and reason, protocol/reconnect/cursor
  failures, duplicate/idempotency conflicts, resume/cancel outcomes, artifact
  integrity failures, and legacy usage remaining at cycle close.
- Earliest Phase F objective: only after one completed published compatibility
  cycle with those metrics, plus real control acceptance for the supported
  launcher paths and rollback evidence, may the project decide whether to
  remove legacy state-file compatibility or retain/deprecate the optional
  agent. Phase F is not authorized by this record.
- Rollback: keep the legacy backend available at the run boundary; if the
  published control path regresses, stop automatic control selection, preserve
  producer revisions and JobDesk provenance, and revert the consumer release
  to the last compatible release. Do not delete the legacy path, producer
  state, tags, or release refs during rollback.

**Status:** Revised draft — Route B unified control architecture selected; implementation remains phase-gated

**Baseline:** JobDesk `44719e9`; ConfFlow `10e457d`

**Target release:** ConfFlow `1.4.4`（不得重写或复用不可变的 `v1.4.3`）

**Protocol target:** Post-`1.4.4` 独立版本/分支（不得与 1.4.4 release closure 混成一次大改）

**Architecture decision:** 采用路线 B：建立唯一 ConfFlow `ExecutionService` 和版本化 control protocol。先设计协议、再收敛内部状态机、最后增加薄 adapter；不采用“删除 agent 后永久保留 JobDesk shell/file 控制面”的路线 A。

## 1. 目标与范围

本计划分成两个相互解耦的轨道：

1. **Release closure（阻塞当前发布）:** 补全 Milestone 2 尚未落地的设计约束，并把 M2-4 从“源码功能已实现”推进到“可验证、可部署、可审计的 release candidate”
2. **Collaboration architecture（不阻塞 1.4.4）:** 参考 PyTRIO 公开 SDK 的 client/handle/future 边界，将 JobDesk 与 ConfFlow 的协作从“consumer 拼 shell、轮询 producer 文件”逐步收敛为版本化的 typed client + run handle + JSON control protocol

PyTRIO 的服务端源码不是本计划的依据；这里只借鉴其公开 API 所体现的职责划分：本地 client 负责流程编排，远端服务负责重计算、状态与 checkpoint，异步提交返回可等待的 handle/future。JobDesk/ConfFlow 仍保留 SSH/SFTP、本地 WSL 和双仓发布方式，不引入云服务依赖。

**本轮解决：**
- 建立不依赖 wheel 自描述哈希的真实发布/安装 provenance
- 在 probe、dry-run、runner、resume runner 每个阶段绑定同一 executable identity
- 完成旧 DAG API 的一个发布周期弃用路径
- 修复 capability v4 与四种内容 schema 的 producer/consumer 镜像和解析
- 先完成非计算 M2-4 smoke；真实 Gaussian smoke 作为独立、需授权的验收门禁
- 修复 ConfFlow README/CHANGELOG 的事实漂移
- 定义 `ConfFlowClient` / `RemoteRunHandle` 应用边界和版本化 `control --json` 协议
- 明确 JobDesk、ConfFlow 与现有 `confflow-agent` 的状态所有权，避免三套状态机继续演化
- 建立双仓 contract-first CI，而不是依靠人工同步版本、schema 和 artifact 字符串

**不在范围内：**
- Gaussian/ORCA 计算器实现改动
- 将现有 `confflow-agent` 直接接入 JobDesk
- monorepo/submodule 迁移
- HTTP/cloud service、WebSocket 或新的常驻系统服务
- 在 ConfFlow 1.4.4 中一次性替换现有 SSH/shell/file 协议
- 移动、覆盖或重新创建现有 `v1.4.3` tag

### 1.1 目标职责边界

```text
JobDesk GUI / CLI
    -> application use cases
    -> ConfFlowClient
    -> RemoteRunHandle
    -> SSHTransport
    -> confflow control <command> --json
    -> ConfFlow ExecutionService
    -> workflow engine / calc executor / artifact writer
```

边界规则：

1. SSH/SFTP 只是 transport，不再承载散落在 consumer 各层的业务语义
2. ConfFlow 是远端 execution status、checkpoint、event stream 和 artifact manifest 的权威来源
3. JobDesk SQLite 保存用户意图、submit journal、本地 projection/cache 和 UI 状态，不复制一套 producer 内部 workflow 状态机
4. JobDesk 生成稳定 `run_id` / idempotency key；ConfFlow 对同一 key 的重复 submit 执行幂等判定
5. 普通 one-shot CLI 与未来 daemon 必须调用同一个 `ExecutionService`；agent 只是可选 backend，不是第二套协议
6. sync、Qt worker callback 与未来 async API 共享同一 handle 语义；本计划不要求把 PySide6 改造成 asyncio 应用

---

## 2. 前置条件验证

实施开始前必须重新读取实际状态，不从本文假定 clean worktree：

1. **JobDesk:** `C:\dft\tool\jobdesk-dev`，expected base `44719e9415071933a9edb1021182788871be7e9c`
2. **ConfFlow:** `Ubuntu-24.04:/opt/ConfFlow`，expected base `10e457daa92a2f6c48608428197cc3503b82d08f`
3. **不可变发布:** `refs/tags/v1.4.3^{}` 当前为 `7b37c223d2c07a062ab62965911c3cd8d6641591`
4. **当前状态:** 两仓库均有用户修改；不得 reset、checkout、stash 或覆盖。先列出 `git status --short --branch` 和相关 diff，重叠时先报告
5. **版本边界:** `10e457d` 之后的 release 内容使用 `1.4.4` 和新 tag，不得以 `1.4.3` 名义重发
6. **consumer 顺序:** ConfFlow 1.4.4 producer candidate 单仓验收完成后，才提升 JobDesk 最低版本/CI ref
7. **g16 安全:** `.cursor/rules/wsl-g16-safety.mdc` 是硬约束；真实计算仍需单独授权

---

## 3. 改进计划分解

### P0 - ConfFlow 1.4.4 release/install provenance（阻塞 M2-4）

**设计决策:**

wheel 不能可靠包含“自身最终文件 SHA-256”：把第一次构建的摘要写进第二只 wheel 会改变 wheel 字节，因此第二只 wheel 的实际摘要必然不同。禁止两阶段自回填方案。

采用三层 provenance：

1. **wheel 内 build provenance:** package version、git commit、dirty
2. **wheel 外 release provenance:** release workflow 对最终 wheel 生成 `SHA256SUMS` 和绑定 trusted repository/tag/commit 的 artifact attestation
3. **版本化目标 venv 的 install provenance:** 部署器验证最终 wheel 后，原子写入 `<sys.prefix>/share/confflow/install-provenance.json`

`SHA256SUMS` 只证明 wheel 与 checksum manifest 一致，不单独证明发布者身份。Production 部署必须同时验证 approved repository/tag/peeled commit 和 artifact attestation；离线环境如不能验证 attestation，只能生成待人工批准的 candidate，不能自动激活为 production executable。

部署不得原地修改当前 `/opt/ConfFlow/.venv`。目标使用全新、不可复用的版本化 venv，例如：

```text
/opt/ConfFlow/.venvs/confflow-1.4.4-<wheel-digest-prefix>/
```

部署器只创建并验证该 candidate venv，成功后输出其 executable 路径；JobDesk `ServerConfig.confflow_executable` 的切换是后续显式激活动作。旧 venv 在新版本完成 smoke 前保持不变。

建议安装记录：

```json
{
  "schema": "confflow.install-provenance.v1",
  "package": "confflow",
  "version": "1.4.4",
  "wheel": {"filename": "confflow-1.4.4-py3-none-any.whl", "sha256": "<64 lowercase hex>"},
  "build": {"commit": "<40 hex>", "dirty": false},
  "release": {
    "repository": "<approved owner/repository>",
    "tag": "v1.4.4",
    "tag_commit": "<40 hex>",
    "attestation_verified": true
  },
  "installed_at": "<UTC RFC3339>"
}
```

capability v4 在 1.4.4 首次正式发布前冻结以下机器可读 diagnostic shape；不得临时拼接自由文本字段：

```json
{
  "producer": {
    "wheel": {"filename": null, "sha256": null},
    "install_provenance": {
      "status": "missing|invalid|verified",
      "reason_code": "missing_file|invalid_json|schema_mismatch|version_mismatch|commit_mismatch|attestation_unverified|null"
    }
  }
}
```

`status == "verified"` 时 `reason_code` 必须为 `null` 且 wheel filename/digest 必须非空；其他状态只能用于 diagnostic，JobDesk production gate 一律拒绝。reason 中不得包含 secret、完整环境变量或未清洗的远端输出。

**实施步骤 (ConfFlow):**

1. **build hook 清理:** `setup.py::BuildPyWithProvenance` 只写 package version、`COMMIT`、`DIRTY`；删除把 `CONFFLOW_WHEEL_FILENAME` / `CONFFLOW_WHEEL_SHA256` 注入 wheel 并作为最终 wheel identity 的逻辑
2. **release workflow:** `.github/workflows/release.yml` 使用当前 tag checkout，不在 workflow 内再次 clone。清空专用输出目录后只构建一次，并用 Python 脚本断言恰好一个 `confflow-1.4.4-*.whl`，逐字节生成 `SHA256SUMS`，验证 wheel 内 version/commit/dirty，再生成绑定 repository、tag、workflow identity 的 artifact attestation。上传 wheel、sdist、checksum、SBOM、attestation；构建后禁止修改 wheel
3. **部署器接口:** 创建 `scripts/install_release_wheel.py`，只接受一个目标模型：`--wheel`、`--sha256sums`、`--target-venv`、`--expected-version`、`--expected-commit`、`--expected-repository`、`--expected-tag`，以及 approved attestation/bundle 输入。`--target-python` 与 `--target-venv` 不得并存
4. **部署前验证:** 要求 target venv 不存在；拒绝 glob、多 wheel、已有目录、checksum 缺项/重复项、basename 不匹配。计算最终 wheel SHA-256；解析 wheel metadata 和 `confflow/__build__.py`（不执行 wheel 代码），验证 version、commit、dirty、tag commit 与 attestation subject
5. **隔离安装:** 在与目标同一父目录创建唯一 staging venv；按批准的 dependency lock/wheelhouse 安装依赖，再用 `python -m pip install --no-deps <exact-wheel>` 安装目标 wheel。既有 production venv 不参与写入
6. **安装后验证:** 使用 staging venv 的 Python/`confflow` 运行 capability probe，核对 package/build/executable；然后原子写 `<sys.prefix>/share/confflow/install-provenance.json`，再次 probe 并核对 wheel digest
7. **完成/失败语义:** 全部验证通过后，将 staging venv 原子重命名为尚不存在的 versioned target venv，并输出 executable 路径，但不自动修改 JobDesk server config。失败只允许清理本次创建且已验证位于 `.venvs/` 下的 staging 目录；不得尝试按文件删除/回滚一个部分修改的既有 pip 环境
8. **capability 读取:** 从 `sys.prefix/share/confflow/install-provenance.json` 读取 `producer.wheel.filename/sha256`，与当前 package version/build commit 及 executable identity 交叉校验。缺失、损坏或不一致时使用冻结后的 v4 diagnostic shape 输出 `null` 和机器可读 reason；JobDesk production gate fail closed，禁止伪造 `"unbound"`
9. 更新 `docs/RELEASE.md` 和部署文档，明确 checksum integrity、attestation authenticity、candidate venv、显式激活和旧 venv 保留策略

**测试与验收:**

1. checksum、attestation、repository、tag、version、commit 任一不匹配时拒绝，既有 production venv 和 server config 不变
2. target 已存在、staging 越界、安装失败、provenance 原子写失败时均不产生可激活的 candidate；清理只作用于本次安全解析出的 staging 目录
3. provenance 截断、schema 错误、错误 sys.prefix 时 capability fail closed
4. source/editable install 只能标记为 development diagnostic，不能通过 production gate
5. install provenance digest 等于最终 wheel 实际字节 digest，capability 与该记录一致
6. `build.dirty == false`，`build.commit == v1.4.4^{}`；attestation subject digest 等于同一 wheel digest
7. 新 candidate 完成 M2-4A 前不激活、不删除旧 venv；`v1.4.3` tag 和已有 artifacts 完全不变

**责任方:** ConfFlow

**估算工时:** 8–10 小时

---

### P1 - M2 D1 executable identity 全阶段绑定

**真实调用链:**

```text
RunCoordinator
  -> RunService.submit_run()
  -> services/run_service/_submit.py::submit_run()
  -> remote/submitter.py::JobSubmitter.submit_batch()
     -> _preflight_capabilities()
     -> _submit_nohup() / _submit_scheduler()
        -> generate_task_runner()
        -> _preflight_tasks()
        -> remote start
```

当前不存在 `_build_task_runner()`、`_build_resume_runner()`、`services/run_service/_runner.py` 或 `core/exceptions.py`。不得为匹配旧伪代码创建平行调用链。

**identity 字段:**

`requested_executable`、`resolved_executable`、`resolved_realpath`、`resolved_device`、`resolved_inode`、`executable_sha256`。

**实施步骤 (JobDesk):**

1. 在 `remote/confflow_probe.py` 增加 shell-safe identity probe；同一初始化环境中执行 `readlink -f`、`stat -Lc '%d:%i'` 和 `sha256sum`
2. `JobSubmitter._preflight_capabilities()` 成功后立即独立解析 identity，并与 capability executable block 比对；在 runner 生成/上传前完成
3. 将 immutable expected identity 写入 run provenance、submit operation payload，并通过现有 `TaskRecord` 扩展或 `generate_task_runner()` 显式参数注入 runner
4. `generate_task_runner()` 在真正执行 `_task_launch_command(task)` 前重新计算 realpath、device/inode、sha256；任一变化都写失败状态、exit code 和明确日志后退出
5. normal/resume 与 nohup/scheduler 共用同一 runner guard，不从可变 server config 重新取值
6. 通过现有 `SubmitResult.errors`、task update callback 和 submit journal 持久化错误；不依赖不存在的 `_start_task()`
7. `services/run_service/_submit.py` 必须在远端启动前持久化 accepted provenance；持久化失败时不得启动任务

**测试与验收:**

1. 显式 executable、PATH fallback、带空格路径、symlink 均覆盖
2. probe/identity probe 不一致，以及 realpath、symlink target、inode、内容 digest 替换均在 exec 前拒绝
3. normal/resume × nohup/scheduler 四条路径覆盖
4. provenance 持久化失败时没有远端启动副作用
5. 拒绝原因进入 task error、activity/journal；正常路径不重读 server config
6. 测试只使用临时 fake executable/symlink，不移动 `/opt/ConfFlow/.venv/bin/confflow`
7. 分别模拟 `readlink -f`、`stat`、`sha256sum` 超时、非零退出和畸形输出；验证 submit/resume 均在生成或启动 runner 前拒绝，并把阶段、命令类型和安全诊断持久化到 submit journal

**责任方:** JobDesk

**估算工时:** 6–8 小时

---

### P1 - M2 D6 DAG legacy 迁移与弃用警告

**问题描述:**

M2 设计 D6 要求"旧 `DAGGraph` / `WorkflowDAG` 在一个兼容发布周期内迁移到 `dag/legacy.py`，保留导入但发出弃用诊断"。当前 `workflow/dag/explicit.py` 已是唯一执行语义，但 `dag/__init__.py` 仍导出旧 API，未发出弃用警告。

**实施步骤:**

1. 创建 `confflow/workflow/dag/legacy.py`，移入 `DAGStep` / `DAGGraph` / `WorkflowDAG` 的旧实现
2. `dag/__init__.py` 不得把公共旧名称直接绑定到 globals；使用私有别名和模块级 `__getattr__()`：
   ```python
   import warnings

   from .explicit import build_step_graph, topo_order
   from .legacy import DAGGraph as _LegacyDAGGraph
   from .legacy import DAGStep as _LegacyDAGStep
   from .legacy import WorkflowDAG as _LegacyWorkflowDAG

   _LEGACY_EXPORTS = {
       "DAGStep": _LegacyDAGStep,
       "DAGGraph": _LegacyDAGGraph,
       "WorkflowDAG": _LegacyWorkflowDAG,
   }

   def __getattr__(name: str):
       if name in _LEGACY_EXPORTS:
           warnings.warn(
               f"{name} is deprecated; use confflow.workflow.dag.explicit",
               DeprecationWarning,
               stacklevel=2,
           )
           return _LEGACY_EXPORTS[name]
       raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
   ```
3. 明确 `__all__`/star-import 行为并测试；`workflow/engine.py` 直接从 `.dag.explicit` 导入
4. CHANGELOG 标记 1.4.4 开始弃用、最早 1.5.0 移除，且至少保留一个已发布兼容周期

**验收标准:**

1. `from confflow.workflow.dag import DAGGraph` 触发一次且 warning location 指向调用方
2. `from confflow.workflow.dag.explicit import build_step_graph` 不触发警告
3. engine 和新代码不引用 legacy API
4. 测试覆盖直接读取、from-import、重复读取、`__all__`/star import、未知属性和 legacy 行为回归

**责任方:** ConfFlow

**估算工时:** 2 小时（迁移 + 测试 + 文档）

---

### P1 - capability/artifact/schema 对接闭环

**问题描述:**

当前 capability 只有五个 artifact 字段，`output_manifest` 尚未进入 producer payload；JobDesk 的 summary/stats/state readers 仍使用错误的短 schema（如 `run_summary.v1`），而 producer 写的是完整 `confflow.run_summary.v1`。这必须作为 producer/consumer 协同 wire-contract 变更处理，不能单边修改 JobDesk。

**实施步骤:**

**Phase 1 — ConfFlow 1.4.4 producer（先行）:**

1. `confflow/contract.py` / `confflow/cli.py` 的 capability `artifacts` 增加 `output_manifest: OUTPUT_MANIFEST_FILE`
2. producer 单仓测试固定六字段 artifacts、四个内容 schema、实际 writer 输出和 CLI JSON
3. 按 P0 candidate gate 验证 producer commit；取得授权后创建/推送 `v1.4.4`，再从 tag checkout 重建最终 wheel、生成 checksum/attestation 并验证远端 ref

**Phase 2 — JobDesk consumer（只在 Phase 1 发布证据完成后开始）:**

4. 先更新 `pyproject.toml` dependency pin、`core/confflow_contract.py::MIN_VERSION`、`.github/workflows/ci.yml` 两处 producer checkout/build/install、`.github/workflows/optional-coverage.yml` 对应 producer checkout/build/install，以及 `tests/test_version_consistency.py`、README/部署文档镜像到 1.4.4，确保本地和 CI 明确安装 Phase 1 的最终 wheel
5. `core/confflow_contract.py::ConfFlowArtifactContract`、`EXPECTED_ARTIFACTS` 与 `core/confflow_preflight.py::_parse_artifacts()` 同步增加必填 `output_manifest`
6. JobDesk 增加四个完整内容 schema 常量：
   ```python
   RUN_SUMMARY_SCHEMA = "confflow.run_summary.v1"
   WORKFLOW_STATS_SCHEMA = "confflow.workflow_stats.v1"
   WORKFLOW_STATE_SCHEMA = "confflow.workflow_state.v1"
   OUTPUT_MANIFEST_SCHEMA = "confflow.output_manifest.v1"
   ```
7. `services/confflow_results.py` 的 summary/stats/state readers 使用完整 schema；错误短名称不再作为当前格式接受
8. 为 `output_manifest.json` 新增明确 parser，验证 `content_schema`、terminal 名称、相对路径、安全目录边界和文件列表类型
9. 下载逻辑只接受 manifest 声明且位于工作目录内的路径；拒绝绝对路径、`..` 穿越、重复/冲突目标
10. legacy 无 schema 文件只通过有诊断、有测试的 adapter；未知 schema fail closed

**验收标准:**

1. producer capability 与 consumer dataclass 六字段完全一致
2. 四个 schema 常量逐项与 producer 定义一致；不得用 YAML validator differential 代替 wire-contract 测试
3. producer 实际写出的 summary/stats/state/manifest 能被 JobDesk 读取
4. 错误短 schema、未知 schema、截断 JSON、错误 envelope 被拒绝
5. manifest path traversal、绝对路径、重复 terminal、非字符串条目被拒绝
6. Phase 1 发布证据缺失时 Phase 2 必须阻塞；不得在 JobDesk 主线形成“只接受尚未发布 producer”的状态

**责任方:** ConfFlow + JobDesk

**估算工时:** 4–5 小时

---

### P2 - ConfFlow 文档事实修复

**问题描述:**

评审 §7.1 短期任务表 + §9 评审建议指出 ConfFlow README §ConfFlow↔JobDesk 段落与 CHANGELOG 顶部存在文档漂移。

**实施步骤 (ConfFlow):**

1. 更新 ConfFlow `README.md` §ConfFlow↔JobDesk 段落：
   - capability schema v3 → **v4**
   - 增加 `producer` / `executable` block 说明
   - 增加四个完整 v1 内容 schema 说明
   - 增加 `OUTPUT_MANIFEST_FILE` / `output_manifest.v1` 多终端输出说明
   - 更新 `commands` 7 项必含 `bash` 的约定
   - 说明 build、外部 release checksum 与 install provenance 的区别

2. 更新 `CHANGELOG.md`：
   - 当前“ConfFlow 已合并进 JobDesk”文字已经位于 `## Archived (2026-07-06) — final reference snapshot` 历史区块；保留该历史，不把它改写成现状，也不误删历史记录
   - 增加 v1.4.4 executable identity、DAG deprecation、六字段 artifacts 与 provenance 内容
3. 更新 `docs/RELEASE.md`：记录 immutable tag、外部 checksum、安装 provenance 和远端 ref 验证流程
4. 审计 `confcalc` 残留：当前 README 已无 `confcalc`，`[project.scripts]` 也未注册；只有历史 `REVIEW.md` 和 `confflow/cli.py` 的进程名识别集合仍提及。历史 review 保留原文；是否删除进程识别项作为单独代码决策，不在 P2 中擅自恢复或新增 CLI 入口

**验收标准:**

1. capability schema 引用为 v4；内容 schema 保持各自 v1，禁止笼统替换为 v4
2. 文档中的 version/tag/schema/commands/artifacts 与实际 CLI payload 和 release workflow 一致
3. v1.4.3 历史不被改写，v1.4.4 变更单独记录
4. 不把历史 REVIEW 中的 `confcalc` 记录误判为当前 README 漂移；任何入口增删都有独立代码依据和测试

**责任方:** ConfFlow

**估算工时:** 1–2 小时

---

## 4. M2-4 release-candidate 验收

### 4.1 M2-4A — 非计算 smoke（必跑）

M2-4A 分成三个有明确证据边界的 gate，避免“必须先有 tag 才能验收、又必须先验收才能打 tag”的循环。

**Gate A — pre-tag producer candidate:**

1. 从独立、clean 的 candidate commit worktree 构建 candidate wheel；不使用带用户修改的 `/opt/ConfFlow` worktree
2. 跑 ConfFlow 全量测试、contract/DAG/provenance/deployer 专项测试
3. 用 candidate checksum/attestation 安装到全新 staging venv，运行 capability、`--dry-run`、resume-state 和 executable identity 非计算 smoke
4. 记录 candidate commit 和测试证据；本 gate 不把 candidate digest 宣称为最终 release digest

Gate A 全绿后停止并请求创建/推送 annotated `v1.4.4` tag 和发布 artifacts 的授权。

**Gate B — tagged final producer release:**

Gate B 是从已批准 tag checkout 进行的一次独立最终构建，不读取、不修改、也不回填 Gate A wheel 的摘要；它不是 P0 禁止的“把第一次构建哈希写入第二只 wheel”的自描述哈希两阶段构建。

5. 从 `v1.4.4` tag 的 clean checkout 重新构建最终 wheel；验证 `v1.4.4^{}`、wheel build commit、checksum 和 attestation subject 完全一致
6. 通过部署器安装到新的 versioned candidate venv，重复 capability、`--dry-run`、resume-state 非计算 smoke
7. 上传并远端验证 tag、wheel、sdist、`SHA256SUMS`、SBOM、attestation；最终 wheel 生成后不再修改

**Gate C — JobDesk consumer integration:**

8. Phase 2 JobDesk branch 固定 Gate B 的 tag/peeled commit/final wheel，运行专用跨仓 contract-parity tests
9. JobDesk 对 Gate B candidate venv 的 executable 完成 probe、dry-run、normal/resume × nohup/scheduler identity guard smoke
10. 验证 source/editable install 被 production gate 拒绝或明确标记为 diagnostic

**验收:** payload 与最终 checksum、tag commit、wheel build commit、attestation 和目标 executable 一致；无 `"unbound"`、dirty/unknown provenance 或 identity mismatch；不执行 g16/ORCA，不写 `/opt/g16`。Gate C 全绿前不修改持久 server config、不删除旧 venv。

### 4.2 M2-4B — 真实 g16 smoke（单独授权）

本节不是 M2-4A 的自动步骤。只有 P0/P1/P2 和 M2-4A 全绿、目标 executable 指向已验证 1.4.4 venv，并取得用户对本次真实计算的明确授权后才能运行。

运行前先修复现有两个脚本：

- 删除规则禁止的 `/opt/gauopen` PATH 项
- timeout 从 600 提升到至少 900 秒
- 明确使用 `Ubuntu-24.04`
- 自动逐项断言 4-line pre-flight 输出，不只打印
- 准确区分 methane opt 与 checkpoint opt→SP，不再称为 `opt=ts`
- 在真实计算之前验证 capability payload
- 临时目录唯一化并安全清理；失败时保留诊断但不修改 g16 安装

用户授权后运行：

1. `scripts/smoke_confflow_real_g16_wsl.py`
2. `scripts/smoke_confflow_real_g16_chk_wsl.py`

验收除 exit code 0 外，还要检查 summary/stats/state/output manifest、checkpoint 传递、Gaussian normal termination，以及执行前后 `/opt/g16/g16` 和 `/opt/g16/l1.exe` 身份未变化。

**责任方:** JobDesk

**估算工时:** M2-4A Gate A/B/C 3–4 小时；M2-4B 2–4 小时

---

## 5. PyTRIO-style collaboration architecture（Post-M2，不阻塞 1.4.4）

### 5.1 当前问题与设计决策

JobDesk 当前通过 `nohup setsid`、runner shell、`.jobdesk_status`、`.jobdesk_exit_code`、`events.log` 和产物文件共同推断远端生命周期；ConfFlow 同时保留一套未被 JobDesk 使用的 `confflow-agent` queue/SQLite/pause/resume/cancel 状态机。继续直接接入现有 agent 会让 JobDesk submit journal 与 agent state db 形成双写，不能从根本上简化协作。

本计划作出以下决策：

1. **保留双仓与独立 release cadence**；ConfFlow 仍可脱离 JobDesk 单独作为 Linux/WSL CLI 使用
2. **保留 SSH/SFTP transport**；不要求 HTTP、云服务或常驻 daemon
3. **不直接迁移现有 agent**；先建立 `ConfFlowClient` / `RemoteRunHandle` 和 producer-owned control protocol
4. **先 façade、后协议、再 backend**；每一步都保持现有功能可回归和可回退
5. **远端执行状态单一权威**；JobDesk 只保存投影，不直接读取 agent SQLite，也不与 producer DB 双写

### 5.2 状态与数据所有权

| 数据 | 权威 owner | JobDesk 可保存内容 |
|---|---|---|
| 用户提交意图、server、local workspace | JobDesk | 完整记录 |
| submit operation、owner lease、上传补偿 | JobDesk | 完整 journal |
| 远端 workflow state、step transition、checkpoint | ConfFlow | 带 revision/cursor 的只读 projection |
| executable/build/install provenance | ConfFlow 产生，JobDesk gate | accepted snapshot + digest |
| event stream | ConfFlow | last consumed cursor + UI cache |
| artifact manifest 和 terminal outputs | ConfFlow | 已验证 manifest snapshot + 下载记录 |
| GUI 展示状态 | JobDesk | 从 projection 派生，不反向覆盖 producer state |

远端 `run_id` 与 JobDesk `run_id` 必须相同。每个变更响应都返回单调递增 `revision` 或 event `cursor`；JobDesk 只接受不回退的 projection，禁止用较旧轮询结果覆盖较新状态。

### 5.3 Phase A — JobDesk compatibility façade（先实施，无远端行为变化）

新增应用层接口，先包装现有 `JobSubmitter`、`RunMonitor`、SFTP download 和 resume 路径：

```python
class ConfFlowClient(Protocol):
    def probe(self, *, require_dag: bool = False) -> ConfFlowCapabilities: ...
    def submit(self, request: SubmitRequest) -> RemoteRunHandle: ...
    def attach(self, run_id: str) -> RemoteRunHandle: ...

class RemoteRunHandle(Protocol):
    @property
    def run_id(self) -> str: ...
    def status(self) -> RemoteRunSnapshot: ...
    def events(self, *, after: str | None = None) -> EventPage: ...
    def cancel(self) -> RemoteRunSnapshot: ...
    def resume(self, checkpoint: str | None = None) -> RemoteRunSnapshot: ...
    def artifacts(self) -> ArtifactManifest: ...
```

实施要求：

1. façade 位于 services/application 边界；GUI 不再 import `remote.*`，远端异常在 façade 中转换为 typed application errors
2. 第一版 `SSHConfFlowClient` 复用现有 `SessionPool`、`JobSubmitter` 和 `RunMonitor`，不得复制 shell 生成或状态解析代码
3. `SubmitUseCase` 只负责输入校验与 `SubmitRequest` 组装；上传/submit/status/download/resume 通过 client/handle
4. 在 `tests/test_architecture_boundaries.py` 增加 `gui -> remote` 禁止规则，并固定只有 transport adapter 可以依赖 Paramiko/SFTP 实现
5. façade 的 sync API 为现有 CLI 和 Qt worker 服务；若以后增加 async API，必须返回同语义 handle，不在 GUI 内直接引入 event loop
6. handle 必须可序列化和重新 attach，只保存 `server_id`、`run_id`、accepted protocol/version 与必要 identity snapshot；不得持有不可恢复的 SSH session、线程或进程对象

**验收:** 现有 submit/monitor/download/resume 行为和数据库内容不变；GUI 没有 `remote.*` import；所有新增接口有 fake transport contract tests。

**估算工时:** 5–7 小时

### 5.4 Phase B — Control protocol RFC 与 schema freeze（只设计，不增加入口）

本阶段只冻结机器可读协议，不实现 `confflow control` 命令，不改变现有 CLI/agent/JobDesk 行为。RFC 至少定义：

```text
confflow control capabilities --json
confflow control prepare --request <request.json> --json
confflow control execute --run-id <id> --json
confflow control status --run-id <id> --json
confflow control events --run-id <id> --after <cursor> --json
confflow control cancel --run-id <id> --json
confflow control resume --run-id <id> [--checkpoint <id>] --json
confflow control artifacts --run-id <id> --json
```

RFC 必须包含 request/response JSON Schema、状态转换表、error code registry、revision/cursor 规则、idempotency/recovery 语义、artifact path 安全规则和 compatibility policy。所有示例必须先由 schema fixture 验证，但本阶段不得新增第三套状态机或可执行入口。

**独立批准门:** 用户批准路线 B 只表示认可该方向；RFC/schema review 通过后，仍需单独批准 Phase C 实施。未经批准不得开始 `ExecutionService` 提取或 control CLI 编码。

**验收:** schema golden/negative fixtures 通过；JobDesk 与 ConfFlow reviewer 对状态 owner、scheduler 边界、错误语义和兼容周期达成书面一致；源码运行行为不变。

**估算工时:** 3–5 小时

### 5.5 Phase C — ConfFlow 唯一 `ExecutionService` 内部收敛

本阶段先消除重复状态语义，再增加新入口：

1. 从 `workflow/engine.py` 和 agent lifecycle 中提取应用级 `ExecutionService`，但不重写 calc executor、workflow step semantics 或 scheduler adapter
2. service 统一拥有 prepare/execute/status/events/cancel/resume/artifacts 状态转换；artifact writers、checkpoint store、event append 和 revision 分配形成单一实现
3. 普通 `confflow` CLI 改为调用 service 的 direct-run adapter，外部命令行为保持兼容
4. `confflow-agent` 改为调用同一 service；queue/slot/daemon 只负责调度和进程托管，不再定义第二套 job 状态
5. JobDesk 此时仍使用 legacy backend，不读取 agent SQLite，也不依赖尚未实现的 control CLI
6. service ports 使用 fake/in-memory store 做 contract tests；SQLite/file implementation 是内部 adapter，不暴露给 consumer

**验收:** normal CLI 与 agent adapter 对同一 fake workflow 产生一致 state transition、events、checkpoint 和 artifact manifest；旧 CLI/agent 回归通过；仓库中只有一个 service-level 状态转换实现。

**估算工时:** 8–12 小时

### 5.6 Phase D — 薄 one-shot control adapter

在 Phase C 单一 service 之上实现 Phase B 已冻结的 `confflow control ... --json`；adapter 只负责 CLI 参数、schema decode/encode、exit code 映射和调用 `ExecutionService`，不得复制状态判断。

协议执行规则：

1. request/response 使用显式 `protocol_schema`；未知 major fail closed，新增 optional 字段保持 forward compatibility
2. `prepare` 接受 JobDesk 生成的 `run_id`、idempotency key、workflow config digest、input manifest digest、expected executable identity，并在不启动计算的前提下原子写入 durable `prepared` record
3. 相同 idempotency key + 相同 request digest 返回原 handle；相同 key + 不同 payload 返回稳定 conflict error
4. 所有响应包含 `run_id`、`revision`、状态、机器可读 `error.code`；human message 只用于展示，不参与分支判断
5. `events` 使用稳定 cursor，支持断线重连和增量读取；不要求 WebSocket
6. `artifacts` 只返回经过 producer 校验的相对路径、terminal 分组、digest、size 和 content schema
7. `cancel`/`resume` 是 service 状态转换，不允许 JobDesk 直接写 beacon、删除 checkpoint 或修改 producer DB
8. one-shot CLI 可以继续用文件/SQLite adapter 持久化，但布局不进入公开协议
9. `ConfFlowClient.submit()` 是 JobDesk 应用层原子操作，不等同于远端单个命令：内部顺序是 upload → `control prepare` → JobDesk 持久化 handle/provenance → 现有 nohup/scheduler launcher 启动 `control execute`
10. `control execute` 是普通前台进程，既可被 `nohup setsid` 启动，也可作为 Slurm/PBS job body；它不自行选择 scheduler、不二次 daemonize
11. JobDesk submit journal 权威覆盖 upload/prepare/launch/remote-acceptance；ConfFlow 从 `prepared` record 开始权威记录 execution state。scheduler queued metadata 可保留在 JobDesk projection，但不得覆盖 producer revision
12. `prepared` record 永不自行开始计算；JobDesk 在 prepare 成功但本地持久化失败时，通过 idempotency key 重新 attach 并完成持久化，或对仍为 prepared 的 run 执行幂等 cancel

**验收:** control adapter 与 direct CLI/agent 运行同一 service contract；prepare 不产生计算进程；重复 prepare、重复/并发 execute、cursor replay、terminal race、非法转换和路径穿越均覆盖；nohup/Slurm/PBS 共用同一 execute contract。

**估算工时:** 6–10 小时

### 5.7 Phase E — JobDesk 切换到 control backend

1. `SSHConfFlowClient` 从 legacy shell/file adapter 切换为 `control --json`
2. JobDesk monitor 以 cursor 拉取 events，以 revision 更新 projection；旧 `events.log` tail 仅作兼容 fallback
3. download 只消费 `artifacts()` manifest；不再由 `result_templates` 猜测 ConfFlow 产物
4. normal/resume/cancel/status 共用 `RemoteRunHandle`；GUI、CLI 和恢复流程不再各自理解远端文件布局
5. compatibility feature flag 只允许在完整 run 边界选择 legacy 或 control backend，不允许同一 run 中途混用两种状态源

**验收:** capability → upload → idempotent prepare → persisted handle → execute → reconnect/events → status → artifacts/download → resume/cancel 的真实 WSL 链路通过；JobDesk control backend 不解析 producer 私有状态文件。

**估算工时:** 8–12 小时

### 5.8 双仓 contract-first CI

1. ConfFlow 是 control protocol JSON Schema、capability schema 和 artifact schema 的唯一 owner
2. producer release artifacts 携带 schema bundle；JobDesk pin 精确 tag/commit，并从 bundle 生成或镜像 typed fixtures
3. ConfFlow PR 运行 `producer candidate × JobDesk main` compatibility suite
4. JobDesk PR 运行 `JobDesk candidate × current stable producer × next producer candidate` matrix
5. 自动 PR 更新 pinned producer tag/schema fixture；禁止人工只改一侧字符串后直接合并
6. protocol/schema breaking change 必须提高 major；兼容字段扩展只提高 minor，并保留至少一个 consumer 迁移周期

**估算工时:** 4–6 小时

### 5.9 Phase F — 兼容层删除与 agent 策略

1. legacy shell/file backend 至少保留一个已发布兼容周期；期间记录实际使用率和 fallback 原因
2. control backend 完成真实 WSL、nohup、Slurm/PBS、断线恢复和下载验收后，删除 JobDesk 对 `.jobdesk_status`、`.jobdesk_exit_code`、producer 私有 state/artifact 猜测的业务依赖；底层 launcher 日志可保留为诊断
3. `confflow-agent` 保留为可选 daemon adapter，但只能调用统一 `ExecutionService`；若确认无独立用户，可另行 deprecate/remove，其删除不影响 control protocol
4. 删除兼容代码时同步删除专用 parser、分支和过时测试，记录生产代码与测试代码的净变化

**验收:** 每个远端 run 只选择一个 backend；无 JobDesk/agent DB 双写；删除后全量回归和真实 acceptance 通过；代码量报告区分新增协议模型、删除重复状态机和测试净变化。

**估算工时:** 4–6 小时

---

## 6. Monorepo/submodule 评估（不实施）

**背景:**

`docs/MONOREPO_RFC.md` 草案存在 4 个硬阻塞未答（§1.1-1.4）：
1. 远端 CLI 入口如何保持独立
2. Linux 端依赖（rdkit/numba/scipy）如何不污染 Windows 构建
3. 过渡方案（现有 WSL `/opt/ConfFlow` 如何平滑迁移）
4. 发布机制（单 tag 双 wheel or 单 wheel 条件依赖）

**建议:**

本计划**不启动 monorepo/submodule 迁移，也不预先选定“submodule + 单 wheel”**。必须先回答 RFC 4 个硬阻塞，再在独立 RFC 中比较保留双仓、submodule、subtree/vendor 和真正 monorepo；发布载体与 producer 独立性属于待决项，不在本文先下结论。

**责任方:** 用户决策

**估算工时:** N/A（不实施）

---

## 7. 实施优先级与时间表

| 任务 | 优先级 | 责任方 | 估算工时 | 依赖 | 目标完成时间 |
|---|---|---|---|---|---|
| P0 - 1.4.4 provenance/release/deployer | **P0** | ConfFlow | 8–10h | 无 | 第一阶段 |
| P1 - executable identity | **P0** | JobDesk | 6–8h | producer identity payload | 第三阶段 |
| P1 - DAG legacy 迁移 | P1 | ConfFlow | 2–3h | 无 | 第一阶段 |
| P1 - capability/artifact/schema | P1 | 两仓库 | 4–5h | producer 先行 | 第一/三阶段 |
| P2 - 文档事实修复 | P2 | ConfFlow | 1–2h | producer 实现完成 | 第一阶段 |
| M2-4A Gate A/B/C | **P0** | 两仓库 | 3–4h | 分阶段实现 | 第一/二/三阶段 |
| M2-4B 真实 g16 smoke | 单独授权 | JobDesk | 2–4h | M2-4A 全绿 | 第四阶段 |
| Phase A - client/handle compatibility façade | P1 | JobDesk | 5–7h | M2-4A 或并行独立分支 | 第五阶段 |
| Phase B - control protocol RFC/schema | 设计门 | 两仓库 | 3–5h | 1.4.4 closure 完成 | 第六阶段 |
| Phase C - ExecutionService 收敛 | P1 | ConfFlow | 8–12h | Phase B 单独批准 | 第七阶段 |
| Phase D - thin control adapter | P1 | ConfFlow | 6–10h | Phase C 全绿 | 第八阶段 |
| Phase E - JobDesk control backend | P1 | 两仓库 | 8–12h | Phase A/B/C/D | 第九阶段 |
| 双仓 contract-first CI | P1 | 两仓库 | 4–6h | Phase B schema bundle | 第八/九阶段 |
| Phase F - legacy cleanup/agent policy | P2 | 两仓库 | 4–6h | 一个兼容发布周期 | 第十阶段 |
| Monorepo | 不实施 | 独立 RFC | N/A | control protocol 稳定 | 待明确需求 |

**1.4.4 release closure 基线工程工时:** 26–36 小时，已经包含编码和计划内测试，不包含等待用户批准、远端服务排队或网络恢复时间。

**Post-M2 collaboration architecture 增量:** 38–58 小时。它不并入 1.4.4 release gate，不得为了追求一次性完成而扩大当前发布范围。

**总计划基线:** 两条轨道合计 64–94 小时；分阶段实施和验收，不作为单个大 PR 或同一 release 的承诺。

建议拆分：

1. **第一阶段 — Producer pre-tag candidate:** P0、DAG、producer contract、相关文档和 M2-4A Gate A；全绿后停止
2. **第二阶段 — Tagged producer release:** 单独授权创建/推送 `v1.4.4` 和发布 artifacts，然后执行 Gate B
3. **第三阶段 — Consumer integration:** executable identity、JobDesk contract/schema、专用 parity gate和 M2-4A Gate C
4. **第四阶段 — Real g16:** 先修复 smoke 脚本，再单独请求授权运行 M2-4B
5. **第五阶段 — Compatibility façade:** 在 JobDesk 内建立 client/handle 边界，保持 legacy backend 行为不变
6. **第六阶段 — Protocol design gate:** 只完成 RFC/schema/fixtures，review 后再次请求实施授权
7. **第七阶段 — Producer state convergence:** 提取 ExecutionService，先让 normal CLI/agent 共用状态语义
8. **第八阶段 — Thin control adapter:** 实现 one-shot JSON adapter 和 producer-side contract CI
9. **第九阶段 — Consumer cutover:** JobDesk 切换 control backend，跑真实 WSL/scheduler 验收
10. **第十阶段 — Compatibility removal:** 保留一个发布周期后删除 legacy backend，并决定 agent 保留或弃用

每阶段独立设计检查、独立授权、独立提交、独立验收；producer 发布失败时不得提前合并只接受 1.4.4 的 consumer 变更。第五阶段以后不得与 1.4.4 release hotfix 混在同一提交或 PR。批准路线 B 或批准本文档不自动授权第二至第十阶段连续实施。

---

## 8. 验收标准总结

### 8.1 ConfFlow 1.4.4 release closure

This checklist is the historical 1.4.4/1.4.6 release-closure record. The
current v1.5.0 consumer reference, v1.4.6 legacy exception, and review status
are recorded at the top of this plan; the post-M2 control track is tracked in
§8.2 below.

以下项目全部完成后，才可宣布 M2-4 / 1.4.4 release closure：

- [x] M2-1~M2-3 基线为 JobDesk `44719e9` + ConfFlow `10e457d`
- [ ] `v1.4.3` tag/artifacts 未改变
- [ ] ConfFlow `v1.4.4` tag、peeled commit、wheel、`SHA256SUMS` 和远端 ref 已验证
- [ ] install provenance digest 等于最终 wheel 字节 digest；capability 不含 `"unbound"`
- [ ] probe/dry-run/runner/resume 对 realpath、device/inode、digest fail closed
- [ ] DAG legacy import 发出有效弃用警告，新 engine 只用 explicit DAG
- [ ] producer/consumer 同步六个 artifact 字段和四个完整内容 schema
- [ ] 专用 contract-parity gate 证明 CI producer checkout HEAD、`v1.4.4^{}`、wheel `__build__.COMMIT`、attestation subject 和 capability payload 来自同一 commit/artifact
- [ ] output manifest parser/download path traversal 防护通过
- [ ] M2-4A 非计算 smoke 通过
- [ ] JobDesk targeted、全量 non-integration、Ruff、MyPy、offscreen smoke 与 diff check 通过
- [ ] 如用户另行批准，M2-4B 两个真实 g16 smoke 通过且 g16 安装身份未变化
- [ ] 两仓库最终 commit、tag、remote ref、验证结果和残余用户修改均明确记录

M2-4B 未获授权时必须明确记录为“release closure 已完成，真实 g16 gate 未执行”，不得伪装成已通过，也不得阻塞非计算 release 证据的归档。

### 8.2 Post-M2 collaboration architecture

以下项目属于后续独立轨道，不阻塞 8.1：

- [x] GUI 不再 import `remote.*`；architecture test 固定依赖方向
- [x] `ConfFlowClient` / `RemoteRunHandle` façade 覆盖 probe/submit/attach/status/events/cancel/resume/artifacts
- [x] façade 先通过 legacy backend 回归，未改变现有远端行为
- [x] producer 发布版本化 `control --json` schema bundle 和 one-shot CLI（Phase D；commit `1d25594cb15404c984f1dd2bf618f152d486f49d`，remote `origin/codex/control-json-adapter`）
- [x] `run_id`、idempotency key、revision、event cursor 和 typed error contract 通过正反例测试
- [x] CLI normal run、control CLI 与可选 agent backend 共用 `ExecutionService` 和状态转换语义（Phase C/D 已验证）
- [x] JobDesk control backend 真实完成 reconnect、incremental events、manifest download、resume/cancel（Phase E；WSL 使用 pinned Phase D producer；artifact 为非计算 synthetic lifecycle fixture，未运行 g16）
- [ ] 双仓 CI 覆盖 producer candidate × JobDesk main，以及 JobDesk candidate × stable/next producer（ConfFlow PR #50 `730decf` green；JobDesk matrix workflow 已在本隔离分支提交并固定 stable/next wheel digest，2026-08-08 本地 Python 3.13 两矩阵各 `88 passed`，但 JobDesk feature branch 尚未发布，故第二方向尚未取得远端 CI 结果）
- [ ] legacy shell/file backend 至少保留一个兼容发布周期后才删除
- [x] JobDesk 不读取 agent SQLite，不与 producer 状态库双写

The remaining real-acceptance gate is intentionally open. The 2026-08-08 direct
v1.5.0 g16 and ORCA probes completed, but they were not JobDesk SSH lifecycle
samples. A pre-restart JobDesk SSH/SFTP attempt was blocked by the WSL SSH
listener's `Exceeded MaxStartups`/`rtnl_dumpit` failure; after the authorized
restart, one real v1.5.0 legacy batch, one stable v1.4.6 rollback probe, and a
real control launcher handoff reaching queued were captured separately. The
pinned producer's `control execute` still returns a queued launch intent until
an external worker handoff is supplied, so there is no real control-computation
sample. Keep both backends and the v1.4.6 rollback path; do not mark Phase F
ready or delete compatibility code from these probes.

---

## 9. 风险与回退策略

### 风险 1: release/install provenance 不一致

**症状:** checksum、tag commit、安装记录或 capability 任一不一致

**回退:** 拒绝安装/提交并保留已验证的旧 venv；不得修改 wheel、移动 `v1.4.3` tag 或把未知值写成 `"unbound"`

**预防:** 最终 wheel 只生成一次；外部 `SHA256SUMS` 对最终字节计算；部署器和 capability 双向校验 version/commit/digest

### 风险 2: executable realpath 验证误报

**症状:** 合法的符号链接被误判为"路径变化"

**回退:** 停止该次提交并保留诊断；production 不提供跳过 identity verification 的开关

**预防:** 临时夹具覆盖稳定 symlink、retarget、同路径 inode 替换和内容 digest 替换

### 风险 3: WSL g16 污染

**症状:** smoke 脚本意外写入 `/opt/g16/g16` 或 `/opt/g16/l1.exe`

**回退:** 按 `.cursor/rules/wsl-g16-safety.mdc` §1 硬规则，立即停止所有 smoke，报告给用户

**预防:** M2-4A 不运行真实计算；M2-4B 单独授权，4-line probe 自动断言；脚本不得传 `--yes`，不得加入 `/opt/gauopen`，timeout 至少 900 秒

### 风险 4: client façade 变成新的平行调用链

**症状:** GUI/CLI 一部分走 `ConfFlowClient`，另一部分继续直接实例化 `JobSubmitter`、解析 `events.log` 或调用 SFTP

**回退:** Phase A 不删除现有 backend，只回退 façade wiring；不得保留两套 shell generator 或 parser

**预防:** façade 第一版必须委托现有实现；architecture test 禁止 GUI 依赖 remote，并用调用链测试证明 submit/status/download/resume 均经过 client/handle

### 风险 5: JobDesk/ConfFlow split-brain

**症状:** producer 已进入 terminal revision，JobDesk 被旧轮询结果回写为 running；或同一 idempotency key 对应不同 payload

**回退:** 停止自动状态推进，保留 producer snapshot 和本地 journal，要求显式 reconcile；不得用 JobDesk projection 反写 producer

**预防:** revision 单调、event cursor 可重放、request digest 冲突 fail closed、terminal state 不允许回退；用断线重连和乱序响应测试验证

### 风险 6: control protocol 与 agent 再次分叉

**症状:** direct CLI、control CLI 和 agent 对 pause/resume/cancel、artifact 或错误码给出不同语义

**回退:** agent backend 保持关闭，JobDesk 继续使用验证过的 one-shot backend

**预防:** 三个入口共用 `ExecutionService`；状态转换、events 和 artifact contract tests 不针对入口复制，而针对 service 运行后再对各 adapter 做薄层一致性测试

---

## 10. 验证矩阵

### ConfFlow

```bash
python -m ruff check .
python -m mypy confflow
python -m pytest -q
python -m build
```

另运行 release provenance、CLI capability、DAG compatibility 和部署器专项测试。发布物必须来自 clean tagged worktree。

### JobDesk

```powershell
python -m ruff check .
python -m mypy src
python -m pytest tests/test_confflow_preflight.py tests/test_confflow_results.py tests/test_confflow_executable_binding.py tests/test_submitter.py tests/test_run_service.py -q --basetemp .pytest_tmp_post_m2_targeted
python -m pytest tests -q --ignore=tests/integration --basetemp .pytest_tmp_post_m2_full
python scripts/smoke_gui_offscreen.py
git diff --check
```

跨仓测试必须安装本轮最终 ConfFlow 1.4.4 wheel，不得误用旧 editable install 或 public PyPI 上的同名无关包。

### 跨仓 contract-parity gate

新增专用测试（例如 `tests/test_confflow_contract_parity.py`）；`tests/test_confflow_validation_differential.py` 继续只负责 YAML validator differential，不得承担 wire-contract 验证。

该 gate 使用明确的 producer checkout、最终 wheel、checksum/attestation 和已安装 executable，至少证明：

1. ConfFlow `contract.py` 的六个 artifact 字段与 JobDesk `ConfFlowArtifactContract` 逐字段一致
2. ConfFlow 四个内容 schema 常量与 JobDesk 常量逐字符串一致
3. JobDesk `.github/workflows/ci.yml` 的两处 producer checkout 及 `.github/workflows/optional-coverage.yml` 的 producer checkout 都使用精确 `v1.4.4`，相应 wheel glob/version assertion 同步；各 checkout HEAD 等于远端 `v1.4.4^{}`
4. 最终 wheel 中 `__build__.COMMIT` 等于该 peeled commit，wheel digest 等于 `SHA256SUMS` 和 attestation subject digest
5. 该 wheel 安装后的真实 `confflow --capabilities --json` 能被 JobDesk producer/artifacts/executable parsers 和 production validator 接受
6. test 输出记录 exact tag、peeled commit、wheel filename/digest 和 executable path，便于发布审计

### Post-M2 control-protocol matrix

Phase A–E 采用 transport-independent contract tests。fake/in-memory transport 必须覆盖全部语义；真实 SSH 只验证 adapter 和端到端边界，不把业务测试全部绑定到 WSL。

| 维度 | 必测场景 |
|---|---|
| Submit | first prepare、identical retry、same-key different-payload conflict、prepare accepted/local persist failure、duplicate/concurrent execute |
| Identity | explicit path、PATH fallback、symlink stable/retarget、inode/content replacement |
| Status | revision monotonicity、out-of-order snapshot、terminal non-regression、unknown run |
| Events | empty page、pagination、cursor replay、disconnect/reconnect、malformed/unknown event |
| Resume | valid checkpoint、missing/stale checkpoint、already running、terminal-state policy |
| Cancel | pending/running/terminal transitions、repeated cancel、process already exited |
| Artifacts | multi-terminal manifest、digest/size mismatch、absolute path、`..`、duplicate/conflicting target |
| Compatibility | legacy backend vs control backend equivalent projection；stable producer vs next candidate |
| Entrypoints | normal CLI、control CLI、optional agent adapter 产生相同 service-level state/events/artifacts |

真实 WSL acceptance 链：

```text
capability/protocol negotiation
  -> upload request/input manifest
  -> idempotent prepare returns handle
  -> persist handle/provenance
  -> nohup/scheduler launches execute
  -> detach/reconnect
  -> incremental events + monotonic status
  -> artifact manifest + verified SFTP download
  -> resume/cancel transition
```

测试通过数量不能替代该链路的逐阶段证据。

---

## 11. 参考文档

| 文档 | 路径 | 用途 |
|---|---|---|
| Architecture Review | `jobdesk_confflow_architecture_review.md` | 评审发现与建议来源 |
| M2 Design | `docs/superpowers/plans/2026-07-28-jobdesk-confflow-milestone2-design.md` | D1/D6 设计决策 |
| M2 Remediation | `docs/superpowers/plans/2026-07-28-jobdesk-confflow-architecture-remediation.md` | M2-1~M2-3 实施记录 |
| WSL g16 Safety | `.cursor/rules/wsl-g16-safety.mdc` | WSL 不可写规则 |
| Monorepo RFC | `docs/MONOREPO_RFC.md` | 中期评估路径 |
| ConfFlow RELEASE.md | `/opt/ConfFlow/docs/RELEASE.md` | 外部 checksum、安装 provenance 与 immutable tag 发布流程 |
| PyTRIO ServiceClient | `https://docs.pytrio.com/docs/api/ServiceClient` | client factory 与 scoped client 公开 API 参考 |
| PyTRIO async guide | `https://docs.pytrio.com/docs/guide/async` | submit 与 await 分离的 handle/future 参考 |
| PyTRIO TrainingClient | `https://docs.pytrio.com/docs/api/TrainingClient` | checkpoint、resume 与 typed result 公开 API 参考 |

---

## 12. 后续计划

完成本计划后，下一阶段可考虑：

1. **M3 - 多终端聚合与可视化** — GUI 直接消费 `RemoteRunHandle.artifacts()` 的 terminal 分组，资源统计按 manifest 分项
2. **M4 - 远端诊断增强** — 在 capability/control protocol 增加版本化 environment diagnostics；避免自由扩展未建模字符串
3. **M5 - 提交事务可视化** — GUI 分开展示 JobDesk submit journal 与 ConfFlow execution revision，不把两套状态压成一个不可审计字段
4. **Agent backend decision gate** — 只有出现 multi-tenancy、quota 或共享远端队列需求时才启用，并复用既有 control protocol
5. **Monorepo RFC 硬阻塞回答** — 如决定继续评估，先比较保留双仓、submodule、vendor/subtree 与真正 monorepo；client/protocol 边界稳定后，monorepo 的收益会进一步降低

---

> 执行授权边界：批准本文档或选择路线 B 只表示认可总体架构和阶段划分，不自动授权任何实施阶段。每个阶段均需单独授权；第一阶段授权不包含创建/push tag、发布 artifacts、第二至第十阶段、推送源代码或运行真实 Gaussian 计算。
