# ConfFlow 1.4.0 Wheel 构建与部署指南

JobDesk 的 `chem` extra 要求 `confflow>=1.4.0`。权威源码仓库位于
`Ubuntu-24.04:/opt/ConfFlow`。

## 构建

在不访问网络、不安装 WSL 包的前提下，使用现有构建工具：

```bash
cd /opt/ConfFlow
python3 -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir dist .
sha256sum dist/confflow-1.4.0-py3-none-any.whl
```

## Windows 验证安装

```powershell
C:\dft\tool\verify-venv\Scripts\python.exe -m pip install `
  --no-index --no-deps --force-reinstall `
  \\wsl.localhost\Ubuntu-24.04\opt\ConfFlow\dist\confflow-1.4.0-py3-none-any.whl
```

验证版本、来源和 DAG API：

```powershell
C:\dft\tool\verify-venv\Scripts\python.exe -c `
  "import confflow; from confflow.workflow.dag import build_step_graph, topo_order; print(confflow.__version__, confflow.__file__)"
```

预期版本为 `1.4.0`，导入路径必须位于 `verify-venv\Lib\site-packages`。

## 发布

推送 `v1.4.0` tag 会触发 ConfFlow 的 `Release Artifacts` workflow，生成
wheel、source distribution、校验和及可选 SBOM。该 workflow 不发布到
PyPI，因此 JobDesk 离线部署仍需使用经过校验的本地或 GitHub artifact。
