# computer-use-linux 性能优化补丁

对上游 [agent-sh/computer-use-linux](https://github.com/agent-sh/computer-use-linux)
的三处性能改动，以 patch 形式随仓库保存（源码来自上游，不入库二进制）。

## 改动内容（`fast_app_filter.patch`，约 122 行，2 个文件）

1. **`src/atspi_tree.rs::connect()` — AT-SPI 连接复用**
   原实现每次 `AccessibilityConnection::new()`（重建 session bus 连接 + a11y
   bus 连接 + 代理初始化）。改为全局 `OnceLock` 缓存（zbus 连接是 Arc 句柄，
   `Clone` 零成本），首次建连后跨调用复用。

2. **`src/server.rs::get_app_state` — doctor_report 加 5s TTL 缓存**
   doctor_report 每次做 7 组系统探测（platform/portal/accessibility/windowing/
   input），反映"系统能力"而非瞬时状态，加 TTL 缓存避免每次 get_app_state 白付
   ~150ms。

3. **`src/server.rs::resolve_accessibility_app_filter` — 新增 `fast_app_filter` 参数**
   原逻辑：为把已知 pid 反查成 AT-SPI object_ref，**遍历全部 AT-SPI apps 逐个读
   pid**（全桌面，~280ms/次）。新增 `fast_app_filter`（默认 false）：为 true 且
   已知 target_pid 时跳过遍历，让 snapshot_tree/select_roots 按 pid 选 app。
   false（默认）保留原精确 object_ref 匹配行为。

## 实测收益（计算器，70 节点，热连接）

| 阶段 | 原版 | 改版 fast=false | 改版 fast=true |
|---|---|---|---|
| doctor_report | ~150ms | ~0ms(缓存) | ~0ms(缓存) |
| app_filter | ~280ms | ~280ms | ~0ms |
| snapshot_tree | ~145ms | ~145ms | ~140ms |
| **合计** | **~630ms** | **~478ms** | **~220ms** |

agent-gavel 端到端：`read_state` 620→235ms（2.6x）；`act_and_verify` 闭环
~1.5s→0.65s（2.3x）。树/动作结果与原版一致。

## 编译

```bash
# 需要 Rust 工具链
git clone --depth 1 https://github.com/agent-sh/computer-use-linux cul-src
cd cul-src
git apply /path/to/agent-gavel/third_party/computer-use-linux/fast_app_filter.patch
cargo build --release
# 产物: target/release/computer-use-linux
cp target/release/computer-use-linux ~/.local/bin/computer-use-linux-fast
```

## 启用（agent-gavel 默认已开）

`run-mcp.sh` 默认设置：
```bash
COMPUTER_USE_LINUX_BIN=$HOME/.local/bin/computer-use-linux-fast
AGENT_GAVEL_FAST_APP_FILTER=1
```

本地备用二进制在 `bin/computer-use-linux-fast`（git 忽略，不入库）。

## 回退

```bash
COMPUTER_USE_LINUX_BIN=computer-use-linux AGENT_GAVEL_FAST_APP_FILTER=0 run-mcp.sh
```
或从环境去掉 `COMPUTER_USE_LINUX_BIN`，adapter 默认回 `computer-use-linux`。
