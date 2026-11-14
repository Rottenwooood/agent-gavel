# agent-gavel 开发路线图

> 定位快照：2026-11-13，0.5.1 已发布（PyPI `agent-gavel` / GitHub `Rottenwooood/agent-gavel`），旧通道归档。

## 一句话定位

agent-gavel 是一个**验证驱动的浏览器操作框架**：让 AI 对浏览器执行动作后，由
**程序断言**（而非再调一次模型）判定是否成功，从而把"执行→验证→反馈"压成一次调用。
核心差异化：**预声明断言 + 程序判定 + 省掉一次模型往返**。

## 当前架构

```
agent 核心（外部：opencode / 任意 MCP 客户端）
  └─ agent_gavel.main（MCP server, stdio）
       └─ tools/*（MCP 工具）
            └─ runtime/*（session/context/page/队列/产物/策略/事件/指标）
                 └─ backends/playwright/（Playwright 驱动系统 Chrome：actions/locator/reader/observation）
                      └─ workflows/*（模板 schema/编译/录制/执行）

legacy/  旧裸 CDP 网页通道 + AT-SPI 桌面通道：归档，不打包、不注册、不运行
```

## 已完成

| 能力 | 状态 | 证据 |
|---|---|---|
| Playwright runtime（资源模型/队列/产物） | ✅ | `tests/test_runtime.py`、`test_advanced_runtime.py` |
| 基础 + 高级浏览器能力 | ✅ | `test_basic_dom.py`、`test_m3_advanced.py` |
| 工作流录制/编译/重放/修复/暂停恢复 | ✅ | `test_m4_workflow.py` |
| 断言三态 + diff 证据 + 事件优先等待 | ✅ | `test_retry_diff_wait.py` |
| 安全加固（前置条件/导出/域名/副作用） | ✅ | `test_security_fixes.py` |
| 真实任务级回归 | ✅ | `test_real_tasks.py`（登录下载校验去重、搜索读价、失败可解释、重放更省） |
| 真实任务基准（含真实站点） | ✅ | `bench_tasks.py`（5/5，重放 5 调用/1681B → 1 调用/192B） |
| 旧通道归档 | ✅ | `legacy/`（不打包/不运行） |
| 文档与打包 | ✅ | README 重写、Dockerfile、CHANGELOG、CONTRIBUTING |
| 工具面收敛（0.5） | ✅ | 14 入口 / 89 操作；`test_schema_compat.py` |
| 统一 diff（0.5） | ✅ | `observation.py`、`test_observation_edit.py` |
| 模板可编辑（0.5） | ✅ | 模板即 JSON、`$VAR` 全覆盖、`test_observation_edit.py` |
| 跨客户端 schema 兼容（0.5.1） | ✅ | 顶层无 union，Claude 系可正常调用 |

## 发布后待办

### 已随 0.5.1 完成

- [x] 失败可解释并续跑：`reason` / `resume_hint` / `proposed_repairs`，`workflow(op=resume)` 从失败步继续。
- [x] 真实站点联调：高校/政府站点采集、附件下载、权限墙识别、多页并发读取。

### 仍待办

- [ ] **独立 benchmark**：与 Playwright MCP / Stagehand / browser-use 在成功率/误判率/
      token/延迟上对比（当前只有自测）。

## 诚实差距

- 真实站点验证不足：目前真实站点只用 example.com / wikipedia 这类简单站；强风控站点被拦。
- benchmark 的"省 token"是回传字节代理，不是真实 token 计量。
- 跨页并发/性能门槛是本地计时，机器负载会影响抖动。
- 模板社区未启动（见下）——格式已冻结，但生态尚未验证。

## 关于模板社区

**结论：暂不启动，先铺路。** 理由与铺垫：

- 格式刚冻结（schema v2），且失败/重试/diff 语义近期仍在动，此时开社区会让贡献者建在移动靶上。
- 模板天然脆（反爬、DOM 漂移），在"失效检测/局部修复"未经真实规模验证前，社区会攒死模板。
- 还没有 5 分钟安装/发布渠道（Docker/README 刚补），贡献无处落地。
- 先做：冻结格式契约 + 模板校验入口（`workflow(op=validate)`）+ 贡献指南（已写）。
  CI 目前只跑测试、打包与联调冒烟，提交模板的自动校验尚未实现；
  待真实模板验证跑通、发布渠道就绪，再开模板仓库征集。
