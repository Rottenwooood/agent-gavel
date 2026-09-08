# 微信模板耗时优化：详细实现方案

## 一、现状与目标

微信发消息模板（wechat.json，4 步）窗口打开时实测 ~6.8s/轮，环节细分：

| 步骤 | 动作 | 总耗时 | before读树 | 动作 | 稳定等待 |
|---|---|---|---|---|---|
| step0 | activate_window | 1472ms | 753ms | 131ms | 579ms |
| step1 | click 文件传输助手 | 1508ms | 581ms | 362ms | 564ms |
| step2 | type(clipboard) | 1507ms | 574ms | 346ms | 587ms |
| step3 | click 发送+断言 | 2533ms | 576ms | 354ms | 1602ms |

**结论：70% 时间花在读树。** 每步实际做 2 次 `get_app_state`（before 一次 + 稳定确认一次），每次 ~580ms。step3 多一次（等消息）。

目标：6.8s → ~4s（读树从 8 次减到 4 次 + 去掉窗口解析歧义）。

## 二、优化项（按收益排序）

### 1. [适配层] read_state 传 pid 代替 app_id（省窗口解析 + 避免歧义）

**现状**：`adapter.py:201` 的 read_state 只传 `app_id`。cul 的 `resolve_window_context` 拿 app_id 去窗口列表匹配，微信有两个窗口（主窗口 wechat.desktop / WeChatAppEx window:431 同名"微信"），匹配歧义；且每次 get_app_state 都做窗口解析。

**改法**：read_state 支持传 `pid` 或 `window_id`，优先使用：
```python
async def read_state(self, app_id=None, pid=None, fast_app_filter=None):
    args = {}
    if pid:
        args["pid"] = pid          # cul 按 pid 精确选 root，跳过窗口解析
    elif app_id:
        args["app_id"] = app_id
    ...
```
server.py 里 act_and_verify 拿 `app_id` 前先调一次 `list_windows` 解析出 pid 缓存，传给 client。pid 路径实测 1.18s vs app_id 全桌面 2.7s，但窗口开时两者都只读微信树，收益主要是**省窗口解析 + 消除歧义**（~50-100ms/次）。

**注意**：pid 会随微信重启变化，需每次会话重新解析一次并缓存。

### 2. [server 层] 稳定确认复用 before 树（每步省 1 次读树）

**现状**：`server.py:247` `wait_until_stable` 传入 `seed_hash=before_norm["signature"]`、`seed_data=before_raw`。但 `wait.py:27` 逻辑是：seed 算 1 次连续，然后**至少再 fetch 1 次**才能连续>=2 返回。也就是说无论树变没变，**稳定路径必读 1 次树**。

**改法**：对"动作后树无变化"的场景（activate_window、move_window 这类确定性动作），**不需要确认**——before 就是稳定态。加参数：
```python
# wait.py 新增
async def wait_until_stable(..., confirm_changed=True):
    # confirm_changed=True(默认)：现行为，必须 fetch 确认
    # confirm_changed=False：若 seed_hash 非空，直接认为稳定，0 次 fetch
```
server.py 里：
```python
confirm = action not in ("activate_window", "move_window", "press_key")
stable, ... = await wait_until_stable(fetch, ..., seed_hash=..., seed_data=...,
                                      confirm_changed=confirm)
```
**收益**：step0（activate）省 579ms。更激进：所有 auto 模式无断言的步骤都用 `confirm_changed=False`（这些动作本就是 fire-and-forget，树变不变都要等下一个动作的 before），把稳定路径的读树全部去掉。**每步省 ~570ms，4 步省 ~2.3s。**

### 3. [server 层] auto 模式的读树次数减半（before 复用）

**现状**：每步读 before（步骤 N 的 after 就是步骤 N+1 的 before 的前态，但代码不共享）。

**改法**：desktop_run_template 里串行跑步骤时，把上一步的 `after_raw` 作为下一步的 `before_raw` 传入 act_and_verify（加 `seed_before` 参数），避免重复读。模板 4 步内省 3 次读树。

**实现**：act_and_verify 增加可选 `before_raw` 参数：
```python
async def act_and_verify(..., before_raw=None):
    if before_raw is None:
        before_raw = await client.read_state(app_id=app_id)
    ...
```
desktop_run_template 循环里透传上一步的 final_data。

### 4. [cul 侧，可选] AT-SPI 节点读取缓存（每次读树 ~580ms → ~200ms）

**现状**：`atspi_tree.rs` 的 `snapshot_tree_inner` 每次 `get_app_state` 都全量抓微信树：
```
BFS 遍历（while let Some((object_ref,...)) = traversal.pop()）：
  ├─ open_accessible(&conn, object_ref)      # 每个节点建 AccessibleProxy
  ├─ children_up_to(&proxy, ...)             # 读子节点 object_ref 列表
  └─ read_node(&proxy, ...)                  # 对每个节点 ~10 次 DBus 读：
       role / name / description / child_count / bounds /
       states / actions / value / text / supports_editable_text
```
微信 ~600 节点 × 10 次 = **~6000 次 DBus 往返**，这就是 ~580ms 的本质。

**核心洞察**：agent-gavel 的 `read_state` 目的不是拿到"绝对新鲜的树"，而是拿来做**前后 diff 判稳定 / 跑断言**（`normalize` + `quick_hash` + `element_appears/disappears`）。微信 UI 树在两次 read_state 之间**绝大部分节点没变**。所以不需要每次全量重读。

**改法（两个层次）**：

**4a. 属性级缓存（简单，改 read_node 路径）**
每个节点由 `object_ref`（AT-SPI 的唯一 ID，如 `"1:4:7"`）+ 属性构成。加一个全局缓存：
```rust
static NODE_CACHE: Mutex<HashMap<String, Arc<AccessibilityNodeMeta>>>; // key: object_ref
```
`snapshot_tree_inner` 遍历时：
1. 先读 `child_count` + 子节点 `object_ref` 列表（这两项几乎每次都会变，必须读）
2. 对每个子节点，查缓存：
   - **命中**：直接用缓存的 role/name/bounds/states...（0 次 DBus）
   - **未命中**：全量 `read_node` 后写入缓存
3. 微信树 600 节点里，真正新增/消失的大多是子列表变化，**已存在节点的属性不变** → 命中率 >90%，DBus 次数从 6000 降到 ~600

这样每次 read_state 只有 `children_up_to`（BFS 骨架）+ 增量节点的 `read_node`。

**难点**：
- **失效**：微信会真实增删元素（消息列表滚动、菜单开合）。处理：子列表 `object_ref` 集合 diff——出现新 ref 就全量读，消失的 ref 从缓存删。属性变化（如按钮 disabled 状态）会漏——用 `child_count` 变化 + 缓存节点数量兜底，或对 bounds 变化敏感的应用跳过缓存。
- **并发**：agent-gavel 单进程内只有一个 client 线程在跑，`NODE_CACHE` 用 Mutex 即可。
- **DBus 断开重连**：AT-SPI 重启（应用崩溃）后 object_ref 失效，需要清缓存——用 connection 的 generation 或 registry 变化事件兜底。

**4b. 骨架级缓存（更深，改 snapshot_tree_inner 结构）**
连子列表都缓存：BFS 时若某节点的子列表 ref 集合没变，就跳过该子树整层遍历，直接把缓存的子树拼进结果。收益更大（连 ~600 次 DBus 都省），但**子树拼接要重建 parent_index 链**，实现复杂，且滚动场景缓存命中率低。建议先做 4a。

**验证**：agent-gavel 里跑 `read_state` 计时，微信窗口开着时应从 ~580ms 降到 ~200ms；确认发送消息后新消息节点能出现（断言仍 pass）。

**注意**：这步是改 Rust + 重编译 cul（`cargo build --release`），改动在 `/tmp/opencode/cul-mod`，打 patch 同步到 `third_party/computer-use-linux/`。工作量最大，放最后。

## 三、实施顺序与预估收益

| 步骤 | 改动 | 文件 | 预估 |
|---|---|---|---|
| 1 | read_state 传 pid | adapter.py / server.py | -0.1s |
| 2 | auto 步骤跳过稳定确认 | wait.py / server.py | **-2.3s** |
| 3 | before 树跨步复用 | server.py | **-1.7s** |
| 4 | cul 节点缓存（可选） | atspi_tree.rs | -2.4s |

**只做 1+2+3：6.8s → ~3s**（微信窗口开时）。

## 四、验证方式

- 微信窗口开着时跑 3 轮 wechat 模板，对比平均总耗时
- 用 server.py 新加的 `cost.phase` 字段看 before/action/wait 三段
- 回归：发送消息必须真实出现（断言 element_appears），删除模板仍 pass

## 五、风险与注意

- pid 缓存：微信重启后 pid 变，需在 act_and_verify 里检测 pid 失效（窗口解析失败则重新解析）
- confirm_changed=False 后，auto 模式的"动作后树突变但动作实际失败"场景会漏检——目前 activate/move 本就是 no_change，风险低
- 不要在 type(发消息) 这类需要确认生效的动作上用 confirm_changed=False
