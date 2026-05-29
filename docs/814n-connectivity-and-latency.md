# 814n 连接稳定性与延迟优化说明

**日期：** 2026-05-29
**范围：** `814n` 服务器（经 WSL + frp 隧道访问的 814 计算服务器）的「连不上」诊断、延迟分析、已尝试/已采纳的优化，以及运维备注。

> 注：本文涉及的 frp `auth.token`、`secretKey` 等机密一律以 `<redacted>` 表示，不在文档中记录其值。

## 1. 服务器条目与连接链路

`%APPDATA%\JobDesk\servers.yaml` 中与 814 相关的两个条目：

| 条目 | 目标 | 路径 | 现状 |
|---|---|---|---|
| `814new` | `100.112.123.8:22` | Tailscale 直连（`100.64/10` 网段） | 当前网络下不可用（见第 5 节） |
| `814n` | `127.0.0.1:10022` + `wsl_distro: Ubuntu` | WSL 内 frpc STCP 隧道 → frps 中继 → 814 sshd | 可用，但延迟高 |

`814n` 的实际数据通路：

```
Windows 127.0.0.1:10022
  →(WSL 镜像网络)→ WSL 内 frpc(STCP visitor, /opt/frp/visitor.toml)
    → frps 中继 159.65.130.195:9443
      → 814 服务器 frpc(STCP proxy, /opt/frp/frpc.toml)
        → 814 本机 sshd:22
```

- WSL 端 frpc 由 systemd 服务 `jobdesk-frpc-visitor` 启动。
- 814 端 frpc 由 systemd 服务 `frpc.service`（enabled）启动。
- WSL 采用 `networkingMode=Mirrored`。

## 2. 「连不上」根因（间歇性）

不是配置错误，而是**时序竞态 + WSL 空闲休眠**：

1. WSL 在无活动约 15 秒后自动休眠；隧道（frpc）随之消失，`127.0.0.1:10022` 关闭。
2. JobDesk 连接时通过 `wsl.exe -d Ubuntu -- true` 唤醒 WSL，但**唤醒后未等隧道就绪就立即发起 SSH 连接**。
3. 端口尚未监听时，TCP 立即返回 `connection refused`（不是超时），表现为「连不上」。
4. `_start_wsl_if_configured` 的 10 秒冷却（`_WSL_BOOT_COOLDOWN`）会在失败后短时间内抑制再次唤醒，使快速重试也快速失败。

隧道本身健康（frpc 日志每次 `login to server success` / `start proxy success`，无错误）。

## 3. 延迟根因与实测数据

瓶颈是 **frp 中继的高 RTT 被 SSH 的多次串行往返放大**：

| 指标 | 实测 |
|---|---|
| 到中继 `159.65.130.195` 的 ICMP RTT | ~220 ms（TTL=41，境外） |
| 直连 `100.112.123.8`（814new） | 100% 丢包（当前不可达） |
| SSH 握手（`connect()`，WSL 已热） | ~2.2 s |
| 复用连接上单条命令（`echo x`） | ~1.85 s |

一次状态刷新（优化前）= 重新建连 + 3 条 SSH 命令 ≈ **`2.2 + 3×1.85 ≈ 7.7 s`**。
（3 条命令来自 `refresh_batch_status`：`_read_batch_control` 的 2 条 + 批量状态 1 条。）

## 4. 方案 #1：frp xtcp（P2P）—— 已尝试，已回滚

按 frp 官方推荐的 `xtcp + stcp fallback` 模型在两端配置并上线（保留 stcp 作回退，永不丢失现有入口），但 **xtcp 在本环境不可行**：

- WSL frpc 日志：`nathole prepare error: discover error: lookup stun.miwifi.com ... no such host`，随后 `open tunnel error: open tunnel timeout`，**永久回落 stcp**。
- 根因：本机出网经 **fake-IP 代理**（Clash 类）。证据：STUN 域名在 Windows 上解析成回环段假地址（如 `stun.miwifi.com → 127.135.4.2`），WSL 内 Go 解析直接 NXDOMAIN。fake-IP 代理下 STUN 拿不到真实 NAT 映射，也无法与 814 直接打洞。
- 副作用：`fallbackTimeoutMs` 使每次连接先等待再回落，反而**增加**延迟。

**处置：已将两端完整回滚到原始纯 stcp 配置并验证可用。** 备份见第 7 节。

## 5. Tailscale（`814new`）测试结论 —— 当前不可用

`100.112.123.8` 属 Tailscale `100.64/10` 网段。测试结果：

- `tailscale ping 100.112.123.8` → `unexpected state: NoState`（本机后端未进入 Running，`status` 中本机显示 offline）。
- `100.112.123.8:22` TCP 不可达。
- `tailscale netcheck`：`using proxy "http://127.0.0.1:8080"`，且 `Failed to fetch a DERP map ... controlplane.tailscale.com ... context deadline exceeded`；健康检查报 `set DNS configuration: Access is denied`。

**根因与 xtcp 同源**：本机出网经 `127.0.0.1:8080` 代理，Tailscale 连不上控制面/DERP，无法上线；外加缺管理员权限。

若要启用 Tailscale，需在系统侧（非本仓库）：①让 Tailscale 流量绕开 fake-IP 代理直连 `controlplane.tailscale.com:443` 与 DERP；②以管理员运行 Tailscale 服务；③`tailscale up`。

## 6. 方案 #2 + #3：已实施的代码优化（与代理/隧道无关）

转向仓库内、不依赖网络改造的提速：

### #2 一次刷新只发 1 条 SSH 命令（原 3 条）
- `src/jobdesk_app/remote/status.py`：`read_remote_task_statuses_batch` 与 `_build_batch_script` 新增 `extra_files` / `extra_out`，可在同一条批量命令里读取任意附加文件。
- `src/jobdesk_app/remote/status_refresh.py`：`refresh_batch_status` 把 `batch_control` 的两次读取并入批量命令；以纯解析函数 `_parse_batch_control` 取代会发起 SSH 的 `_read_batch_control`；移除不再使用的 `shlex` 导入。

### #3 跨刷新复用持久 SSH 连接 + keepalive
- `src/jobdesk_app/remote/ssh.py`：连接成功后 `transport.set_keepalive(15)`；新增 `is_alive()`。
- `src/jobdesk_app/gui/pages/runs_results_page.py`：按 `server_id` 缓存 `(ssh, sftp)`，自动刷新与手动刷新共用；单锁串行化（SFTP 不可并发）；存活检测 + 懒重连；失败时仅丢弃**已死**的连接；`shutdown()` 关闭全部。keepalive 流量顺带让 WSL/隧道保温，缓解第 2 节的冷启动。

### 效果
单次状态刷新由 ~7.7 s 降到 ~1.85 s（仍走中继时）。

### 验证
`ruff` 通过；`mypy` 通过；`pytest tests` 全量 **561 passed / 9 skipped**。
涉及测试：`tests/test_status_refresh.py`、`tests/test_remote_status.py`、`tests/test_gui_behavior.py`。

## 7. 运维备注

- **frp 配置当前为原始纯 stcp 状态**（xtcp 实验已回滚）。
- 回滚时保留的备份（如需清理可删）：
  - 814 端：`/opt/frp/frpc.toml.bak.20260529_162622`
  - WSL 端：`/opt/frp/visitor.toml.bak.20260529_163122`
- 重启 frpc（如再次调整）的安全做法：先 `frpc verify -c <新配置>`；814 端因唯一入口即该隧道，用 `systemd-run --on-active=3 systemctl restart frpc` 脱离当前会话重启，并保留 stcp 以免锁死。

## 8. 后续可选项（按收益）

1. **更就近的 frps 中继**：220 ms 主要是境外中继的物理距离；换同地域中继可让 stcp 也大幅提速（收益最大，需部署）。
2. **代理放直连 + Tailscale**：在 Clash/代理规则中对 Tailscale 控制面/DERP 与 `100.64/10` 走直连，并以管理员启用 Tailscale，则 `814new` 可绕开中继。
3. xtcp 仅在去除 fake-IP 代理出网后才有意义，当前不推荐。
