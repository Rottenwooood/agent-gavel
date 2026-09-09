<div align="center">

# agent-gavel

**AI 操作浏览器/桌面，动作发出后由程序断言判成败，省掉一次"把页面喂回模型判断"的往返。**

![License](https://img.shields.io/badge/license-MIT-blue) ![Python](https://img.shields.io/badge/python-3.13-brightgreen)

---

</div>

## 这是什么

agent-gavel 是一个给 AI 用的 MCP server，提供"操作网页 / 桌面 + 程序化验证"。

典型的浏览器 agent 循环是：动作 → 页面快照喂回模型 → 模型判断成败 → 下一步。agent-gavel 改成：

```
AI 声明动作 + "做完后页面应该长什么样" → 执行 → 程序断言 → 返回 pass/fail + 实际值
```

成败由程序判定，模型不用回头"看"一遍页面。省掉每次动作后的一次模型往返（慢 + 费 token + grep容易漏,全量看token花费大）。

**两个通道，可独立安装：**

| 通道 | 干什么 | 需要什么 |
|---|---|---|
| **DOM**（网页，主通道） | 通过 Chrome CDP 操作真实浏览器：填表 / 点击 / 搜索 / 登录，全部 `isTrusted=true` 真实输入 | 本机 Chrome + Python 3.13，纯 Python 依赖 |
| **AT-SPI**（桌面，实验性·停止开发） | 通过无障碍树操作桌面应用 | 额外 `npm i -g computer-use-linux` |

不装 computer-use-linux 也能用 DOM；装了才解锁桌面工具（不装时桌面工具返回 `atspi_unavailable` 的明确提示）。

## Install

**兼容性**：Linux + Python 3.13 验证过，需本机有 Chrome/Chromium。Windows/macOS 未适配（见文末 TODO）。

三种安装方式，按场景选：

```sh
# ① 全局安装（推荐正式用）——命令装进 ~/.local/bin，全局 PATH 可用
uv tool install agent-gavel

# ② 装进当前 Python 环境（项目 venv / conda env）
pip install agent-gavel

# 桌面操作（可选）：不装则 DOM 照常可用，只是桌面工具返回 atspi_unavailable
npm install -g computer-use-linux
```

装好后在 MCP 客户端（见下）里配置`["agent-gavel"]`（方式①②，命令已在 PATH），重启后 `doctor` 工具会报告 DOM / AT-SPI 双通道状态——这就是安装成功的信号。

### 开发者：clone 源码运行

```sh
git clone https://github.com/Rottenwooood/agent-gavel.git
cd agent-gavel
uv sync                    # 装依赖（Python 3.13）
uv run python3 -m agent_gavel.main    # 起 MCP server（等价 ./run-mcp.sh）
uv run python3 tests/browser_manager_smoke.py   # Chrome 生命周期冒烟测试
uv build                   # 本地构建 wheel/sdist
```

源码跑通后，想贡献你验证过的模板 → 见文末"贡献你验证过的模板"。

## 安装后到底发生了什么（流程说明）

### 两种安装方式的区别

**`uv tool install agent-gavel`** —— 全局安装（推荐正式用）：
- uv 把 agent-gavel 装进 `~/.local/share/uv/tools/` 的独立环境
- 可执行命令 `agent-gavel` 链接到 `~/.local/bin/`（已在你的 PATH 里）
- 任何目录都能直接 `agent-gavel` 起 server，类似 `npm i -g`

**`pip install agent-gavel`** —— 装进当前 Python 环境（项目 venv / conda env）：
- `agent_gavel/` 包落到 `<venv>/lib/python3.13/site-packages/`
- 同时生成可执行命令 `agent-gavel`（指向 `agent_gavel.main:main`），在所在环境 PATH 里

三种方式最终效果一致：**启动一个在 stdio 上说话的 MCP server 进程**，等 MCP 客户端连它。区别只在命令装在哪、是否全局可用。

### 怎么接到 opencode

opencode 通过 `command` 数组拉起这个进程，两者用 stdio 通信：

```json
// ~/.config/opencode/opencode.json
{
  "mcp": {
    "agent-gavel": {
      "type": "local",
      "command": ["agent-gavel"],
      "enabled": true
    }
  }
}
```

重启 opencode，工具列表出现 `agent-gavel_dom_*` 系列。**无需手动开 Chrome**——首次 DOM 调用时 server 自动用独立 profile 拉起可见窗口的调试 Chrome，崩溃自动重拉，退出自动清理。

### 模板存在哪

- **随包模板**：安装自带的模板在 `site-packages/agent_gavel/channels/{dom,desktop}/templates/`（只读默认，如百度/必应/豆瓣等 8 个验证过的）
- **你的模板**：`dom_save_template` / `desktop_save_template` 保存到你自己的用户目录 `~/.config/agent-gavel/{templates,desktop_templates}/`——跨 uvx 缓存、跨安装版本持久存在，不会被升级覆盖
- 读取时**用户目录优先**：你保存的同名模板覆盖自带模板；失效检测记录（stats）也存用户目录

## 实测耗时

### DOM 通道（当前各步骤耗时，测试环境：Linux + 本机 Chrome）

| 阶段 | 耗时 | 说明 |
|---|---|---|
| Chrome 冷启动（首次连接） | ~5s | 只在进程没起时一次；热连接 ~30ms |
| `navigate`（跳转等加载） | 150–300ms | 热站，readyState complete 即返，无固定 sleep |
| `set_value`（填输入框+断言） | 20–50ms | 真实输入 |
| `click`（点击+断言） | ~10ms | 单点 |
| `press_key`（按键） | ~5ms | 单键/组合键 |
| `press_enter`（提交+等结果页） | 150–400ms | 含页面真实响应 |
| 单模板（3 步：导航+填+提交） | ~0.5–1s | 视站点加载速度 |

数据来自 `tests/dom_smoke.py` 分段计时与 `tests/dom_smoke_loop.py` 多模板循环压测（5 模板 × 3 轮全过，平均单次 ~0.6s）。慢的是页面真实加载，工具本身动作步都在毫秒级。

### AT-SPI 桌面通道：微信发消息模板

微信发消息模板（4 步：激活窗口→点会话→输入→发送）的耗时构成，实测微信窗口开着时（优化前基准）：

| 步骤 | 动作 | 总耗时 | 其中读树(before+稳定) | 动作本身 |
|---|---|---|---|---|
| step0 | activate_window | 1472ms | 1332ms | 131ms |
| step1 | click 文件传输助手 | 1508ms | 1145ms | 362ms |
| step2 | type(剪贴板) | 1507ms | 1161ms | 346ms |
| step3 | click 发送+断言 | 2533ms | 576ms+稳定 | 354ms |
| **合计** | | **~6.8s** | | |

**耗时大头在读树**：每步做 2 次 `get_app_state`（before + 稳定确认），每次 ~580ms（微信 ~600 节点 × ~10 次 DBus 读）。step3 多一次等消息。

**优化**：给 computer-use-linux 打补丁（`third_party/` 保存）——AT-SPI 连接复用 + doctor 缓存 + `fast_app_filter` 跳过全桌面 pid 反查 + 坐标操作不再触发 portal 截屏；server 层读树次数减半（before 复用 + 确定性动作跳过稳定确认）。桌面 70 节点应用实测单次读树：

| 阶段 | 原版 | 优化后(fast=true) |
|---|---|---|
| doctor_report | ~150ms | ~0ms（缓存） |
| app_filter | ~280ms | ~0ms（按 pid 直选） |
| snapshot_tree | ~145ms | ~140ms |
| **合计/次读树** | **~630ms** | **~220ms**（2.9x） |

端到端对比：`read_state` 620→235ms（2.6x）；`act_and_verify` 单次闭环 ~1.5s→0.65s（2.3x）；**微信发消息模板整条流程 6.8s→4.8s**。树内容与动作结果和原版一致，无回归。

## 功能一览

### DOM（网页）

- **Chrome 自管**：首次调用自动起独立 profile 调试 Chrome（可见窗口，永不用 headless），崩溃自愈，退出清理
- **`dom_step`**：通用单步闭环（动作 + 选择器 + 断言一次调用），动作空间按**输入通道参数化**——键盘 `press_key`（任意键/组合键：Enter/Tab/Ctrl+A/F5…）+ 文本 `type_text`（含中文）+ 鼠标 `click`（左/右/中、单击/双击）+ `hover`/`drag`/`scroll` + 导航 `navigate`；`set_value/press_enter/clear/focus` 作便捷动作保留
- **`dom_explore`**：枚举页面可交互元素，给验证过唯一的锚点（`#id` / `input[name=q]` / `__text__:登录` / 同名按钮用 `__text_nth__:N::`）
- **模板复用**：跑通的流程存 JSON（`templates/`，按网站组织：`site` 纯站名，文件名 `site_功能.json`），换参数直接跑
- **失败诊断**：环境错误（Chrome 没起 / DISPLAY 缺失 / CDP 断）返回 `reason + hint`，不会让 agent 对着一个 "Error executing tool" 猜
- **断言降级重试**：fail 自动换策略（trusted 翻转 → 重新 explore 换锚点 → 滚动 → 切换等待模式）；`strict` 参数关掉降级暴露真实 fail（写模板/排查时用）
- **模板失效检测**：连续失败 ≥3 次标 suspected，再跑返回 warning 建议重新探索；任何一次 pass 清零

### AT-SPI（桌面，实验性·停止开发）

> 曾用于操作桌面应用（`act_and_verify` / `run_operation` / 桌面模板，经 computer-use-linux 无障碍树）。Linux 上性能不佳、微信等闭源应用 A11y 树不完整，已停止开发。工具保留可用但不投入；纯 DOM 用法无需安装 computer-use-linux。

### 验证机制（核心，DOM 在用）

- 断言：`eq / neq / exists / not_exists / contains`（特征用 JS 表达式提取）
- 等待：`poll`（轮询断言）/ `event`（MutationObserver，DOM 一变即醒，适合异步长等待）
- 每动作返回 `cost.phase` 毫秒级拆分（before_read / action / wait / after_verify），性能可观测

## TODO

### 通道状态

- **DOM（网页）**：主通道，维护中。
- **AT-SPI（桌面）**：**实验性，Linux 上性能不佳（读树慢、微信等闭源应用 A11y 树不完整），暂时停止开发**。不装 computer-use-linux 也可用 DOM；桌面工具保留但不再投入。

### 修复（动作空间）

- [ ] **press_key 组合键 bug**：三种拼写行为不一致——`Ctrl+A`/`Control+A` 能识别修饰键但主键 `A` 走 raw_char 分支（`_KEYS` 只收小写字母）；`ctrl+a` 的修饰键完全丢失（`_MODIFIERS` 只认 `Control` 不认 `Ctrl`/小写）。需：修饰键名规范化（大小写/全称 `Control`↔`Ctrl`），主键字母统一转小写查 `_KEYS`，组合按下带修饰位。当前单键特殊键 OK（Enter/Tab/F5 等），普通字符键走 type_text。
- [x] **单键特殊键已确认**：Home 光标归零、双击选词（click count=2）、及其他原子动作（type_text 中文/右键/hover/drag/scroll）实测正常（`tests/dom_atomic_coverage.py` 17 步全过）。

### 平台 / 能力

- [ ] **适配其他平台**：DOM 理论跨平台但只在 Linux 验证过；Windows/macOS 需补 Chrome 自管 + 测试。
- [ ] **完善模板共享机制**：当前模板存本地用户目录，缺少"模板共享/导入"通道（按站点从远端拉模板、版本化、社区模板源）。
- [ ] **登录态模板**：Chrome profile 登录态保留，覆盖真实登录类流程。
- [ ] **多步骤 / 分页 / 滚动模板**：现有模板多是"导航+填+提交"，缺连续点进详情、无限滚动、多 tab。
- [ ] **断言语言增强**：`eq/neq/exists/contains` 之外，补数值比较 / 正则 / 列表断言（结果条数等）。

### 产品化

- [ ] **权限 / 确认机制**：高危操作（提交表单 / 发送）前人工确认。
- [ ] **可观测性**：debug 日志之上，补任务级 trace / 会话重放。

## 贡献你验证过的模板（PR template）

agent-gavel 的模板按网站组织（`site` 纯站名，文件名 `site_功能.json`），随包带 8 个验证过的。你在一台机器上跑通了新网站的流程，**PR 给我**，合并后所有用户随包可用。

仓库内的验证过的模板：百度 / 百度百科 / 必应 / 博客园 / 豆瓣电影 / Google / 菜鸟教程 / 知乎登录填表。
设计说明见 [docs/internal-beta.md](docs/internal-beta.md)，路线见 [docs/roadmap.md](docs/roadmap.md)。

### 模板去哪、怎么存

模板默认读**用户目录**优先（`~/.config/agent-gavel/{templates,desktop_templates}/`），随包模板在 `agent_gavel/channels/{dom,desktop}/templates/`。

贡献模板的路径：源码 clone → 用 `dom_explore` + `dom_step` 现场探索跑通 → `dom_save_template` 存 JSON → 确认稳定后放进仓库对应目录提 PR。

### 模板 PR 检查清单

模板 PR 要能直接合并，请过一遍：

- [ ] 模板 JSON 在 `agent_gavel/channels/dom/templates/<site>_<func>.json`（或 desktop 对应目录）
- [ ] `site`（DOM）/ `app`（desktop）字段是纯站名，不含功能名；文件名 `站名_功能`
- [ ] 真实跑通过（`dom_run_template` / `desktop_run_template` 全 pass），PR 描述里贴结果
- [ ] 步骤含断言（`page_features` + `expected_feature`），不是只发动作不验证
- [ ] 用了稳定锚点（`#id` / `input[name=x]` / `__text__:`），没有写死易变的 CSS 路径
- [ ] 用 `$VAR` 占位符替代可变输入（关键词/账号/密码），**不要写死真实值**——`params` 字段自动从 `$VAR` 提取（`dom_list_templates` 会显示模板需要哪些参数）
- [ ] **密码/token 等敏感参数在模板里显式声明**：`dom_save_template(..., sensitive_params=["PASSWORD"])` → params 存 `{"PASSWORD": {"sensitive": true}}`。声明后运行时传的真实值不落日志、不进返回；不声明则默认不敏感（仅参数名命中 PASSWORD/TOKEN 等关键词时兜底脱敏）
- [ ] 涉及登录/个人数据时：模板文件里用**假凭据占位**（如 `$USERNAME`/`$PASSWORD`，跑的时候才传真实值）；若流程依赖"已登录浏览器"（如删订单），PR 里说明依赖的登录态，别假装模板能独立登录
- [ ] 描述里写清：站点 URL、功能、测过的关键词/参数

模板存疑或失效会走失效检测（连续失败 ≥3 次标 suspected），无需担心一次不完美。

## License

MIT
