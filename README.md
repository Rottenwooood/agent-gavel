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

## 安装后到底发生了什么（流程说明）

### 两条安装命令的区别

**`uvx agent-gavel`** —— 临时环境运行，不污染你的 Python：
- uvx 在 `~/.cache/uv` 建一个**临时虚拟环境**，把 agent-gavel 包装进去
- 包代码落在该环境 `site-packages/agent_gavel/`（含 `channels/dom` 网页通道、
  `channels/desktop` 桌面通道，及随包模板 JSON）
- 然后执行包注册的入口命令 `agent-gavel`（指向 `agent_gavel.main:main`）→ 在**标准输入/输出**上启动 MCP server 协议
- 退出后临时环境保留在缓存（下次秒起），不占你的全局 site-packages

**`pip install agent-gavel`** —— 装进当前 Python 环境的 site-packages：
- `agent_gavel/` 包落到 `<venv>/lib/python3.13/site-packages/`
- 同时生成可执行命令 `agent-gavel`（指向 `agent_gavel.main:main`），在你 PATH 里
- 之后随时 `agent-gavel` 就能起 MCP server

两种方式最终效果一致：**启动一个在 stdio 上说话的 MCP server 进程**，等 MCP 客户端连它。

### 怎么接到 opencode

opencode 通过 `command` 数组拉起这个进程，两者用 stdio 通信：

```json
// ~/.config/opencode/opencode.json
{
  "mcp": {
    "agent-gavel": {
      "type": "local",
      "command": ["uvx", "agent-gavel"],   // 或 ["agent-gavel"] 若已 pip install
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

剩余耗时基本是 `navigate` 单步（~3.1s，页面真实加载，省不掉）。动作步本身：`set_value` 5–25ms，`click / enter` 100–1200ms。全部模板 pass，无回归。

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

## 开发

```sh
uv sync                       # 装依赖
uv run python3 tests/browser_manager_smoke.py   # Chrome 生命周期冒烟测试
uv build                      # 构建 wheel/sdist
```

仓库内的验证过的模板：百度 / 百度百科 / 必应 / 博客园 / 豆瓣电影 / Google / 菜鸟教程 / 知乎登录填表。

设计说明见 [docs/internal-beta.md](docs/internal-beta.md)，路线见 [docs/roadmap.md](docs/roadmap.md)。

## License

MIT
