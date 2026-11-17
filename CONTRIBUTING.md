# 贡献指南

## 开发环境

```bash
git clone https://github.com/Rottenwooood/agent-gavel
cd agent-gavel
uv sync
uv run pytest -q
```

代码分层：

```
agent_gavel/tools/*      MCP 工具（对外接口）
agent_gavel/runtime/*    资源模型（profile/context/page/队列/产物/策略/事件/指标）
agent_gavel/backends/*   执行后端（Playwright）
agent_gavel/workflows/*  模板 schema/加载/编译/录制/执行
legacy/                  旧通道归档，不打包不运行（勿在此开发）
```

## 提交前

```bash
AGENT_GAVEL_HEADLESS=0 uv run pytest -q # 本机有头，CI可无头；必须全绿
uv run python tests/e2e_mcp.py --output /tmp/gavel-headed-e2e.json
```

端到端脚本使用独立测试资料，经MCP stdio、adapter、daemon调用真实浏览器。
不能用语法检查或纯函数测试替代。真实站点的 `bench_tasks.py` 与 `mcp_live_smoke.py`
用于额外联调，不进入必跑用例；不在用户登录资料上执行回归测试。

新增行为请先补对应测试：功能放 `tests/test_*.py`，安全/契约放
`tests/test_security_fixes.py`，多步真实任务放 `tests/test_real_tasks.py`。

## 贡献模板

模板是**按网站组织**的纯 JSON：一个网站一个 `site`，文件名 `site_功能.json`。

### 命名与组织

- `template_id` = `site_功能`（小写、下划线），例如 `bing_search`、`zhihu_login`。
- 一个网站可以有多个模板文件；不做跨站通用模板。
- 只放**你在真实站点上亲手跑通并验证过**的模板。

### 必须遵守

1. **不得写入真实凭据**。敏感输入用 `$VAR` 占位，并在 `parameters` 里声明
   `{"sensitive": true}`；不要把真实值写入模板、PR或日志。
2. **每个步骤尽量带检查点**（`checkpoint`），让重放可验证，而不是"点完就算"。
3. **副作用步骤**（提交/保存/删除/支付/发送）保持默认的
   `requires_confirmation=true` / `safe_to_retry=false`，不要为跑通而关掉。
4. 不用写死坐标；用语义/稳定选择器（优先 `role`/`label`/`placeholder`，其次 `css`）。

### 生成与校验

先在你的本地会话里跑通，用 `workflow(op=record_start)` → 操作 → `workflow(op=record_stop)`
生成草稿，再：

```
workflow(op=validate, template=...)   # 必须 valid
workflow(op=publish, template_id=...) # 产出干净 JSON（无本地路径/凭据）
```

模板由loader唯一管理，默认存 `<数据根>/workflows/`；Linux默认数据根为
`~/.local/share/agent-gavel/`（或遵循XDG_DATA_HOME），Windows/macOS遵循平台目录。
AGENT_GAVEL_DATA_DIR或AGENT_GAVEL_WORKFLOWS_DIR可覆盖；仓库不内置模板目录。
提 PR 时把 `workflow(op=publish)` 导出的 JSON 贴在 PR 里，并附 `workflow(op=validate)`
的valid结果与一次 `workflow(op=run)` 的实测输出。CI常规运行测试与打包；真实站点冒烟由workflow_dispatch触发且允许失败。
不对提交的模板自动校验，合并前由维护者按上述证据人工确认。

### 失效处理

模板会因站点改版失效。loader记录累计success_count、fail_count和最近状态，
不自动标记suspected。报告失效时请附复现步骤与失败证据，修正JSON、save后可用resume续跑仍驻留的失败run。

## 报告问题

请附：复现步骤、`workflow(op=run)` 的 `verbosity="full"` 输出、相关截图/trace artifact，
以及浏览器/系统/版本信息。真实站点问题请注明是否涉及登录/风控。
