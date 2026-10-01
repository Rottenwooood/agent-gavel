# agent-gavel 技术方案（Playwright Runtime 重建）

> **文档地位**：本文档是 agent-gavel 下一阶段开发的**长程基准**。实现遇到本文未覆盖的分支时，先回到本文更新，再改代码；不允许悄悄偏离。
>
> **来源**：由 `docs/agent-gavel-discussion.md`（设计讨论稿）收敛而来。讨论稿回答"为什么做、做成什么样"，本文回答"每个模块怎么落地"。两者冲突时以本文为准（本文更具体），但产品的价值主张以讨论稿为准。
>
> **本轮约定（已与作者确认）**：
> 1. 本轮只产出本方案，不写实现代码。
> 2. 新 Playwright runtime 与现有裸 CDP 的 `dom_*` 工具**并行共存**，先不动旧代码。
> 3. 验收方式：本地综合 fixture 站点 + pytest。

---

## 1. 目标与成功标准

### 1.1 产品目标

把 agent-gavel 从"验证驱动的 DOM 工具库（裸 CDP）"升级为：

> **面向 AI agent 的可探索、可验证、可复用、可恢复的浏览器运行时。**

核心闭环（讨论稿 §22）：

```
探索一次 → 编译模板 → 一次调用高速重放 → 检查点验证
        → 下载和业务结果验证 → 失败时局部修复
```

### 1.2 技术目标

以 Playwright 为执行内核，建立 `session → context → page → frame → locator` 资源模型，
让以下讨论稿中的痛点得到结构性解决，而不是打补丁：

| 现状痛点（讨论稿 §3） | 本方案的结构性解决 |
|---|---|
| adapter 直接维护 CDP、键盘码表、页面生命周期 | Playwright 承担浏览器语义；agent-gavel 只写业务验证与资源模型 |
| 单端口、单 profile、隐式"当前页面" | session/context/page 显式寻址，稳定 `page_id` |
| 全局 PAGE_LOCK 让所有导航串行 | 每 page 一个 actor 队列：同 page 串行、跨 page/context/session 并行 |
| 动作/等待/验证/重试耦合在一个工具里 | 拆成 action / observation / assertion / evidence / retry policy |
| 自动重试可能重复副作用 | 动作显式标 `idempotent` / `safe_to_retry` / `requires_confirmation` |

### 1.3 成功标准（可验证）

- **S1 资源隔离**：多 session 并行时 cookie/storage/proxy 互不污染；测试可证明。
- **S2 并发正确**：同一 page 的动作严格串行且无状态错乱；不同 page 的动作可并行。
- **S3 一次调用重放**：一个模板可用一次 MCP 调用执行完，只返回检查点摘要与产物。
- **S4 每次运行有证明**：返回通过/失败/不确定三态 + 证据（截图/下载/网络）引用。
- **S5 失败可恢复**：失败返回结构化 `error_code` + `resume_hint`，支持从失败步局部修复。
- **S6 无泄漏**：连续 1000 次操作后 page/context/process 数量回到基线。
- **S7 旧接口不回归**：现有 `dom_*` 全部测试在本方案实施后仍通过。

---

## 2. 范围与反范围

### 2.1 范围（本方案覆盖）

- Playwright 执行内核 + 浏览器进程管理。
- session / context / page / frame / locator 资源模型。
- 基础与高级浏览器能力：导航、读取、探索、动作、等待、断言、截图、PDF、下载、上传、popup、iframe、dialog、cookie、storage state、headers、proxy、permissions。
- workflow：录制、模板编译、校验、一次调用重放、暂停/恢复/取消、失败局部修复。
- artifact：下载捕获、哈希、MIME、文本抽取、导出、生命周期。
- 诊断：结构化日志、metrics、trace、网络/console/pageerror 捕获。
- 兼容层：`dom_*` → 新工具的映射（M5 实施）。
- 本地综合测试站点 + pytest 验收体系。
- 视觉/坐标后端**设计**（M6）：仅"截图 → AI 判断坐标 → 坐标操作"循环，**不引入 OCR/OpenCV 等额外视觉技术栈**（见 §5.17）。

### 2.2 反范围（本方案明确不做或后置）

- 不自研 agent 大脑（讨论稿 §roadmap；只做"手"）。
- AT-SPI 桌面通道不再投入（列为冻结）。
- 不做实时竞技游戏、帧级操作、强反作弊页面自动化（讨论稿 §13）。
- 第一版不做"通用网页小游戏自动化"的承诺。
- 不把任意绝对路径暴露给模型（只给 artifact id）。
- 不在 M1–M4 阶段删除/重写旧 `dom_*`（M5 才动，且是转发 shim）。

---

## 3. 约束

### 3.1 环境约束

- Python 3.13；Linux 为主要开发/验收平台，Windows DOM 需保持可用。
- 系统 Chrome 优先（`channel="chrome"`），避免额外下载 Playwright chromium；
  复用 `agent_gavel/browser_manager.py:chrome_binary()` 的跨平台探测。
- 有桌面会话时可 headed（默认），无桌面会话用 `AGENT_GAVEL_HEADLESS=1`。
- MCP server 为 stdio 模式，工具注册风格沿用现有 `mcp.server.mcpserver.MCPServer` 的 `@mcp.tool()`。

### 3.2 技术约束（Playwright 的硬限制，决定了资源模型）

1. **proxy 只能在 launch 层设置**，`browser.new_context()` 不接受 `proxy`。
   → 需要 context 级代理的 session，必须为它单独启动一个浏览器进程。
2. **`launch_persistent_context()` 直接返回一个 BrowserContext**，它绑定自己的浏览器进程。
   → persistent session 是"一个持久 context 独占一个浏览器进程"。
3. **`page.pdf()` 只在 headless Chromium 可用**。
   → 需要 PDF 的调用若浏览器是 headed，返回 `pdf_requires_headless` 并给 hint。
4. 下载默认由 `context` 捕获（`accept_downloads=True`）；persistent context 需显式确认该参数。
5. Playwright 的"自动等待"只解决**执行层**（元素存在/可操作/动作完成）；**业务层验证**必须由 agent-gavel 自己断言（讨论稿 §10）。

### 3.3 用户决定

- 新 runtime 与旧 CDP `dom_*` 并行，旧代码本阶段不动。
- 验收用本地 fixture 站点 + pytest。

---

## 4. 总体架构与模块划分

```
MCP 客户端
   │  stdio
   ▼
agent_gavel.main（聚合入口）
   ├── 旧通道（保留）：channels/dom（裸 CDP）+ channels/desktop（冻结）
   └── 新通道：tools/*（MCP 工具）→ runtime/*（资源模型）→ backends/*（Playwright / CDP 逃生）
                                            └── workflows/*（模板录制/编译/重放）
                                            └── diagnostics/*（日志/指标/健康）
```

建议源码结构（讨论稿 §5）：

```
agent_gavel/
  runtime/
    browser_runtime.py    # 顶层单例：拥有 process/session/context/page 注册表与生命周期
    browser_process.py    # Playwright 浏览器进程的启动/探测/关闭（跨平台）
    sessions.py           # session 资源与模式（ephemeral/persistent/clone）
    contexts.py           # BrowserContext 资源（cookie/storage/proxy/locale/device 隔离）
    pages.py              # Page 资源、稳定 page_id、popup/下载/dialog 绑定
    queue.py              # 每 page 的 actor 命令队列（串行/并行/取消/超时/优先级）
    events.py             # 事件总线（page_created/download/dialog/...）
    artifacts.py          # 下载与产物存储（sha256/MIME/抽取/导出/清理）
    tracing.py            # Playwright trace 与运行记录
    policies.py           # 域名白名单/文件白名单/大小限制/脱敏/副作用审批
    metrics.py            # 成本与延迟指标（p50/p95/p99）
    errors.py             # 结构化错误码
  backends/
    playwright_backend.py # 把 runtime 的语义操作翻译成 Playwright 调用
    cdp_escape_hatch.py   # context.new_cdp_session 逃生通道（高级，默认不用）
  tools/
    browser_tools.py      # browser_* / session_* / context_* / page_* 生命周期
    action_tools.py       # locator_* 动作
    assertion_tools.py    # expect / page_wait_*
    artifact_tools.py     # download_* / artifact_*
    workflow_tools.py     # workflow_* / trace_* / network_* / console_*
  workflows/
    schema.py             # 模板 JSON schema 与校验
    loader.py             # 模板存取（复用现有双层目录策略）
    compiler.py           # 探索记录 → 模板（去噪/合并/参数化/找 checkpoints）
    runner.py             # 模板执行/暂停/恢复/取消/重放
    recorder.py           # 录制动作与状态
  diagnostics/
    logging.py            # JSON structured logs
    health.py             # doctor/health 报告
  main.py                 # 只加一行 register_runtime_tools(mcp)
```

**关键取舍：为什么新代码放 `runtime/` 而不是继续扩 `channels/dom/`**

`channels/dom/adapter.py` 直接持有 CDP WebSocket、键盘码表、页面等待。继续在
其上叠加 session/context 会把它变成"CDP 全栈"，与讨论稿 §3.1 的问题同源。因此新运行时
单独成层，`channels/dom/` 冻结为兼容资产，M5 再决定其去留。

---

## 5. 逐模块详细机制

### 5.1 `runtime/errors.py` — 结构化错误

**输入**：内部异常 / Playwright 异常 / 业务校验失败。
**处理规则**：所有对外失败都归一为 `GavelError(code, message, detail, hint, retryable)`。
**输出**：MCP 响应里的 `status:"error", reason:<code>, error, hint`（沿用现有 `_dom_error` 的形状，便于模型一致处理）。

错误码表（首版，可扩展）：

| code | 触发 | retryable | hint 要点 |
|---|---|---|---|
| `chrome_env` | 找不到 Chrome / launch 失败 / 无 DISPLAY 且有头 | true(切无头) | 设 `AGENT_GAVEL_HEADLESS=1` |
| `browser_crashed` | 进程崩溃 | true | 自动重拉起；保留 session 配置 |
| `session_not_found` | session_id 无效 | false | 调 session_list |
| `context_closed` | context 已关 | false | 重新 context_create |
| `page_not_found` / `page_closed` | page_id 无效/已关 | false | page_list；或 page_open |
| `page_crashed` | 渲染进程崩溃 | true | 自动重建 page |
| `navigation_failed` | 导航错误/超时 | true | 检查 URL/网络 |
| `locator_not_found` | 0 命中 | true(仅 safe 动作) | 重新 page_explore |
| `locator_not_unique` | strict 下 >1 命中 | false | 返回候选项，要求收窄 |
| `assertion_failed` | 断言不满足且无变化 | false | 返回实际值 |
| `timeout` | 操作用尽 timeout | true | 调大 timeout_s |
| `pdf_requires_headless` | headed 下调 page_pdf | false | 切无头 |
| `download_failed` / `download_too_large` | 下载失败/超限 | 视情况 | 调整大小限制 |
| `policy_blocked` | 域名/文件不在白名单 | false | 让用户加白名单 |
| `requires_confirmation` | 高危动作未批准 | false | 用确认机制后重试 |
| `unsupported_backend` | 请求了未实现的 backend | false | 见 §5.13 |

**边界情况**：`retryable=true` 只表示"技术上可重试"，不等于"业务上应重试"——
是否重试由动作的 `safe_to_retry`（§5.10）与 runner 的 failure_policy 决定。

### 5.2 `runtime/browser_process.py` — 浏览器进程管理

**职责**：新 runtime 独占的 Playwright 浏览器进程的启动/探测/关闭；**不复用**旧
`browser_manager.py` 的调试 Chrome（两者端口、profile、生命周期完全不同）。

**输入**：`launch(opts)`，`opts = {headless, channel, proxy, user_data_dir, args}`。
**处理规则**：
1. 选择可执行：`channel="chrome"` + 系统 Chrome 存在（`chrome_binary()`）→ 用系统 Chrome；
   否则回退 `p.chromium.launch()`（Playwright 自带 chromium，可能需 `playwright install`）。
2. headless 决定：显式参数 > `AGENT_GAVEL_HEADLESS` 环境变量 > 默认 headed。
   headed 且无 `DISPLAY`/`WAYLAND_DISPLAY` → `chrome_env` 错误（沿用旧逻辑的提示）。
3. 普通模式：`browser = await browser_type.launch(...)`，登记 `process_id`。
4. 持久模式：`ctx = await browser_type.launch_persistent_context(user_data_dir, ...)`，
   浏览器句柄从 `ctx.browser` 取，登记为独立进程。
5. 崩溃自愈：监听 `browser.on("disconnected")`，标记进程失效；下次访问该 session 时按
   原参数重拉（session 的 context/page 可能失效，返回 `browser_crashed` 并给恢复提示）。
**输出**：`BrowserHandle(process_id, pid, browser, mode, opts, created_at)`。
**边界情况**：
- 系统 Chrome 被更新/移动 → launch 失败 → 回退自带 chromium → 再失败报 `chrome_env`。
- Windows 清理：`browser.close()` 优先，超时后用 psutil/taskkill 兜底（复用旧思路）。
- 退出清理：`main.py` 退出 handler 追加 `runtime.shutdown()`，关闭所有浏览器进程。

**为什么每个 session 可能各占一个进程**：Playwright 的 proxy 与 persistent profile 是
launch 级参数（§3.2）。为满足"不同 session 不同代理/账号"，宁可以进程数换隔离。默认
ephemeral session 共享 runtime 的"默认浏览器进程"，只在需要 launch 级差异时才独立进程。

### 5.3 `runtime/browser_runtime.py` — 顶层单例

**职责**：持有全部注册表与生命周期；是 tools 层唯一入口。
**状态**：
```
BrowserRuntime
  processes: dict[process_id -> BrowserHandle]
  sessions:  dict[session_id  -> Session]
  contexts:  dict[context_id  -> Context]
  pages:     dict[page_id     -> PageHandle]
  events:    EventBus
  artifacts: ArtifactStore
  counters:  {sess/ctx/page/op: int}
  policies:  PolicyManager
  metrics:   MetricsRecorder
```
**处理规则**：
- `id` 生成：`<prefix>_<n:03d>`，n 单调递增，进程内唯一，**不做跨进程持久**（讨论稿要求稳定
  的是"同一会话内不变"，不是全局永久）。
- 惰性启动：首个需要浏览器的调用触发 `browser_launch` 等价逻辑。
- `shutdown()`：逆序关 page→context→session→process，再关 artifacts 临时目录。
**边界情况**：并发首次启动用 `asyncio.Lock` 防重复拉起。

### 5.4 `runtime/sessions.py` — Session

**职责**：会话级隔离与配置。
**Session 数据**：
```
Session(session_id, mode, config, browser_handle, context_ids, storage_state,
        created_at, last_used_at)
config = {locale, timezone_id, viewport, device, proxy, headless,
          accept_downloads, permissions, geolocation, extra_http_headers}
```
**模式与规则**：

| mode | 实现 | 状态持久性 | 进程 |
|---|---|---|---|
| `ephemeral` | 共享默认浏览器上 `browser.new_context(...)` | 关闭即弃（可显式 export_state） | 默认进程 |
| `persistent` | `launch_persistent_context(user_data_dir)` | 落盘 user_data_dir | 独占进程 |
| `clone` | `new_context(storage_state=...)` | 每次从未污染的 storage_state 起 | 默认进程 |

- `config.proxy` 非空 → 该 session 强制独占进程（Playwright 限制）。
- `device` 非空 → 用 `playwright.devices[name]` 展开为 viewport/UA/scale 等。
- **session 独占 / 忙状态（并发决策 B）**：workflow 执行时 `acquire(session_id, run_id)` 独占
  该 session；已占用时再 acquire 返回 `session_busy`。不同 session 互不阻塞，可并行跑模板。
  单次动作级调用不受此锁约束（由 PageActor 保证串行），锁只用于 workflow 粒度。
- `session_reset`：关闭该 session 所有 context 再用同配置重建（保留 mode 与 config，
  persistent 重置 storage_state 或 profile 由参数决定）。
- `session_export_state(path?)`：`context.storage_state()`；返回 artifact 或写文件。
- `session_import_state`：创建 clone session 的入参。
**边界情况**：session 关闭时其下未完成操作被取消（§5.7）；persistent profile 被占用
（同 user_data_dir 已有进程）→ `session_conflict` 错误并给 hint。

### 5.5 `runtime/contexts.py` — Context

**职责**：一个 BrowserContext 的资源与隔离面。
**输入**：`context_create(session_id, {locale, viewport, device, permissions,
timezone_id, extra_http_headers, storage_state, geolocation})`。
**处理规则**：
- 在 session 的浏览器上 `new_context(**filtered)`；persistent session 的既有 context
  直接登记，不再新建。
- 绑定事件：`page`/`request`/`response`/`requestfailed`（当开启网络监听）。
- cookie 读写：`context.cookies()` / `add_cookies()`。
- headers：`context.set_extra_http_headers()`。
**输出**：`Context(context_id, session_id, browser_context, page_ids, config)`。
**边界情况**：context 关闭 → 其下 page 全部注销并取消队列。

### 5.6 `runtime/pages.py` — Page

**职责**：页面资源、稳定 `page_id`、事件绑定、健康检查。
**输入**：`page_open(context_id, url?)`，或由 `context.on("page")` 自动登记 opener 关系。
**处理规则**：
1. 新建/接管 Playwright Page，分配 `page_id`，记 `opener_page_id`。
2. 绑定：`download`（→ ArtifactStore 捕获）、`dialog`（缓存，默认不自动处理，等 `page_handle_dialog` 或 `expect`）、
   `pageerror`、`console`、`crash`、`load`、`framenavigated`。
3. `page_state` 缓存 `url/title/load_state`（供摘要与断言用）。
4. popup：`context.on("page")` 也分配 page_id；`page_wait_for_popup` 基于 `page.expect_popup()`。
**输出**：`PageHandle(page_id, context_id, session_id, page, opener_page_id, status)`。
**边界情况**：
- `page_closed` 事件 → 状态置 closed，取消队列，保留摘要供读取历史。
- `page_crashed` → 标 crashed，`page_reload`/`page_open` 可恢复。
- `target=_blank` 点击自动产生 child page（由 context page 事件覆盖）。

### 5.7 `runtime/queue.py` — PageActor（并发模型）

**职责**：把讨论稿 §14 的"每 page 一个 actor/命令队列"落成可取消、可设超时的调度器。

**数据结构**：
```
PageActor(page_id)
  pq: asyncio.PriorityQueue[Op]      # (priority, seq, op)
  worker: asyncio.Task
  pending: dict[operation_id -> Op]
Op(operation_id, coro_factory, priority=100, timeout_s=None, deadline=None,
   cancel_event, created_at, action_type, safe_to_retry, idempotent)
```
**处理规则**：
- 同一个 page 的所有操作进同一队列；worker 顺序 `await`，天然串行。
- 不同 page 是不同 actor → 真并行；不同 context/session 亦然（无全局锁）。
- `priority` 小者先执行；同优先级按 `seq` FIFO。
- `timeout_s`/`deadline` 到 → `asyncio.wait_for` 取消，返回 `timeout`。
- 取消：`operation_cancel(operation_id)` 触发 `cancel_event` 并 cancel task；未开始的从队列摘除。
- page 关闭 → worker 退出，所有 pending 以 `page_closed` 拒绝。
- 每个 op 完成后写 metrics 与结构化日志。
**边界情况**：动作本身含 Playwright 的自动等待 → 队列超时是**端到端**上限，包含自动等待；
动作内部错误不吞，逐层归一到 `GavelError`。

**为什么不用全局锁**：讨论稿 §3.3 明确指出全局 PAGE_LOCK 让导航串行化是问题；actor 模型
把串行粒度收窄到单 page，跨资源真并行。

### 5.8 `runtime/events.py` — 事件总线

**职责**：把浏览器事件归一成 MCP 可观察事件（讨论稿 §11），并按需投递给等待者。
**处理规则**：
- 生产者：page/context 的 Playwright 事件处理器。
- 事件类型：`page_created` / `page_closed` / `page_crashed` / `download_started` /
  `download_completed` / `dialog_opened` / `popup_opened` / `request_failed` / `console_error` / `page_error`。
- 两个消费面：① 有界环形缓冲（供 `network_*`/`console_get` 拉取）；② `wait_for_event` 的一次性等待。
**输出**：事件字典，含 `event/session_id/context_id/page_id/ts` 与类型特有字段
（如 `page_created` 带 `opener_page_id`、`url`）。
**边界情况**：缓冲区满丢弃最旧并计数（`dropped`），不阻塞生产者。

### 5.9 `runtime/artifacts.py` — 下载与产物

**职责**：下载一级能力（讨论稿 §11）。
**输入**：Playwright `Download` 对象 / 任意产出文件 / 用户导出请求。
**处理规则**：
1. 捕获：`page.on("download")` → 等待完成，取 `suggested_filename`、`path()`、大小。
2. 计算 `sha256`；探测 MIME（用下载响应头 + magic bytes，复用现有
   `_extract_document` 的判定思路）。
3. 存到 artifact 目录（`AGENT_GAVEL_DATA_DIR/artifacts/<artifact_id>`），登记元数据：
   ```
   {artifact_id, kind, suggested_filename, mime_type, size_bytes, sha256,
    source_url, session_id, page_id, created_at}
   ```
4. 文本抽取：PDF(pypdf) / docx(python-docx) / 老 doc(soffice→antiword/catdoc)，抽出的
   文本也作为派生 artifact 存。
5. 导出：`artifact_export(artifact_id, dest_dir, filename?)` —— 只接受白名单目录；
   **模型永远拿不到任意绝对路径**，只拿 artifact_id。
6. 生命周期：默认保留 7 天 / 总 2GB，超出按最旧优先清理（`AGENT_GAVEL_ARTIFACT_TTL_DAYS`
   / `AGENT_GAVEL_ARTIFACT_MAX_BYTES` 可覆盖）；`artifact_cleanup` 手动触发；session 关闭
   不立即删（可能仍需导出），由 TTL 决定。
**输出**：artifact 元数据 + `artifact://<id>` 引用。
**边界情况**：文件名冲突 → 加序号；大小超限 → `download_too_large`（不落地或立即删）；
下载取消/超时 → `download_failed`。

### 5.10 `runtime/policies.py` — 策略与安全

**职责**：讨论稿 §14 的安全边界。
**规则**：
- `domain_allowlist`：非空时，`page_navigate` 与顶层请求在此集合外 → `policy_blocked`；
  可选在 `context.route("**/*")` 层拦截子请求（默认只拦导航，避免误伤 CDN）。
- `file_allowlist`：`artifact_export` 目标目录、`locator_upload` 源文件必须在此内。
- `max_download_bytes`：超出即拒。
- `sensitive_params`：配合模板 `params`，值不落日志/不进响应（沿用现有脱敏字段）。
- **副作用审批**：动作元数据 `requires_confirmation=true` 时，执行前要求显式批准
  （工具参数 `confirm=true` 或先调 `workflow_pause`）；未批准返回 `requires_confirmation`。
- **prompt injection 边界**：页面文本/snapshot 只作为"数据"返回，绝不解释为指令；
  snapshot 中标注 `untrusted=true`。
**输出**：允许/拒绝 + 原因。
**边界情况**：allowlist 为空的语义随环境不同——开发默认关（空 = 不限制）；发布/Docker
默认开且初始为空集（需显式配置才放行）。由 `AGENT_GAVEL_ALLOW_DOMAINS`（逗号分隔）控制。

### 5.11 `runtime/metrics.py` — 成本与延迟

**职责**：讨论稿 §9 的成本拆分与 §14 的指标。
**处理规则**：每次 operation 记 `cost = {connect_ms, locator_ms, action_ms, wait_ms,
assertion_ms, total_ms}`；维护各动作类型的 p50/p95/p99 环形样本。
**输出**：随每个响应回 `elapsed_ms`；`performance_metrics` 返回聚合。
**边界情况**：样本有上限（如每类型 1000），避免内存无界。

### 5.12 `runtime/tracing.py` — trace 与运行记录

**职责**：Playwright trace + agent-gavel 自己的操作流水。
**处理规则**：
- `trace_start(context_id)` → `context.tracing.start(screenshots=True, snapshots=True, sources=True)`。
- `trace_stop` → 存为 artifact，返回 artifact_id。
- 操作流水：每个 operation 的结构化记录（讨论稿 §14 字段表），落 JSON 行日志 +
  内存最近 N 条；失败自动附失败截图/HTML snapshot。
**边界情况**：trace 文件可能很大 → 纳入 artifact 大小策略。

### 5.13 `backends/playwright_backend.py` — 执行后端

**职责**：把 runtime 的语义操作翻译成 Playwright 调用。tools 层不直接碰 Playwright。

**locator 解析（讨论稿 §12 优先级）**：

```
target = {by: role|label|placeholder|text|testid|css|xpath,
          value: str, name?: str, exact?: bool, strict?: bool(default True),
          nth?: int, has_text?: str}
```

解析顺序固定（由调用方显式指定 `by`，不猜）：
`get_by_role` > `get_by_label` > `get_by_placeholder` > `get_by_text` >
`get_by_test_id` > `css` > `xpath` > 坐标（仅在 coordinate backend，见 §5.17）。

- `strict=true` 且命中 >1 → `locator_not_unique`，响应候选（元素摘要）。
- 命中 0 → `locator_not_found`，仅当动作 `safe_to_retry` 才允许 runner 自动换 locator。
- 组合：`filter(has_text=...)`、`nth` 收窄。

**动作清单（首版覆盖）**：click / fill / type / press / check / uncheck / select_option /
hover / drag_to / scroll_into_view_if_needed / focus / clear / set_input_files。

**等待与断言分层**（讨论稿 §10）：

| 层 | 由谁负责 | 例子 |
|---|---|---|
| 执行验证 | Playwright 自动等待 | click 成功、fill 成功、page 关闭、download 发生、popup 出现 |
| UI 验证 | agent-gavel expect | URL、文本出现、按钮消失、输入值、表格行数 |
| 业务验证 | agent-gavel expect | 下载哈希、PDF 金额=页面金额、订单号、接口状态 |

**expect 断言类型（首版）**：`eq/neq/exists/not_exists/contains/not_contains/regex/
gt/lt/count_eq/count_gte` + `page` 作用域表达式（JS/属性读取）。三态语义沿用现有
`channels/dom/verify.py`：满足=pass；不满足但有实质变化=ambiguous；不满足且无变化=fail。

**为什么保留三态**：把"程序能明确判的"判掉，只把真拿不准的交回模型（讨论稿 §1、
README）。这是产品差异化核心，不因换 Playwright 而丢。

**`cdp_escape_hatch.py`**：`context.new_cdp_session(page)` 暴露原始 CDP（讨论稿 §5：
CDP 保留为高级逃生通道）。默认禁用，需显式 `backend="cdp"` + 工具白名单。

### 5.14 `backends/` 之外：`runtime/pages.py` 的 snapshot / explore

**snapshot（页面摘要）**：默认返回精简结构——`url/title/ready_state` + 可交互元素锚点清单
（role/name/value/states/bounds），**不是**整棵 DOM。为控制 token：
- `page_snapshot(level="summary"|"interactive"|"full")`，默认 summary。
- `page_explore` 产出带唯一性校验的锚点 + 候选 locator + 置信度（讨论稿 §12）。
- 页面文本标注 `untrusted`（§5.10）。

**explore 候选打分**（讨论稿 §12）：role+name 精确 > label > placeholder > testid >
css id > css class > xpath；分数用于模板编译时选稳定 locator。

### 5.15 `workflows/*` — 模板录制/编译/重放

#### 5.15.1 `schema.py` — 模板结构

吸收讨论稿 §8，落到可校验 JSON：

```json
{
  "template_id": "supplier_invoice_export",
  "version": 3,
  "site": "supplier.example.com",
  "desc": "下载上周发票",
  "backend_requirements": ["semantic"],
  "parameters": {"START_DATE": {"type": "date"}, "PASSWORD": {"type": "string", "sensitive": true}},
  "preconditions": [
    {"type": "url_matches", "value": "*/login*"},
    {"type": "session_authenticated", "value": true}
  ],
  "steps": [
    {
      "id": "open_invoice",
      "action": {"type": "click", "target": {"by": "role", "value": "link", "name": "发票管理"}},
      "checkpoint": {"type": "url_contains", "value": "/invoices"},
      "idempotent": true,
      "safe_to_retry": true,
      "requires_confirmation": false
    }
  ],
  "outputs": [{"name": "downloaded_files", "type": "artifact_list"}],
  "failure_policy": {"retry": "safe_only", "repair": "explore_local_step"},
  "last_status": "pass",
  "last_failure": null,
  "success_count": 12,
  "fail_count": 1
}
```

**兼容**：现有 `channels/dom` 模板（`site/desc/home/params/steps[{action,selectors,expect,...}]`）
是**旧 schema**。新 workflow 加载器遇到旧格式时按 §5.15.5 迁移；旧 `dom_run_template`
继续跑旧 schema（并行共存，互不影响）。

#### 5.15.2 `compiler.py` — 探索 → 模板

输入：一次或多次 `workflow_record` 的原始动作流水 + 页面快照。
处理规则（讨论稿 §8 编译职责）：
1. 删除无效动作：录制期与最终 DOM 状态无关的点击/悬停。
2. 合并连续输入：多个 `press`/`type` 合成一次 `fill`/`type`。
3. 固定值 → 参数：把重复出现且跨运行会变的字面量提为 `$VAR`（启发式 + 显式标注）。
4. 敏感参数：字段名/输入类型命中密码/token → 标 `sensitive`。
5. 生成稳定 locator：按 §5.14 打分选最高分候选。
6. 添加关键检查点：在 URL 变化、页面出现关键元素处插入 checkpoint。
7. 标记不可自动重试动作：提交/删除/支付类 → `requires_confirmation=true`,
   `safe_to_retry=false`。
8. 评估后端需求：出现坐标动作 → `backend_requirements` 加 `coordinate`。
9. 版本：同 `template_id` 保存即 `version+1`，记录 `success/fail`。
**输出**：模板草稿（未发布）。
**边界情况**：无法参数化的值仍以字面量保留并告警（不静默丢）。

#### 5.15.3 `runner.py` — 重放/暂停/恢复/取消/修复

**重放（replay）**：
- 一次 MCP 调用执行整模板。**默认不返回每步中间状态**，只返回检查点摘要 + 产物
  （讨论稿 §10 的 `steps_completed/checkpoints_passed/artifacts/elapsed_ms`）。
- 每步：检查 precondition → 执行 action（经 PageActor）→ 若该步有 checkpoint 则断言 →
  记录结果。任一步 fail/ambiguous 即停，返回失败步索引 + 证据引用。
- 只对 `safe_to_retry=true` 的动作按 `failure_policy.retry` 重试；副作用动作绝不自动重试。

**返回粒度开关 `verbosity`（对应待定问题 3）**：控制"要不要把每个中间检查点返回给模型"。

| 值 | 返回内容 | 用途 | token |
|---|---|---|---|
| `summary`（默认） | `status/template_id/version/steps_completed/checkpoints_passed/artifacts/elapsed_ms` | 常规重放——模型只关心成没成、产物在哪 | 最小 |
| `steps` | summary + 每步 `{step_id, status, checkpoint{type,expected,actual,passed}, elapsed_ms}` | 审计/调模板：看哪步性质变了 | 中 |
| `full` | steps + 每步 evidence（截图/DOM 摘要/网络） | 深度排障/录制后校验 | 大 |

**失败自动升级（关键规则）**：无论 `verbosity` 取何值，只要某步 **fail 或 ambiguous**，
该步**自动**附带该步的 `actual` + evidence，并把 `verbosity` 临时提升到 `full`（仅限失败步，
前缀成功步仍按原粒度）。理由：失败正是最需要现场信息的时候；成功路径不该为诊断付 token。

**为什么默认 `summary`**：讨论稿 §9/§10 的核心是压缩一次重放的往返与 token；每步都回
会把"一次调用"重新摊成"模型逐步看"。开关交给需要审计的场景，默认走省 token 的一侧。

**歧义在哪**：`ambiguous` 步会返回证据但不自动继续——因为模型可能需要介入（点错/断言写窄）。
`full` 也可能被模板自身的 `failure_policy` 覆盖（如 `repair=explore_local_step` 会先尝试
局部修复再返回）。
**暂停/恢复**：
- `workflow_pause`：runner 在步边界暂停，保存"已完整落地的步骤 + 当前 session/context/page"。
- `workflow_resume`：从暂停点继续，**不重登、不重放已完成步骤**（讨论稿 §20 验证码场景）。
**并发（决策 B）**：`workflow_run` 开始时 `session.acquire(run_id)` 独占所用 session；
同 session 的另一条 run 返回 `session_busy`；不同 session 的 run 真并行。`run_id` 用于
pause/resume/cancel/artifacts/日志的归属。
**取消**：`workflow_cancel` 取消当前 op 并停止后续步。
**局部修复（repair）**：
- 第 k 步失败 → 保留前 k-1 步结果 → 只对当前 page 重新 `page_explore` → 生成新 locator →
  重验第 k 步 → 成功则发布 v(N+1)，失败返回需人工介入。
- 修复只重新探索局部，不重复执行已完成的副作用动作（讨论稿 §8）。
**输出**：见讨论稿 §10 摘要格式；失败时附完整证据。

#### 5.15.4 `recorder.py` — 录制

- `workflow_record_start(session_id/context_id/page_id)` 开启记录。
- 记录：动作、页面 url/title、locator 候选、断言、下载、popup。
- `workflow_record_stop` → 交给 compiler 出草稿 → `workflow_validate` → `workflow_publish`。
**边界情况**：录制期间跨 context 的操作也记录并标注所属 page。

#### 5.15.5 `loader.py` — 模板的保存、存储、传播（对应待定问题 5）

**存储分层**（沿用现有双层策略，讨论稿 §8）：

| 层 | 位置 | 可写 | 优先级 | 内容 |
|---|---|---|---|---|
| 用户层 | Linux `~/.config/agent-gavel/workflows/`，Windows `%APPDATA%\agent-gavel\workflows\` | 是 | 高 | `workflow_save` 写的模板 + `stats.json` |
| 包内层 | `agent_gavel/channels/dom/templates/`（旧）与随新 runtime 的 `workflows/` 包数据目录 | 否 | 低 | 随包发布、众包合并进来的模板 |

读取规则：同名 `template_id` **用户层覆盖包内层**；两层各自独立文件，互不删除。
旧目录 `templates/` 继续由旧 `channels/dom` 通道使用；新 workflow 写 `workflows/`，
`loader` 可**只读**加载旧格式做迁移，但绝不改写旧文件。

**保存**：
- `workflow_save(template)` → 归一化 `template_id`（= `site_功能` 的 slug，与旧命名规范一致）
  → 校验 schema（§5.15.1）→ 写用户层 `<template_id>.json`。
- 同 `template_id` 再次保存 = 新版本：`version+1`，保留 `last_status/last_failure/
  success_count/fail_count`（写入模板文件本体，不再单独放 stats，避免"模板与统计分离"）。
- 敏感参数只存 `$VAR` 占位 + `{"sensitive": true}` 声明，真实值永不落盘（§5.10）。
- `workflow_publish(template_id)` = 把用户层模板**导出为可提交文件**（保持纯 JSON、
  无本地路径/凭据），用于 PR；它不联网。

**传播（当前设计 = 本地文件 + Git PR，无自动同步）**：
- 现有机制：README 的"贡献模板"流程——本地跑通 → 存 JSON → 按站点命名放入仓库
  `agent_gavel/.../templates/` → 提 PR → 合并后随包分发。
- 新 workflow 沿用同一精神：`workflow_publish` 产出干净 JSON，人工/脚本拷进包内目录再提 PR。
- **明确不做**（本方案范围外，讨论稿 §19.3 的远期项）：远程模板仓库、按站自动拉取、
  模板市场、跨设备自动同步。这些留作未来独立里程碑，需要时再设计。

**同步语义（若未来做）需先定的问题**：模板源鉴权、版本冲突合并、模板签名/信任、
用户层与远端层的优先级、失效模板下架。当前不做，故不展开。

**失效与统计**：
- 每次运行 `pass`→ 连续失败清零；`fail/ambiguous`→ 连续失败 +1；≥3 标 `suspected`。
- 统计写在模板文件本体的 `last_status/success_count/fail_count`；`workflow_stats` 汇总。

**旧格式迁移表**：`action`+`selectors` → `action.target`；`expect` → `checkpoint`；
`$VAR` 提取复用现有 `templates._extract_vars`；`params` 数组 → 对象并保留 `sensitive`。

### 5.16 `diagnostics/*` — 日志/指标/健康

- `logging.py`：JSON 行日志，含讨论稿 §14 字段（operation_id/session_id/context_id/
  page_id/action_type/locator/url_before/url_after/wait_ms/action_ms/assertion_ms/
  retry_count/browser_pid/artifacts/error_code）。默认写 `logs/runtime-<date>.jsonl`。
- `health.py`：扩展现有 doctor（不改旧 doctor 语义，新增 runtime 段落）报告：
  浏览器进程数、session/context/page 数、是否 connected、metrics 基线、Chrome 模式。

### 5.17 视觉/坐标后端（M6）— 不引入额外视觉技术栈

**定位**：DOM 覆盖不到的场景（Canvas、WebGL、地图、自定义控件、简单小游戏）。
**设计原则（已定）**：**不做 OCR、不做 OpenCV 模板匹配、不引入任何第三方视觉库**。
"看图判断在哪、点哪里"交给调用 runtime 的 **AI**；runtime 只提供两个原语：
**截图** 和 **坐标操作**。这是一个"AI 在环"的循环，不是自动视觉流水线。

```
page_screenshot → AI 看图决定坐标 → coordinate_click → 再截图由 AI 判断结果 → 下一步
```

**提供的能力**：
- `page_screenshot`（M2 已有）：整页 / 指定元素 / 指定区域；响应带图片与元数据。
- 坐标原语：`coordinate_move / coordinate_click / coordinate_drag / coordinate_scroll`，
  底层 `page.mouse.*`，同样经 PageActor 串行。
- `screenshot_grid`（可选辅助）：在截图上叠加坐标网格/十字，帮助 AI 把图像像素映射到页面坐标。

**坐标映射规则（防脱靶，复用 computer-use-linux 同一约定）**：
- 截图可能被下采样；响应必须带 `coordinate_width / coordinate_height / scale /
  device_scale_factor`，AI 按比例换算后再点。
- 统一坐标空间 = **CSS 像素**（`page.mouse` 坐标系）；非 1:1 缩放时在响应里显式说明。
- 每次坐标操作前**先截图**，不缓存旧坐标（页面滚动/响应式都会让坐标漂移）。

**验证**：
- DOM 可用时优先用 DOM 断言（坐标操作用来触发，验证仍走语义）。
- 纯视觉场景：runtime 只提供"操作前后截图 diff"的标量结果；是否达标由 AI 再截图判断。
- **不承诺自动收敛**：坐标循环的智能在 AI 侧，runtime 不猜。

**模板落盘（视觉模板可重放性天然低于语义模板）**：
- 不保存绝对坐标。保存 `{region, description, relative_position, precondition_screenshot?}`。
- 重放时由 AI 重新看当前截图定位（AI 在环）再给坐标；runtime 不做图像锚点自动匹配。
- 因此视觉步骤的 `failure_policy.repair = "rerun_ai"`，并在模板里标注 `backend_requirements:["coordinate"]`。

**反范围重申**：不承诺"通用网页小游戏自动化"，不做帧级/实时竞技操作（讨论稿 §13）。

---

## 6. 数据流与模块关系

### 6.1 单次动作（如 locator_click）

```
tools/action_tools.locator_click(session_id?, context_id?, page_id, target, expect?, ...)
  → runtime 解析目标 page（缺省用 session 的 active page）
  → PageActor.submit(action_type="click", coro_factory=backend.click(page, target), ...)
      → queue 串行执行 → playwright_backend 解析 locator → Playwright click（自动等待）
  → 若带 expect：assertion 在 backend 里按 wait_mode 求值 → 三态
  → 组装响应 {ids, status, elapsed_ms, cost, execution, assertions, evidence?}
```

### 6.2 模板重放

```
tools/workflow_tools.workflow_run(name_or_id, params)
  → workflows/loader 加载 + schema 校验
  → policies 校验 preconditions/domain/副作用
  → workflows/runner 逐步：PageActor 执行 → checkpoint 断言 → 记录
  → artifacts 汇总产物
  → 返回检查点摘要（失败才带完整证据）
```

### 6.3 资源层级与 id 传递

```
runtime → session(<sess_id>) → context(<ctx_id>) → page(<page_id>) → frame → locator
```
每个响应都带当前 `session_id/context_id/page_id/operation_id`（讨论稿 §6），
便于模型在后续调用中显式寻址，不再依赖"第几个 tab"。

---

## 7. 关键边界情况与失败处理

| 场景 | 处理 |
|---|---|
| 浏览器崩溃 | `browser_crashed`；自动重拉；session 配置保留，page 需重开 |
| 页面崩溃 | `page_crashed`；`page_reload` 或重开 page |
| popup 超时 | `page_wait_for_popup` 超时返回 `timeout` + 已捕获事件 |
| dialog 未处理 | 缓存并在响应提示；未处理会阻塞后续动作（须显式 accept/dismiss） |
| 下载超时/取消 | `download_failed`；临时文件清理 |
| 同名下载 | 自动加序号，不覆盖 |
| locator 多重命中 | `locator_not_unique` + 候选项；不自动 guess |
| 断言失败但有变化 | `ambiguous`，附 diff 摘要与截图引用 |
| 断网/请求失败 | `navigation_failed`；`request_failed` 事件可查 |
| 高危动作 | `requires_confirmation`；未批准不执行 |
| 页面含恶意指令 | 页面文本标 `untrusted`，绝不执行页面文字 |
| 1000 次操作 | actor 完成即回收；metrics 环形缓冲有界；定期断言无泄漏 |
| session/context 关闭时仍有在途操作 | 取消所有 pending，返回 `page_closed/context_closed` |

---

## 8. MCP API 清单（按里程碑）

命名与讨论稿 §7 一致。统一约定：
- 列表返回值包成 `{"items": [...]}`（讨论稿 §2.2：避免 MCP SDK 把 list 拆成多个 TextContent）。
- 所有响应含 `status` 与 id 字段；`elapsed_ms` 可选。
- 资源参数可用省略：无 `page_id` 时用 session 的 active page。

### M1 — 资源生命周期（26 个）

```
browser_launch  browser_status  browser_close
session_create  session_list  session_get  session_reset  session_close
session_export_state  session_import_state
context_create  context_close
page_open  page_list  page_get  page_focus  page_close  page_select
page_wait  page_reload  page_go_back  page_go_forward
```

### M2 — 基础 DOM API（21 个）

```
page_navigate  page_snapshot  page_text  page_read  page_links
page_explore   page_screenshot  page_pdf
locator_click  locator_fill  locator_type  locator_press
locator_hover  locator_scroll  locator_focus  locator_clear
page_wait_for_url  page_wait_for_load  page_wait_for_selector
expect
```

### M3 — 高级浏览器能力（20+ 个）

```
locator_check  locator_uncheck  locator_select  locator_drag  locator_upload
locator_set_files  page_handle_dialog
page_wait_for_response  page_wait_for_download  page_wait_for_popup  page_wait_for_event
download_list  download_get  download_save  download_read_text  download_delete
artifact_list  artifact_get  artifact_export  artifact_cleanup
context_cookies_get  context_cookies_set  context_set_headers  context_set_permissions
```

### M4 — 工作流与诊断

```
workflow_save  workflow_validate  workflow_publish  workflow_list  workflow_get
workflow_run  workflow_pause  workflow_resume  workflow_cancel
workflow_record_start  workflow_record_stop  workflow_replay  workflow_stats
trace_start  trace_stop  trace_get
network_start  network_stop  console_get  page_errors  performance_metrics
```

### M5 — 兼容层

```
dom_navigate     → page_navigate
dom_read         → page_read
dom_text         → page_text
dom_step         → action + expect
dom_run_template → workflow_run
```
（M5 前旧 `dom_*` 保持原实现，不迁移。）

### M6 — 视觉/坐标后端（仅截图 + 坐标，无 OCR/CV）

```
page_screenshot            # 已有于 M2，扩展 region/element 参数
screenshot_grid            # 可选：截图叠加坐标网格辅助 AI 定位
coordinate_move  coordinate_click  coordinate_drag  coordinate_scroll
```
（"看"由 AI 完成；runtime 不提供 find_anchor / OCR / 模板匹配。）

---

## 9. 里程碑分期

| 里程碑 | 对应 Phase | 交付物 | 验收 |
|---|---|---|---|
| **M0 能力规格** | Phase 0 | 本文档 §8 工具清单 + §7 边界表 + 本文档即能力验收基准 | 清单经作者确认 |
| **M1 runtime** | Phase 1 | `runtime/*` + `backends/playwright_backend`（仅生命周期）+ `browser_* / session_* / context_* / page_*` | `tests/test_runtime.py`：冷启动 <1.5s、热<20ms、多 session 隔离、并发同 page 串行、1000 次无泄漏 |
| **M2 基础 DOM** | Phase 2 | `tools/action_tools.py` + `assertion_tools.py`；snapshot/explore/expect | `tests/test_basic_dom.py`：导航/读取/点击/填表/中文/断言三态/Ctrl+A 真实语义 |
| **M3 高级能力** | Phase 3 | 下载/上传/popup/iframe/dialog/storage state/cookies/headers/proxy | `tests/test_advanced.py`：下载哈希、popup 捕获、iframe 操作、dialog、cookie 隔离 |
| **M4 workflow** | Phase 4 | `workflows/*` + artifacts + trace/network/console | `tests/test_workflow.py`：录制→编译→一次调用重放→暂停恢复→局部修复；同一模板二次执行更快更省 token |
| **M5 兼容层** | Phase 5 | `dom_*` → 新工具 shim（可选开关） | 旧 `dom_*` 测试改写为走 shim 仍全过 |
| **M6 视觉** | Phase 6 | 截图 + 坐标原语（不引入 OCR/CV，"看"由 AI 完成，见 §5.17） | 一个 Canvas 控件或地图拖拽的「截图→AI 定坐标→点击→再截图」端到端用例（AI 在环） |

**依赖关系**：M1 ← M2 ← M3 ← M4 ← M5；M6 依赖 M2+M3 稳定。每期结束跑全量 pytest +
旧脚本回归（`tests/*.py`）。

---

## 10. 与现有 CDP 代码的共存策略

1. **物理隔离**：新代码在 `runtime/ backends/ tools/ workflows/ diagnostics/`；旧代码在
   `channels/dom/`、`channels/desktop/`，本阶段不改。
2. **进程隔离**：新 runtime 用 Playwright 自管的浏览器（新 profile / 新进程）；
   旧 `dom_*` 继续用 `browser_manager.py` 的调试 Chrome（9222）。互不抢占。
3. **入口聚合**：`main.py` 只新增 `register_runtime_tools(mcp)` 一行；退出 handler
   追加 `runtime.shutdown()`。旧 `close_resident` / `stop_own` 不动。
4. **模板目录分离**：旧模板仍在 `channels/dom/templates` + 用户 `templates/`；
   新 workflow 放用户 `workflows/`。`loader.py` 可读旧格式做迁移，但不写回旧文件。
5. **M5 决策点**：只有当 M2–M4 的等价能力覆盖旧 `dom_*` 全部测试后，才把 `dom_*` 改为
   转发 shim（或保留双轨，由环境变量 `AGENT_GAVEL_LEGACY_DOM=1` 选择）。

**为什么先并行不替换**：旧 `dom_*` 是本项目目前唯一验证过的闭环（讨论稿 §2.3）。
一次性替换会同时承担"新内核不成熟"与"旧能力回归"两类风险；并行能让新内核用测试
逐步证明等价，再切换。

---

## 11. 测试体系（本地 fixture 站 + pytest）

### 11.1 目录

```
tests/
  conftest.py            # pytest fixtures: 静态站点 http server、runtime、browser
  webapp/                # 本地综合测试站点（讨论稿 §15）
    basic.html           # 标题/文本/链接/输入/按钮
    forms.html           # 各类表单、select、checkbox、radio、file
    delayed.html         # 异步延迟出现内容（测 wait/poll/event）
    popup.html           # target=_blank / window.open
    download.html        # 触发下载（文本/PDF/csv）
    upload.html          # 文件上传
    iframe.html          # 嵌套 iframe
    dialog.html          # alert/confirm/prompt
    multiple_pages.html  # 多页跳转
    redirect.html        # 302 / meta refresh / JS 跳转
    websocket.html       # 异步推送
  test_runtime.py
  test_basic_dom.py
  test_advanced.py
  test_workflow.py
```
旧脚本（`browser_manager_smoke.py` 等）保留，作为旧通道回归。

### 11.2 fixtures

- `webapp_server`：session 级，用 `http.server` 在线程里起 `tests/webapp/`，返回 base_url。
- `runtime`：function/class 级，起 headless Playwright（`AGENT_GAVEL_HEADLESS=1`），
  结束后断言进程/context/page 归零。
- 默认 headless，CI 友好；本地调试可加 `--headed`。

### 11.3 必测项（讨论稿 §15）

多 session 并行、多 context cookie 隔离、多 page、popup、iframe、下载、上传、dialog、
页面崩溃恢复、浏览器崩溃恢复、网络超时、重定向、下载取消、locator 严格匹配、中文输入、
Ctrl+A/Shift+Tab 组合键、storage state 导入导出、trace 生成、进程泄漏、1000 次连续动作、
多 MCP 客户端连接。

### 11.4 性能门槛（CI 断言）

见 §12；超标即失败（可标记 xfail 以适配慢 CI）。

---

## 12. 性能目标与成本模型

| 指标 | 目标 | 测量点 |
|---|---|---|
| 冷启动 | < 1.5s | `browser_launch` |
| 热连接 | < 20ms | 已连接时一次简单读取 |
| 本地简单动作 p95 | < 100ms | click+expect 闭环 |
| 本地简单读取 p95 | < 50ms | page_read |
| 同 page 并发 | 严格串行无错乱 | 并发提交 N 个动作后校验顺序 |
| 跨 page 并发 | 真并行 | 两 page 同时跑，总耗时 < 串行 |
| 崩溃恢复 | < 5s | kill 浏览器后下次调用恢复 |
| 1000 次操作 | 无 page/context/process 泄漏 | 前后资源计数比对 |

成本拆分随响应返回：

```json
{"cost": {"connect_ms": 2, "locator_ms": 3, "action_ms": 8,
          "wait_ms": 120, "assertion_ms": 4, "total_ms": 137}}
```

---

## 13. 风险与取舍记录

| 风险/取舍 | 决策 | 理由 |
|---|---|---|
| 重建 vs 保留旧实现 | 并行保留，新层独立 | 旧闭环已验证；降低双风险（§10） |
| 进程数 vs 隔离 | 默认共享浏览器，需 proxy/persistent 时独立进程 | Playwright proxy 是 launch 级；隔离优先 |
| 三态验证是否保留 | 保留并强化 | 产品差异化核心（讨论稿 §1、roadmap） |
| 是否自研 agent | 否 | 只做手，接现成大脑（roadmap） |
| 视觉后端时机 | 语义稳定后再做（M6） | 需求占比低但技术价值高，避免早期分心 |
| 视觉后端技术栈 | **仅截图 + 坐标，AI 在环；不引入 OCR/OpenCV/第三方视觉库**（已定，§5.17） | 避免技术栈杂乱；"看"交给 AI，runtime 保持单一 |
| 旧模板迁移 | 只读兼容，不改写旧文件 | 避免破坏仍被旧通道使用的资产 |
| Playwright 自带 chromium 下载 | 默认用系统 Chrome | 免安装、体积小；缺 Chrome 才回退自带 |
| MCP list 序列化 | 统一 `{"items":[]}` | 讨论稿 §2.2 明确 MCP SDK 拆包问题 |

**已定（全部拍板）**：
1. 视觉后端只做截图 + 坐标原语，不引入 OCR/CV（§5.17）。
2. `workflow_run` 默认 `verbosity="summary"`，失败步自动升级为 full（§5.15.3）。
3. 模板传播 = 本地文件 + Git PR，**不做自动同步**；远程模板源为远期独立事项（§5.15.5）。
4. **单实例并发跑多模板 = 方案 B**：每条 workflow 独占一个 session，不同 session 可并行，
   同一 session 再来一条返回 `session_busy`（见 §5.7、§5.15.3）。
5. **artifact 配额**：默认保留 7 天 / 总 2GB，超出按最旧优先清理；可用环境变量
   `AGENT_GAVEL_ARTIFACT_TTL_DAYS` / `AGENT_GAVEL_ARTIFACT_MAX_BYTES` 覆盖（§5.9）。
6. **domain allowlist 默认**：开发默认**关**（空 = 不限制）；发布/Docker 默认**开**且为
   保守空集（需显式配置才放行）。由 `AGENT_GAVEL_ALLOW_DOMAINS` 控制（§5.10）。

---

## 14. 验收方式

1. **文档验收（本轮）**：本方案经作者确认，作为后续长程基准。
2. **逐里程碑验收**：每期用 §9 的验收项 + 全量 pytest + 旧脚本回归；性能门槛见 §12。
3. **产品闭环验收（M4 后）**：按讨论稿 §21 的真实场景跑一次——打开网页→探索记录→
   保存模板→改参数→**一次调用重放**→返回验证结果与下载文件；对比"第二次是否明显比
   第一次更快、更省 token"。
4. **失败可解释验收**：制造一次失败（如验证码/元素消失），确认返回
   `status/reason/page_id/证据 artifact/resume_hint` 可被模型读懂并恢复。

---

## 附录 A：模板 schema 版本

- `schema_version` 缺省 = 旧版（`channels/dom` 格式）。
- 新版模板显式写 `"schema_version": 2`；loader 据此分派。
- 新字段与旧字段映射见 §5.15.5。

## 附录 B：响应外形约定

**成功（动作）**
```json
{
  "status": "ok",
  "session_id": "sess_001", "context_id": "ctx_001", "page_id": "page_003",
  "operation_id": "op_9f12", "elapsed_ms": 42,
  "execution": {"action_completed": true},
  "assertions": [{"name": "result_row_visible", "passed": true}],
  "evidence": []
}
```
**失败/不确定**
```json
{
  "status": "ambiguous",
  "reason": "assertion_failed_but_changed",
  "actual": {"v": "..."},
  "evidence": ["page://page_003/screenshot", "artifact://artifact_91"],
  "hint": "页面已变化但断言未满足——可能点错或断言写窄，建议 page_explore 复查"
}
```
**列表**：`{"items": [...]}`。

## 附录 C：讨论稿条款 → 本文模块映射

| 讨论稿 | 本文 |
|---|---|
| §3 结构问题 | §1.2 解决表 |
| §5 架构方向 | §4 |
| §6 Session/Context/Page | §5.4–5.6 |
| §7 MCP API | §8 |
| §8 模板设计 | §5.15 |
| §9 速度优化 | §5.11、§12 |
| §10 验证设计 | §5.13、附录 B |
| §11 下载/artifact | §5.9 |
| §12 Locator | §5.13–5.14 |
| §13 视觉后端 | M6（§9） |
| §14 并发/安全/观测 | §5.7、§5.10、§5.16 |
| §15 测试体系 | §11 |
| §16 重建路线 | §9 |
| §17/§18 市场与需求 | 不落代码，保留为产品判断 |
