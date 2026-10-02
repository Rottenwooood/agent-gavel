# agent-gavel 内测说明（Playwright runtime）

> 安装、MCP 接入、工具总览见根目录 `README.md`。本文只讲内测需要知道的重点。

## 一句话原理

AI 声明动作 + "做完后页面应该长什么样" → 工具执行、等待、**程序断言** →
一次调用返回 `pass / fail / ambiguous`（拿不准时附页面变化 diff 证据）。
成败不消耗模型判断，省掉每次动作后"把页面喂回模型再看一遍"的往返。

## 内测范围

- **可用**：资源模型（session/context/page）、基础 + 高级浏览器能力、工作流
  录制/重放/局部修复、产物（下载/截图/PDF/trace）、安全加固。
- **已归档不运行**：旧的裸 CDP 网页通道与 AT-SPI 桌面通道（`legacy/`）。
- **未做**：视觉/坐标后端（M6）、跨站模板市场。

## 工具分组（`agent-gavel_` 前缀）

| 组 | 代表工具 | 用途 |
|---|---|---|
| 资源 | `session_create` `context_create` `page_open` `page_list` | 建隔离环境、管页面 |
| 读 | `page_navigate` `page_read` `page_text` `page_explore` `page_snapshot` | 导航、取值/正文、找锚点 |
| 动 | `locator_click` `locator_fill` `locator_type` `locator_press` … | 动作（可带断言） |
| 断言 | `expect` `page_wait_for_*` | 独立断言、等响应/加载/下载/事件 |
| 产物 | `artifact_list/get/export/cleanup` `download_*` `page_screenshot` `page_pdf` | 落盘与查看 |
| 工作流 | `workflow_record_*` `workflow_save/validate/publish/run/replay` `workflow_pause/resume/cancel` | 模板闭环 |
| 诊断 | `trace_*` `network_*` `console_get` `page_errors` `performance_metrics` | 排查 |

## 使用要点

1. 动作能带断言就带断言——这是省 token 的关键。
2. 写窄了/指错了会返回 `ambiguous` + diff 证据，看证据再决定，不要无脑重试。
3. 陌生站先 `page_explore` 拿稳定锚点，再动作；跑通后录成模板。
4. 副作用步骤（提交/删除/支付）默认需确认，别为省事关掉。
5. 失败先看结构化 `reason` 和 `hint`，多数情况直接告诉你怎么修。

## 已知限制 / 反馈时请附

- 强风控站点（如淘宝/京东）会拦截自动化，属预期，不在内测承诺范围。
- 真实站点靠网络；无头/服务器环境可能需要显式代理配置。
- 报告问题时附：复现步骤、`workflow_run` 的 `verbosity="full"` 输出、trace/截图 artifact、
  浏览器与系统版本。

## 自检

```bash
uv run pytest -q                      # 全绿
uv run python tests/bench_tasks.py    # 真实任务（可 AGENT_GAVEL_BENCH_OFFLINE=1 跳真站）
uv run python tests/mcp_live_smoke.py # 真实 MCP 协议联调
```
