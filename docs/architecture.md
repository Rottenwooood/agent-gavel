# agent-gavel 架构与修改约束

本文是项目的约束性文件。它同时描述目标架构、必须保持的不变量、以及为达成目标而执行的分阶段计划。

任何实现与本文冲突时，以本文为准；本文与代码不一致时，视为缺陷，需要在同一次变更中修好其中一方。

---

## 0. 适用范围

- 适用于 `agent_gavel/` 下的全部生产代码、`tests/` 下的门禁测试、以及对外文档中的架构描述。
- `legacy/` 已归档，不参与本文约束，也不得被新代码引用。
- 本文不描述桌面（AT-SPI）通道；该通道已从发行包中移除。

---

## 1. 核心原则

1. **一个概念只有一个主人。** 同一个事实不允许在两处表达、两处推导、两处校验。
2. **依赖单向。** 下层不得 import 上层；调用方不得为绕开分层在本地兜底。
3. **修改必须落在主人的文件里。** 如果必须改第二个文件才能成立，先确认归属是否选错。
4. **补丁禁令。** 不允许用调用方特判、兼容分支、模糊匹配来掩盖结构问题。遇到这类情况，先改结构。
5. **行为契约优先于内部整洁。** 重构期间，模板格式、保留的工具名、错误码、三态语义、并发语义不得改变。

---

## 2. 不变量

不变量是"必须始终为真"的命题。每条都必须有机器可执行的检查，检查未通过即视为回归。

| 编号 | 不变量 | 检查方式 |
|---|---|---|
| I1 | 只有 `agent_gavel/core/config.py` 读取环境变量 | AST 扫描：`agent_gavel/**` 中 `os.environ` / `os.getenv` 只允许出现在 `core/config.py`；`tests/` 豁免 |
| I2 | backend 中所有涉及页面的公共函数，首参统一为 `handle: PageHandle`，页对象只在函数内部取 `.page`；纯函数（比较、渲染、diff）不在此列 | AST 扫描：`backends/**` 中带页面操作的公共函数，第一个参数名必须为 `handle` |
| I3 | 动作类型只在 `core/actions.py` 定义一次 | 全仓库只允许一处 `ACTIONS` 定义；`schema.py` 不得出现 `ACTION_TYPES` |
| I4 | 断言的读取类型与比较操作只在 `core/checks.py` 定义一次 | `READ_KINDS` / `OPS` 全仓库各只允许一处定义；其他模块不得出现 op 字符串字面量 |
| I5 | 对外 `status` 取值固定，且只由统一封装产生 | `STATUS_VALUES` 只允许在 `core/errors.py` 定义；`tools/` 中不得出现手写的 `"status": "<字面量>"` |
| I6 | 模板写路径只接受精确 id | AST 扫描：`loader.save` / `delete` / `record_run` 内部不得引用 `resolve_id` |
| I7 | async 调用链禁止同步阻塞 I/O | AST 扫描 async 函数体 + 阻塞入口清单双向核对（见第 9 节） |
| I8 | 包依赖单向 | 导入图检查：`runtime` 不得 import `workflows` / `backends` / `tools`；`core` 不得 import 其他包 |
| I9 | 一个数据根在任意时刻只有一个运行时守护进程（daemon）持有浏览器与可变状态 | 进程模型检查：`flock(<数据根>/daemon.lock)`；任何 adapter 不得创建浏览器、不得持有注册表 |
| I10 | 所有资源 id（session/context/page/run/artifact）只由 daemon 分配；跨 adapter 必须一致 | 集成测试：多个 adapter 并发 `page_list` / `browser_status` 看到同一组 id |

### 2.1 进程模型的定义

MCP 客户端（opencode 等）会为同一用户拉起**多个** agent-gavel 进程。这是环境事实，不是异常。因此进程模型必须显式定义，不能假设"只有一个 server"。

- **daemon**：唯一持有浏览器、注册表、队列、事件、产物索引与所有锁的进程。
- **adapter**：面向 MCP 客户端的 stdio 进程。不导入 Playwright、不持有任何运行时状态，只负责把工具调用转发给 daemon 并回传结果。
- **串行化只发生在 daemon 内部**：PageActor 负责同页串行，session 锁负责 run 独占。因此不存在"跨进程动作交错"这一类别的问题。
- **并行发生在 daemon 内部的 context/page 之间**；adapter 数量不影响浏览器数量。
- **资源预算**：一个数据根一个 Chrome；context 与 page 是廉价并行单位；adapter 必须保持轻量（不得因导入工具模块而加载 Playwright）。

### 2.2 I7 的完整含义

"阻塞 I/O"包括但不限于：

- `time.sleep`
- `subprocess.run` / `subprocess.Popen(...).wait()`
- `urllib.request.urlopen`
- 目录遍历与整目录复制（`os.walk`、`shutil.copytree`）
- 对可能很大的文件做哈希、解析（PDF、DOCX、图片）
- 扫描 `/proc` 判断进程

同步函数如果会被 async 路径调用，必须在调用处用 `await asyncio.to_thread(...)`，并在 `tests/test_invariants.py` 的 `BLOCKING_INVENTORY` 中登记：函数名、包装位置、原因。该清单只能通过代码审查增加，不能由实现者自行放宽。

---

## 3. 依赖方向与目录形态

唯一合法方向：

```
main(adapter) ──► remote ──(unix socket)──► daemon ──► tools ──► workflows ──► backends ──► runtime ──► core
                                                        └──────────────────────► backends

core = config / errors / actions / checks（基础词表，不依赖其他包）
```

目标目录：

```
agent_gavel/
  main.py              # MCP stdio adapter：连接或拉起 daemon，转发工具调用
  remote.py            # adapter 侧：daemon 客户端 + 按 daemon 返回的 schema 动态注册工具
  daemon.py            # 单实例运行时：持有 runtime 与工具表，暴露本地 socket

  core/
    config.py          # 唯一 env 入口；全部为函数（惰性读取），不缓存模块常量
    errors.py          # GavelError / wrap_exception / STATUS_VALUES
    actions.py         # 动作词表：类型、必填字段、幂等性、副作用判定、录制字段
    checks.py          # 断言 IR：读取类型、比较操作、解析入口

  runtime/
    browser_process.py # 进程：ensure / close / reconcile（全异步）
    sessions.py        # 租约：配置 + 独占锁
    contexts.py        # 窗口：cookie 空间 + active page 的唯一真相
    pages.py           # 标签页：事件绑定 + actor
    queue.py           # 每页串行队列
    events.py
    artifacts.py
    policies.py
    metrics.py
    tracing.py
    recorder.py        # 录制缓冲
    browser_runtime.py # facade + 注册表

  backends/playwright/
    __init__.py        # 编排：导航、等待、截图、PDF
    actions.py         # 每个动作一个函数，签名统一 (backend, handle, action)
    locator.py         # target 归一、strict 解析、候选样例
    reader.py          # read / snapshot / explore / anchor / screenshot
    assertion.py       # Check IR 的 Python 读取与三态求值
    jsrender.py        # Check IR 的浏览器侧谓词
    signature.py       # 页面签名与 diff
    frames.py          # iframe 作用域
    util.py            # 内部小工具

  workflows/
    schema.py          # 模板结构校验（读 core/actions.py）
    compiler.py
    runner.py
    loader.py          # resolve_id（只读）+ get/save/delete（精确）

  tools/
    common.py          # guard / backend() / envelope() / action_tool()
    browser_tools.py       # 浏览器进程
    session_tools.py       # 会话
    context_tools.py       # 容器
    page_lifecycle.py      # 页面打开/选择/关闭/等待/前进后退
    page_tools.py          # 导航/读取/快照/探索/截图/PDF
    action_tools.py
    assertion_tools.py
    artifact_tools.py
    profile_tools.py
    workflow_tools.py
    diagnostics_tools.py
```

文件规模：单个文件超过 300 行时，提交说明中必须写明不能继续拆分的原因。

---

## 4. 唯一归属表

| 概念 | 主人 | 禁止出现的位置 |
|---|---|---|
| 环境变量与路径 | `core/config.py` | 任何其他模块 |
| 错误码与状态取值 | `core/errors.py` | 工具函数内手拼 |
| 对外响应封装 | `tools/common.envelope` | 各工具自行拼装 |
| 动作类型、必填字段、幂等性、副作用判定 | `core/actions.py` | `schema`、`runner`、`backend`、`recorder` |
| 断言读取类型、比较操作、解析 | `core/checks.py` | `schema`、`backend`、`assertion_schema` 工具 |
| active page | `Context.active_page_id` | `BrowserRuntime` |
| 页面归属 | `PageHandle.context_id` | `PageHandle.session_id` |
| 响应中的会话标识 | `tools/common.envelope`（按本次调用解析） | 页面对象、backend |
| session 归属 | `Session`（租约） | `Context.session` |
| 默认容器复用 | `ContextManager`（按浏览器分槽） | 单槽全局变量 |
| 进程启动幂等 | `BrowserProcessManager.ensure()` | 工具层 |
| 模板精确读写 | `loader` | `delete` / `record_run` 走模糊匹配 |
| 断言三态判定 | `backends/playwright/assertion.py` | `runner` / 工具层各自判 |
| 产物索引 | `ArtifactStore` | 其他模块直接读写索引文件 |

---

## 5. 冻结契约

以下内容在收敛过程中不得改变。确需改变时，按第 12 节处理。

| 契约 | 内容 |
|---|---|
| 模板格式 | `schema_version=2`；字段名、`$VAR` 语义、checkpoint 类型、`failure_policy` 键保持不变 |
| 保留工具名 | 除第 6 节阶段 4 明确删除的三个别名外，其余工具名不变 |
| 错误码 | 现有 `GavelError.code` 字符串不得重命名；新增错误码集中登记在 `core/errors.py` |
| 断言三态 | `pass` / `fail` / `ambiguous` 的判定条件不变；`ambiguous` 必须附带有意义的变化证据 |
| 并发语义 | 同页串行、跨页并行；`session_busy` 语义不变 |
| 安全 | 域名白名单覆盖 popup 与重定向；导出路径二次包含校验；文件白名单；下载大小上限 |
| 资料副本 | `strict` / `default` / `full` 三档语义与剔除清单不变 |
| 资源 id | session/context/page/run/artifact id 只由 daemon 分配并在所有 adapter 间一致 |
| 文本等待 | `page_wait_for_text` 使用首个文本命中（`first`）是定义行为；strict 唯一性只约束 locator 解析，不约束文本等待 |

---

## 6. 分阶段实现计划

阶段顺序不可颠倒。每个阶段结束时，门禁必须为绿；未绿不得进入下一阶段。

### 阶段 0：冻结行为

**目的**：在改动实现前建立行为基线，使后续每一步都能证明"行为未变"。

**改动**

1. 新增 `tests/test_contract_snapshot.py`
   - 固定场景：`session_create` → `page_navigate` → `locator_fill` → `locator_click`（带断言）→ `expect` → `workflow_save` → `workflow_validate` → `workflow_run` → `artifact_list` → `browser_status`
   - 断言每个响应的字段集合与 `status` 取值，不比对易变值（URL、时间戳、资源 id）
   - 覆盖错误路径：`locator_not_found`、`ambiguous_page`、`session_busy`、`precondition_failed`
2. 新增 `tests/test_invariants.py`：按第 2 节实现八条检查
3. 新增 `tests/test_vocabulary.py` 骨架：阶段 3 填充断言

**验收**

- 上述测试通过
- `test_invariants` 对现存违规使用显式白名单（文件 + 符号），白名单只能减少

**禁止**

- 不得为了让基线通过而修改实现；白名单只记录现状

### 阶段 1：配置、进程与清理

**目的**：消除配置语义不一致、事件循环阻塞、退出竞态三类系统性问题。

**改动**

1. 建立基础包 `agent_gavel/core/`：
   - 把 `runtime/errors.py` 移入 `core/errors.py`，更新全部导入
   - 新建 `core/config.py`，提供函数而非常量：
     `data_dir`、`profile_dir`、`profiles_root`、`workflows_dir`、`artifacts_dir`、`headless`、`browser_mode`、`cdp_endpoint`、`chrome_bin`、`chrome_user_data`、`allow_domains`、`domain_guard`、`allow_paths`、`max_download_bytes`、`artifact_ttl_days`、`artifact_max_bytes`
   - 统一语义：`os.environ.get(k) or default`，空字符串视为未设置
2. 替换所有直接环境变量读取：`runtime/sessions.py`、`runtime/profiles.py`、`runtime/browser_process.py`、`runtime/policies.py`、`runtime/artifacts.py`、`workflows/loader.py`、`tools/browser_tools.py`。
   删除模块级常量 `PROFILE_DIR`、`USER_DIR` 等。
3. 进程操作异步化（`runtime/browser_process.py`）：
   - `kill_tree` 改为 async，用 `os.killpg` + `await asyncio.sleep`
   - Windows 分支用 `asyncio.create_subprocess_exec`
   - `reconcile`、`BrowserProcessManager.close` 的轮询全部改为 `await asyncio.sleep`
4. 阻塞入口清单化：`/proc` 扫描、`_cdp_ready` 的网络探测、`ArtifactStore.register_file` 的大文件哈希、`ArtifactStore.read_text` 的文档解析、`profiles.list_profiles` 的目录统计，全部改走 `asyncio.to_thread` 或异步等价实现，并登记进 `BLOCKING_INVENTORY`。
5. 退出顺序（`main.py`）：收到信号后先置退出标志，再在事件循环中 `await shutdown_runtime()`（设上限），最后 `os._exit`；禁止"起线程后立即 `os._exit`"。
6. 错误可定位性（`core/errors.py`）：`wrap_exception` 附带截断堆栈与 `trace_id`，同时写入日志文件；工具返回 `trace_id`。对外错误码保持 `internal` 不变。
7. `workflows/loader.py` 拆分：新增 `resolve_id(name)` 作为唯一宽松入口（仅只读工具使用）；`get`、`save`、`delete`、`record_run` 只接受精确 id，并在入口处校验。
8. 进程启动收敛（`runtime/browser_process.py`）：
   - `launch` 与 `launch_persistent` 合并为一个内核 + 两个薄包装
   - 新增 `BrowserProcessManager.ensure(**opts)` 承担幂等判断，工具层不再判断参数是否一致
   - 模式解析显式化：`attach` 必须给端点；`playwright` 与 `managed` 由调用方或配置决定；因参数（如 proxy/channel）导致的模式改变必须在响应中返回 `mode_resolved` 与 `warning`，不得静默切换
9. 清理：
   - 删除死代码：`runner._TARGETLESS`、`browser_runtime.page_id_for`、`profiles._HEADLESS_TRUE`
   - 删除仓库根由空环境变量误建的 `sess_*` 目录（删除前确认其中不含需要保留的登录态）
   - 删除空目录 `templates/`
   - 移除包内模板层：当前 `agent_gavel/workflows/templates` 不存在，删除 `_dirs()` 双层逻辑、`layer` 字段、`PKG_DIR` 分支；将来确有随包模板时重新设计
   - 清理 `~/.config/opencode/opencode.json` 中仅用于触发重载的空配置项

**验收**

- I1、I7 白名单清零
- 契约快照通过
- `browser_close` 执行期间，另一会话调用 `browser_status` 的响应时间小于 1 秒
- `internal` 错误的返回值中包含 `trace_id`
- 模板的读取/写入行为与阶段 0 快照一致

**禁止**

- 保留任何模块级环境变量常量作为兼容
- 用 `# noqa` 绕过 I7
- 为旧路径保留第二条启动分支

### 阶段 2：进程模型、身份与产物一致性

**目的**：先解决"多个 MCP 进程共享一个浏览器"的结构性问题，再解决 session/context/page 的归属二义性，最后修掉产物索引的并发丢项。

#### 2A. 进程模型：单 daemon + 多 adapter

**背景**：MCP 客户端会为同一用户拉起多个 agent-gavel 进程。当前每个进程各自持有运行时并连到同一个 Chrome，导致注册表分裂、id 冲突、`session_busy` 失效、同页动作跨进程交错。这不是配置问题，必须在架构层解决。

**改动**

1. 新增 `agent_gavel/daemon.py`：
   - 唯一持有 `BrowserRuntime`、工具注册表与本地 socket 服务的进程
   - 启动时对 `<数据根>/daemon.lock` 取 `flock`；取不到即退出（单实例由内核保证）
   - 监听本地 socket（POSIX 为 `<数据根>/daemon.sock`；Windows 为 127.0.0.1 随机端口），协议为逐行 JSON：`{"id", "token", "tool", "args"}` → `{"id", "result"}` 或 `{"id", "error"}`；token 写入 0600 的 `daemon.json`
   - 提供 `describe` 请求，返回工具名与 JSON Schema（供 adapter 注册）
   - 进程内的工具调用与现在完全一致，串行化仍由 PageActor 与 session 锁负责
2. 新增 `agent_gavel/remote.py`（adapter 侧，不得 import playwright / runtime / tools）：
   - 连接 daemon；不存在时以 detached 方式拉起 `python -m agent_gavel.daemon`，等待 socket 就绪
   - 向 daemon 请求工具清单，用 MCP 低层接口动态注册同名工具；每个 handler 只做转发
   - daemon 断开时返回结构化错误 `daemon_unavailable`（含重连提示），不得静默降级为本地运行时
3. `agent_gavel/main.py`：从"创建运行时"改为"启动 adapter"。
4. 生命周期：
   - adapter 退出不影响 daemon 与浏览器
   - daemon 崩溃后 `flock` 由内核释放；下一个 adapter 调用时重新拉起
   - daemon 空闲退出策略由 `browser_close` / 显式关闭决定，不做超时驱逐（避免正在执行的任务被误杀）
5. 资源预算：
   - 一个数据根一个 Chrome
   - adapter 不得因导入工具模块而加载 Playwright；启动后常驻内存应显著小于 daemon
   - `browser_status` 增加 `daemon_pid` 与 `adapters` 计数

**验收（必须跨进程真实执行）**

- 在同一父进程下启动 4 个 adapter：进程表中有 1 个 daemon、4 个 adapter，且只有 daemon 派生了 Chrome
- 4 个 adapter 并发调用 `page_list` / `browser_status`：返回完全相同的 page id 集合与 daemon pid
- 从两个不同 adapter 对同一 session 并发发起 `workflow_run`：一个执行，另一个得到 `session_busy`
- 杀掉 daemon：下一次调用要么成功拉起新 daemon，要么返回 `daemon_unavailable` 并说明原因；不得出现两个 daemon 同时持有浏览器
- 全过程中 `ps` 里该数据根的 Chrome 数量恒为 1

#### 2B. 身份与产物

**改动**

1. 录制缓冲下沉：`workflows/recorder.py` 迁移为 `runtime/recorder.py`，由 `BrowserRuntime` 持有；删除 `runtime` 对 `workflows` 的 import。
   - `Recording(context_id, page_id)`
   - `record()` 按 `context_id` 过滤
   - 每个步骤记录 `page_id`（为将来的多标签预留，不改变当前执行模型）
2. `runtime/pages.py`：
   - `PageHandle` 删除 `session_id` 属性，只保留 `context_id`
   - `PageManager.resolve(page_id=None, *, context_id=None)`：显式作用域；有歧义时报 `ambiguous_page` 并列出候选；禁止回落到全局 active
   - 新增 `find_by_pw_page(pw_page)`；删除对 `_by_key` 的外部访问
   - `register()` 只写 `ctx.active_page_id`，不再改写全局状态
3. `runtime/contexts.py`：
   - `Context` 的 `session` 改为 `creator_session_id`；`session_ids` 改为集合
   - 删除 `refcount`，租约数量由 `session_ids` 表达
   - `_default_ctx` / `_default_browser` 单槽改为按浏览器实例分槽的字典
   - `close()` 语义写死：`default` 为解除租约，`isolated` 为销毁
4. `runtime/browser_runtime.py`：删除 `active_page_id`；`status()` 输出每个 context 的 active page。
5. `workflows/runner.py`：页面解析改为 `page_id` → `context_id` → 会话内唯一 context，否则报 `ambiguous_page`。
6. 工具层：`session_create` 响应增加 `shared_container`；共享默认容器时明确告知该会话与其他会话共用同一标签页。
7. 响应中的身份字段：页面归属只到 `context_id`。`session_id` 表示**本次调用所属会话**（由解析路径确定），不是页面的固有属性；当页面来自多会话共享的默认容器且调用未指定会话时，响应省略 `session_id`，只给 `context_id`，避免伪造归属。
8. `runtime/artifacts.py`：索引读改写加锁（或用追加式索引），新增并发下载测试。

**验收**

- 新增 `tests/test_identity.py`：
  - 两个会话共享默认容器时，`page_list` 的归属正确，且在第二个会话中录制不丢步
  - 同一默认容器上的并发 workflow 不得交错执行，第二个应得到 `session_busy` 或明确的页面占用错误
  - 打开 popup 后未显式选择页面时，解析应报歧义，不得静默选中
  - 共享容器且未指定会话时，响应不含伪造的 `session_id`
- 契约快照通过（字段变化需在快照中显式更新）
- I8 检查通过：`runtime` 不再依赖 `workflows`
- 并发下载不会再丢失索引项

**禁止**

- 以"最后打开的页面"作为隐式默认
- 在工具层遍历运行时注册表推断归属
- 让 adapter 持有浏览器、注册表或锁
- 用跨进程文件锁去"协调"多个 runtime 共享一个浏览器

### 阶段 3：动作与断言词表

**目的**：让新增动作或断言只改一个文件，从结构上杜绝漏配。

**改动**

1. 新建 `agent_gavel/core/actions.py`：
   ```
   @dataclass(frozen=True)
   class ActionSpec:
       type: str
       target_required: bool
       idempotent: bool
       side_effect_by_default: bool
       wait_kind: bool
       record_fields: tuple[str, ...]

   ACTIONS: dict[str, ActionSpec]
   def spec(type: str) -> ActionSpec
   def looks_like_side_effect(action: dict) -> bool
   ```
   - `looks_like_side_effect` 是本项目唯一一份"类型默认值 + 目标文案"的副作用判定
   - `fill`、`upload` 的幂等性在代码注释中给出依据（`fill` 会触发输入事件，可能诱发即时提交，因此不视为天然幂等）
2. `workflows/schema.py`：删除 `ACTION_TYPES`、`_TARGET_ACTIONS`、`_INHERENTLY_IDEMPOTENT`、`_SIDE_EFFECT_HINTS`，全部改为查询 `core/actions.py`。
3. `workflows/runner.py`：删除 `_TARGET_ACTIONS_SAFE` 与 `_TARGETLESS`；`_dispatch` 改为以动作类型为键的分发表；重试判定只读 `spec()`。
4. `backends/playwright/actions.py`：把 `act()` 的分支链拆成 `do_click` / `do_fill` / ...，统一签名 `(backend, handle, action)`。
5. 新建 `agent_gavel/core/checks.py`：
   ```
   READ_KINDS: frozenset[str]
   OPS: frozenset[str]

   @dataclass(frozen=True)
   class Check:
       name: str
       read: str
       op: str
       expected: Any
       selector: str | None
       attr: str | None
       expr: str | None
       frame: Any

   def parse(raw) -> list[Check]
   def from_checkpoint(cp) -> list[Check]
   ```
   `parse` 是唯一解析入口，覆盖映射、扁平、顶层 op、单条字段四种写法。
6. `backends/playwright/assertion.py`：
   - `read_value(handle, check)`：唯一的"读什么"实现
   - `compare(op, actual, expected)`：唯一的比较实现（纯函数）
   - `render_js(check)`：唯一的浏览器侧谓词实现（纯函数）
   - `evaluate(handle, checks, before_sig, wait_s, target)`：三态判定
   - 删除 `_normalize_checks`、`_norm_one`、`_norm_single`、`_actual` 的推导分支、`_event_predicate` 的推导分支、`_cmp_js`、`_compare`
7. `workflows/schema.checkpoint_to_checks` 改为返回 `list[Check]`，删除 `{"checkpoint": ...}` 包装形态。
8. `tools/assertion_tools.assertion_schema` 的可用取值由 `core/checks.py` 生成，不再手写。
9. 录制语义（`runtime/recorder.py`、`tools/common.py`、`tools/page_tools.py`）：
   - checkpoint 只在断言判定为 `pass` 时记录；断言失败的动作不写 checkpoint
   - 连续同类同目标动作合并，同一目标只保留最后一次输入
   - `focus` / `scroll` / `hover` 默认不录制，仅在录制参数 `keep_all=True` 时保留
10. 编译器（`workflows/compiler.py`）：
    - 隐式生成的 `url_contains` 断言改为 `workflow_record_stop(auto_checkpoint=True)` 才启用，默认关闭
    - 参数化只接受显式传入的映射，删除"按字面值全量替换"的推断

**验收**

- `tests/test_vocabulary.py`：
  - 每个动作都有 spec、分发函数与录制字段
  - 每个读取类型都有 Python 读取与 JS 读取
  - 每个比较操作都有 Python 比较与 JS 渲染
- 仅修改 `core/actions.py` 并添加一个分发函数即可注册新动作（由测试内的假动作证明）
- 录制回归测试：探针式 `fill`、hover、scroll 不进入模板；失败的断言不成为 checkpoint
- 契约快照通过

**禁止**

- 在 `core/checks.py` 之外出现断言操作字符串字面量
- 在 `schema.py` 中再写一份必填字段表
- 保留任何隐式推断作为默认行为

### 阶段 4：工具层、后端拆分与工程门禁

**目的**：消除工具层重复，统一对外外形，建立静态检查。

**改动**

1. `tools/common.py` 成为唯一入口：`backend()` 单例、`guard()`、`envelope()`、`action_tool()` 工厂。删除 `browser_tools._guard` 与多个 backend 实例。
2. `envelope()` 统一补齐：`status`（取自 `STATUS_VALUES`）、`operation_id`、`elapsed_ms`；证据统一用 `diff` 与 `artifact_id` 表达。
3. 状态取值归一：
   - `no_match` → `fail` + `reason: "selector_not_found"`
   - `partial_error` → `ok` + `complete: false` + `errors`
   - 所有工具只经 `envelope` 返回
4. `page_navigate` 复用统一流水线（签名 → 执行 → 录制 → 断言 → 封装）；HTTP 状态与空页判定作为后置检查接入，不再复制流程。
5. `action_tools.py` 的 locator 工具由 `action_tool` 工厂生成，函数体只保留签名。
6. 别名合并（对外破坏性变更，写入 CHANGELOG）：
   - 删除 `page_focus`，保留 `page_select`
   - 删除 `locator_set_files`，保留 `locator_upload`
   - 删除 `workflow_replay`，保留 `workflow_run`
7. backend 拆分落位：`__init__.py` 只保留入口与编排；`actions` / `locator` / `reader` / `assertion` / `signature` 各自独立。
8. 工具文件按资源拆分：browser / session / context / page_lifecycle / page；每个文件少于 300 行。
9. 异常审计：逐处检查 `except Exception: pass`，能删的删；必须保留的写 `# silent-ok: 理由`，并由不变量测试要求存在该标注。
10. 工具数量快照：删除三个别名后为 94 个；新增或删除工具必须同步更新快照并说明理由。
11. 工程门禁：
    - `ruff`：`E,F,I,B,SIM,ASYNC`，行宽 100
    - `ruff format --check`
    - `mypy agent_gavel`（配置写入 `pyproject.toml`；存量错误记基线，只能减少）
    - CI 增加不变量测试、词表测试、契约快照
12. 文档同步：`README` 的环境变量与工具表、`CHANGELOG` 的破坏性变更、`docs/tech-plan.md` 附录 B 的响应外形。

**验收**

- 十条不变量全部为绿，白名单为空
- 契约快照除已声明的破坏性变更外无差异
- 工具数量快照为 94
- `ruff` 与 `mypy` 通过
- 文档与实现一致（响应字段、环境变量、工具列表）

**禁止**

- 在工具层判断浏览器模式、拼接路径、读取环境变量
- 以兼容为由保留别名或第二条代码路径

---

## 7. 后续修改的固定流程

任何新增或修改，按顺序执行：

1. **定位概念**：本次改动属于动作、断言、资源、配置、模板中的哪一类。
2. **只改主人**：动作 → `core/actions.py`；断言 → `core/checks.py`；配置 → `core/config.py`；身份 → `pages.py` / `contexts.py`。若必须改第二个文件才能成立，先确认归属。
3. **先写测试**：修 bug 写能复现的回归测试；加能力写契约测试。
4. **实现**：工具层只做"解析参数 → 调用 backend → `envelope`"。
5. **跑门禁**：`test_invariants`、`test_vocabulary`、契约快照、受影响测试。
6. **同步文档**：`CHANGELOG`、`README`、必要时本文。

新增能力时的落点：

| 要加的东西 | 只改这里 | 额外要做 |
|---|---|---|
| 一个动作 | `core/actions.py` + 一个分发函数 | 若有录制价值，补 `record_fields` |
| 一个断言操作 | `core/checks.py` | 补 Python 比较与 JS 渲染 |
| 一种读取目标 | `core/checks.py` | 补 Python 读取与 JS 读取 |
| 一个工具 | `tools/` 下对应域文件 | 只做薄封装，返回走 `envelope` |
| 一个环境变量 | `core/config.py` | 更新 README 表格 |
| 一个错误码 | `core/errors.py` | 更新文档中的错误码列表 |

---

## 8. 代码规则

1. 新增或修改的公共函数必须有类型标注；`mypy` 不得新增错误。
2. `except Exception` 不得静默：要么 `emit` 事件，要么写日志，要么标注 `# silent-ok: 理由`。
3. 模块之间只走公共方法，禁止跨模块访问 `._private`。
4. 一个文件一个职责；超过 300 行需要说明理由。
5. 工具 docstring 必须写明：参数含义、返回的 `status` 取值、是否隐私敏感。
6. 不得在工具层拼路径、读环境变量、判断浏览器模式、写业务分支。
7. 不得引入别名工具；同一能力只保留一个名字。
8. 文本等待使用首个命中是定义行为；locator 的 strict 唯一性不适用于文本等待。
9. 副作用的判定只允许通过 `core/actions.looks_like_side_effect` 进行，不允许在各处自行匹配关键词。

---

## 9. 门禁

CI 与本地提交前必须执行：

```
ruff check
ruff format --check
mypy agent_gavel
pytest -q   # 配置 pytest-timeout，单测上限 90s
```

`mypy` 的配置写入 `pyproject.toml`；首次启用时把存量错误数记为基线，之后只能减少，不能增加。

其中 `pytest` 必须包含：

- `tests/test_invariants.py`：第 2 节的八条检查，含 `BLOCKING_INVENTORY` 与 `silent-ok` 标注检查
- `tests/test_vocabulary.py`：动作、读取类型、比较操作的三方一致性
- `tests/test_contract_snapshot.py`：对外响应外形
- `tests/test_identity.py`：资源归属与并发
- `tests/test_multiprocess.py`：1 个 daemon + 4 个 adapter，id 一致、锁有效、只有一个 Chrome
- 打包检查：wheel 中不得包含 `legacy/`；干净安装可导入

各测试在对应阶段引入：阶段 0 建立不变量、词表骨架与契约快照；阶段 2 引入身份测试。未引入前不放入 CI 必跑列表。

不变量检查的实现约定：

- 使用 AST 而非文本 grep，避免注释与字符串造成误判
- 白名单以 `(文件, 符号)` 为键，只能通过代码审查删除，不能由实现者新增
- 检查失败时的错误信息必须直接指出违规文件与符号

---

## 10. 完成定义

同时满足以下条件，才算收敛完成：

1. 十条不变量全部由测试强制，白名单为空。
2. 契约快照、词表测试、身份测试与既有测试全部通过。
3. `core/actions.py` 与 `core/checks.py` 各只有一份定义，全仓库不存在第二份动作或断言词表。
4. `runtime` 不依赖上层；`tools` 不含业务分支。
5. backend 拆分后每个文件少于 300 行；工具层只有一份 guard 与一个 backend 实例。
6. 环境变量只出现在 `core/config.py`；仓库根不再产生运行期目录。
7. 浏览器关闭期间，其他调用不被阻塞（有测试证明）。
8. 对外响应均经 `envelope`，`status` 取值受限，包含 `operation_id` 与 `elapsed_ms`。
9. `README`、`CHANGELOG`、`docs/tech-plan.md` 与实现一致。
10. 进程模型达标：一个数据根只有一个 daemon、一个 Chrome；adapter 不持有浏览器与注册表。

---

## 11. 非目标

以下内容不在本轮收敛范围内，不得以其为由扩大改动：

| 项 | 状态 |
|---|---|
| 多标签模板（打开、切换、等待 popup 作为模板步骤） | 暂不做；阶段 2 仅让录制记录 `page_id`，不实现执行 |
| 参数化助手（自动提示可参数化字段） | 不做；参数化由录制入口显式传入 |
| 模板社区征集 | 冻结，仅保持格式不变 |
| 并行步骤组 | 不做 |
| 多个 runtime 共享同一浏览器 | 不支持；adapter 必须转发给 daemon，禁止用跨进程文件锁协调多个 runtime |
| 测试覆盖率指标 | 不追求；只保留契约、不变量、身份、词表四类测试 |
| 推翻重写 | 不做；按阶段增量收敛，每阶段保持行为可用 |

---

## 12. 变更控制

- 修改本文中的不变量、冻结契约或阶段顺序，必须在同一次变更中同时修改对应的门禁测试与文档，否则不予合并。
- 若实现确实无法满足某条不变量，应先修改本文并说明理由，再改代码；不允许先违规后补文档。
- 本文与 `docs/tech-plan.md` 冲突时，以本文为准；`tech-plan` 作为设计背景保留，不再作为实现约束。
