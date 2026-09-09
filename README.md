<div align="center">

# agent-gavel

**AI 操作浏览器/桌面，动作发出后由程序断言判成败，省掉一次"把页面喂回模型判断"的往返。**

![License](https://img.shields.io/badge/license-MIT-blue) ![Python](https://img.shields.io/badge/python-3.13-brightgreen)

---

</div>

## 这是什么

agent-gavel 是一个给 AI 用的 MCP server（stdio 模式），提供"操作网页 / 桌面 + 程序化验证"。

典型的浏览器 agent 循环是：动作 → 页面快照喂回模型 → 模型判断成败 → 下一步。agent-gavel 改成：

```
AI 声明动作 + "做完后页面应该长什么样" → 执行 → 程序断言 → 返回 pass/fail + 实际值
```

成败由程序判定，模型不用回头"看"一遍页面。省掉每次动作后的一次模型往返（慢 + 费 token + 靠"看"容易漏）。

**两个通道，可独立安装：**

| 通道 | 干什么 | 需要什么 |
|---|---|---|
| **DOM**（网页，主通道） | 通过 Chrome CDP 操作真实浏览器：填表 / 点击 / 搜索 / 登录，全部 `isTrusted=true` 真实输入 | 本机 Chrome + Python 3.13，纯 Python 依赖 |
| **AT-SPI**（桌面，可选） | 通过无障碍树操作桌面应用 | 额外 `npm i -g computer-use-linux` |

不装 computer-use-linux 也能用 DOM；装了才解锁桌面工具（不装时桌面工具返回 `atspi_unavailable` 的明确提示）。

## Install

**兼容性（诚实说明）**：Linux + Python 3.13 验证过，需本机有 Chrome/Chromium。Windows/macOS 未适配（见文末 TODO）。

三种安装方式，按场景选：

```sh
# ① 一次性运行（不装任何东西，临时缓存即装即跑）——适合"先试试"
uvx agent-gavel

# ② 全局安装（推荐正式用）——命令装进 ~/.local/bin，全局 PATH 可用
uv tool install agent-gavel

# ③ 装进当前 Python 环境（项目 venv / conda env）
pip install agent-gavel

# 桌面操作（可选）：不装则 DOM 照常可用，只是桌面工具返回 atspi_unavailable
npm install -g computer-use-linux
```

装好后在 MCP 客户端（见下）里配置 `["uvx", "agent-gavel"]`（方式①）或 `["agent-gavel"]`（方式②③，命令已在 PATH），重启后 `doctor` 工具会报告 DOM / AT-SPI 双通道状态——这就是安装成功的信号。

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

### 三种安装方式的区别

**`uvx agent-gavel`** —— 一次性运行，不装进任何环境：
- uvx 在 `~/.cache/uv` 建**临时虚拟环境**装包，跑完即弃（缓存留档，下次秒起）
- 适合"先试试"，不污染你的 Python；代价是每次 `uvx` 首次要解析依赖

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
      "command": ["uvx", "agent-gavel"],   // 一次性
      // 或 ["agent-gavel"]  // uv tool install 或 pip install 后(命令已在 PATH)
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

## 实测耗时（优化前后对比）

### DOM 通道：固定等待 → 断言轮询

早期版本每个动作后固定 `sleep 3s` 再断言——`set_value` 这种立即生效的动作也白等 3s。改成动作后直接轮询断言（0.25s 间隔，满足即返）后，模板总耗时降 2.5–5.3x：

| 模板 | 优化前总耗时 | 优化后总耗时 | 提速 |
|---|---|---|---|
| baidu 搜索 | 13.8s | **4.3s** | 3.2x |
| baike 搜索 | 14.1s | **3.2s** | 4.4x |
| bing 搜索 | 13.8s | **3.3s** | 4.2x |
| douban 电影搜索 | 13.7s | **3.3s** | 4.2x |
| zhihu 登录填表 | 16.8s | **3.2s** | 5.3x |
| runoob 站内搜 | 7.6s | **3.0s** | 2.5x |

剩余耗时基本是 `navigate` 单步——**只等页面真实加载**（readyState complete 事件驱动，无固定 sleep；热站加载完成后单步 ~150ms）。动作步本身：`set_value` 5–25ms，`click / enter` 100–1200ms。全部模板 pass，无回归。

### AT-SPI 桌面通道：computer-use-linux 优化补丁

给上游 computer-use-linux 打补丁（`third_party/` 保存）：AT-SPI 连接复用 + doctor 缓存 + `fast_app_filter` 跳过全桌面 pid 反查 + 坐标操作不再触发 portal 截屏。桌面 70 节点应用实测：

| 阶段 | 原版 | 优化后(fast=true) |
|---|---|---|
| doctor_report | ~150ms | ~0ms（缓存） |
| app_filter | ~280ms | ~0ms（按 pid 直选） |
| snapshot_tree | ~145ms | ~140ms |
| **合计/次读树** | **~630ms** | **~220ms**（2.9x） |

agent-gavel 端到端：`read_state` 620→235ms（2.6x）；`act_and_verify` 单次闭环 ~1.5s→0.65s（2.3x）。微信发消息模板整条流程 6.8s→4.8s。树内容与动作结果和原版一致，无回归。

## 功能一览

### DOM（网页）

- **Chrome 自管**：首次调用自动起独立 profile 调试 Chrome（可见窗口，永不用 headless），崩溃自愈，退出清理
- **`dom_step`**：通用单步闭环（动作 + 选择器 + 断言一次调用），`navigate / set_value / click / press_enter / clear / focus`
- **`dom_explore`**：枚举页面可交互元素，给验证过唯一的锚点（`#id` / `input[name=q]` / `__text__:登录` / 同名按钮用 `__text_nth__:N::`）
- **模板复用**：跑通的流程存 JSON（`templates/`，按网站组织：`site` 纯站名，文件名 `site_功能.json`），换参数直接跑
- **失败诊断**：环境错误（Chrome 没起 / DISPLAY 缺失 / CDP 断）返回 `reason + hint`，不会让 agent 对着一个 "Error executing tool" 猜
- **断言降级重试**：fail 自动换策略（trusted 翻转 → 重新 explore 换锚点 → 滚动 → 切换等待模式）；`strict` 参数关掉降级暴露真实 fail（写模板/排查时用）
- **模板失效检测**：连续失败 ≥3 次标 suspected，再跑返回 warning 建议重新探索；任何一次 pass 清零

### AT-SPI（桌面，可选）

- `act_and_verify` / `run_operation` / 桌面模板，经 computer-use-linux 无障碍树操作桌面应用
- 默认走优化补丁版（`bin/computer-use-linux-fast`，连接复用 + fast_app_filter）

### 验证机制（两个通道共用）

- 断言：`eq / neq / exists / not_exists / contains`（特征用 JS 表达式提取）
- 等待：`poll`（轮询断言）/ `event`（MutationObserver，DOM 一变即醒，适合异步长等待）
- 每动作返回 `cost.phase` 毫秒级拆分（before_read / action / wait / after_verify），性能可观测

## TODO

"可用"和"正式发布"之间的差距：

- [ ] **适配 Windows**：AT-SPI 桌面通道是 Linux 无障碍树，Windows 应走 UI Automation 或对应后端；DOM 通道理论上跨平台但只在 Linux 验证过
- [ ] **完善模板共享机制**：当前模板存本地用户目录，缺少"模板共享/导入"通道（如按站点从远端拉模板、版本化、社区模板源）
- [ ] **登录态模板**：Chrome profile 登录态保留，覆盖真实登录类流程
- [ ] **多步骤 / 分页 / 滚动模板**：现有模板多是"导航+填+提交"，缺连续点进详情、无限滚动、多 tab
- [ ] **断言语言增强**：`eq/neq/exists/contains` 之外，补数值比较 / 正则 / 列表断言（结果条数等）
- [ ] **权限 / 确认机制**：高危操作（提交表单 / 发送）前人工确认
- [ ] **可观测性**：debug 日志之上，补任务级 trace / 会话重放

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
- [ ] 涉及登录/个人数据时用假凭据，PR 里说明依赖的登录态
- [ ] 描述里写清：站点 URL、功能、测过的关键词/参数

模板存疑或失效会走失效检测（连续失败 ≥3 次标 suspected），无需担心一次不完美。

## License

MIT
