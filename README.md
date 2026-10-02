<div align="center">

# agent-gavel

**AI 操作浏览器，动作发出后由程序断言判成败，省掉一次"把页面喂回模型判断"的往返。**

![License](https://img.shields.io/badge/license-MIT-blue) ![Python](https://img.shields.io/badge/python-3.13-brightgreen)

</div>

## 这是什么

agent-gavel 是一个给 AI 用的 MCP server：AI 声明一个动作 + "做完后页面应该长什么样"，
它执行、等待、**由程序断言**，一次调用返回结果。成败不再由模型回头"看"页面判断。

```
普通浏览器 agent：动作 → 整页快照喂回模型 → 模型判断 → 下一步   （慢、费 token、会看漏）
agent-gavel     ：动作 + 声明期望 → 执行 → 程序断言 → pass/fail/ambiguous + 证据
```

结果**三态**：

- `pass`：断言满足。
- `fail`：断言未满足，且页面没变化（动作没生效）——明确失败。
- `ambiguous`：拿不准——断言没过但页面确实变了（断言写窄/指错元素），或没有断言但检测到变化。
  此时会返回**页面变化的 diff 证据**，交回模型仲裁；没有变化时不返回 diff。

底层用 **Playwright** 驱动真实浏览器（默认系统 Chrome），每页一个串行队列、跨页并行。

## 能力

- **资源模型**：`session`（隔离面，可 ephemeral / persistent / clone）→ `context`（cookie/
  语言/设备隔离）→ `page`（标签页/popup），稳定 id，省略 id 用当前活动对象。
- **动作**：导航、点击、填值、逐键输入（含中文）、按键、勾选、下拉、拖拽、上传、悬停、
  滚动、iframe 内操作；默认严格定位（0 命中或多命中都报结构化错误，不瞎猜）。
- **断言**：URL/标题/文本/值/属性/元素存在性/计数/JS 表达式，支持比较与正则；
  动作可带断言一次调用闭环。
- **工作流**：录制 → 编译成模板（参数化 `$VAR`、标记副作用、补检查点）→ 一次调用重放；
  失败时可选**一次安全重试**（仅幂等/安全步骤）和**一次换锚点局部修复**；
  暂停 / 恢复 / 取消；模板按网站组织、带失效检测。
- **产物**：下载自动捕获（sha256/大小/MIME）、截图、PDF、trace；按 id 引用，
  **不向模型暴露绝对路径**，导出需落盘白名单。
- **诊断**：trace / 网络 / console / 页面错误 / 性能指标。
- **安全**：域名白名单、落盘白名单、下载大小上限、副作用步骤需确认、
  导出防路径穿越。

## 安装

前置：Python 3.13+、一个 Chrome/Chromium、[uv](https://docs.astral.sh/uv/)（或 pip）。

```bash
git clone https://github.com/Rottenwooood/agent-gavel ~/agent-gavel
cd ~/agent-gavel
uv sync
```

浏览器由 Playwright 自管，默认用系统 Chrome；未装 Chrome 时会回退到 Playwright 自带内核
（首次可 `uv run playwright install chromium`）。

## 接入 MCP 客户端

`run-mcp.sh` 是启动器（内部 `uv run python -m agent_gavel.main`）。以 opencode 为例，
编辑 `~/.config/opencode/opencode.json`：

```json
{
  "mcp": {
    "agent-gavel": {
      "type": "local",
      "command": ["/home/<你>/agent-gavel/run-mcp.sh"],
      "enabled": true
    }
  }
}
```

重启客户端后，工具列表里会出现 `agent-gavel_session_* / page_* / locator_* / workflow_*`
等。无图形会话（CI/服务器）设 `AGENT_GAVEL_HEADLESS=1`。

## 快速上手（典型会话）

对陌生网站做一件事，并沉淀成可复用模板：

```
1. session_create                     # 建隔离会话，拿到 page_id
2. page_navigate(url=...)             # 开到目标站
3. page_explore                       # 列出可交互元素 + 稳定锚点
4. locator_fill(target=..., value=...)   # 填
5. locator_press(target=..., keys="Enter",
                  assertions={"u":{"read":"url","op":"contains","value":"结果"}})
                                       # 提交并断言跳转
6. workflow_record_start → ... → workflow_record_stop   # 录成模板
7. workflow_run(name_or_id=..., params={...})           # 以后一次调用重放
```

## 工作流模板

- 存 `~/.config/agent-gavel/workflows/<template_id>.json`（用户层优先）；
  随包模板为低优先回退层。
- `workflow_save / validate / publish / list / get / stats / run / replay /
  pause / resume / cancel`。
- 参数用 `$VAR` 占位，敏感参数只存占位声明，真实值不落盘。
- 失效检测：连续失败 ≥3 标 `suspected`，运行返回 warning；pass 清零。
- 传播（当前）：本地文件 + Git PR，不做自动同步/模板市场（见 roadmap）。

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `AGENT_GAVEL_HEADLESS` | 无头模式 | 按客户端环境 |
| `AGENT_GAVEL_ALLOW_DOMAINS` | 域名白名单（逗号分隔） | 空=不限制（开发） |
| `AGENT_GAVEL_DOMAIN_GUARD` | 发布加固：即使白名单为空也限制（空集=全拒） | 关 |
| `AGENT_GAVEL_ALLOW_PATHS` | 允许落盘的本地目录（逗号分隔） | 空=不限制 |
| `AGENT_GAVEL_MAX_DOWNLOAD_BYTES` | 单文件下载上限 | 256MB |
| `AGENT_GAVEL_ARTIFACT_TTL_DAYS` | 产物保留天数 | 7 |
| `AGENT_GAVEL_ARTIFACT_MAX_BYTES` | 产物总量上限 | 2GB |
| `AGENT_GAVEL_WORKFLOWS_DIR` | 模板目录覆盖（测试隔离用） | `~/.config/agent-gavel/workflows` |

## Docker（MCP over stdio）

```bash
docker build -t agent-gavel .
docker run -i --rm -e AGENT_GAVEL_HEADLESS=1 agent-gavel
```

镜像默认开启发布加固（`AGENT_GAVEL_DOMAIN_GUARD=1`）：**默认拒绝所有域名**，
必须显式 `-e AGENT_GAVEL_ALLOW_DOMAINS=example.com,...` 才放行。

## 测试与基准

```bash
uv run pytest -q                 # 功能 + 安全 + 真实任务回归（本地站点）
uv run python tests/bench_tasks.py    # 真实任务基准（含真实网站，可 AGENT_GAVEL_BENCH_OFFLINE=1 跳过）
uv run python tests/bench_runtime.py  # 单操作/生命周期性能基准 p50/p95/p99
uv run python tests/mcp_live_smoke.py # 真实 MCP 协议联调
```

**真实任务基准**（本机实测，含真实网站）：

| 任务 | 结果 | 调用次数 | 耗时 |
|---|---|---|---|
| 登录后台→选最新发票→下载→校验内容 | pass | 10 | ~0.9s |
| 搜索→打开结果→读价格并判区间 | pass | 6 | ~0.3s |
| 同一任务：模板重放 vs 探索 | pass | 探索 5 次 → 重放 1 次 | 回传 1681B → 192B |
| 真实 example.com 导航+读正文 | pass | 3 | ~1.8s |
| 真实 wikipedia 搜索→结果页 | pass | 6 | ~6.6s |

> 诚实说明：真实站点受网络/风控影响；上表 wikipedia 走本机代理。
> "回传字节"是 token 的代理指标，不是真实 token 数。淘宝/京东等强风控站点
> 对自动化有拦截，不在承诺范围内。

## 支持范围与限制

- **平台**：Linux、Windows（网页通道已适配）；macOS 未验证。Python 3.13+。
- **API 只有一套**：新 runtime。旧的 `dom_*` / AT-SPI 工具已归档到 `legacy/`，
  不注册、不运行，**也不与新版共用浏览器**。
- **浏览器**：Playwright 自管，默认系统 Chrome；缺 Chrome 时才回退自带 chromium。
- **网络**：真实站点需可达；无头/服务器环境若受限，用 `session_create` 的 `proxy`
  参数显式配置（浏览器不自动读 `*_proxy` 环境变量）。
- **已实现的恢复**：安全幂等动作瞬时失败重试一次、定位失败的换锚点修复、
  工作流暂停/恢复/取消、失败返回结构化 `reason`/`hint`。
- **尚未实现**：验证码自动识别、`captcha_detected → 人工完成后 resume` 的成品闭环、
  视觉/坐标后端（M6）、跨站模板市场。
- **不在承诺范围**：强风控站点（淘宝/京东等）会拦截自动化；不提供绕过验证码能力。

## 从旧版迁移

旧 `dom_*` 工具被新 runtime 取代，主要对应关系：

| 旧 | 新 |
|---|---|
| `dom_navigate` | `page_navigate` |
| `dom_explore` | `page_explore` |
| `dom_step`（动作+断言） | `locator_*`（可带 `assertions`） |
| `dom_read` / `dom_text` | `page_read` / `page_text` |
| `dom_save_template` / `dom_run_template` | `workflow_save` / `workflow_run` |
| `act_and_verify`（桌面） | 已归档，不提供 |

旧模板（schema v1）由 `loader` 只读兼容迁移；建议在新 runtime 重跑并另存为新模板。

## 状态与路线

- 已完成：Playwright runtime、资源模型、基础/高级浏览器能力、工作流、诊断、
  安全加固、真实任务回归。
- 旧实现（裸 CDP 网页通道 + AT-SPI 桌面通道）已归档到仓库根 `legacy/`，
  **不打包、不注册、不运行**（见 `legacy/README.md`）。
- 规划见 `docs/tech-plan.md`、`docs/roadmap.md`。

## License

MIT
