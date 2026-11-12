# OpenCode 接入验收（2026-11-12）

状态：**已安装并写入项目级配置，尚未确认 OpenCode 可用**。

## 已完成的安装

- 本机 OpenCode 为 `v2.0.24`。
- 本检出目录有独立 `.venv`；以 editable 方式安装 `agent-gavel==0.5.0`，包元数据和模块路径均指向本检出目录。
- `uv pip check --python .venv/bin/python` 检查 57 个已安装包，依赖兼容。
- 仓库根目录的 [opencode.json](../../opencode.json) 按 V2 的 `mcp.servers` 格式配置本地 stdio server，直接调用本地虚拟环境，`codemode=false` 暴露真实工具入口。
- 测试资料、工作流、产物和 daemon 数据限定在 `.runtime/opencode/`，不复用用户原有浏览器资料或旧 daemon。
- 未修改只读的全局 OpenCode 配置。它仍指向旧仓库；本项目配置需要从本检出目录启动 OpenCode 才会生效。V1 配置本身仍受 V2 兼容支持，不需要为此次接入迁移其他配置。

配置依据：[OpenCode MCP 文档](https://opencode.ai/docs/mcp-servers/)、[配置位置与覆盖规则](https://opencode.ai/docs/config/)、[V1 兼容说明](https://opencode.ai/docs/migrate-v1/)。

## 实际测试结果

没有获得 MCP 加载成功或浏览器操作成功的证据：

| 实际执行 | 结果 |
| --- | --- |
| `opencode mcp list` | 返回码 1；OpenCode 后台服务监听失败，`EPERM: operation not permitted, listen` |
| `opencode run --standalone --format json ...` | 返回码 1；独立服务未就绪即退出 |
| 本地已安装 Python 执行 `tests/e2e_mcp.py --headless` | 返回码 1；TCP 和 Unix socket 被拒绝，`status=blocked`、`calls=[]` |

OpenCode 运行提示词明确要求通过 agent-gavel 新建会话、导航本地测试页、填写/点击/断言、返回原生截图并清理；没有允许模型使用其他工具代替。由于 OpenCode 服务没有启动，提示词尚未进入模型执行阶段。

完整命令、返回码和错误输出见 [opencode-install.json](opencode-install.json)；独立浏览器验收报告见 [e2e-headless-install.json](e2e-headless-install.json)。测试使用 `/tmp/gavel-opencode-check/` 下的独立 OpenCode 数据、缓存和状态目录，以避开本会话对全局日志目录的只读限制；随后仍因 socket 权限被阻断。

## 继续验收的入口

在允许本地 socket 的执行环境，从此检出目录运行：

```sh
opencode mcp list
opencode
```

项目路径：`/home/c6h4o2/Documents/Codex/2026-11-09/new-chat/work/agent-gavel`。

应先由实现者确认列表中 `agent-gavel` 已连接，再在新会话中完成上述真实操作和截图验收。当前尚未达到“确认能用之后再交用户测试”的条件。
