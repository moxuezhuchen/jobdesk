> **Current release:** ConfFlow 1.4.6 is the certified producer for this JobDesk branch. The filename is retained for deployment-history compatibility.

> **Current 1.4.6 contract:** use `confflow>=1.4.6,<2.0` and the released
> `confflow-1.4.6-py3-none-any.whl` whose SHA-256 is
> `7d036a44784d581b5b2fec2443f9cac7a0b2257d08b85c1a1b797bae565f75f5`.
> The older 1.4.5 command examples below are retained only as deployment
> history; substitute the current approved wheel and preserve the controlled
> dependency lock/wheelhouse procedure.

# ConfFlow 1.4.5 Wheel 构建与部署指南

JobDesk 的 `chem` extra 要求 `confflow>=1.4.5,<2.0`。公共 PyPI 上名为
`confflow` 的项目不是本化学工作流引擎；请先使用经过批准的 ConfFlow
1.4.5 release wheel，再安装 JobDesk 的化学 extra。

权威源码仓库位于 `Ubuntu-24.04:/opt/ConfFlow`。

## 构建

在不访问网络、不安装 WSL 包的前提下，使用现有构建工具：

```bash
wheel=confflow-1.4.6-py3-none-any.whl
sha256sum "$wheel"
# expected: 7d036a44784d581b5b2fec2443f9cac7a0b2257d08b85c1a1b797bae565f75f5
```

## Windows 验证安装

```powershell
C:\dft\tool\verify-venv\Scripts\python.exe -m pip install `
  --no-index --no-deps --force-reinstall `
  \\wsl.localhost\Ubuntu-24.04\tmp\confflow-1.4.6-release-download-verify2\confflow-1.4.6-py3-none-any.whl
```

验证版本、来源和 capability handshake：

```powershell
C:\dft\tool\verify-venv\Scripts\python.exe -c `
  "import confflow; print(confflow.__version__, confflow.__file__)"
C:\dft\tool\verify-venv\Scripts\confflow.exe --capabilities --json
```

预期版本为 `1.4.5`，且 capability JSON 必须满足 schema v4：

```json
{
  "schema_version": 4,
  "artifacts": {
    "run_summary": "run_summary.json",
    "workflow_stats": "workflow_stats.json",
    "workflow_state": ".workflow_state.json",
    "output_manifest": "output_manifest.json",
    "run_report": "{basename}.txt",
    "min_xyz": "{basename}min.xyz"
  }
}
```

schema v4 还必须包含 `producer`（package/version/build/wheel）和 `executable`
（path/sha256/python）两个 provenance 块；正式 wheel 发布要求 `wheel.sha256` 非空。

同时，`capabilities.workflow_state`、`capabilities.resume`、`capabilities.dag`
均必须为 `true`。

## 远端计算节点

Linux 计算节点也必须安装同一份 1.4.5 release wheel，并先安装受控依赖：

```bash
python3 -m pip install --no-index --find-links /path/to/wheelhouse --require-hashes -r /path/to/confflow-1.4.5-py312-linux-x86_64.lock
python3 -m pip install --no-deps /path/to/confflow-1.4.6-py3-none-any.whl
python3 -m pip check
confflow --version
confflow --capabilities --json
```

JobDesk 在输入上传前和提交阶段各执行一次 capability v4 preflight，并拒绝
不满足 `>=1.4.5,<2.0`、缺少任一必需能力、artifacts 不匹配、dirty/development/unverified provenance 或 release digest 不匹配的远端 ConfFlow。

## 发布边界

ConfFlow 的 release workflow 生成 wheel、source distribution、校验和及可选
SBOM，但不自动发布到公共 PyPI。离线部署仍需使用经过校验的本地或 GitHub
release artifact。
