# agent-gavel 讨论整理

本文整理围绕 agent-gavel 的测试结果、产品定位、架构重建、速度、模板复用、验证、视觉操作、市场需求、开源宣发和个人决策等讨论内容。

本文尽量保留原始讨论中的细节，只合并重复内容。各节按主题独立组织：测试结论、架构设计、市场判断和个人决策分别讨论。

## 1. 项目背景和核心设想

agent-gavel 是一个面向 AI 的 MCP 浏览器/桌面操作工具。最初的核心想法是：

~~~
AI 声明动作 + “做完后页面应该长什么样”
→ 执行动作
→ 程序断言
→ 返回 pass / fail / ambiguous
~~~

目标是让模型少做一次“把页面重新看一遍再判断”的往返。

后来进一步明确，核心价值不只是验证，而是：

~~~
探索一次
→ 记录动作和状态
→ 编译成可验证模板
→ 后续一次调用高速重放
→ 失败时局部修复模板
~~~

模板复用的目的包括：

- 减少模型往返次数。
- 减少重复探索次数。
- 减少模型思考长度。
- 减少 token 消耗。
- 降低单次操作和整个任务的时间。
- 让重复任务从“每次重新教模型”变成“调用已验证流程”。

产品逐步应成为：

> 面向 AI agent 的可探索、可验证、可复用、可恢复浏览器任务执行器。

## 2. DOM 测试记录

### 2.1 测试范围和环境

后来用户明确要求只测试 DOM 部分，因此桌面 AT-SPI 结果不纳入最终结论。

测试环境：

- Python 3.13.11
- Chrome 144
- Linux 图形会话
- 源码版 uv run
- 项目版本 0.2.3

测试过程中发现，多个浏览器脚本同时共用默认 9222 端口和 Chrome profile 会互相抢状态，造成假失败。因此最终 DOM 结果以串行、隔离端口和独立 profile 的测试为准。

### 2.2 MCP 工具注册和协议层

MCP 层成功注册 21 个工具，其中 DOM 工具 12 个：

~~~
chrome
dom_step
dom_navigate
dom_read
dom_text
dom_document
dom_resolve
dom_explore
dom_save_template
dom_list_templates
dom_template_stats
dom_run_template
~~~

一次独立 MCP 探测结果：

| 操作 | 结果 | 耗时 |
|---|---:|---:|
| MCP initialize | 通过 | 约 545ms |
| Chrome 冷启动 | 通过 | 约 503ms |
| Chrome 热状态检查 | 通过 | 约 1ms |
| dom_navigate 本地页面 | 通过 | 约 32ms |
| dom_text | 通过 | 约 4ms |
| dom_explore | 通过 | 约 114ms |
| dom_read | 通过 | 约 3ms |
| dom_step 点击和断言 | 通过 | 约 7ms |

MCP 返回列表值时，MCP SDK 会把列表序列化成多个 TextContent。对于客户端来说，最好返回 {"items": [...]} 或使用结构化返回。

### 2.3 已通过的测试

| 测试 | 结果 | 耗时 |
|---|---:|---:|
| browser_manager_smoke.py | 8 个生命周期场景全部通过 | 6.27s |
| verify_branches.py | 10/10 分支通过 | 4.18s |
| dom_atomic_coverage.py | 17/17 原子动作通过 | 0.82s |
| dom_read_text_nav.py | 8/8 通过 | 2.09s |
| dom_enhancements.py | 14/14 通过 | 5.23s |
| DOM 模板保存、列表、执行、统计 | 通过 | 模板执行约 69ms |
| python -m compileall | 通过 | - |
| uv build | 通过 | - |

覆盖内容包括：

- 导航、读取页面值、正文、链接、元素探索。
- Word 文档抽取。
- 点击、左键、右键、双击、悬停、拖拽、滚动。
- 聚焦、中文输入、清空、回车、普通按键和组合键。
- 重定向、新标签页、动态延迟断言、事件等待。
- DOM diff 兜底、断言失败诊断、模板保存和重放。

### 2.4 验证分支耗时

| 分支 | 状态 | 典型耗时 |
|---|---|---:|
| 有断言且满足 | pass | 8ms |
| 断言不满足但页面发生变化 | ambiguous | 6ms |
| 断言不满足且页面无变化 | fail | 约 1027ms |
| 无断言但有变化 | pass | 7ms |
| 无断言且无变化 | ambiguous | 6ms |
| 事件等待满足 | pass | 7ms |
| 延迟满足的 poll | pass | 约 515ms |
| 降级重试全部失败 | fail | 约 1843ms |
| 导航加断言 | pass | 9ms |

### 2.5 本地热连接性能

独立本地 fixture 页面进行了 20 次循环：

| 操作 | p50 | p95 |
|---|---:|---:|
| CDP 简单读取 | 0.22ms | 0.46ms |
| dom_explore | 0.74ms | 1.16ms |
| 点击加断言闭环 | 3.71ms | 4.29ms |
| 本地页面导航 | 6.4ms | 107.7ms |

浏览器生命周期：

- 冷启动约 518ms。
- 热调用约 0.4–0.6ms。

动态延迟页面导航约 2559ms，这是等待页面内容的预期成本。

### 2.6 外网站点稳定性

Bing 测试失败：

~~~
实际标题：Search - Microsoft Bing
模板期望：标题包含“必应”
~~~

一轮多模板测试：

| 模板 | 结果 | 耗时 |
|---|---:|---:|
| bing_search | ambiguous | 230ms |
| douban_movie_search | pass | 472ms |
| baike_search | pass | 1.06s |
| cnblogs_search | pass | 11.33s |
| runoob_search | pass | 218ms |

不能把外网站点模板当成稳定 CI 基准。Bing 主要是地区化标题或页面变化，博客园明显受到网络和反爬影响。建议使用 URL、结果区域、结果数量或稳定 DOM 结构断言，不要依赖固定语言标题。

### 2.7 组合键缺陷

源码中 key spec 位于 adapter.py。实际结果：

~~~
Ctrl+A     主键变成 A，但 code 为空、virtual key code 为 0
Control+A  同样存在问题
ctrl+a     Ctrl 修饰键完全丢失
~~~

输入框实测：

~~~
原值：abc
执行 Ctrl+A
selectionStart：3，期望 0
输入 X 后：abcX，期望 X
~~~

组合键不能只验证 keydown 是否产生，还必须验证真实编辑语义。

## 3. 当前实现的结构问题

### 3.1 CDP 直接实现过多

当前 adapter.py 直接维护 CDP WebSocket、键盘码表、真实和合成键盘事件、页面切换、页面等待、DOM 取值、探索和浏览器状态。长期扩展时会不断遇到浏览器语义、页面生命周期、popup、iframe、下载和事件同步问题。

### 3.2 单端口、单 profile、隐式当前页面

默认单一 CDP 端口和 profile 会造成页面相互导航、profile 冲突、操作落在错误页面和测试假失败。

### 3.3 全局页面锁限制并发

全局 PAGE_LOCK 能保护同一页面状态，但会让所有导航共享串行通道。未来应做到：

~~~
同一 page：串行
不同 page：并行
不同 context：并行
不同 session：并行
~~~

### 3.4 动作、等待、验证、重试耦合

dom_step 把动作执行、页面等待、DOM diff、断言和降级重试放在一条调用路径里。建议拆成：

~~~
action
observation
assertion
evidence
retry policy
~~~

### 3.5 自动重试可能重复副作用

读取、等待、填充通常可以重试；提交、发送、购买、删除可能产生重复副作用。每个动作应明确：

~~~
idempotent
safe_to_retry
requires_confirmation
~~~

## 4. 产品定位

不建议定位成：

> 又一个能让 AI 点击网页的浏览器 MCP。

建议定位成：

> 面向 AI agent 的可探索、可验证、可复用、可恢复浏览器运行时。

一句产品化的说法：

> 探索一次，之后高速重放；每次运行都有可验证结果和证据。

核心闭环：

~~~
探索
→ 记录
→ 编译模板
→ 验证
→ 发布模板
→ 高速重放
→ 局部修复
→ 新版本模板
~~~

## 5. Playwright 架构方向

Playwright 应作为主要执行内核，CDP 保留为高级逃生通道。Playwright 原生支持 BrowserContext 和多 Page；Page 可对应 tab 或 popup。[Playwright Pages](https://playwright.dev/docs/pages)

建议架构：

~~~
MCP Tools
    ↓
Command Router / Schema Validation
    ↓
Browser Runtime
    ├── BrowserProcessManager
    ├── SessionManager
    ├── ContextManager
    ├── PageManager
    ├── EventBus
    ├── ArtifactStore
    ├── TraceStore
    └── PolicyManager
            ↓
      Playwright Backend
            ↓
      Chromium / Chrome / Edge
~~~

建议源码结构：

~~~
agent_gavel/
  runtime/
    browser_runtime.py
    browser_process.py
    sessions.py
    contexts.py
    pages.py
    events.py
    artifacts.py
    tracing.py
    policies.py
    errors.py

  backends/
    playwright_backend.py
    cdp_escape_hatch.py

  tools/
    browser_tools.py
    session_tools.py
    page_tools.py
    action_tools.py
    assertion_tools.py
    artifact_tools.py
    workflow_tools.py

  workflows/
    schema.py
    loader.py
    runner.py
    recorder.py

  diagnostics/
    logging.py
    metrics.py
    health.py
~~~

## 6. Session、Context、Page

资源层级：

~~~
runtime
  └── session
        └── browser_context
              └── page
                    └── frame
                          └── locator
~~~

每个 MCP 响应带：

~~~
{
  "session_id": "sess_01",
  "context_id": "ctx_01",
  "page_id": "page_03",
  "operation_id": "op_9f12",
  "status": "ok",
  "elapsed_ms": 42
}
~~~

Session 模式：

- ephemeral：临时 profile，任务结束删除。
- persistent：持久 profile，保留登录状态。
- clone：从 storage state 或已有 profile 克隆。

Context 支持：

- 不同账号。
- 不同 cookie。
- 不同代理。
- 不同语言。
- 不同设备环境。
- 不同无痕隔离环境。

Page 支持：

- 普通标签页。
- popup。
- 新窗口。
- 登录页。
- 下载页。
- 支付页。

必须使用稳定 page_id，不能长期依赖“第几个 tab”。

## 7. 建议的 MCP API

### 浏览器和 session

~~~
browser_launch
browser_status
browser_close
session_create
session_list
session_get
session_reset
session_close
session_export_state
session_import_state
~~~

### Context 和 Page

~~~
context_create
context_close
page_open
page_list
page_get
page_focus
page_close
page_select
page_wait
page_reload
page_go_back
page_go_forward
~~~

### 导航和读取

~~~
page_navigate
page_snapshot
page_text
page_read
page_links
page_explore
page_screenshot
page_pdf
~~~

### 页面操作

~~~
locator_click
locator_fill
locator_type
locator_press
locator_check
locator_uncheck
locator_select
locator_hover
locator_drag
locator_scroll
locator_focus
locator_clear
locator_upload
~~~

### 等待和断言

~~~
page_wait_for_url
page_wait_for_load
page_wait_for_selector
page_wait_for_response
page_wait_for_download
page_wait_for_popup
page_wait_for_event
expect
~~~

### 下载和 artifact

~~~
download_list
download_get
download_save
download_read_text
download_delete
artifact_list
artifact_get
artifact_export
artifact_cleanup
~~~

### 诊断和录制

~~~
trace_start
trace_stop
trace_get
network_start
network_stop
console_get
page_errors
performance_metrics
~~~

### 工作流和模板

~~~
workflow_save
workflow_validate
workflow_run
workflow_pause
workflow_resume
workflow_cancel
workflow_record
workflow_replay
~~~

旧 dom API 可以作为兼容层：

~~~
dom_navigate       → page_navigate
dom_read           → page_read
dom_text           → page_text
dom_step           → action + expect
dom_run_template   → workflow_run
~~~

## 8. 模板设计、探索和编译

模板不应只是动作数组，而应是带参数、前置条件、检查点、输出和恢复策略的可执行流程：

~~~json
{
  "template_id": "invoice_export_v3",
  "version": 3,
  "site": "supplier.example.com",
  "parameters": {
    "START_DATE": {"type": "date"},
    "END_DATE": {"type": "date"}
  },
  "preconditions": [
    {"type": "url_matches", "value": "*/login*"},
    {"type": "session_authenticated", "value": true}
  ],
  "steps": [
    {
      "id": "open_invoice",
      "action": {
        "backend": "semantic",
        "type": "click",
        "target": {"role": "link", "name": "发票管理"}
      },
      "checkpoint": {"type": "url_contains", "value": "/invoices"}
    }
  ],
  "outputs": [
    {"name": "downloaded_files", "type": "artifact_list"}
  ],
  "failure_policy": {
    "retry": "safe_only",
    "repair": "explore_local_step"
  }
}
~~~

模板应包含：

- 参数。
- 前置条件。
- 页面状态。
- 动作。
- 检查点。
- 输出。
- 可重试性。
- 失败恢复策略。
- 所需后端。
- 模板版本。
- 成功率和最近失败原因。

模板编译负责：

- 删除无效动作。
- 合并连续输入。
- 把固定值替换成参数。
- 找出敏感参数。
- 生成稳定 locator。
- 添加关键检查点。
- 标记不可自动重试动作。
- 评估所需后端。
- 生成模板版本。

建议录制接口：

~~~
workflow_start_recording
→ 用户或 AI 正常操作
→ 记录动作、页面、locator、断言、下载和 popup
→ workflow_stop_recording
→ 生成模板草稿
→ workflow_validate
→ workflow_publish
~~~

模板失效后：

~~~
模板第 5 步失败
→ 保留前 4 步结果
→ 只重新探索当前页面
→ 生成新 locator
→ 重新验证第 5 步
→ 发布 v4
~~~

## 9. 速度和端到端优化

产品真正要优化的不是单次 click，而是：

~~~
首次探索耗时
+ 模型思考耗时
+ MCP 往返次数
+ 浏览器执行耗时
+ 验证耗时
+ 失败恢复耗时
~~~

三种模式：

### explore

- 完整 snapshot。
- 允许截图。
- 记录网络和页面事件。
- 输出候选 locator。
- 记录失败证据。
- 适合首次执行。

### replay

- 一次 MCP 调用执行整个模板。
- 默认不返回中间页面。
- 只返回检查点和最终结果。
- 只等待明确条件。
- 只对安全动作重试。

### repair

- 从失败步骤开始。
- 只重新探索局部页面。
- 不重复执行已完成副作用动作。
- 修复模板新版本。

性能目标：

~~~
冷启动：< 1.5s
热连接：< 20ms
本地简单动作 p95：< 100ms
本地简单读取 p95：< 50ms
同一页面并发：严格串行且无状态错乱
不同 page 并发：可并行
浏览器崩溃恢复：< 5s
连续 1000 次操作：无 page/context/process 泄漏
~~~

成本应拆成：

~~~json
{
  "cost": {
    "connect_ms": 2,
    "locator_ms": 3,
    "action_ms": 8,
    "wait_ms": 120,
    "assertion_ms": 4,
    "total_ms": 137
  }
}
~~~

## 10. 验证设计

引入 Playwright 不会削弱验证，只要把 Playwright 的自动等待和 agent-gavel 的业务验证分开。

Playwright 负责：

- 元素是否存在。
- 元素是否可操作。
- 动作是否完成。
- 页面是否加载。

agent-gavel 负责：

- 业务结果是否符合预期。
- 动作是否产生正确副作用。
- 下载文件是否正确。
- 页面是否处于目标流程。
- 是否需要人工介入。

三层验证：

### 执行验证

~~~
click 是否成功
fill 是否成功
page 是否关闭
download 是否发生
popup 是否出现
~~~

### UI 验证

~~~
URL 是否正确
按钮是否消失
文本是否出现
输入框值是否正确
表格行数是否变化
~~~

### 业务验证

~~~
下载文件 SHA256 是否符合
PDF 金额是否等于页面金额
订单号是否存在
接口响应是否为预期状态
库存是否真的变化
~~~

结果示例：

~~~json
{
  "status": "ok",
  "execution": {"action_completed": true},
  "assertions": [
    {"name": "result_row_visible", "passed": true},
    {"name": "download_amount_matches_page", "passed": true}
  ],
  "evidence": [
    "artifact://download_01",
    "page://page_03/screenshot",
    "network://response_82"
  ]
}
~~~

模板重放只返回检查点摘要：

~~~json
{
  "status": "ok",
  "template_id": "invoice_export_v3",
  "steps_completed": 8,
  "checkpoints_passed": 5,
  "artifacts": ["invoice_A.pdf", "invoice_B.pdf"],
  "elapsed_ms": 1840
}
~~~

失败时才返回完整截图、DOM、日志和 trace。

## 11. 下载、popup 和 artifact

下载应该是一级能力：

~~~json
{
  "artifact_id": "artifact_123",
  "kind": "download",
  "suggested_filename": "report.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 182930,
  "sha256": "...",
  "source_url": "https://example.com/report",
  "session_id": "sess_01",
  "page_id": "page_03"
}
~~~

建议支持：

- 自动捕获下载。
- 下载超时和取消。
- 文件名冲突处理。
- MIME 检查。
- SHA256。
- PDF/Word/Excel 文本抽取。
- 保存到用户指定目录。
- artifact 生命周期清理。

不要把任意绝对路径直接暴露给模型，使用 artifact ID，再通过单独工具导出。

统一处理：

~~~
page.expect_popup
context.on("page")
page.on("download")
page.on("dialog")
page.on("pageerror")
~~~

MCP 事件：

~~~json
{
  "event": "page_created",
  "page_id": "page_04",
  "opener_page_id": "page_03",
  "url": "https://example.com/detail"
}
~~~

需要处理 target=_blank、popup、OAuth 登录窗口、支付窗口、页面关闭、页面崩溃、popup 超时、opener 和 child page 关系。

## 12. Locator 和探索

建议 locator 优先级：

1. get_by_role
2. get_by_label
3. get_by_placeholder
4. get_by_text
5. get_by_test_id
6. CSS
7. XPath
8. 坐标

探索结果应返回候选 locator 和置信度：

~~~json
{
  "role": "button",
  "name": "提交",
  "candidates": [
    {
      "kind": "role",
      "value": "button[name=\"提交\"]",
      "strict": true,
      "score": 0.98
    },
    {
      "kind": "css",
      "value": "#submit",
      "strict": true,
      "score": 0.91
    }
  ]
}
~~~

默认启用 strict locator。匹配多个元素时返回明确错误和候选项。自动换 locator 重试需要动作被标记为可重试，不能对所有点击都自动猜测。

## 13. Canvas、小游戏和视觉后端

DOM 不可能覆盖所有网页。

DOM 适合：

- 普通表单、搜索、表格和链接。
- React/Vue 普通控件。
- 后台系统。
- 文件下载和上传。
- popup。
- iframe。

需要视觉或坐标：

- Canvas 控件。
- WebGL 游戏。
- 地图拖动和缩放。
- 图表交互。
- 自定义日期选择器。
- 画布上的流程图。
- 远程桌面。
- 视频播放器自定义控件。
- 网页小游戏。

不建议优先支持：

- 实时竞技游戏。
- 帧级别操作。
- 强反作弊页面。
- 完全随机的视觉页面。
- 坐标变化极大的响应式页面。
- 远程桌面中嵌套浏览器。

后端能力模型：

~~~
semantic
visual
coordinate
javascript
network
~~~

视觉步骤示例：

~~~json
{
  "backend": "visual",
  "target": {
    "region": [0.2, 0.3, 0.6, 0.4],
    "description": "游戏棋盘右上角的红色按钮"
  }
}
~~~

第一版视觉后端可以只做：

~~~
screenshot
→ 局部区域识别
→ 坐标归一化
→ mouse.click / move / down / up
→ screenshot diff 或 OCR 验证
~~~

视觉模板不能保存绝对坐标，应保存区域、图像锚点、相对点击位置和相似度前置条件。执行时重新找锚点，再计算坐标，用局部图像差异、OCR、颜色变化或 DOM 状态验证。

是否实现视觉后端，可以按以下因素排序：

~~~
使用频率
× 模板复用价值
× 视觉稳定性
× 失败可恢复性
× 用户实际价值
~~~

值得优先做：

- Canvas 表单控件。
- 地图拖动和缩放。
- 自定义日期选择器。
- 图表交互。
- 可视化流程图。
- 内部系统的非标准控件。
- 简单网页小游戏。

不建议一开始承诺“通用网页游戏自动化”。

## 14. 并发、生命周期、安全和观测

建议每个 page 使用一个轻量 actor 或 command queue：

~~~
SessionManager
  ├── Context A
  │     ├── Page 1 queue
  │     └── Page 2 queue
  └── Context B
        └── Page 3 queue
~~~

做到：

- 同一页面动作串行。
- 不同页面并行。
- 不同 context 并行。
- 下载事件独立监听。
- popup 事件不丢失。
- 页面关闭后取消未完成操作。

每个操作支持 timeout、cancel、deadline、priority 和 operation_id。

每个调用记录：

~~~
operation_id
session_id
context_id
page_id
action_type
locator
url_before
url_after
wait_time
action_time
assertion_time
retry_count
browser_pid
artifacts
error_code
~~~

需要支持 JSON structured logs、p50/p95/p99 指标、page/context 数量、浏览器重启次数、下载大小、网络失败数、Playwright trace、失败自动截图、失败 HTML snapshot、console error、page error 和 network error。

安全方面：

- 域名白名单。
- 文件访问白名单。
- 下载大小限制。
- session 隔离。
- 敏感参数脱敏。
- storage state 权限。
- artifact 生命周期。
- 外部副作用审批。
- prompt injection 防护。
- 页面内容和系统指令的边界。

网页 accessibility snapshot 或正文可能包含恶意指令，不能把页面文本当成可信指令。

## 15. 测试体系

建议建立本地综合测试站点：

~~~
/tests/webapp/
  basic.html
  forms.html
  delayed.html
  popup.html
  download.html
  upload.html
  iframe.html
  dialog.html
  multiple_pages.html
  redirect.html
  websocket.html
  crash.html
~~~

必须覆盖：

- 多 session 并行。
- 多 context cookie 隔离。
- 多 page。
- popup。
- iframe。
- 下载。
- 上传。
- dialog。
- 页面崩溃恢复。
- 浏览器崩溃恢复。
- 网络超时。
- 重定向。
- 下载取消。
- locator 严格匹配。
- 中文输入。
- Ctrl+A、Shift+Tab 和组合键。
- storage state 导入导出。
- trace 生成。
- 进程泄漏。
- 1000 次连续动作。
- 多 MCP 客户端同时连接。

目前项目测试主要是可执行脚本，pytest 下没有正式测试用例。建议迁移为 pytest，并在 CI 中加入本地 fixture、组合键回归、多实例隔离和性能门槛。

## 16. 重建路线

### Phase 0：能力规格

先写清楚 40～60 个浏览器能力的验收标准：输入、点击、导航、等待、popup、多页面、下载、上传、iframe、dialog、session、storage state、cookies、headers、proxy、screenshot、PDF、trace、网络监听和视觉坐标。

### Phase 1：Playwright Runtime

实现浏览器进程、session、context、page、生命周期、并发队列、结构化错误和基础 metrics。

### Phase 2：基础 DOM API

迁移 navigate、read、text、explore、click、fill、type、press、wait 和 expect。

### Phase 3：高级浏览器能力

加入下载、上传、popup、iframe、dialog、多窗口、storage state、cookies、headers、proxy 和 permissions。

### Phase 4：工作流和录制

加入 workflow schema、参数系统、敏感参数、录制、回放、失败重放、局部修复、trace 和 artifact。

### Phase 5：兼容层

让旧接口继续工作：

~~~
dom_navigate  → page_navigate
dom_read      → page_read
dom_text      → page_text
dom_step      → action + expect
dom_run_template → workflow_run
~~~

### Phase 6：视觉扩展

在语义 DOM 流程稳定后，再加入 screenshot、局部视觉识别、OCR、坐标归一化、图像锚点、Canvas 操作和简单小游戏。

## 17. 市场和相似产品

市场已经分成几层：

| 类型 | 代表 | 主要能力 |
|---|---|---|
| 浏览器自动化库 | Playwright、Selenium、Cypress | 编程式操作、断言、截图、网络控制 |
| 大规模自动化基础设施 | Selenium Grid、BrowserStack、Sauce Labs | 多浏览器、多系统、多设备、并行执行 |
| 云浏览器基础设施 | Browserbase、Browserless | 远程浏览器 session、持久 profile、并发和托管 |
| AI 浏览器框架 | Stagehand、browser-use | 自然语言驱动 Playwright 或浏览器 agent |
| MCP 浏览器工具 | Microsoft Playwright MCP | 给 MCP 客户端提供 headed/headless 浏览器控制 |
| Web Agent 研究平台 | BrowserGym、WorkArena | 任务环境、数据集、评测和研究 |

Microsoft Playwright MCP 是最直接的竞争对象，已经提供 MCP 浏览器控制、结构化 accessibility snapshot、persistent profile 和 isolated session。[Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp)

Stagehand 位于 Playwright 和 LLM 之间，提供 act、extract、observe、agent 等原语。[Stagehand](https://stagehand.dev/)

Browserbase 更偏云端浏览器基础设施，提供 session、远程浏览器和调试视图，并与 Stagehand 集成。[Browserbase + Stagehand](https://docs.browserbase.com/integrations/vercel/quickstart)

单纯做 Playwright 的 MCP 包装层很难形成独立优势。更有区分度的方向是：

> Verification-first and replay-first browser runtime for AI agents。

区别在于：

- 首次探索可以较慢。
- 后续重放只需一次调用。
- 结果带有可验证证据。
- 下载和业务结果可以验证。
- 失败支持暂停、恢复和局部修复。
- 模板可以版本化。
- 默认返回少量结果，完整证据按需获取。

## 18. 需求类型和大致占比

下面不是全球市场统计，而是用于产品规划的估算模型，假设有 100 个真实网页自动化任务：

| 需求类别 | 估计占比 | 典型任务 | 模板复用价值 |
|---|---:|---|---|
| 多站搜索和研究 | 18% | 搜索政策、职位、论文、商品、供应商 | 高 |
| 定期监控和提醒 | 15% | 监控价格、库存、公告、网页状态 | 很高 |
| 下载和文档处理 | 17% | 下载 PDF/Excel、提取字段、去重、归档 | 很高 |
| 后台录入和管理操作 | 16% | CRM、报销、发票、保险、ERP、教务系统 | 很高 |
| 商品比较和购物准备 | 10% | 比价、筛选、加入购物车、提交前确认 | 中高 |
| 内容发布和运营 | 8% | CMS、博客、商品上架、社交媒体、邮件后台 | 高 |
| 网页测试和回归验证 | 7% | 测试登录、表单、流程、后台变更 | 很高 |
| 客服和 CRM 操作 | 5% | 查客户资料、更新工单、批量处理记录 | 高 |
| 预约、报名、申请 | 3% | 预约、职位申请、学校申请、活动报名 | 中高 |
| Canvas、地图、小游戏等视觉交互 | 1% | 画布控件、地图拖拽、网页小游戏 | 技术价值高，普遍需求较低 |

这意味着 agent-gavel 不应只定位为表单工具。更大的长期需求组合是：

~~~
搜索与研究
+ 监控
+ 下载与文档处理
+ 后台业务
+ 模板复用
~~~

### 多站搜索和研究

需求很大，但一次性研究已经被搜索 API、联网模型和通用浏览器 agent 覆盖一部分。更适合 agent-gavel 的场景：

- 每周固定查十几个网站。
- 网站需要登录。
- 有复杂筛选条件。
- 没有 API。
- 需要下载附件。
- 需要合并多个站点结果。
- 需要确认结果确实来自目标页面。

### 监控和定期任务

模板复用价值最高：

~~~
每天检查 30 个网站
→ 找出新增内容
→ 下载附件
→ 提取标题、日期、金额
→ 去重
→ 汇总结果
~~~

### 下载和资料处理

很多用户真正需要的是：

~~~
登录几个系统
→ 找到目标文件
→ 下载
→ 验证内容
→ 整理目录
→ 告诉我哪些失败
~~~

### 后台录入和管理操作

包括学校教务系统、公司 OA、财务后台、保险后台、CRM、供应商门户、政务系统、招聘管理系统和内部管理平台。这些网站通常没有好用 API，且需要登录、重复操作和错误追踪。

### 模糊购物

建议先做：

~~~
理解需求
→ 多站比较
→ 生成候选
→ 加购物车
→ 用户确认
~~~

真正支付放在后续阶段，因为它涉及价格、库存、优惠券、支付授权、售后和责任边界。

### 表单并不低价值

很多企业流程虽然表现为表单，但实际包含登录、多页面、文件上传、下载凭证、条件分支、人工审批和结果核对。

产品不应宣传成“自动帮你填表”，而应宣传成：

> 把一次人工网页操作变成可验证的浏览器工作流。

## 19. 宣传和开源方向

如果目标是做成有影响力的开源作品，不要宣传成“又一个 AI 浏览器 MCP”。

建议主题：

> 让 AI 浏览器操作拥有可验证结果、可审计证据和可恢复执行。

### 19.1 三个公开演示

#### 多账户、多窗口、下载

~~~
打开三个供应商后台
→ 分别进入账单页面
→ 下载 PDF
→ 校验日期和金额
→ 跳过重复文件
~~~

演示三个隔离 session 并行、popup、下载、PDF 校验和局部验证码暂停。

#### 有副作用的安全自动化

~~~
创建测试订单
→ 提交前告诉用户订单金额和商品
→ 用户批准
→ 提交
→ 验证订单号
~~~

#### 失败可解释

~~~json
{
  "status": "blocked",
  "reason": "captcha_detected",
  "page_id": "page_02",
  "screenshot_artifact": "artifact_91",
  "resume_hint": "人工完成验证码后调用 workflow_resume"
}
~~~

### 19.2 Benchmark

建立独立 benchmark，测量：

~~~
任务成功率
验证正确率
误判率
副作用重复率
下载成功率
popup 捕获率
多 session 隔离正确率
p50/p95 延迟
每任务 token 消耗
崩溃恢复时间
~~~

需要和 Microsoft Playwright MCP、直接 Playwright、Stagehand、browser-use 做公平比较。

不能只测速度，还要测：

~~~
正确执行
+ 正确判断
+ 没有重复副作用
~~~

### 19.3 开源传播路径

1. README 做到 5 分钟可运行。
2. 发布 Docker 镜像。
3. 提供 MCP 一键配置。
4. 提供 Python SDK 和 TypeScript SDK。
5. 提供独立 CLI。
6. 接入 MCP registry。
7. 发布 benchmark 仓库。
8. 写 verification-first 和 replay-first 技术文章。
9. 发布真实失败恢复短视频。
10. 在 Hacker News、Reddit、中文开发者社区和 Playwright 社区发布。
11. 征集已验证模板。
12. 建立模板仓库和贡献规范。

开源项目不一定要靠几十万用户证明成功。更适合的指标是：

- 有多少人保存模板。
- 有多少模板被重复运行。
- 模板减少多少模型调用。
- 模板减少多少 token。
- 模板是否减少人工等待。
- 模板失败后是否能局部修复。
- 是否有人贡献模板。

## 20. 典型使用体验

假设任务：

> 每周一从三个供应商后台下载上周的发票，按供应商分类保存，不重复下载。如果某个站点需要验证码，暂停该站点，其他站点继续。

系统创建三个隔离 session：

~~~
session supplier_a
session supplier_b
session supplier_c
~~~

结果：

~~~
A：已进入发票列表
B：已进入发票列表
C：页面要求验证码，已暂停
~~~

A 完成下载：

~~~json
{
  "status": "ok",
  "supplier": "A",
  "artifact_id": "invoice_A_2026_09_30",
  "filename": "A-2026-09-30.pdf",
  "verified": {
    "invoice_date": true,
    "amount": true,
    "duplicate": false
  }
}
~~~

B 点击下载时打开 popup，系统自动捕获新 page，下载并验证 PDF 金额。

C 返回：

~~~
C：已暂停。
原因：captcha_required。
截图已保存：artifact://captcha_c_01。
完成验证码后回复“继续”即可恢复。
~~~

用户完成验证码后说“继续”，系统恢复原 session、context 和 page，不重新登录，也不重复执行已经完成的步骤。

最终结果：

~~~
供应商 A：下载 4 份，跳过 1 份重复文件
供应商 B：下载 3 份
供应商 C：下载 2 份

总计：9 份新发票
校验通过：9/9
重复下载：0
失败：0
人工介入：1 次验证码
~~~

## 21. 个人困惑和是否值得继续

当前困惑不是“技术上能不能做”，而是：

> 做出来有没有人愿意持续使用？

这是正常的，因为这不是一个小功能，而是一个可能持续数月的项目；而且之前更完整的 Playwright 版本源码丢失，会让人感觉之前的努力被重置。

但已有工作没有白费。已经验证了：

- AI 操作浏览器真正卡在哪里。
- 单纯“点击成功”不等于任务成功。
- 验证、模板、下载、多窗口和 session 必须组合起来。
- 浏览器生命周期可以管理。
- DOM 动作、断言、diff、模板和失败诊断可以工作。
- 速度、token、探索次数和可靠性之间存在真实矛盾。

不建议现在做一个“是否做 agent-gavel”的终身决定。应该验证一个小问题：

> 能不能在一周内，用新的架构做出一个自己愿意继续使用的模板重放闭环？

闭环：

~~~
打开网页
→ 探索并记录动作
→ 保存模板
→ 修改参数
→ 一次调用重放
→ 返回验证结果和下载文件
~~~

场景可以是：

- 搜索并导出结果。
- 登录后台下载报表。
- 多页面填写并提交表单。
- 下载文件并校验内容。

如果第二次执行明显比第一次更快、更省 token，就有继续的依据。

不要用“它是不是人人都会用”判断。更实际的问题是：

> 有没有一小群人，会因为这个工具而反复省下时间？

只要有人会说“原来第二次真的不用再教一遍了”，项目就有继续发展的理由。

## 22. 最终建议

建议把 agent-gavel 的主线确定为：

~~~
探索一次
→ 编译模板
→ 一次调用高速重放
→ 检查点验证
→ 下载和业务结果验证
→ 失败时局部修复
~~~

优先顺序：

1. Playwright runtime。
2. session/context/page 资源模型。
3. workflow 录制和模板编译。
4. 单调用模板重放。
5. 下载 artifact。
6. popup、iframe、上传、storage state。
7. page 级并发队列。
8. 业务级验证。
9. 本地综合 benchmark。
10. 视觉/坐标后端。

产品真正的竞争力不在于“能不能点击一个网页元素”，而在于：

~~~
第一次允许探索
以后不再重复探索
每次运行有证明
失败可以恢复
复杂任务可以复用
模型 token 明显下降
~~~

