# agent-gavel

本地浏览器运行时与 MCP server：现场操作页面并附带断言，或录制工作流后重放。

当前版本 **0.6.0**（2026-11-17）。对外提供13个工具、84个操作；旧工具名不再注册。完整参数、返回与迁移表见 [工具参考](docs/tools.md)。

## 怎样减少模型往返

优化落在工具的执行与返回设计中，具体任务需要多少轮取决于页面、判据与操作链：

- **动作、等待、断言一次完成**：`locator` 动作和 `page(op=open/navigate)` 可带 `assertions`。工具执行后在 `wait_s` 内等待条件并返回 `pass/fail/ambiguous`，省去模型再次读取页面、判断预期结果的往返。`ambiguous` 表示检查未通过但动作期间观测到了有意义的 DOM 变化，需结合证据处理。
- **导航直接带回正文**：`open/navigate` 加载后默认返回最多4000字正文，省去导航后立即再调 `text`。compact 下不附 anchors；确实需要定位信息时再读取或探索。
- **正文与定位线索一起返回**：`page(op=text)` 同时返回正文和 `anchors`，其中的 `target` 可直接用于 `locator`。`snapshot/explore` 提供可用锚点；不必为了取得定位格式单独查询一次。
- **多字段一次读取、截图直接可看**：`page(op=read, page_features=...)` 在一次调用中返回多项命名读取结果，避免为每个字段分别调用。`screenshot` 同次返回原生MCP图片与产物元数据，无需先取得路径再调用工具读取图片。
- **动作返回变化信息**：同次动作返回新开/关闭页、active切换、产物、dialog、页面与DOM变化等已观测到的差异，未变化的项省略。新页信息已在 `diff.pages.opened` 中时，无需再列一次标签页来发现它。
- **等待交给工具执行**：文本、选择器、URL、响应、下载、popup和事件都有条件等待操作，模型无需反复读取页面轮询。
- **模板整条执行，失败从断点继续**：一次 `workflow(op=run)` 执行动作链与检查点，默认返回汇总；`observe` 标记的步骤在结果中附带观测，失败返回证据。修正JSON并保存后，`resume` 从失败步继续，省去模型逐步调度和从头重跑。
- **默认初始化和页面跟随**：首次 `open/navigate` 可惰性创建默认浏览器、容器和页面。点击/按键产生且opener为当前页的新标签页默认自动设为active，后续省略 `page_id` 可继续操作它。
- **入口描述直接给调用线索**：工具描述首行列出全部op与常用签名，并标注必填参数；结构化错误提供 `reason/hint`，减少为查操作名、必填项和恢复方式而产生的往返。

compact结果按契约省略重复和诊断字段，减少每轮需要处理的信息；这与减少工具调用轮次是不同的优化。

## 安装与启动

需要Python 3.13+、Chrome/Chromium和uv。

```bash
git clone https://github.com/Rottenwooood/agent-gavel.git
cd agent-gavel
uv sync
./run-mcp.sh
```

本地MCP配置（替换为实际绝对路径）：

```json
{"mcpServers":{"agent-gavel":{"command":"/absolute/path/agent-gavel/run-mcp.sh"}}}
```

Codex可在MCP配置中指定本地虚拟环境与源码目录，避免从其他项目启动时导入错误的安装：

```toml
[mcp_servers.agent-gavel]
command = "/absolute/path/agent-gavel/.venv/bin/python"
args = ["-m", "agent_gavel.main"]
cwd = "/absolute/path/agent-gavel"

[mcp_servers.agent-gavel.env]
AGENT_GAVEL_HEADLESS = "0"
```

该配置不覆盖数据根，默认使用用户级primary。虚拟环境须已安装当前源码；Windows下使用对应的Scripts/python.exe路径。

OpenCode V2 使用本仓库的 `opencode.json`（`mcp.servers` 格式）。执行 `uv sync` 后从仓库根目录启动 `opencode`，它使用本地 `.venv` 中的安装；浏览器资料、工作流和产物存入已忽略的 `.runtime/opencode/`。该项目配置将同名旧入口替换为当前安装，其他全局配置仍按 OpenCode 的规则加载。接入验收状态见 [OpenCode 验收记录](docs/validation/opencode-install.md)。

从0.5升级时，先确认没有运行中的workflow，再用旧客户端停止旧daemon并重启MCP连接。0.6删除session层，状态导入导出移到context；旧工具和旧身份字段不能继续使用。旧数据不会自动迁移：停止使用源与目标Chrome后，将需保留的模板放入新数据根的workflows目录，将所选资料目录放入profiles/primary；不覆盖已有登录资料，不迁移旧daemon/socket/锁文件。变更列表见 [CHANGELOG](CHANGELOG.md)。

adapter转发stdio；daemon唯一持有浏览器、资源和锁。多个客户端共享资源编号，同页串行，跨页可并行。

## 公开接口

```text
browser        浏览器进程与资料副本
context        容器、cookie、请求头与权限
page           生命周期、读取、等待与PDF
locator        元素动作，可附带断言
assertion      条件检查与格式说明
workflow       模板、录制与运行控制
artifact       所有产物，包含下载的查询、导出、抽取与清理
trace          追踪开始/停止
network        网络记录开始/停止
screenshot     直接返回PNG图片及产物元数据
console_get    控制台错误与警告
performance_metrics 动作延迟统计
daemon_shutdown      停止共享daemon
```

系列工具用op选择操作。参数集、业务必填、类型和默认值分别校验。返回也按操作定义：page.get返回单页；page.list返回items数组；page.meta和page.read分别返回元信息、自定义读取结果。

默认detail=compact，省略明确重复或未指定的元数据；full保留业务字段和诊断。读取值里的null、false、0、空字符串、空集合与失败证据不会被递归删除。

动作类调用会附带非空的 `diff`：只列出这一步观测到的变化（新开页、下载产物、焦点、局部 DOM）。`page(op=navigate)` 与 `page(op=open)` 共用加载参数和返回契约；open另有context/profile/isolated/activate参数。加载后默认带回4000字正文摘要（`text_chars=0` 关闭）；compact下导航不返回diff与anchors。链接清单用 `page(op=text, mode="links")` 取（原 `links` 操作已并入）。

导航结果可能带这些提示，含义固定：

- `access_restricted` + `page_status=blocked`：被重定向到登录/权限页，别重试，换入口或补登录态。
- `sparse_content`：正文很少但页面有附件/下载链接，内容大概率在文件里，用 `artifact(op=read_text)`。
- `page_looks_empty`：正文少于 20 字，可能没渲染完或选错了页面。
- `navigated_to_download`：链接是文件不是网页，返回里直接给了 `artifact_id` 与 `mime_type`。
- `http_404` / `http_5xx`：HTTP 层失败，`http_error=true`。

## 调用示例

以下为MCP tools/call的工具名与参数。编号替换为实际返回值；先探索目标站点，不假定示例控件存在。

```json
{"name":"page","arguments":{"op":"open","url":"https://example.com"}}
{"name":"page","arguments":{"op":"explore","page_id":"page_001"}}
{"name":"locator","arguments":{"op":"fill","page_id":"page_001","target":{"by":"label","value":"Search"},"value":"hello"}}
{"name":"locator","arguments":{"op":"click","page_id":"page_001","target":{"by":"role","value":"button","name":"Search"},"assertions":{"url_contains":"search"}}}
{"name":"screenshot","arguments":{"page_id":"page_001"}}
```

定位支持role/label/placeholder/text/testid/css/xpath、strict、nth和嵌套iframe。ok表示动作执行返回；pass表示检查通过；fail表示未满足；ambiguous保留变化证据。技术错误返回错误码和恢复提示，MCP isError为真。

## 截图、下载与工作流

screenshot(page_id?, selector?, full_page=false, save_to?)一次返回原生MCP图片和artifact_id/width/height等元数据。selector优先截元素，否则截视口或整页；width/height为图片像素；save_to是目录。图片不放入普通JSON文本。PDF使用page的pdf操作。

下载自动捕获为kind=download的产物：

```json
{"name":"artifact","arguments":{"op":"list","kind":"download","page_id":"page_001"}}
{"name":"artifact","arguments":{"op":"get","artifact_id":"artifact_001","expected_kind":"download"}}
{"name":"artifact","arguments":{"op":"export","artifact_id":"artifact_001","dest_dir":"/tmp","path_visibility":"hidden"}}
{"name":"artifact","arguments":{"op":"read_text","artifact_id":"artifact_001"}}
```

导出受路径白名单约束，默认隐藏绝对路径；user_visible才返回路径。expected_kind=download保留not_a_download错误。

workflow的record_start开始录制，正常执行动作后record_stop编译保存草稿并返回template；通过get读取JSON，编辑动作、target、checkpoint和parameters后save写回。动作与断言中的字符串支持 `$VAR`，run的params覆盖模板default，缺值报错。通过run重放，verbosity为summary/steps/full，失败步保留证据；局部修复只返回proposed_repairs，不自动改模板。模板仍为schema_version=2；写操作要求精确id。同context并发运行返回context_busy，暂停和取消在步骤边界处理。

## 浏览器与资料

默认有头managed：启动Chrome并以CDP连接，使用持久资料。没有session层：profile对应Chrome进程与磁盘目录，context是cookie分区，page是标签页。默认落在primary资料目录的default容器，扫码与登录写回磁盘；显式isolated容器创建独立分区，仅继承默认容器的cookie，其内新增登录状态不写回。`context(op=create, profile="account_2")` 可创建另一资料目录的浏览器与默认容器。

默认数据根由平台决定：Windows为 `%LOCALAPPDATA%\agent-gavel`，macOS为 `~/Library/Application Support/agent-gavel`，Linux为 `$XDG_DATA_HOME/agent-gavel` 或 `~/.local/share/agent-gavel`。`AGENT_GAVEL_DATA_DIR` 可覆盖；资料在 `<数据根>/profiles/<name>/`，模板在 `<数据根>/workflows/`，默认产物在系统临时目录的 `agent-gavel/artifacts/`。各目录也可单独配置。没有ACTIVE指针，未指定名字就使用primary。

同一资料目录的浏览器活着就复用/重连，已死才重新启动；不同profile各自持有进程。adapter退出不关闭共享daemon；无adapter连接达到空闲阈值后daemon退出，保留受管Chrome和标签页。`browser(op=close, profile="account_2")` 显式关闭该资料目录的浏览器；不传profile则关闭全部。默认primary禁止无头，已有浏览器与显式请求的headless不一致时报错。

显式page_id直接操作对应标签页，无需先select。省略时使用唯一default容器的active页，只有一个context时也可复用；多个可选context无法唯一决定时返回ambiguous_page。click/press的新页跟随会在 `diff.switched_to_new_tab` 与 `diff.pages.active` 中说明，`follow_popup=false` 可关闭。

browser_mode=attach配合cdp_endpoint接管现有浏览器；playwright模式直接启动。接管真实环境、操作真实资料、导入导出登录态须获得相应授权。测试只用新建私有资料。

常用配置：AGENT_GAVEL_HEADLESS、AGENT_GAVEL_DATA_DIR、AGENT_GAVEL_BROWSER_PROFILES、AGENT_GAVEL_WORKFLOWS_DIR、AGENT_GAVEL_ARTIFACT_DIR、AGENT_GAVEL_IDLE_EXIT、AGENT_GAVEL_ALLOW_PATHS、AGENT_GAVEL_ALLOW_DOMAINS。环境变量仅由core/config.py读取；AGENT_GAVEL_BROWSER_PROFILE已删除。默认初始化无需先调用profile_ensure。

## 端到端验收

验收走真实stdio、adapter、daemon和浏览器，不调用内部注册表或mock。数据和资料隔离，结束后仅清理本次测试拥有的进程及文件。

```bash
uv run python tests/e2e_mcp.py --output /tmp/gavel-headed-e2e.json
uv run python tests/e2e_mcp.py --headless --output /tmp/gavel-headless-e2e.json
```

脚本验收13个入口、参数错误、真实交互/上传/下载、产物过滤/导出/抽取、cookie与状态迁移、工作流及并发、双客户端共享资源、三种PNG范围、PDF、追踪与网络记录。PNG验证图片内容块、实际尺寸及元数据一致；执行结果与具体限制见 [验收记录](docs/validation/README.md)。

--list-only只验证协议和不依赖浏览器的调用，不能替代浏览器验收。环境必须允许本地socket并提供显示会话；环境失败不能用内部函数检查宣告通过。

接口参考和schema从同一操作声明导出：

```bash
uv run python -m agent_gavel.tools.describe
uv run python -m agent_gavel.tools.describe --markdown docs/tools.md
```

架构与唯一归属见 [docs/architecture.md](docs/architecture.md)。
