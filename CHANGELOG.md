# Changelog

## Unreleased

### 统一响应与工具收敛（阶段 4）

- 所有工具经 `guard` 统一封装：`status` 取值受限，自动补 `operation_id` 与 `elapsed_ms`。
- 状态归一：`no_match` → `fail` + `reason`；`partial_error` → `ok` + `complete:false`。
- `page_navigate` 复用统一执行流水线（`post` 钩子承接 HTTP/空页检查）。
- `locator_*` 共用 `_run_target`，删除重复执行代码；签名保持显式。
- 删除三个别名工具：`page_focus`、`locator_set_files`、`workflow_replay`（工具数 97 → 94）。
- 后端拆分为 `locator` / `reader` / `jsrender` / `frames` / `util`，`__init__` 只保留编排。
- 工具按资源拆分：`session_tools` / `context_tools` / `page_lifecycle`，`browser_tools` 只留浏览器。
- 宽泛 `except Exception: pass` 全部标注 `silent-ok`，并加入不变量测试。
- 新增 `daemon_shutdown`（需 confirm，运行中需 force），停止后下一次调用自动拉起新 daemon。
- 工具数量快照 95；新增/删除必须显式更新。
- 加入 `ruff`（E/F/I/B/SIM/ASYNC）与 `mypy`，CI 增加 lint/format/type 检查。

### 动作与断言词表（阶段 3）

- 无头 managed 启动时通过 `--user-agent` 给默认持久容器设置桌面 UA，默认容器不再
  暴露 `HeadlessChrome`（此前只有隔离容器会替换 UA）。
- 测试默认无头；会话结束时按测试资料目录清理 Chrome（含未登记的残留）。


- 新增 `core/actions.py`：动作类型、必填字段、幂等性、副作用判定、录制字段的唯一来源；
  schema / runner / backend 全部改为查询它，删除四套重复分类。
- 后端拆为 `backends/playwright/` 包，目标型动作移入 `actions.py`；`act()` 统一以
  `handle` 为第一参数。
- 新增 `core/checks.py`（断言 IR）：`Check`、`READ_KINDS`、`OPS`、`parse`、
  `from_checkpoint`；未知 checkpoint 直接报错，不再静默放行。
- 新增 `backends/playwright/assertion.py`：读取、比较、JS 谓词、三态求值各一份；
  删除 `_normalize_checks` / `_actual` / `_event_predicate` / `_cmp_js` 等重复实现。
- `assertion_schema` 的 read/op 列表由 `core/checks.py` 生成。
- 录制：`focus/scroll/hover` 默认不录（`keep_all=True` 才保留）；同目标连续输入合并；
  只有断言 `pass` 时才写入 checkpoint。
- 编译器：`url_contains` 自动断言改为 `workflow_record_stop(auto_checkpoint=True)`
  显式开启，默认关闭。

### 测试与生命周期（本轮）

- 测试复用同一个浏览器进程（`reset_runtime(keep_browser=True)`），用例共享一个事件
  循环；全量套件（除真实站点）从 400s+ 降到 142s。
- `pytest-timeout` 单测上限 90s；不再出现无界等待。
- 修复 context 的 `page` 监听器泄漏：旧 context 会在 reset 后抢注册新页面。
- `soft_reset` / `shutdown` 清理 pages/contexts 注册表与默认容器索引。
- 有头模式 PDF 改走 CDP `Page.printToPDF`，不再是"仅无头可用"。
- 托管 Chrome 增加 `--keep-alive-for-test`：关闭最后一个标签不再退出浏览器进程。

### 身份与产物一致性（阶段 2B）

- 录制缓冲下沉到 runtime，按 **context** 归属；每步记录 `page_id`，共享容器下不再丢步。
- `PageHandle` 只保留 `context_id`；页面不再伪造单一 session 归属。响应中的 `session_id`
  表示本次调用所属会话，共享容器且无法唯一确定时省略。
- `page_list` / `page_get` 增加 `creator_session_id`；`session_create` 增加 `shared_container`。
- 页面解析改为显式作用域（`page_id` → `context_id` → 唯一有页面的 context）；
  多个 context 有页面时报 `ambiguous_page`，不再静默选一页。
- 新增 `context_busy`：同一 context（含跨 session 共享默认容器）上的两个 run 必须串行，
  第二个明确失败而不是交错执行。
- 默认容器按浏览器实例分槽；`Context` 删除 `refcount`，租约由 `session_ids` 集合表达。
- `browser_status` 用 `active_pages`（每个 context 一项）取代全局 `active_page_id`。
- 产物索引加锁；并发下载先预留 id，避免索引丢项。
- 页面/context/actor 的关闭路径全部加超时，避免退出时无限等待。
- 测试配置加入 `pytest-timeout`（单测 90s），不再出现整套用例卡死无法定位的情况。

### 多进程资源所有权（阶段 2A）

- 新增单实例 daemon（`agent_gavel/daemon.py`）：唯一持有浏览器、session/context/page、
  队列、事件、产物索引与锁；用 `<数据根>/daemon.lock` 的 flock 保证单实例，崩溃由内核释放。
- 新增 adapter（`agent_gavel/remote.py` + 重写的 `main.py`）：MCP stdio 进程只转发工具调用，
  不导入 Playwright、不持有运行时状态；daemon 不在时自动拉起并等待就绪。
- 工具 schema 由 daemon 提供，adapter 动态注册，避免两处维护工具清单。
- 跨 adapter：资源 id 一致；同一 session 的并发 `workflow_run` 由一个 daemon 统一裁决，
  `session_busy` 真正生效；同页动作由同一 PageActor 串行化。
- `browser_status` 增加 `daemon_pid` 与 `adapters` 计数。
- daemon 崩溃后：adapter 自动重连并拉起新 daemon，复用原 Chrome，不产生第二个浏览器；
  本地传输在 POSIX 用 Unix socket，在 Windows 用 127.0.0.1 + token。
- 新增 `tests/test_multiprocess.py`：4 个真实 adapter、1 个 daemon、1 个 Chrome；
  验证 id 一致、跨进程 `session_busy`、崩溃恢复与单实例。

### 架构收敛（阶段 0 / 阶段 1）

- 新增 `tests/test_contract_snapshot.py`：真实浏览器端到端固定对外字段、三态语义、
  `session_busy` 与 `precondition_failed`。
- 新增 `tests/test_invariants.py` 与 `tests/test_vocabulary.py`：环境变量、词表唯一性、
  写路径、async 阻塞、包依赖方向等结构不变量。
- 新增 `agent_gavel/core/`：`config`（唯一环境变量入口）、`errors`（含 `trace_id` 与日志）。
- 修复：浏览器关闭时 `Browser.close` 的 await 可能永久挂起，导致整个服务卡死。
- 修复：`AGENT_GAVEL_PROFILE_DIR` 为空字符串时会在进程工作目录创建 `sess_*` 资料目录。
- 浏览器进程的 kill / 扫描 / 探测改为异步或线程化，不再阻塞事件循环。
- 退出信号改为等待清理完成（有上限）后再退出，减少孤儿 Chrome。
- `internal` 错误现在带 `trace_id`，完整堆栈写入 `<数据目录>/logs/agent-gavel.log`。
- 模板存储收敛为单一用户目录；`workflow_list` / `workflow_get` 不再返回 `layer` 字段。
- `loader` 拆分 `resolve_id`（只读宽松解析）与精确读写；`delete` / `record_run` 只接受精确 id。
- 浏览器启动合并为单一实现；因 proxy/channel 等原因切换模式时返回 `mode_resolved` 与
  `mode_warning`，不再静默改变。
- 持久 session 的资料目录从临时目录改到 `<数据目录>/profiles/`，重启后可保留。

## 0.3.2 — 2026-10-14

### Workflow 修复与增强（依测试反馈）

- **修 checkpoint 归一 bug（关键）**：单条 check 字段形态 `{read,selector,op,value}` 曾被
  "顶层 op"逻辑误绑（`value` 取成了 `selector`），导致录制下来的异步断言（如"成功加入购物车"）
  必假失败。现在单条字段形态 / 顶层 op `{title:'百度',op:'contains'}` / 扁平 `{url_contains:'x'}`
  / `expected` 别名 各自正确。
- 新增 action 类型 `wait_for_text`：模板可表达"等待异步文案出现"；`page_wait_for_text` /
  `page_wait_for_selector` 会被录制器落成步骤。
- 新增 `workflow_delete`；`workflow_get`/`workflow_run` 支持**不带 site 前缀的名字**自动补全；
  录制生成 template_id 时若 name 已含 site 前缀则不重复拼（不再 `taobao_taobao_...`）。
- `workflow_run`/`workflow_replay` 支持显式 `page_id`。

## 0.3.1 — 2026-10-13

### 浏览器启动与资料副本

- 新增**资料副本工具** `browser_profile_copy / list / use / delete`：从用户 Chrome 资料目录
  复制一份可累计的副本（档位 `strict|default|full`），默认剔除密码/自动填充/同步/设备标识/
  缓存，保留 cookie/历史/站点态；首个副本自动成为当前，可在多份副本间切换。
- **容器策略**：`session_create` 默认走 **profile 默认容器**（继承 profile 登录/cookie，
  改动累积回 profile，可迭代）；`container="isolated"` 新建隔离容器（继承 profile 的 cookie，
  但不写回）。`context_create` 默认 isolated；`clone` 默认 isolated。
- 浏览器默认 **C1**：自起加固 Chrome（持久真实 profile）+ `connect_over_cdp`；`browser_mode="attach"`
  + `cdp_endpoint` 可**接管**已存在浏览器（C2，返回 warning）；`playwright` 为旧路径。
- 启动前若已有同 profile 的 Chrome 且调试口可用则**复用**，避免资料目录单实例冲突与残留窗口。
- 关闭自起 Chrome 时改用 CDP `Browser.close` 干净退出，确保 localStorage/cookie 落盘。
- 修复：`session_create` 传 `headless` 不再强制另起进程（此前会用**同一 profile** 起第二个
  Chrome → 单实例冲突、约 25s 超时、并弹出一个空窗口）；真正需要独占时（proxy/channel/
  browser_mode/endpoint）才另起，且使用**独立资料目录**。

### 浏览器生命周期对账（修"孤儿 Chrome 占 profile 卡死"）

- `browser_launch` 前**对账**：先查同 profile 是否已有我们的 Chrome —— 能复用就复用
  （优先读状态文件 `<profile>/.agent-gavel-browser.json`，**MCP 重启后仍能 re-attach**；
  再退回 /proc 扫描），否则清掉遗留进程并删除 `Singleton*` 锁再干净启动，绝不盲起第二台。
- `browser_status` 增加 `os_browsers`：列出 OS 里真实在用我们资料目录的 Chrome（含重启后
  丢失登记的孤儿，标 `registered:false`），不再只看内存登记表。
- `browser_launch` **幂等**：已存在且参数一致 → 返回原句柄，不重启。
- 关闭时会清状态文件与单例锁；启动等待从 25s 降到 12s，失败带明确 hint（profile 被占用）。
- 修复 `/proc/<pid>/cmdline` 在某些环境非 NUL 分隔导致进程匹配失效的问题。

### 错误语义 / 等待 / 定位 / 元信息（依外部测试反馈）

- `page_navigate`：HTTP≥400 返回 `http_status`/`http_error`/`warning`（`fail_on_http_error=True`
  可直接判失败）；`min_text_chars` 给定且正文更短 → `empty_page` 失败；正文极短默认给
  `page_looks_empty` 警告。
- 导航竞态（"Execution context was destroyed" 等）在只读操作上自动重试一次，仍失败则报
  可重试的 `nav_race`；触发下载的导航给独立错误码 `download_started`（不再当可重试）。
- 新增 `page_wait_for_text`；`page_snapshot` 增加 `level="compact"`；新增 `page_find_text`、
  `page_meta`；`page_read`/`page_meta` 标 `untrusted`。
- 启动自愈：复用不了时清理我们自己遗留的僵尸 Chrome（带自动化标志、占着 profile）再起。

### 断言 DSL 修复（URL/文本被错当标题）

- **`actual` 现按目标取值**：`{url:{...}}`→location.href、`{title:{...}}`→document.title、
  `text`/`count`/`value`/`attr`/`html` 各取各的；不再一律拿标题去比。
- 支持扁平便利写法 `{url_contains:"x", text_contains:"y", count_gte:1}`。
- `expected` 与 `value` 等价；支持顶层 `{title:"百度", op:"contains"}`。
- 读不到"要读什么"（未识别 key 且无 selector/read）→ 明确报错，不再静默退回标题比较。
- `expect` 的 `checks` 与 `assertions` 等价；新增只读 `assertion_schema` 工具，并在
  `expect`/`page_navigate` 描述里给出字段与 op 枚举。

### 反检测

- 启动参数加 `--disable-blink-features=AutomationControlled`：关闭 `navigator.webdriver`
  （实测 Playwright 默认 True，页面第一眼即可判自动化）。
- 不再默认 `--disable-gpu`：有头时使用真实 GPU 渲染（WebGL 从 `SwiftShader` 恢复为真实显卡）；
  无 GPU 环境用 `AGENT_GAVEL_DISABLE_GPU=1` 显式加回。
- 无头模式默认 UA 去掉 `HeadlessChrome`。
- 新增 `AGENT_GAVEL_EXTRA_ARGS`（追加启动参数）、`AGENT_GAVEL_CHROME_CHANNEL`。
- 新增回归测试 `tests/test_stealth.py`；调研与对比见 `docs/detection.md`。

## 0.3.0 — Playwright runtime 重建

### 新增（新 runtime，替换旧的裸 CDP 网页通道）

- **资源模型**：session / context / page，稳定 id；ephemeral 共享浏览器、persistent
  持久 profile、clone 从 storage state 起；每页串行队列、跨页并行。
- **基础 DOM**：导航/读取/探索（返回验证过的稳定锚点）/动作/三态断言/截图/PDF。
- **高级能力**：勾选/下拉/拖拽/上传、popup、iframe、dialog（手动/自动）、
  等响应/等加载/等下载/等事件、cookie/请求头/权限、storage state 导出导入。
- **工作流**：录制 → 编译（参数化/合并输入/标记副作用/补检查点/版本）→ 单调用重放；
  暂停/恢复/取消；失效检测（连续失败 ≥3 标 suspected）。
- **产物**：下载自动捕获（sha256/大小/MIME）、PDF/Word/文本抽取、TTL/配额清理、
  白名单导出。
- **诊断**：trace、网络记录、console、页面错误、性能指标。
- **失败恢复（决策 B）**：
  - B1 安全且幂等步骤瞬时失败原样重试一次（副作用步骤绝不重试）；
  - B2 三态用**作用域结构签名 diff** 判定"拿不准"，并只在拿不准时返回 diff 证据；
  - B3 断言等待**事件优先 + 轮询兜底 + 到点 diff 三态**。

### 安全

- 工作流 `preconditions` 失败立即 `blocked`（此前校验结果未被使用）。
- 产物导出：文件名净化 + 目录包含校验（防路径穿越）；默认不返回绝对路径。
- 域名白名单经 context 路由覆盖 popup / 重定向 / window.open。
- 发布加固 `AGENT_GAVEL_DOMAIN_GUARD`：白名单为空也强制限制（空集=全拒）。
- `reset_runtime` 改为异步并先关闭旧实例，防孤儿浏览器进程。

### 变更

- 旧 DOM（裸 CDP）与 Desktop（AT-SPI）通道整体归档到仓库根 `legacy/`：
  **不打包、不注册、不运行**。
- 移除 `websockets` 依赖（旧 CDP 专用）。
- 包 `keywords`/描述更新；版本 0.2.3 → 0.3.0。

### 工作流契约修复（发布阻断清单）

- `expect` 失败不再被置为成功；`type(text=...)` 真正输入；工作流截图传对对象。
- 录制保留原断言；编译器保留 checkpoint，且不再无条件删除 hover/focus。
- 幂等性按动作类型判定（click/press/type/drag 默认不可自动重试），
  副作用关键词仍需确认；定位失败（动作未执行）才给非幂等动作一次重试/修定位。
- 参数替换改为变量名整体匹配，`$QUERY` 不再误替换 `$QUERY_ID`。
- `template_id` 只允许 `[a-z0-9_]`，存储/发布拒绝路径穿越。
- 局部修复改为"语义/标识 token 评分选锚点"，并要求非幂等动作修复时必须有检查点。

### 发布工程

- 新增 GitHub Actions CI：功能/安全/真实任务测试、wheel 不含 legacy、干净安装导入。
- README 补"支持范围与限制""从旧版迁移"；Docker 默认发布加固。
- 已验证：从 wheel 装进独立 venv，注册 88 个工具，无头导航+断言通过。

### 测试

- `tests/test_security_fixes.py`、`tests/test_retry_diff_wait.py`、
  `tests/test_workflow_contract.py`（工作流契约 8 项）、
  `tests/test_real_tasks.py`（真实任务级，含页面改版后局部修复并复用）。
- `tests/bench_tasks.py`：多步真实任务基准（本地 + 真实网站），实测
  探索 5 调用/1681B → 重放 1 调用/192B；页面改版后局部修复通过并发布 v2。

## 0.2.x — 旧裸 CDP 网页通道 / AT-SPI 桌面通道

- 声明式验证闭环、探索即固化、模板库、失效检测等（见 `legacy/`）。
