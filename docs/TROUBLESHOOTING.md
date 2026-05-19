# JobDesk Troubleshooting

## SSH 连接失败

检查：
- Settings 页服务器配置（host, port, username, key_path）
- SSH 密钥文件是否存在且权限正确
- 服务器网络是否可达

验证：
```powershell
pytest tests/integration/test_real_ssh.py -v
```

## SFTP 上传失败

检查：
- 远端目录是否有写权限
- 路径是否使用 POSIX `/` 格式
- 本地文件是否存在

## 提交后无反应

确认：
- Files 页已连接到正确的服务器
- 选择了远端文件
- 命令模板正确（如 `g16 {name}`）

## 任务一直显示"运行中"

手动检查远端状态文件：
```text
<remote_dir>/.jobdesk_runs/<run_id>/<task_id>/.jobdesk_status
<remote_dir>/.jobdesk_runs/<run_id>/<task_id>/.jobdesk_exit_code
<remote_dir>/.jobdesk_runs/<run_id>/<task_id>/.jobdesk_submit.log
```

可能原因：
- 任务仍在运行（正常）
- 任务崩溃但未写 status 文件 → 右键"刷新状态"手动检测
- SSH monitor 断连 → 重启应用或手动刷新

## 下载失败

检查：
- Settings 页下载模式配置（Gaussian: `*.log,*.chk`）
- 远端是否生成了对应输出文件
- 本地 workspace 目录是否有写权限

状态保持 `remote_completed` 时可再次右键刷新重试。

## 结果分析为空

确认：
- 文件已下载到 `<workspace>/results/<run_id>/<task_id>/`
- 文件内容包含可识别的能量行（如 `SCF Done` 或 `HF=`）

## 应用关闭时卡住

正常退出应在 1-2 秒内完成。如果卡住：
- 可能是后台 SSH 操作未完成
- 强制关闭不会丢失数据（manifest 是原子写入的）

## Monitor 不自动更新状态

确认：
- Run 处于 `running` 或 `submitted` 状态
- 已切换到 Runs 页（monitor 在页面激活时启动）
- 服务器 SSH 连接正常
- 新提交的任务使用了最新版本的 run 脚本（旧 run 不会写 events.log）
