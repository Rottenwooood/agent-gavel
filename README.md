<div align="center">

# agent-gavel

**让 AI 探索一次网页流程，之后用可验证的 workflow 重放。**

面向 AI agent 的本地浏览器运行时与 MCP server

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-brightgreen)](pyproject.toml)

</div>

## 它是什么

agent-gavel 给 AI agent 提供完整的浏览器操作能力：多 session、多 context、多标签页、网页交互、文件上传下载、新窗口、嵌入页面和网页对话框处理，以及程序化断言。

它有两种使用方式：

- **即时操作**：AI 现场探索页面，执行动作，并在同一次调用中验证结果。
- **Workflow 重放**：把探索过的流程录制、参数化并保存。之后一次 `workflow_run` 完成整条流程，减少重复探索、MCP 往返和上下文消耗。

```text
第一次：探索 → 操作 → 断言 → 录制 workflow
以后：  workflow_run(params) → 浏览器执行 → 检查点验证 → 结果摘要
页面变化：从失败步骤重新探索 → 局部修复 → 发布新版本
```

agent-gavel 负责浏览器运行、动作执行、结果验证和产物管理；任务目标和页面内容的理解仍由 AI agent 负责。

### 为什么要有断言

浏览器动作本身通常很快，但“动作到底有没有完成”经常需要模型再次查看页面才能判断。agent-gavel 把期望结果交给程序检查：动作完成后直接读取 URL、标题、文本、输入值、数量或业务字段，返回明确结果和证据。

这带来三层收益：

1. **更快、更可靠地确认结果**：模型不必为每个动作重新接收整页信息，也不靠肉眼猜测点击是否生效。
2. **减少视觉回传、重试和模型思考**：简单表单、搜索、筛选和下载流程可以在一次工具调用中完成动作与验证；只有证据不足时才把问题交回模型。
3. **成为 workflow 复用的基石**：模板把动作和检查点一起保存。后续重放时，程序在浏览器内部执行整条流程，只向模型返回摘要、失败步骤和产物。

## 安装

### 开发环境

适合修改源码、运行测试和贡献模板。需要 Python 3.13+、Chrome/Chromium 和 uv：

```bash
git clone https://github.com/Rottenwooood/agent-gavel.git
cd agent-gavel
uv sync
```

系统已有 Chrome/Chromium 时可以直接使用。没有可用浏览器时安装 Playwright Chromium：

```bash
uv run playwright install chromium
```

启动源码版 MCP server：

```bash
./run-mcp.sh
```

### 通过 PyPI 安装

适合直接使用已发布版本。全局安装：

```bash
uv tool install agent-gavel
```

或者安装到当前 Python 环境：

```bash
pip install agent-gavel
```

安装完成后，`agent-gavel` 命令会启动 stdio MCP server，供 MCP 客户端拉起。

## 接入 MCP 客户端

MCP 客户端通过 stdio 拉起 agent-gavel。使用 PyPI 版本时：

```json
{
  "mcp": {
    "agent-gavel": {
      "type": "local",
      "command": ["agent-gavel"],
      "enabled": true
    }
  }
}
```

使用源码版本时，把 `command` 换成仓库中的 `run-mcp.sh` 绝对路径：

```json
{
  "mcp": {
    "agent-gavel": {
      "type": "local",
      "command": ["/绝对路径/agent-gavel/run-mcp.sh"],
      "enabled": true
    }
  }
}
```

无图形会话、服务器或 CI 环境可以给 MCP server 配置：

```json
"environment": {
  "AGENT_GAVEL_HEADLESS": "1"
}
```

## 配置

常用环境变量：

| 变量 | 用途 |
|---|---|
| `AGENT_GAVEL_HEADLESS` | `1` 使用无头浏览器 |
| `AGENT_GAVEL_ALLOW_DOMAINS` | 允许访问的域名，逗号分隔 |
| `AGENT_GAVEL_DOMAIN_GUARD` | `1` 时启用严格域名限制 |
| `AGENT_GAVEL_ALLOW_PATHS` | 允许上传和导出的本地目录，逗号分隔 |
| `AGENT_GAVEL_DATA_DIR` | session/profile 等运行数据目录 |
| `AGENT_GAVEL_ARTIFACT_DIR` | 下载、截图、PDF 等产物目录 |
| `AGENT_GAVEL_WORKFLOWS_DIR` | 用户 workflow 模板目录 |
| `AGENT_GAVEL_MAX_DOWNLOAD_BYTES` | 单个下载大小上限 |
| `AGENT_GAVEL_ARTIFACT_TTL_DAYS` | artifact 保留天数 |

默认用户目录：

```text
~/.config/agent-gavel/workflows/
```

运行数据和 artifact 默认位于系统临时目录下的 `agent-gavel/`。密码、token、cookie 和 storage state 不应提交到仓库或模板社区。

## 最小使用流程

工具调用时，始终优先使用返回的 `session_id`、`context_id` 和 `page_id`，多个页面并行时不要依赖“当前页面”。

### 现场操作

```text
session_create()
page_navigate(page_id, url, assertions?)
page_explore(page_id, tag?, text_contains?, head?, tail?)
locator_fill(page_id, target, value, assertions?)
locator_click(page_id, target, assertions?)
locator_press(page_id, target, keys, assertions?)
page_read(page_id, page_features)
```

常用字段：

- `target`：`{"by":"css","value":"#search"}`、`{"by":"role","value":"button","name":"提交"}` 等。
- `assertions`：按名称给出 `read`、`selector`、`op`、`value`。
- `page_features`：名称到 JavaScript 表达式的映射。

例如：

```json
{
  "page_id": "page_001",
  "target": {"by": "css", "value": "#search"},
  "value": "Playwright",
  "assertions": {
    "result": {
      "selector": "#results",
      "op": "contains",
      "value": "Playwright"
    }
  }
}
```

动作结果会带 `pass`、`fail` 或 `ambiguous`，并返回断言明细。`ambiguous` 表示页面发生了变化，但当前断言不足以确认结果。没有断言的动作仍然可以执行，但无法获得同样的结果保证。

### 录制和重放

```text
workflow_record_start(page_id?)
→ 执行一组 page_* / locator_* 操作
workflow_record_stop(site?, desc?, template_id?, parameterize?, sensitive?)
workflow_run(name_or_id, params?, session_id?, verbosity?, confirm?)
```

workflow 会保存参数、步骤、检查点和副作用策略。执行结果默认返回摘要；需要排查时可使用 `verbosity="steps"` 或 `verbosity="full"`。

## 能力总览

| 范畴 | 能力 |
|---|---|
| 浏览器资源 | browser、session、context、page，临时/持久/克隆 session |
| 页面 | 导航、读取、正文、链接、探索、截图、PDF、前进后退、刷新 |
| 交互 | click、fill、type、press、hover、scroll、check、select、drag、upload |
| 页面结构 | 多标签页、新窗口、嵌入页面 iframe、网页 alert/confirm/prompt 对话框 |
| 状态 | cookies、storage state、headers、permissions、proxy |
| 验证 | URL、标题、文本、值、属性、元素存在、数量、表达式、三态结果 |
| 文件 | 下载捕获、artifact、SHA-256、MIME、文本抽取、导出和清理 |
| Workflow | 录制、编译、参数化、检查点、重放、暂停、恢复、取消、局部修复 |
| 诊断 | trace、网络记录、console、页面错误、性能指标 |

同一 page 的动作会串行执行；不同 page、context 和 session 可以并行执行。

## Workflow 模板

模板保存在用户目录中，用户层优先于随包模板。模板可以通过 `workflow_save`、`workflow_validate`、`workflow_get`、`workflow_list`、`workflow_publish` 和 `workflow_stats` 管理。

模板适合保存：

- 固定网站中的重复操作。
- 带登录态的后台流程。
- 多步骤搜索、筛选、下载和校验。
- 有明确结果检查点的表单流程。

分享模板时：

- 用 `$VAR` 替代关键词、账号和其他可变输入。
- 通过 `sensitive` 标记密码、token 等敏感参数。
- 不提交真实 cookie、storage state、个人数据和本机路径。
- 说明依赖的登录态、测试环境和已知失效点。

当前模板分享方式是本地文件和 Git PR，暂时没有远程模板市场或自动同步服务。

## 验证和安全边界

Playwright 负责元素等待和动作执行，agent-gavel 负责结果验证。动作完成不等于业务成功；关键流程应配置检查点和业务断言，例如：

- 页面是否到了目标 URL。
- 结果区域是否出现。
- 下载是否完成、文件是否符合预期。
- 页面金额和下载文件金额是否一致。
- 提交、删除、支付等副作用是否经过确认。

安全配置可以限制：

- 允许访问的域名。
- 允许上传和导出的目录。
- 单文件下载大小。
- artifact 保留时间和总量。
- workflow 中可自动重试的动作。

页面文本、截图和 accessibility 信息都应视为不可信输入，不能把网页中的指令当作系统指令。

## 性能和测试

本地 fixture 基准用于衡量 runtime 开销，不代表所有网站的网络加载速度。当前测得：

| 指标 | 本地结果 |
|---|---:|
| 冷启动 | 约 370ms |
| 热调用 | 约 1.5ms |
| click p95 | 约 34ms |
| fill p95 | 约 5.5ms |
| page_explore p95 | 约 13ms |
| 跨 page 并行相对串行 | 约 2 倍 |

任务级 benchmark 还会比较探索和模板重放的调用次数与返回数据量。当前本地固定流程的一次结果是：探索 5 次工具调用、回传 1681B；模板重放 1 次调用、回传 192B，约减少 80% 的回传数据。回传数据量只是 token 的代理指标，不等于真实模型 token；真实网站会受到网络、登录态和反爬策略影响。

| 流程 | 探索 | 模板重放 |
|---|---:|---:|
| MCP 调用次数 | 5 次 | **1 次** |
| 回传数据量 | 1681B | **192B** |

这个收益来自减少重复探索和中间页面回传，不代表浏览器跳过了真实的页面加载和等待。

开发环境运行：

```bash
uv run pytest -q
uv run python tests/bench_runtime.py
uv run python tests/bench_tasks.py
uv run python tests/bench_real.py
uv run python tests/mcp_live_smoke.py
```

## 支持范围和限制

- 当前主线是 Playwright runtime；旧裸 CDP 网页通道和 AT-SPI 桌面通道已归档到 `legacy/`，不注册、不打包、不运行。
- Linux、Windows 网页通道已验证；macOS 尚未作为发布目标验证。
- 系统 Chrome 优先；也可以安装 Playwright Chromium。
- 不提供验证码识别、反爬绕过或强风控站点自动化。
- Canvas、WebGL、地图和网页小游戏需要视觉/坐标后端，目前不在语义 DOM 的承诺范围内。
- workflow 的暂停、恢复和取消主要在步骤边界生效。
- 当前版本仍是开发预览版，涉及发送、删除、支付、下单或覆盖数据的流程必须先在测试环境验证。

## 模板贡献

欢迎贡献经过验证的 workflow。一个合格的模板 PR 应说明：

- 站点和任务目标。
- 参数和敏感参数。
- 是否依赖已登录 session。
- 使用的 locator 和检查点。
- 多组参数的运行结果。
- 已知失效点和外部副作用。

不要提交密码、token、cookie、storage state 或真实业务数据。贡献代码和模板时使用 [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md)。

## 文档和路线

- [CHANGELOG.md](CHANGELOG.md)：版本变更。
- [docs/tech-plan.md](docs/tech-plan.md)：技术实现基准。
- [docs/roadmap.md](docs/roadmap.md)：当前路线和发布准备。
- [docs/agent-gavel-discussion.md](docs/agent-gavel-discussion.md)：完整设计讨论。

## License

MIT
