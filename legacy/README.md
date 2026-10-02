# legacy —— 旧通道归档（不打包、不注册、不运行）

本目录是 agent-gavel 重构前的旧实现，**已冻结**：

- `channels/dom/`：旧的裸 CDP 网页通道（`dom_*` 工具、`verify.py` 的 S1–S5 降级重试）。
- `channels/desktop/`：旧的 AT-SPI 桌面通道（已停止开发）。
- `browser_manager.py`：旧通道自管的调试 Chrome（9222）。
- `tests/`：旧通道的独立验证脚本（`dom_*.py` / `verify_branches.py` /
  `browser_manager_smoke.py`）。

## 为什么放在这里

- 新实现是 `agent_gavel/{runtime,backends,tools,workflows}/`（Playwright 内核）。
  旧代码在仓库根目录、且不在 `pyproject.toml` 的打包清单里，**不会被 wheel/sdist 打包**。
- `agent_gavel/main.py` **不再注册**旧工具，旧通道**不会运行**；这里只作历史与对照。

## 怎么用

不要 import、不要调用。它只供查阅旧设计（尤其是旧的降级重试 / scoped diff 语义，
新实现的选择性恢复设计见 `docs/tech-plan.md`）。如需复活某段逻辑，应在新 runtime
中重新设计并补测试，而不是从这里搬。
