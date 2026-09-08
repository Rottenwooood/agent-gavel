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
- [x] 清理重复模板（bing.json vs bing_search.json 重复）
- [x] 浏览器进程管理：Chrome 自管（browser_manager.py 按需自启/自愈/退出清理，
      不再依赖手动开 CDP；doctor 报告 owner）
- [ ] 断言失败的重试 / 降级策略（现在 fail 即停，缺"换策略重试"）。
      设计：默认自动降级（换 trusted / 备用选择器 / 重新 explore / event↔poll），
      测试/调试用独立字段 `strict`（不用 debug——debug 已用于日志/截图）关掉降级，
      暴露真实 fail。
- [ ] server.py 已混 AT-SPI + DOM 两界，拆模块（桌面保留功能、标实验性）

### 第二层：多操作域联动（不在此阶段自研 agent）
agent-gavel 不自研 agent 循环，现阶段也不集成 agent。仅当需要跨操作域联动
（如手机/微信/网页组合任务、共享操作域信息）时，才接入**别人实现好的大脑**
（pi / opencode 自身 agent），agent-gavel 继续扮演"手"（操作 + 验证）。
- [ ] 手机操作域：自研无障碍服务客户端 + 长连接（设计过，未实现）
- [ ] 消息渠道：微信 / Telegram（操作域之一，非 agent 触发源）
- [ ] 手表：依赖手机网关，最后做

### 第三层：产品化
- [ ] 权限 / 确认机制（工具能操作真实桌面/浏览器 = 高风险，需人审）
- [ ] 模板库管理：按**网站**为单位组织模板（不做跨站统一的"搜索类"模板——
      每个站点的锚点/断言/交互差异大），含失效检测（累计失败率超阈值标记
      疑似失效，提示重新 explore）
- [ ] 可观测性（已有 debug 日志，缺 trace / 会话重放）

## 诚实差距

- 目前是"验证过的 MCP 工具库"，**不是完整产品**——不打算自研 agent 大脑，
  多操作域联动阶段接现成大脑（pi/opencode）
- AT-SPI 桌面侧比 DOM 网页侧粗糙（网页才是真正跑通闭环的，主攻 DOM）
- "声明式验证"是差异化核心，但还没专项打磨（断言语言表达能力 / 失效处理）

## 下一步建议

顺序：先 T2（拆模块）→ 再 T3（断言降级重试，带 strict 关闭开关）→ 再 T4
（模板按网站组织 + 失效检测）。这三项把 DOM 通道从"验证过的工具库"打磨成
"可靠的操作域底座"，之后才谈多端联动接大脑。
