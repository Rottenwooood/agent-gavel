# 接口与浏览器验收记录

报告只说明对应日期、版本和运行方式。0.6已删除session，旧0.5记录中的工具数、字段与目录不能当作新版结论。

## 2026-11-17：0.6文档核对与有头黑盒验收

基于43e177e工作区，生产代码只修正工具说明和文档标题，没有改变执行逻辑。
先实际运行旧黑盒脚本，复现context仍取page_id的失败；修正为active_page_id后，
又复现资料复制仍指向browser-profile及调用已删profile_use的旧路径。
脚本现按临时browser-profiles/primary取资料，复制后通过context(create, profile=...)验证命名目录，再显式关闭并删除测试副本。
无头命令还复现了headless_forbidden_on_primary：旧脚本未声明隔离测试标记，现只在自己的临时资料环境中设置AGENT_GAVEL_TEST_ISOLATED=1。

命令：

```sh
.venv/bin/python tests/e2e_mcp.py --output /tmp/gavel-docs-headed-final.json
.venv/bin/python tests/e2e_mcp.py --headless --output /tmp/gavel-docs-headless-final.json
```

结果：status=passed、stage=complete；protocol_checks、browser_checks、interaction_checks与profile_checks均为passed。
真实MCP stdio → 独立daemon → 有头Chrome，共133次调用；覆盖13个入口、页面动作与断言、
三种原生PNG、下载、工作流、双adapter共享资源与资料副本。记录见
[0.6有头黑盒报告](e2e-headed-2026-11-17.json)。无头黑盒亦完整通过133次调用，见
[0.6无头黑盒报告](e2e-headless-2026-11-17.json)。截图是当次/tmp下的辅助产物，不随文档归档。
该验收使用独立临时资料，不访问用户登录资料；它不代表真实站点风控验收。

同轮门禁与受影响测试：test_invariants.py、test_vocabulary.py、test_schema_compat.py、
test_contract_snapshot.py、test_navigation_contract.py、test_observation_edit.py、test_retry_diff_wait.py。
AGENT_GAVEL_HEADLESS=1时60 passed；AGENT_GAVEL_HEADLESS=0时56 passed、4 failed，
四项失败为contract_main_flow、sparse_content_requires_attachment、diff_reports_dialog、
edit_json_add_parameterized_assertion，均涉及BrowserContext.new_page的TargetClosedError及其后续缺字段。
该有头pytest问题仍未修复；不将无头通过或黑盒通过替换成有头pytest全绿。

生成的工具参考与catalog逐字一致（13工具、84操作）；修改文件通过Ruff检查与格式检查、git diff --check。

## 0.5历史验收

实现提交：`b1359b2`；黑盒验收脚本提交：`6d59ecd`。

## 2026-11-14：无头端到端复验通过

命令：

```sh
.venv/bin/python tests/e2e_mcp.py --headless --output docs/validation/e2e-headless-2026-11-14.json
```

结果：`status=passed`、`stage=complete`、`browser_checks=passed`、`protocol_checks=passed`；
工具面 14 个入口、契约 25,773 字节；环境报告 `tcp_loopback=available`、`unix_socket=available`。
完整调用序列（136 条，含预期失败分支）见 [e2e-headless-2026-11-14.json](e2e-headless-2026-11-14.json)。

该次复验同时修掉黑盒脚本对已删除操作的调用：`page(op="links")` 在 0.5.0 已并入
`page(op="text", mode="links")`，脚本原先仍按旧操作探测，导致 `unknown_operation` 并把
`status` 判为 failed。

预期失败分支不在通过判据内：`bad_args`、`locator_not_found`、`not_a_download`、
`session_busy`，以及工作流被取消时的 `internal`。


## 源码导出测量

以 `337b508` 的 0.4 接口为基线，在相同 Python 和依赖环境中导出工具契约。
使用 `json.dumps({"tools": tools}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")` 计数；不含 JSON-RPC 信封、空白或模型供应商的格式转换。
原始计数、SHA-256 和依赖版本见 [schema-stats.json](schema-stats.json)。

| 项目 | 0.4 | 当前实现 | 减少 |
| --- | ---: | ---: | ---: |
| 工具入口 | 95 | 14 | 85.3% |
| 完整工具清单（字节） | 42,844 | 36,218 | 15.5% |
| inputSchema（字节） | 25,637 | 24,174 | 5.7% |
| description（字节） | 11,434 | 11,183 | 2.2% |

95 个旧入口有明确迁移映射，对应 90 个当前操作。数量减少包含真正重复入口的消除；不能用入口降幅代替 schema 或描述的压缩率。按操作保留参数类型、必填项、默认值和结果差异，完整迁移表见 [工具参考](../tools.md)。

当前源码导出命令（不启动浏览器，不属于功能测试）：

```sh
uv run python -m agent_gavel.tools.describe > /tmp/agent-gavel-tools.json
```

## 2026-11-12 环境记录：端到端验收被环境阻塞

实际尝试了两种模式，均在传输环境检查阶段退出，返回码为 1：

- [有界面报告](e2e-headed-final.json)：`status=blocked`、`browser_checks=not_run`、`calls=[]`。
- [无界面报告](e2e-headless-final.json)：同上。

当前执行环境禁止创建/绑定 TCP 回环和 Unix socket，均报 `Operation not permitted`。适配器与 daemon 的连接以及浏览器调试通道无法启动。没有浏览器动作得到验证，截图内容、操作兼容性、返回字段和响应耗时均未获得端到端通过证据。

在允许本地 socket 的终端中，从本仓库根目录运行：

```sh
uv run python tests/e2e_mcp.py --output /tmp/gavel-e2e-headed.json
uv run python tests/e2e_mcp.py --headless --output /tmp/gavel-e2e-headless.json
```

脚本走实际 MCP stdio → 独立 daemon → 真实浏览器；默认有界面 managed Chrome，使用临时独立资料与产物目录。覆盖页面与元素操作、原生 PNG 图片、下载与产物过滤/导出、断言失败、工作流执行与控制、第二客户端共享状态和资源清理。不得将 `blocked` 或仅 `--list-only` 的成功算作浏览器验收通过。

## 静态检查

- 所有本次修改的 Python 文件通过 Ruff 检查。
- `git diff --check` 通过。
- 锁文件一致性检查通过，保留原有 PyPI 下载源与依赖版本：
  `uv lock --check --offline --default-index https://pypi.org/simple --python /home/c6h4o2/agent-gavel/.venv/bin/python`（本环境使用 `/tmp/gavel-uv-cache`）。
- 未运行内部功能测试；这些静态检查不替代用户要求的端到端测试。
