# agent-gavel 开发与测试约定

## 工作原则（最重要）

**陌生环境先探索，再正式执行。** 面对一个没验证过的应用/界面/操作：
1. 先做充分的一步步探索（读树、试原子操作、确认每个前置条件）
2. 确认每个步骤真的可行后，再把它组合成"正式"流程/模板/批量执行

**不要假设能一次跑通。** 曾经踩过的坑（真实案例）：
- 微信删除消息"写死坐标"右键：坐标随消息内容/滚动微变，一次成功一次脱靶
- 计算器 `press_key "+"`：computer-use-linux 不认 `+` 键（需 Shift+= 或 KP 键）
- 计算器 `C` 键：在表达式输入框里把 `c` 当字符打进去，不是清屏
- GNOME 计算器按钮 AT-SPI bounds 全为 0,0（相对坐标缺失），label 是按钮文本非显示区
- 每次遇到新 app，显示区/输入焦点/可点区域都要实测确认，不能凭想当然

## 测试安全红线

- 微信等社交应用：**只用文件传输助手**，绝不碰真实联系人/会话
- 破坏性操作（删除/发送/覆盖）前先确认目标，测试残留要清理

## 性能与优化

- agent-gavel 默认走优化版 computer-use-linux（`COMPUTER_USE_LINUX_BIN` + `AGENT_GAVEL_FAST_APP_FILTER`），见 `third_party/computer-use-linux/README.md`
- `act_and_verify` 只在 `debug=1` 且 ambiguous 时截图；常规调用不触发 portal 截屏

## 代码约定

- 桌面模板存 `desktop_templates/*.json`，支持 `locate` 动态定位（读树算坐标，勿写死）
- 坐标类操作优先动态定位（`locate`），不要硬编码屏幕坐标

## Shell 陷阱（真实踩过）

- **`pkill -f <pattern>` 会杀掉执行它的 bash 自己**：bash 命令行里含该 pattern，
  pkill 匹配自身进程链 → opencode 终端会话卡住直到超时。
  正确写法：`pkill -f "[g]nome-calculator"`（拆字符让模式匹配不到自身）
- 进程名 >15 字符时 `pkill -x` 会拒绝（comm 截断），此时用拆字符的 `-f`

