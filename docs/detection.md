# 反检测调研：为什么 Playwright 版本更容易被风控

> 本机实测，2026-10。结论来自直接对比"Playwright 自管启动"与"手动启动 Chrome +
> connect_over_cdp（重构前的做法）"两种方式下页面能读到的指纹。

## 1. 根因（按影响排序）

| # | 特征 | Playwright 启动（修复前） | 手动 Chrome + CDP（旧版） | 说明 |
|---|---|---|---|---|
| 1 | `navigator.webdriver` | **True** | **False** | 页面第一眼就能判定自动化，最廉价、最致命 |
| 2 | WebGL 渲染器 | `ANGLE ... SwiftShader`（软件渲染） | 真实 GPU（有头时 `AMD Radeon 680M`） | 我们默认加了 `--disable-gpu`，逼成软件渲染 |
| 3 | UA | 无头含 `HeadlessChrome` | `--headless=new` 无此标记 | 无头场景直接暴露 |
| 4 | profile | `--user-data-dir=/tmp/playwright_chromiumdev_profile-*` | 持久真实目录（累积 cookie/历史） | 全新临时 profile = "从没用过的浏览器" |
| 5 | 启动参数 | 一大批 `--disable-*`、`--remote-debugging-pipe`、`--enable-unsafe-swiftshader`、`--disable-extensions` | 少量自定义参数 | 部分是进程可见；部分有 JS 可观测后果 |
| 6 | CDP 附着 | 是（`Runtime.enable`） | 是（同一 CDP） | 两者都有；本机探针未触发该泄漏，需更强工具测 |

`navigator.webdriver` 与 WebGL/UA 这三项是页面**无需特殊技巧即可读到**的，所以是主因。

## 2. 已做的修复

- **关闭 `navigator.webdriver`**：启动参数加 `--disable-blink-features=AutomationControlled`
  （实测 True → False）。
- **不再默认 `--disable-gpu`**：有头时用真实 GPU（实测 WebGL 恢复为 `AMD Radeon 680M`）；
  无 GPU 环境可用 `AGENT_GAVEL_DISABLE_GPU=1` 显式加回。
- **无头 UA 去掉 `HeadlessChrome`**：启动后取一次真实 UA、把 `HeadlessChrome` 换成
  `Chrome`，作为默认 context 的 UA。
- **留出扩展口**：`AGENT_GAVEL_EXTRA_ARGS`（空格分隔）追加自定义启动参数。
- **回归测试**：`tests/test_stealth.py` 断言 `navigator.webdriver` 非 True、UA 无
  `HeadlessChrome`、`window.chrome` 存在。

修复后实测：

```
有头: webdriver=False  headless_ua=False  webgl=AMD Radeon 680M（真实）
无头: webdriver=False  headless_ua=False  webgl=SwiftShader（无 GPU，不可避免）
```

## 3. 仍未解决 / 需要决策

- **全新 profile**：默认共享浏览器是临时上下文，没有历史与 cookie，本身就是可疑信号。
  登录类真实任务建议用 `session_create(mode="persistent", user_data_dir=...)` 复用真实
  profile。是否把"持久真实 profile"设为默认，需要权衡（会牺牲上下文隔离模型的简洁性）。
- **无头 WebGL**：无 GPU 时只能软件渲染，属无头固有特征；对风控敏感的站点应使用**有头**。
- **接管用户自己的浏览器**（旧版最强的一点）：Playwright 的 `connect_over_cdp` **不支持
  新建 context**，与本项目的多 context 模型冲突。要用它，需要一次架构取舍（例如牺牲
  context 隔离，或用浏览器内置的多 profile 机制）。**这是最有效的下一步，但需要先定架构。**
- **CDP `Runtime.enable` 泄漏**：本机探针未触发；需要用 creepjs 之类的真实检测页做
  L4 层度量。
- **真实站点 A/B 度量**：同一站、同一任务，对比"Playwright 启动 / 手动启动+CDP /
  接管用户 Chrome"的识别率。这是计划中的 gym L4 层，尚未搭建。

## 4. 配置速查

| 变量 | 作用 |
|---|---|
| `AGENT_GAVEL_HEADLESS=1` | 无头（更易被检测；风控敏感场景建议有头） |
| `AGENT_GAVEL_CHROME_CHANNEL` | 指定 Chrome 通道；空 = 用自带 chromium |
| `AGENT_GAVEL_DISABLE_GPU=1` | 加回 `--disable-gpu`（软件渲染） |
| `AGENT_GAVEL_EXTRA_ARGS` | 追加自定义启动参数（空格分隔） |
