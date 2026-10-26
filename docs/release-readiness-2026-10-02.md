# 发布准备审计与模板社区起步建议

日期：2026-10-02  
审计提交：5fd082b（前一修复提交 b97fe55）  
范围：本地 Git、源码、现有测试、发布材料；未发布、未修改业务源码。  
结论：已具备开发者预览的技术基础，但不能仅凭 M1–M4 标记和测试通过宣布稳定版。应先修正模板核心语义，再开展小范围外测，最后集中宣发。

## 1. 验证与证据范围

- 在独立 AGENT_GAVEL_DATA_DIR、AGENT_GAVEL_ARTIFACT_DIR 下执行完整 pytest。
- 结果：74 passed，64.04 秒。
- 日志：/tmp/agent-gavel-release-audit-Piwl8d/pytest.log（临时目录，可能被系统清理）。
- 本地基准：冷启动 369.9ms；热调用 1.497ms；click p95 33.670ms；fill p95 5.521ms；explore p95 12.987ms。
- 此次另外使用独立临时目录和真实无头浏览器复现 workflow 边界问题。
- 未运行新的跨平台测试、干净机器安装测试或真实用户评测。
- DeepSeek Broker 本次工作区访问被拒绝，没有启动新审计任务；以下结论为主代理直接审阅和验证。

## 2. 前次反馈修复状态

b97fe55 已增加：前置条件失败阻断、artifact 导出文件名净化、默认隐藏导出路径、修复/重试检查副作用标志、context 级域名 route、异步 reset_runtime 清理。相关现有测试此次通过。

边界：
- 新旧浏览器仍是两套实现，M5 兼容层待做，当前仅有部分提示。
- “有副作用标志时不重试”已经修复；“标志的默认判定是否可靠”仍有缺口。
- route 存在不等于已经证明 popup/redirect/service worker 的所有网络边界，现有安全测试主要验证显式导航。

## 3. 发布前必须修的模板闭环问题

### 3.1 独立 expect 失败被汇总为成功（真实浏览器复现）

位置：agent_gavel/workflows/runner.py:219、267。

调用 expect(title == WRONG)，页面标题 Actual：
- execution.status = fail
- step.status = ok
- workflow.status = pass

_dispatch 返回的业务失败未被 _run_one 接纳。修复验收：任何断言失败都必须传播；后续副作用步骤不得执行；不能只测有 checkpoint 的动作。

### 3.2 录制断言丢失，编译会改变操作语义（源码及编译复现）

位置：agent_gavel/tools/common.py:59、agent_gavel/workflows/compiler.py:28、75。

- 工具在断言评估前记录动作，且未把 assertions 传为 checkpoint。
- 编译器即使收到 checkpoint，也未保留原有 checkpoint。
- hover/focus 无条件删除，悬停展开菜单等流程可能失效。
- 连续 type 合成 fill：已有内容或光标非末尾时，追加与覆盖并不等价。

验收：同一初始状态下，探索记录和模板重放具有相同业务结果；原断言保留；失败动作不能被自动标为已验证模板。优化必须证明等价，否则保留原步骤。

### 3.3 Workflow 与原子工具存在参数/对象契约不一致（真实浏览器复现）

位置：agent_gavel/workflows/runner.py:273、289；agent_gavel/backends/playwright_backend.py:273、479。

- workflow type(text="hello") 返回 pass，但输入框仍为空：backend 从 value 读取，runner 传 text。
- workflow screenshot 返回 internal，错误为 PageHandle 没有 screenshot：backend 需要原生 Page。
- 要对每个声明支持的 workflow 动作做契约测试，不能用原子工具测试替代。

### 3.4 Workflow ID 可越过模板目录写文件（临时目录复现）

位置：agent_gavel/workflows/loader.py:42、92；schema.validate 未限制 template_id。

template_id="../escaped" 校验通过，保存后 JSON 位于模板目录上级。Artifact 导出修复没有覆盖这条独立写文件路径。

验收：限制 ID 字符集；拒绝绝对路径和路径片段；最终路径 containment 校验；加载、保存和导出使用相同规则。公开接收社区模板前必须修复。

### 3.5 安全标志默认过于乐观（schema 复现）

位置：agent_gavel/workflows/schema.py:166。

click(target="#btn") 默认 idempotent=true、safe_to_retry=true。按按钮名称关键词推断不能保证没有副作用；type/press 也不能普遍视为幂等。

验收：未知点击和按键默认不重放；由明确动作语义或模板作者声明放行；“需要人工批准”与“是否允许自动重试”分开。不要因此引入每一步都确认的体验。

### 3.6 参数与敏感数据契约未闭环

位置：agent_gavel/workflows/schema.py:28；runner.py:72。

已复现：substitute("$QUERY_ID", {"QUERY":"hello","QUERY_ID":"42"}) 得到 hello_ID。

源码另显示：参数声明的 required/type 没有完整执行前验证；sensitive 标记存在于 schema/compiler，但 runner 未消费该标记以完成返回/错误证据脱敏。

验收：精确占位符替换；缺参在任何动作前报错；声明类型可验证；以虚构秘密测试成功、失败、full 返回与导出模板均不泄露。

## 4. 发布承诺与当前实现的边界

- 暂停/恢复主要是运行中步边界 Event 控制；blocked 或完成时 run 被移除，自建 session 被关闭。不能宣称已经具备“返回 blocked 后，用户下次说继续即可恢复”，更不能宣称断电恢复。
- 录制记录只按 session 筛选，不保留完整 page/context 路由；runner 固定解析第一页。原子多 page 可用不等于多 page workflow 可以正确重放。
- runner 根据整个 artifact store 前后差集收集产物。并行任务可能混入彼此产物；下载后台捕获也需要等待完成并归属于 run。
- 默认 summary 不带 steps，而 workflow_run 从 result.steps 提取修复记录来发布新版本，默认成功路径可能不持久化修复。
- main.py 信号处理启动 daemon 清理线程后立即 os._exit，且清理跨事件循环；需验证 EOF、SIGTERM、客户端断开时确实清理浏览器。
- 动作与断言目前不是全部包在同一个 page actor 操作内；session busy 也需要覆盖外部原子工具调用，才能宣称执行期间不被打断。

以上部分为源码审查发现，未在本轮逐项做完整端到端复现。可选择修复，或明确从首个预览版支持范围中排除；不能默认宣传已完成。

## 5. 工程发布准备

必须具备：
1. 新用户指南：安装 → 创建 session → 探索 → 带断言操作 → 录制/保存 → 换参数重放 → 导出结果。
2. 明确新旧 API 的浏览器隔离；预览版可以独立保留 legacy，但默认路径只能有一条。
3. 更新版本号、CHANGELOG、支持平台和已知限制。当前仍为 0.2.3。
4. GitHub Actions：确定性本地用例、真实 MCP stdio 协议用例、wheel/sdist 构建检查。
5. 干净环境从 wheel 安装并运行；Chrome 存在/不存在时给出可执行指引。
6. sdist 包含 tests/webapp、tests/fixtures、atomic_coverage.json。当前 pyproject 只包含 tests/*.py。
7. 测试产物目录完全隔离；conftest 目前只专门隔离 workflows，artifact_cleanup 不应触及日常产物。
8. 社区模板规范、问题模板、SECURITY.md 和变更说明；当前 PR 模板仍指向 legacy 格式。
9. 先只承诺通过验证的平台，其余标 experimental。

## 6. 性能宣传需要的证据

已有数据能证明本地执行速度，但不能证明整个 AI 任务节省比例。

当前 benchlib：
- 2 倍并行数据来自 asyncio.sleep(0.3) 的队列演示。
- 1000 次无泄漏测试主要是 noop；RSS 只测 Python 进程。
- “崩溃恢复”是主动关闭后重新启动，不是保留状态的故障恢复。

补充三组测量：
A. 同模型首次逐步探索。
B. 同任务经保存模板重放。
C. 网站局部变化后的修复再重放。

统一记录：正确任务完成率、错误通过率、模型调用次数、输入/输出 token、工具往返、冷/热端到端耗时、修复成本。固定模型、任务、初始状态与版本，公布重复运行结果和失败记录。可补直接 Playwright 脚本作为执行成本基线。

文档中的需求百分比为先前无统计样本的讨论假设，不能引用为市场份额或宣传依据。

## 7. 模板社区现在怎么做

现在开始建设“贡献机制和种子模板”，暂不建设账号、评分、交易或自动远程执行的平台。

### 阶段一：同仓库目录与规范
- 使用新 workflow 格式建立 templates 目录和机器可读索引。
- 每份模板：稳定 ID、schema_version、runtime 兼容范围、作者、许可证、站点、任务目标、参数、登录前提、副作用说明、结果断言、最后验证日期、环境、已知限制。
- 模板发布状态：draft / verified / broken / deprecated，验证范围透明。
- 导入时锁定版本/commit/checksum；更新显式进行。社区模板是可执行自动化，不能当普通文本自动信任。
- 用户自己的模板保留在本地；分享必须参数化并扫描凭据、账号、文件路径和样本数据。
- 支持 PR、失效报告、维护者认领；机器 lint 先自动做，行为审查由维护者做。

### 阶段二：3–5 个经过验证的种子模板
建议覆盖：
1. 参数化站内搜索与结果提取。
2. 公告/政策列表与附件下载。
3. 单站后台报表导出（个人凭据不分享）。
4. popup 打开详情并提取结果。
5. 多站信息汇总作为组合范例。

先完成 3 个稳定案例即可招募外测；不为数量收录不可靠模板。每份至少记录多组参数、多次重放，以及一次故意破坏断言的失败证据。实时游戏不作为首发门槛。

### 阶段三：有贡献之后再扩展
出现持续贡献、失效维护和检索需求后，再做独立模板仓库、静态目录网站、检索标签、自动兼容检查。以模板实际重复使用和维护情况衡量社区活跃度。

## 8. 发布与宣发顺序

### 现在：开发日志 / 定向邀请
可以公开展示已实现内容、录制→重放 demo 和明确限制；邀请少量愿意反馈的用户。不要使用“全能力”“生产级”“副作用绝不重复”等尚未证明的表述。

### 开发者预览版（例如 0.3.0a1）
门槛：第3节阻断项解决；一条干净安装路径；CI；3个新格式模板；支持范围文档；回归用例复现失败后通过。版本示例仅建议，不自动修改版本或发布。

### 公开 beta
门槛：5–10位外部用户能独立完成探索→模板→换参重放；至少三个真实流程多次运行；收集安装失败、模板误判和 token/时间数据。

### 稳定版与集中宣发
门槛：冻结 schema 与 API 兼容政策；版本可回滚；平台矩阵与安全边界验证；端到端 benchmark 可复现；维护和失效报告通道可用。
集中素材：90秒真实演示、README 快速上手、3个完整案例、性能与局限文章、模板贡献入口。

建议核心文案：
“让 AI 把第一次网页探索变成下一次可验证的快捷操作。”

## 9. 当前最值得投入的顺序

1. 修复 workflow 真失败却报成功、录制丢断言、动作契约和模板路径边界。
2. 用统一 action/验证执行路径减少原子工具与 workflow 的语义分叉。
3. 完成三个种子模板和面向新用户的安装/录制/重放指南。
4. 增加发布 CI 与真实任务速度/token 基准。
5. 定向外测，修正体验后再集中宣发。

目标不是继续堆工具数量，而是证明一个陌生用户第二次执行任务确实更快、更省心，且系统不会把未完成的任务说成完成。

