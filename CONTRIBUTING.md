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
agent_gavel/runtime/*    资源模型（session/context/page/队列/产物/策略/事件/指标）
agent_gavel/backends/*   执行后端（Playwright）
agent_gavel/workflows/*  模板 schema/加载/编译/录制/执行
legacy/                  旧通道归档，不打包不运行（勿在此开发）
```

## 提交前

```bash
uv run pytest -q                      # 必须全绿
uv run python tests/bench_tasks.py    # 真实任务（联网；可 AGENT_GAVEL_BENCH_OFFLINE=1 跳过真站）
uv run python tests/mcp_live_smoke.py # 真实 MCP 协议联调
```

新增行为请补对应测试：功能放 `tests/test_*.py`，安全/契约放
`tests/test_security_fixes.py`，多步真实任务放 `tests/test_real_tasks.py`。

## 贡献模板

模板是**按网站组织**的纯 JSON：一个网站一个 `site`，文件名 `site_功能.json`。

### 命名与组织

- `template_id` = `site_功能`（小写、下划线），例如 `bing_search`、`zhihu_login`。
- 一个网站可以有多个模板文件；不做跨站通用模板。
- 只放**你在真实站点上亲手跑通并验证过**的模板。

### 必须遵守

1. **不得写入真实凭据**。敏感输入用 `$VAR` 占位，并在 `parameters` 里声明
   `{"sensitive": true}`；真实值永不落盘。
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

模板由 loader 唯一管理，默认存 `~/.config/agent-gavel/workflows/`；仓库不内置模板目录。
提 PR 时把 `workflow(op=publish)` 导出的 JSON 贴在 PR 里，并附 `workflow(op=validate)`
的 valid 结果与一次 `workflow(op=run)` 的实测输出。CI 目前跑测试、打包与联调冒烟，
不对提交的模板自动校验，合并前由维护者按上述证据人工确认。

### 失效处理

模板会因站点改版失效。运行记录连续失败 ≥3 会被标 `suspected`，请在 PR/issue 里
附上复现步骤与失败证据；失效模板应及时修复或下架。

## 报告问题

请附：复现步骤、`workflow(op=run)` 的 `verbosity="full"` 输出、相关截图/trace artifact，
以及浏览器/系统/版本信息。真实站点问题请注明是否涉及登录/风控。
