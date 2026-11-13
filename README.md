# agent-gavel

本地浏览器运行时与 MCP server：现场操作页面并附带断言，或录制工作流后重放。

当前版本 **0.5.1**（2026-11-13）。95个旧入口收敛为14个入口、89个操作；旧工具名不再注册。完整参数、返回与迁移表见 [工具参考](docs/tools.md)。

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

OpenCode V2 使用本仓库的 `opencode.json`（`mcp.servers` 格式）。执行 `uv sync` 后从仓库根目录启动 `opencode`，它使用本地 `.venv` 中的安装；浏览器资料、工作流和产物存入已忽略的 `.runtime/opencode/`。该项目配置将同名旧入口替换为当前安装，其他全局配置仍按 OpenCode 的规则加载。接入验收状态见 [OpenCode 验收记录](docs/validation/opencode-install.md)。

升级0.5前先用旧客户端停止旧daemon；新adapter会拒绝不兼容接口，不会自动终止现有任务。

adapter转发stdio；daemon唯一持有浏览器、资源和锁。多个客户端共享资源编号，同页串行，跨页可并行。

## 公开接口

```text
browser        浏览器进程与资料副本
session        执行租约与状态导入导出
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

动作类调用会附带 `diff`：只列出这一步真正改变了什么（新开页、下载产物、焦点、局部 DOM），没变化就没有该字段。`page(op=navigate)` 与 `page(op=open)` 入参一致，加载后默认带回 4000 字正文摘要（`text_chars=0` 关闭）；compact 下导航不返回 diff 与 anchors。链接清单用 `page(op=text, mode="links")` 取（原 `links` 操作已并入）。

导航结果可能带这些提示，含义固定：

- `access_restricted` + `page_status=blocked`：被重定向到登录/权限页，别重试，换入口或补登录态。
- `sparse_content`：正文很少但页面有附件/下载链接，内容大概率在文件里，用 `artifact(op=read_text)`。
- `page_looks_empty`：正文少于 20 字，可能没渲染完或选错了页面。
- `navigated_to_download`：链接是文件不是网页，返回里直接给了 `artifact_id` 与 `mime_type`。
- `http_404` / `http_5xx`：HTTP 层失败，`http_error=true`。

## 调用示例

以下为MCP tools/call的工具名与参数。编号替换为实际返回值；先探索目标站点，不假定示例控件存在。

```json
{"name":"session","arguments":{"op":"create"}}
{"name":"page","arguments":{"op":"navigate","page_id":"page_001","url":"https://example.com"}}
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

workflow的record_start开始录制，正常执行动作后record_stop编译保存草稿；也可直接save/validate模板，通过run重放。verbosity为summary/steps/full，失败步保留证据。模板仍为schema_version=2；写操作要求精确id。同session并发运行返回session_busy，暂停和取消在步骤边界处理。

## 浏览器与资料

默认有头managed：自起加固Chrome并以CDP连接，使用持久资料。default容器写回资料，isolated继承状态但不写回。session是租约，不是窗口。

browser_mode=attach配合cdp_endpoint接管现有浏览器；playwright模式直接启动。接管真实环境、操作真实资料、导入导出登录态须获得相应授权。测试只用新建私有资料。

常用配置：AGENT_GAVEL_HEADLESS、AGENT_GAVEL_DATA_DIR、AGENT_GAVEL_BROWSER_PROFILE、AGENT_GAVEL_WORKFLOWS_DIR、AGENT_GAVEL_ARTIFACT_DIR、AGENT_GAVEL_ALLOW_PATHS、AGENT_GAVEL_ALLOW_DOMAINS。环境变量仅由core/config.py读取。

## 端到端验收

验收走真实stdio、adapter、daemon和浏览器，不调用内部注册表或mock。数据和资料隔离，结束后仅清理本次测试拥有的进程及文件。

```bash
uv run python tests/e2e_mcp.py --output /tmp/gavel-headed-e2e.json
uv run python tests/e2e_mcp.py --headless --output /tmp/gavel-headless-e2e.json
```

覆盖14个入口、参数错误、真实交互/上传/下载、产物过滤/导出/抽取、cookie与状态迁移、工作流及并发、双客户端共享资源、三种PNG范围、PDF、追踪与网络记录。PNG验证图片内容块、实际尺寸及元数据一致。

--list-only只验证协议和不依赖浏览器的调用，不能替代浏览器验收。环境必须允许本地socket并提供显示会话；环境失败不能用内部函数检查宣告通过。

接口参考和schema从同一操作声明导出：

```bash
uv run python -m agent_gavel.tools.describe
uv run python -m agent_gavel.tools.describe --markdown docs/tools.md
```

架构与唯一归属见 [docs/architecture.md](docs/architecture.md)。
