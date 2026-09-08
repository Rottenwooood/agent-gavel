## 贡献的模板

> 本 PR 贡献一个验证过的操作模板，不是代码改动。

**站点 / 功能**

<!-- 例：bing 搜索，site=bing，文件 bing_search.json -->

**模板文件**

- [ ] 位于 `agent_gavel/channels/dom/templates/<site>_<func>.json`（或 desktop 对应目录）
- [ ] `site`（DOM）/ `app`（desktop）是纯站名，文件名 `站名_功能`
- [ ] 步骤含断言（`page_features` + `expected_feature`），非只发动作

**实测结果（必填）**

<!-- 贴 dom_run_template / desktop_run_template 的 pass 输出，说明测过的关键词/参数 -->

```
status: pass
```

**锚点稳定性**

- [ ] 用稳定锚点（`#id` / `input[name=x]` / `__text__:`），无写死易变路径
- [ ] 涉及登录/个人数据时用假凭据

**备注**

<!-- 依赖的登录态、站点特殊处理、可能失效点等 -->
