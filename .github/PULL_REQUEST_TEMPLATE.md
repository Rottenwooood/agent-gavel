## 改动类型

- [ ] 新功能 / 新模板
- [ ] 修复（bug / 失败诊断）
- [ ] 重构（结构 / 命名）
- [ ] 文档 / 性能优化

## 描述

<!-- 改了什么，为什么。 -->

## 验证（必填——"语法过了"不算验证）

- [ ] `uv run python3 tests/browser_manager_smoke.py` 全过（涉及 Chrome 生命周期改动时）
- [ ] 真实跑通受影响的模板（`dom_run_template` / `desktop_run_template`），贴出 pass/fail
- [ ] 环境错误走结构化返回（`reason + hint`），确认没有裸抛 UnexpectedToolError
- [ ] 改动性能时附 before/after 实测耗时

## 兼容性检查

- [ ] 模板 / API 改动已同步 README
- [ ] 改了模板目录或路径时，用户目录优先（`~/.config/agent-gavel/`）逻辑未被破坏
- [ ] 通道自包含结构（`agent_gavel/channels/{dom,desktop}`）未被破坏

## 截图 / 日志

<!-- 可选：debug=1 的调用日志或关键输出。 -->
