# agent-gavel 开发路线图

> 定位快照：2026-10，Playwright runtime（M1–M4）已完成并加固，旧通道归档，进入"发布准备"阶段。

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
                 └─ backends/playwright_backend.py（Playwright 驱动系统 Chrome）
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

## 距离"可发布"还差什么（按优先级）

### 阻塞
- [ ] **真实复杂站点验证**：找一个非强风控、需登录的真实后台，跑通并沉淀 2–3 个真实
      模板；验证"第二次明显更快、更省 token"。（淘宝/京东等强风控站点不在承诺范围）
- [ ] **失败可解释端到端**：验证码/元素消失 → 返回 `status/reason/artifact/resume_hint`
      并可 `workflow_resume` 恢复，做成演示。
- [ ] **独立 benchmark**：与 Playwright MCP / Stagehand / browser-use 在成功率/误判率/
      token/延迟上对比（当前只有自测）。

### 非阻塞（发布后）
- [ ] M6 视觉/坐标后端（仅截图 + 坐标，AI 在环；不引入 OCR/CV）。
- [ ] Python/TS SDK、独立 CLI、MCP registry 接入。
- [ ] 模板仓库 + 贡献规范落地（当前只到"本地文件 + Git PR"与 CONTRIBUTING）。

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
- 先做：冻结格式契约 + CI 校验提交模板（`workflow_validate`）+ 贡献指南（已写）。
  待真实模板验证跑通、发布渠道就绪，再开模板仓库征集。
