# Changelog

## 0.3.0 — Playwright runtime 重建

### 新增（新 runtime，替换旧的裸 CDP 网页通道）

- **资源模型**：session / context / page，稳定 id；ephemeral 共享浏览器、persistent
  持久 profile、clone 从 storage state 起；每页串行队列、跨页并行。
- **基础 DOM**：导航/读取/探索（返回验证过的稳定锚点）/动作/三态断言/截图/PDF。
- **高级能力**：勾选/下拉/拖拽/上传、popup、iframe、dialog（手动/自动）、
  等响应/等加载/等下载/等事件、cookie/请求头/权限、storage state 导出导入。
- **工作流**：录制 → 编译（参数化/合并输入/标记副作用/补检查点/版本）→ 单调用重放；
  暂停/恢复/取消；失效检测（连续失败 ≥3 标 suspected）。
- **产物**：下载自动捕获（sha256/大小/MIME）、PDF/Word/文本抽取、TTL/配额清理、
  白名单导出。
- **诊断**：trace、网络记录、console、页面错误、性能指标。
- **失败恢复（决策 B）**：
  - B1 安全且幂等步骤瞬时失败原样重试一次（副作用步骤绝不重试）；
  - B2 三态用**作用域结构签名 diff** 判定"拿不准"，并只在拿不准时返回 diff 证据；
  - B3 断言等待**事件优先 + 轮询兜底 + 到点 diff 三态**。

### 安全

- 工作流 `preconditions` 失败立即 `blocked`（此前校验结果未被使用）。
- 产物导出：文件名净化 + 目录包含校验（防路径穿越）；默认不返回绝对路径。
- 域名白名单经 context 路由覆盖 popup / 重定向 / window.open。
- 发布加固 `AGENT_GAVEL_DOMAIN_GUARD`：白名单为空也强制限制（空集=全拒）。
- `reset_runtime` 改为异步并先关闭旧实例，防孤儿浏览器进程。

### 变更

- 旧 DOM（裸 CDP）与 Desktop（AT-SPI）通道整体归档到仓库根 `legacy/`：
  **不打包、不注册、不运行**。
- 移除 `websockets` 依赖（旧 CDP 专用）。
- 包 `keywords`/描述更新；版本 0.2.3 → 0.3.0。

### 测试

- `tests/test_security_fixes.py`、`tests/test_retry_diff_wait.py`、
  `tests/test_real_tasks.py`（真实任务级）。
- `tests/bench_tasks.py`：多步真实任务基准（本地站点 + 真实网站），实测
  模板重放 vs 探索：5 次调用/1681B → 1 次调用/192B。

## 0.2.x — 旧裸 CDP 网页通道 / AT-SPI 桌面通道

- 声明式验证闭环、探索即固化、模板库、失效检测等（见 `legacy/`）。
