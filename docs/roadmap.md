# agent-gavel 状态与后续工作

> 2026-11-17，按0.6.0源码整理。当前接口以生成的[工具参考](tools.md)为准，
> 修改规则以[架构约束](architecture.md)为准。测试文件的存在不等同于本轮执行通过。

## 定位

agent-gavel是验证驱动的浏览器运行时：模型声明动作与预期结果，工具执行、等待并用程序断言判定。
减少往返的机制见[README](../README.md#怎样减少模型往返)，不对未指定任务承诺固定调用轮数。

## 当前结构

```text
MCP客户端 → main/remote（stdio adapter） → 本地socket → daemon
                                                    └─ tools
                                                        ├─ workflows → backend
                                                        └─ backend → runtime → core
```

一个数据根一个daemon；同一profile资料目录一个Chrome。profile对应磁盘身份与进程，
context对应cookie分区和run锁，page对应标签页与动作队列。没有session层。
旧裸CDP通道与AT-SPI桌面通道位于legacy，不打包、不运行。

## 已实现及对应检查

| 能力 | 源码与检查入口 |
|---|---|
| 分组工具、按op校验、结果投影 | tools/catalog.py、tools/results.py；test_schema_compat.py、test_contract_snapshot.py |
| 动作内联断言、三态与等待 | tools/common.py、backends/playwright/assertion.py；test_retry_diff_wait.py |
| 导航带正文、正文带锚点 | tools/page_tools.py、backends/playwright/reader.py；test_navigation_contract.py |
| 动作变化观测、popup与产物 | backends/playwright/observation.py；test_observation_edit.py |
| JSON模板编辑、参数化、重放与失败续跑 | workflows/、tools/workflow_tools.py；test_observation_edit.py、test_workflow_contract.py |
| 默认资料持久化、多profile、context隔离 | runtime/profiles.py、runtime/contexts.py；test_profile_tools.py、test_identity.py |
| 单资料目录复用、daemon退出保留Chrome | runtime/browser_process.py、daemon.py；test_single_browser.py、test_daemon_idle.py |
| 跨adapter资源共享 | remote.py、daemon.py；test_multiprocess.py、e2e_mcp.py |
| 域名与路径策略、下载与副作用约束 | runtime/policies.py、core/actions.py；test_security_fixes.py |

## 后续验证与边界

- 新版本协议与浏览器验收使用tests/e2e_mcp.py，记录真实执行结果；旧报告保留日期，不替代新版验收。
- 独立性能对比尚无可用于通用宣传的结论。若开展，需明确任务、工具版本、模型、机器与成功判据；
  调用轮次、工具执行时间、端到端时间、回传字节和token分别报告。
- 启动参数与登录态持久化机制已实现；真实站点的风控表现仍需具体测量，不能从机制推导通过率。
- 模板依赖站点结构，需要检查点和维护；当前没有模板市场。publish只导出JSON，不联网发布。
- CI常规执行测试与打包；真实站点冒烟仅手动触发且允许失败。提交模板尚无自动验收流水线。
- type/press工具入口的humanize未传给后端；有该参数不代表已有端到端效果。

演示工程和竞品录制不列入当前待办。
