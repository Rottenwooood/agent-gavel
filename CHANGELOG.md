# Changelog

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
