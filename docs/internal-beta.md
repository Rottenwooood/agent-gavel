# agent-gavel MCP 内测说明

一个给 AI 用的**浏览器/桌面操作 MCP**。核心思路一句话：**让 AI 操作网页像操作函数一样——动作发出去，程序立刻告诉它成没成，不用它再自己回头读一遍页面确认。**

## 一、大致原理

### 它解决什么问题

现有浏览器 agent 工具（如 Vercel agent-browser、各家 computer-use）的典型循环是：

```
AI 决定动作 → 执行 → 把整个页面快照返回给 AI → AI 再看一遍 → 判断成功没 → 决定下一步
```

问题：**每次动作后都要把页面喂回给模型判断结果**——慢、费 token、且靠模型"看"容易漏。

agent-gavel 的做法是**声明式验证**：

```
AI 决定动作 + 同时声明"做完后页面应该长什么样" → 执行 → 程序自己断言 → 直接返回 pass/fail
```

成功与否由**程序**判断，不消耗模型判断。AI 只需要在动作里附带一个断言，比如"把这个框填上 opencode，然后验证输入框的值确实等于 opencode"——工具内部执行、等页面反应、断言，一次调用返回结果。

### 两层结构

```
AI（opencode / 任何 MCP 客户端）
  └─ agent-gavel MCP server
       ├─ DOM 通道（网页）  ← 通过 Chrome 的 CDP 调试协议操作真实浏览器
       └─ AT-SPI 通道（桌面）← 通过系统无障碍树操作桌面应用（Windows 上对应 UI Automation）
```

网页操作走 Chrome CDP（真实浏览器，不是 headless 模拟）；验证逻辑（断言、等待、元素定位）都在 server 内本地完成。

### 关键设计：真实输入（trusted）

浏览器里的"填表/点击"有两种做法：

1. **合成事件**：用 JS 直接改 DOM、派发假事件。快，但 `isTrusted=false`——React 这类框架能识别出"不是真人操作"，会忽略或在下一次重渲染时**把你填的值冲掉**。这是很多自动化工具在知乎、SaaS 后台填表失效的根因。
2. **真实输入**：通过 CDP 的 `Input` 通道模拟真实键盘/鼠标（`isTrusted=true`），页面无法区分这是真人还是程序。

agent-gavel **默认用真实输入**。代价是稍慢（毫秒级差异），换来的是 React 受控组件、各类防自动化框架下依然可靠。

### 沉淀复用：模板

AI 现场把陌生站点跑通后，可以把"导航 → 切 tab → 填表 → 提交 → 验证"这整套流程**存成模板**（一个 JSON），以后同类任务直接套模板跑，不用重新探索。模板里的关键词参数化（如 `$QUERY`），换关键词即可复用。

## 二、对比其他类似插件/工具的特性

| 维度 | agent-gavel | 典型浏览器 agent（agent-browser / computer-use 类） |
|---|---|---|
| **验证方式** | **声明式断言**：动作时声明期望，程序判定 pass/fail，一次调用闭环 | 动作后把页面快照返回模型，模型自己判断（多一次往返、费 token） |
| **输入方式** | 默认真实 CDP 输入（isTrusted=true），React/受控组件可靠 | 混合；合成事件对现代框架不稳 |
| **探索** | `dom_explore` 枚举页面可交互元素并给**验证过的稳定锚点**（id→name→文本），含 div/span 实现的 tab/菜单 | snapshot 返回可访问树，靠 refs |
| **复用** | 现场跑通存模板，换参数重跑；无模板的陌生站也能现场探索 | 每次都要重新走一遍 |
| **多端** | DOM(网页) + AT-SPI(桌面) 两通道 | 大多只做浏览器 |
| **速度** | 动作步毫秒级返回（断言轮询，非固定等待）；模板 3 步搜索 ~3-4s（其中 ~3s 是页面加载） | 每步含模型看快照，通常数秒~数十秒 |
| **依赖** | 一个本地 Chrome + Python 3.13，无云端 | 常需云端/付费 |

**一句话差异**：别人是"AI 每步回头读页面确认"，agent-gavel 是"动作时就把验收标准写死，程序替你验收"。后者省掉模型往返，也让失败能被可靠捕获（程序不会看漏）。

## 三、使用流程

### 工具清单

| 工具 | 作用 |
|---|---|
| `dom_navigate` | 导航浏览器到 URL / 已存模板的站点 |
| `dom_explore` | 探索当前页面，列出可交互元素 + 稳定锚点（支持 tag/text_contains/head/tail 过滤） |
| `dom_step` | **核心原语**：执行单个动作 + 断言。动作：`navigate`/`set_value`/`click`/`press_enter`/`clear`/`focus` |
| `dom_save_template` | 把跑通的流程存成模板 |
| `dom_run_template` | 执行已存模板（换参数） |
| `dom_list_templates` | 列出已有模板 |
| `chrome` | 管理调试用 Chrome：`ensure`/`stop`/`status`（T1 起自动，无需手动开） |
| `read_state` / `list_windows` / `act_and_verify` / `run_operation` | 桌面（AT-SPI）通道的操作与验证 |

### 典型会话（对陌生网站搜索）

```
1. dom_navigate   → 打开目标网站
2. dom_explore    → 看页面有哪些可交互元素，找到搜索框
3. dom_step       → 填关键词 + 断言"输入框值=关键词"
4. dom_step       → 回车提交 + 断言"跳转后的 URL/标题含关键词"
5. dom_save_template → 把这 4 步存成模板（关键词参数化）
6. （以后）dom_run_template → 换关键词直接复用
```

### dom_step 一次调用的样子

```json
{
  "action": "set_value",
  "selectors": {"input": "#sb_form_q", "value": "opencode"},
  "page_features": {"v": "document.querySelector('#sb_form_q').value"},
  "expected_feature": {"v": {"op": "eq", "value": "opencode"}}
}
```

含义：往 `#sb_form_q` 填 "opencode"，然后断言这个框的 value 确实等于 "opencode"，`page_features`（JS 表达式提取的页面状态）和 `expected_feature`（断言）由工具内部执行判断，返回 pass/fail + 实际值。

### 锚点

`dom_explore` 返回的 `selector` 都是验证过唯一的：
- `#id` / `input[name=q]`：CSS 选择器
- `__text__:登录`：页内文本唯一的按钮/tab，按文本点
- `__text_nth__:0::刷新`：多个同名按钮，用序号区分

## 四、安装流程

前置：一个能跑 AI agent 的终端（本说明以 opencode 为例，任何支持 MCP 的客户端同理）、Python 3.13+、一个 Chrome/Chromium。

### 1. 拉代码 + 装依赖

```bash
git clone <你的仓库地址> ~/agent-gavel
cd ~/agent-gavel
uv sync   # 需要已装 uv；或 pip install -e .
```

### 2. 启动带调试端口的 Chrome

**（T1 起不需要手动做了）** agent-gavel 自带 Chrome 进程自管理（`browser_manager.py`）：
首次 DOM 调用时自动用独立 profile（`/tmp/agent-gavel-chrome`）拉起调试 Chrome，
崩溃自动重拉，随 MCP server 退出自动清理。也可以手动：

```bash
google-chrome --no-sandbox --disable-gpu \
  --user-data-dir=/tmp/chrome-live \
  --remote-debugging-port=9222 https://www.baidu.com &
```

手动起的 Chrome 会被识别为 `owner=external`，agent-gavel **绝不误杀**；
无 DISPLAY 环境（systemd/cron 等）自动以 `--headless=new` 兜底。

验证：浏览器打开后 `curl http://127.0.0.1:9222/json/version` 有返回即可。

### 3. 注册 MCP server

在你的 MCP 客户端配置里加（opencode 是 `~/.config/opencode/opencode.json`）：

```json
{
  "mcp": {
    "agent-gavel": {
      "type": "local",
      "command": ["/home/<你>/agent-gavel/run-mcp.sh"],
      "enabled": true
    }
  }
}
```

重启客户端，工具列表里应出现 `agent-gavel_dom_*` 系列。

### 4. 快速自检

```bash
curl http://127.0.0.1:9222/json/version   # Chrome CDP 在
# 在 agent 里调用 dom_navigate 打开任意网站，dom_explore 应能列出页面元素
```

### 桌面通道（可选，仅 Linux）

桌面操作（控制非浏览器应用）需要 computer-use-linux 后端，单独安装：

```bash
npm install -g computer-use-linux   # 具体以官方文档为准
```

并保证桌面会话启用了无障碍（AT-SPI）。网页通道不依赖它。

### 常见问题

- **工具报 Not connected / 连不上**：Chrome 没起来或 9222 端口被占，检查第 2 步。
- **改了代码不生效**：MCP server 每次由客户端拉起新进程，改完代码**重启客户端**。
- **填表值被冲掉**：确认在用默认真实输入（不要手动关 trusted）；那是 React 受控组件在拒绝合成事件，真实输入才稳。
