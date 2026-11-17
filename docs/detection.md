# 浏览器启动、资料持久化与自动化特征

本文按0.6.0源码说明默认行为。减小自动化特征的措施，不等同于真实站点的识别率测试结果。

## 默认启动路径

默认managed模式由runtime/browser_process.py启动系统Chrome，再通过CDP连接；默认有头。
首次page(op=open/navigate)可以惰性启动，不要求模型先launch或创建context。
资料默认落在 `<资料根>/primary/`，资料根由core/config.browser_profiles_root()解析。
不传profile和容器时使用持久default容器，扫码与登录写回磁盘，不需要另做export。

同一资料目录已运行的Chrome优先复用/重连；daemon退出保留浏览器和页面。
browser(op=close)显式关闭进程，给profile只关闭该目录。账号登录是否仍有效由站点决定。

## 源码中已有的措施

- _launch_args()包含 `--disable-blink-features=AutomationControlled`；默认不加 `--disable-gpu`。
- 默认启动参数已移除 `--no-sandbox`、`--disable-dev-shm-usage`、`--keep-alive-for-test`。
  保活依靠保留默认容器标签页；CDP的remote-debugging-port继续保留。
- managed无头启动从浏览器版本构造桌面UA，其他后端路径也处理HeadlessChrome标记。
  primary目录禁止以managed无头模式启动；测试使用独立资料并显式设置隔离测试标记。
- locator(op=click/fill, humanize=true)已向后端传递开关：click先移动鼠标，fill先定位点击再逐字输入。
  默认关闭。type/press虽暴露humanize参数，当前工具入口未传给后端，不能将后端分支视为入口已生效。
- Linux启动环境会在需要时补用户会话DBus地址，并在可用时选择gnome-libsecret，
  用于访问Chrome资料的系统钥匙串；这不是自动刷新凭据的机制。

对应检查包括test_stealth.py、test_single_browser.py和test_daemon_idle.py。
这些检查验证具体指纹属性与生命周期，不衡量淘宝等站点的识别率。

## 资料与隔离

browser(op=profile_copy)复制经授权的本机Chrome资料。strict不包含cookie与站点本地态；
default包含站点状态，full可再包含扩展；各档均剔除密码、自动填充与同步等配置中的隐私项。
源Chrome应关闭以保持一致性；已有目标目录默认不覆盖，需要显式force。
profile_ensure在目标已存在时直接复用，并不是每次任务的必需步骤。

没有ACTIVE或profile_use；名字省略固定使用primary。AGENT_GAVEL_BROWSER_PROFILE已删除。
container=isolated创建独立内存容器并继承所选profile默认容器的cookie；
它不完整复制localStorage/IndexedDB，也不把新登录写回。需要长期分离账号时使用不同profile。

## 配置入口

环境变量与默认路径由core/config.py唯一解析：

| 变量 | 作用 |
|---|---|
| AGENT_GAVEL_DATA_DIR | 覆盖平台用户级数据根，派生daemon、profiles和workflows目录 |
| AGENT_GAVEL_BROWSER_PROFILES | 单独覆盖命名资料目录的根 |
| AGENT_GAVEL_CHROME_USER_DATA | 资料复制的来源，不改变默认启动资料目录 |
| AGENT_GAVEL_HEADLESS | 请求无头模式，仍受primary守卫约束 |
| AGENT_GAVEL_CHROME_BIN | managed模式的浏览器可执行文件 |
| AGENT_GAVEL_CHROME_CHANNEL | Playwright模式的channel；空字符串表示不指定 |
| AGENT_GAVEL_DISABLE_GPU | 显式禁用GPU |
| AGENT_GAVEL_EXTRA_ARGS | 追加以空格分隔的启动参数 |
| AGENT_GAVEL_BROWSER_MODE | managed、attach或playwright |
| AGENT_GAVEL_CDP_ENDPOINT | attach使用的已有浏览器调试端点 |
| AGENT_GAVEL_IDLE_EXIT | 无adapter连接后的daemon空闲退出阈值；0禁用 |

attach只连接经用户授权的已有浏览器，不拥有其进程，也不改变它的启动参数。
managed、attach、playwright不能视为同一组启动指纹。实际站点仍可能因账号、网络、操作频率、
浏览器特征或凭据过期要求登录、验证码或拒绝访问；这些结果须在具体站点与任务上验证。
