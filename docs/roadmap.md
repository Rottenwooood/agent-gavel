# agent-gavel 开发路线图

> 定位快照：2026-09，核心机制已验证跑通，处于"从技术验证到产品化"的起点。

## 一句话定位

agent-gavel 是一个**验证驱动的多端操作框架**：让 AI 对桌面/浏览器执行动作后，由
**程序断言**（而非再调一次模型）判定是否成功，从而把"执行→验证→反馈"压成一次调用。
核心差异化：**预声明断言**（行业主流只有"diff 返回给模型"和"独立审计"两种，
无人做"声明式断言 + 程序判定 + 省掉一次模型往返"）。

## 已证明成立的东西（护城河）

| 能力 | 状态 | 证据 |
|------|------|------|
| 声明式验证闭环（执行→断言→程序判定→省循环） | ✅ 成熟 | 所有工具带断言；diff/稳定等待/归一化 |
| 探索即固化（陌生站→explore→试错→模板复用） | ✅ 验证 | subagent 零预置独立跑通必应 |
| 桌面操作（AT-SPI / computer-use-linux） | 🟡 基础 | Zotero/终端/Firefox 可读可点 |
| 网页操作（DOM / Chrome CDP） | 🟡 可用 | 百度/豆瓣/菜鸟/必应搜索闭环 |
| 预声明断言（差异化） | ✅ 无人做 | 行业只有 diff-return 和独立审计 |

## 当前架构

```
agent 核心（外部：opencode / subagent）
  └─ agent-gavel MCP server（server.py，~1700 行）
       ├─ AT-SPI 桌面通道（adapter.py）      → 读控件树 / xdotool 注入
       ├─ DOM 网页通道（dom_adapter.py）     → Chrome CDP / JS 操作
       ├─ 验证框架（normalize/diff/wait/catalog）
       ├─ 通用原语：act_and_verify / dom_step
       ├─ 探索固化：dom_explore → dom_save_template → dom_run_template
       └─ 模板库（templates/*.json，纯数据）
```

## 工具清单（MCP）

- `act_and_verify` / `run_operation`：AT-SPI 桌面闭环（带 debug 参数）
- `dom_step`：通用 DOM 单步闭环（任意 action+选择器+断言，不绑站点）
- `dom_navigate` / `dom_explore`：导航 + 锚点探索（head/tail/tag/text 裁剪）
- `dom_save_template` / `dom_run_template` / `dom_list_templates`：流程固化与复用

## 距离完整产品还缺什么（按优先级）

### 第一层：单机收尾（技术债 + 可用性，短期）
- [ ] 清理重复模板（bing.json vs bing_search.json 重复）
- [ ] 浏览器进程管理：Chrome 常驻的启动 / 健康检查 / 崩溃重启（现靠手动开 CDP）
- [ ] 断言失败的重试 / 降级策略（现在 fail 即停，缺"换策略重试"）
- [ ] server.py 已混 AT-SPI + DOM 两界，拆模块

### 第二层：大脑接入（真正"agent"而非工具库，关键一步）
现在 agent-gavel 是一组 MCP 工具，不是 agent——没有自己的目标/规划/记忆循环，
全靠外部驱动。需要：
- [ ] 接 pi 或 opencode 自身 agent 作大脑
- [ ] 用户给目标 → 规划 → 调工具 → 验证 → 记忆沉淀 的完整循环
- [ ] 跨步骤任务编排（多个 dom_step 串成目标，非每次手动）

### 第三层：渠道与端（原始愿景的"多端"）
- [ ] 手机：自研无障碍服务客户端 + 长连接（设计过，未实现）
- [ ] 消息渠道：微信 / Telegram，让 agent 被聊天触发
- [ ] 手表：依赖手机网关，最后做

### 第四层：产品化
- [ ] 权限 / 确认机制（工具能操作真实桌面/浏览器 = 高风险，需人审）
- [ ] 会话记忆 + 模板库管理（跨会话技能沉淀 / 去重 / 失效检测）
- [ ] 可观测性（已有 debug 日志，缺 trace / 会话重放）

## 诚实差距

- 目前是"验证过的 MCP 工具库"，**不是完整产品**——无 agent 大脑、无渠道、无手机
- AT-SPI 桌面侧比 DOM 网页侧粗糙（网页才是真正跑通闭环的）
- "声明式验证"是差异化核心，但还没专项打磨（断言语言表达能力 / 失效处理）

## 下一步建议

最有杠杆的是**第二层——大脑接入**：把验证能力真正变成"给个目标就能自主干活的
agent"，而不是继续扩端。端（手机/微信）可在大脑跑通后按同样的探索-固化模式补。
