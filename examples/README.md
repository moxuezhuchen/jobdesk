# Examples

这些文件用于 JobDesk 的最小任务提交测试。

## Gaussian

在 GUI 的 **Files** 页上传并选中 `examples/gaussian/water_opt.gjf`，使用软件配置中的 Gaussian 命令提交任务。

CLI 等价示例：

```powershell
jobdesk run create . --server wsl --remote-dir /tmp/jobdesk_water --command "g16 {name}" --files /tmp/jobdesk_water/water_opt.gjf
jobdesk run submit . <run_id>
```

## ORCA

在 GUI 中上传并选中 `examples/orca/water_opt.inp`，使用 ORCA 配置提交。

```powershell
jobdesk run create . --server wsl --remote-dir /tmp/jobdesk_water --command "orca {name} > {basename}.out" --files /tmp/jobdesk_water/water_opt.inp
jobdesk run submit . <run_id>
```

## ConfFlow

ConfFlow 的内部步骤由其 YAML 配置控制。JobDesk 只负责选择一个或多个远端/本地 `.xyz`、选择 YAML、提交批次、自动下载声明的产物并展示执行摘要。请在 GUI 中使用 **运行 ConfFlow**，不要使用已移除的 `jobdesk workflow` 命令。

输出可被解析并不等于计算结果已经完成科学验证。
