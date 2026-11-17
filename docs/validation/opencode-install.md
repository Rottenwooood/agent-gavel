# OpenCode 接入验收（0.5历史记录）

> 以下安装、配置与额度状态均对应2026-11-12/14，不代表当前账号状态或0.6验收。
> 0.6删除session，当前资料目录与调用方式见[README](../../README.md)。项目配置仍显式隔离数据，
> 与不设覆盖变量时的用户级primary是不同环境；不能据此断言用户登录态丢失。

安装：2026-11-12；复验：2026-11-14。

状态：**项目级配置已加载，`opencode mcp list` 显示 `agent-gavel` 连接成功；工具级调用仍未验证。**

## 安装

- 本机 OpenCode 为 `v2.0.24`。
- 检出目录 `/home/c6h4o2/dev/agent-gavel` 有独立 `.venv`；以 editable 方式安装 `agent-gavel==0.5.1`，包元数据和模块路径均指向该目录。
- `uv pip check --python .venv/bin/python` 检查 57 个已安装包，依赖兼容。
- 仓库根目录的 [opencode.json](../../opencode.json) 配置本地 stdio server，直接调用本地虚拟环境，`codemode=false` 暴露真实工具入口。它使用 `mcp.servers` 写法：V2 仍兼容，但配置 schema 已把该字段标为 deprecated，新配置的当前写法是扁平 `mcp` 映射（服务器名即键）。
- 测试资料、工作流、产物和 daemon 数据限定在 `.runtime/opencode/`，不复用用户原有浏览器资料或旧 daemon。
- 未修改只读的全局 OpenCode 配置。全局配置里也有同名的 `agent-gavel` 条目（指向同一检出，数据目录为 `~/.config/agent-gavel-0.5`）；两者同名时以从本检出目录启动的项目配置为准。

配置依据：[OpenCode MCP 文档](https://opencode.ai/docs/mcp-servers/)、[配置位置与覆盖规则](https://opencode.ai/docs/config/)、[V1 兼容说明](https://opencode.ai/docs/migrate-v1/)。

## 复验结果（2026-11-14）

从本检出目录执行：

```sh
opencode mcp list
```

输出含 `✓ agent-gavel         connected`。项目配置对应的数据目录下已生成
`.runtime/opencode/data/{daemon.json,daemon.lock,daemon.sock}`，说明 daemon 按项目级环境变量启动。

未验证部分：没有通过 OpenCode 会话调用 `agent-gavel` 的工具。本机 OpenCode Go 周额度用尽
（`provider.quota`，约 2 天后重置），无法发起模型会话；MCP 加载与连接已确认，浏览器动作未在
OpenCode 侧复验。

## 2026-11-12 环境记录：被 socket 权限阻塞

当次安装后无法取得加载成功证据：

| 实际执行 | 结果 |
| --- | --- |
| `opencode mcp list` | 返回码 1；OpenCode 后台服务监听失败，`EPERM: operation not permitted, listen` |
| `opencode run --standalone --format json ...` | 返回码 1；独立服务未就绪即退出 |
| `tests/e2e_mcp.py --headless` | 返回码 1；TCP 和 Unix socket 被拒绝，`status=blocked`、`calls=[]` |

命令、返回码和错误输出见 [opencode-install.json](opencode-install.json)；当次浏览器验收报告见
[e2e-headless-install.json](e2e-headless-install.json)。该记录只说明当时执行环境的限制，
2026-11-14 在允许本地 socket 的环境中已取得连接证据，浏览器验收见 [阶段验收](README.md)。

## 继续验收的入口

新会话中让模型通过agent-gavel工具open本地测试页（或创建context，使用返回的active_page_id），
填写/点击并内联断言、取原生截图，然后清理本次测试资源。当前验收按[README](../../README.md)运行真实MCP脚本；
旧e2e记录仅供历史对照。工具级调用通过前，不把本页状态当作新版“已确认可用”。
