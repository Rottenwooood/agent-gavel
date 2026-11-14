## 贡献的模板

> 本 PR 贡献一个在真实站点上跑通并验证过的操作模板，不是代码改动。

**站点 / 功能**

<!-- 例：bing 搜索，template_id=bing_search，文件名 bing_search.json -->

**模板文件**

- [ ] `template_id` 为 `站点_功能`，小写、下划线
- [ ] 步骤尽量带 `checkpoint`，不是只发动作
- [ ] 敏感输入用 `$VAR` 占位，并在 `parameters` 里标 `{"sensitive": true}`
- [ ] 侧效应步骤保留 `requires_confirmation=true` / `safe_to_retry=false`
- [ ] 定位用 role/label/placeholder/text/testid，css 只作兜底，无写死坐标

**校验与实测（必填）**

<!-- 贴 workflow(op=validate) 的 valid 结果，以及 workflow(op=run) 的 verbosity="full" 摘要 -->

```json
{"valid": true}
```

```text
steps_completed: 3
status: pass
```

**运行环境**

<!-- 浏览器与系统版本、有头/无头、是否需要登录态或代理 -->

**备注**

<!-- 站点特殊处理、可能失效点、相关 issue -->
