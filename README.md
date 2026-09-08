# agent-gavel

验证驱动的多端操作框架：让 AI 对桌面/浏览器执行动作后，由**程序断言**判定是否成功，
把"执行→验证→反馈"压成一次调用。

- 网页操作：Chrome CDP / DOM（Chrome 进程自管，无需手动开调试口）
- 桌面操作：AT-SPI 无障碍树（computer-use-linux，实验性）
- 核心差异化：预声明断言（省掉动作后的一次模型往返）
- 探索即固化：陌生站点 → explore → 试错 → 模板复用

见 [docs/roadmap.md](docs/roadmap.md) 了解开发位置与后续规划。
