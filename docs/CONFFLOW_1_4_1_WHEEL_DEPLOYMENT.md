# ConfFlow 1.4.1 Wheel 构建与部署指南

JobDesk 的 `chem` extra 要求 `confflow>=1.4.1,<2.0`。公共 PyPI 上名为
`confflow` 的项目不是本化学工作流引擎；请先使用经过批准的 ConfFlow
1.4.1 wheel，再安装 JobDesk 的化学 extra。

权威源码仓库位于 `Ubuntu-24.04:/opt/ConfFlow`。

## 构建

在不访问网络、不安装 WSL 包的前提下，使用现有构建工具：

```bash
cd /opt/ConfFlow
python3 -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir dist .
sha256sum dist/confflow-1.4.1-py3-none-any.whl
```

## Windows 验证安装

```powershell
C:\dft\tool\verify-venv\Scripts\python.exe -m pip install `
  --no-index --no-deps --force-reinstall `
  \\wsl.localhost\Ubuntu-24.04\opt\ConfFlow\dist\confflow-1.4.1-py3-none-any.whl
```

验证版本、来源和 capability handshake：

```powershell
C:\dft\tool\verify-venv\Scripts\python.exe -c `
  "import confflow; print(confflow.__version__, confflow.__file__)"
C:\dft\tool\verify-venv\Scripts\confflow.exe --capabilities --json
```

预期版本为 `1.4.1`，并且 capability JSON 中的
`workflow_state`、`resume`、`dag` 均为 `true`。

## 远端计算节点

Linux 计算节点也必须安装相同的 1.4.1 wheel：

```bash
python3 -m pip install --no-index --no-deps /path/to/confflow-1.4.1-py3-none-any.whl
confflow --version
confflow --capabilities --json
```

JobDesk 在启动工作流前会执行 capability preflight，并拒绝低于
`1.4.1` 或缺少任一必需能力的远端 ConfFlow。

## 发布边界

ConfFlow 的 release workflow 生成 wheel、source distribution、校验和及可选
SBOM，但不自动发布到公共 PyPI。离线部署仍需使用经过校验的本地或 GitHub
release artifact。
