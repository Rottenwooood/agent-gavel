"""DOM 模板仓库：把 AI 现场探索并验证过的流程固化成 JSON，供复用。

模板结构：
{
  "site": "taobao",          // 纯网站名(唯一身份)
  "desc": "在淘宝搜索商品",
  "home": "https://www.taobao.com",
  "steps": [                  // 有序步骤
    {
      "action": "set_value",  // navigate|set_value|click|press_enter|focus
      "selectors": {"input": "#q", "value": "$QUERY"},
      "expect": {"input_value": {"op": "eq", "value": "$QUERY"}},
      "feature_exprs": {"input_value": "..."}   // 提取特征用的 JS
    }
  ]
}
$QUERY 等 $VAR 占位符在执行时由 params 替换。

命名（T4 规范）：
  - site 字段 = 纯网站名（bing / zhihu / cnblogs）
  - 文件名 = site_功能.json（bing_search.json / zhihu_login.json），
    一个 site 可对应多个模板文件
  - 失效检测：templates/stats.json 按模板文件记录连续失败；
    连续失败 >= FAIL_THRESHOLD → suspected；任何 pass → 清零；
    覆盖同名模板 → 该模板 stats 清零
"""

import datetime
import json
import os

# 双层模板目录：
#   - PKG_DIR：安装包内模板（随 wheel 提供，默认只读）
#   - USER_DIR：用户模板目录（可写，优先）。用户保存/失效 stats 都落这里，
#     跨 uvx 临时环境、跨安装版本持久存在。
PKG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _config_root():
    """跨平台用户配置根目录：Windows %APPDATA%；其它 XDG_CONFIG_HOME/~/.config。"""
    if os.name == "nt":
        return os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))


USER_DIR = os.path.join(_config_root(), "agent-gavel", "templates")
STATS_FILE = os.path.join(USER_DIR, "stats.json")
FAIL_THRESHOLD = 3  # 连续失败达到该次数 → suspected


def _ensure_user_dir():
    os.makedirs(USER_DIR, exist_ok=True)


def _dirs():
    """读取候选目录（user 优先，pkg 回退），去重保序。"""
    dirs = []
    for d in (USER_DIR, PKG_DIR):
        if os.path.isdir(d) and d not in dirs:
            dirs.append(d)
    return dirs


def _slugify(name):
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ---------------- 失效检测 stats ----------------

def _read_stats():
    _ensure_user_dir()
    if not os.path.exists(STATS_FILE):
        return {}
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_stats(stats):
    _ensure_user_dir()
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def record_run(template_file, status):
    """记录一次模板运行结果。key=模板文件名。

    连续失败达到 FAIL_THRESHOLD → suspected=true；
    任何 pass → 连续失败清零、suspected 清除。
    返回更新后的该模板 stats。
    """
    stats = _read_stats()
    s = stats.get(template_file, {"consecutive_fails": 0, "suspected": False})
    if status == "pass":
        s["consecutive_fails"] = 0
        s["suspected"] = False
    else:
        s["consecutive_fails"] = s.get("consecutive_fails", 0) + 1
        if s["consecutive_fails"] >= FAIL_THRESHOLD:
            s["suspected"] = True
    s["last"] = status
    s["last_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    stats[template_file] = s
    _write_stats(stats)
    return s


def reset_stats(template_file):
    """覆盖同名模板时清零该模板 stats。"""
    stats = _read_stats()
    stats.pop(template_file, None)
    _write_stats(stats)


def get_stats(template_file):
    """读单个模板 stats（无则给初始结构）。"""
    s = _read_stats().get(template_file, {})
    return {
        "consecutive_fails": s.get("consecutive_fails", 0),
        "suspected": s.get("suspected", False),
        "last": s.get("last"),
        "last_at": s.get("last_at"),
    }


def all_stats():
    """返回 {模板文件名: stats}。"""
    return _read_stats()


def list_templates():
    """列出模板（user + pkg 合并，user 覆盖 pkg 同名），附 stats(suspected)。"""
    _ensure_user_dir()
    stats = _read_stats()
    seen = {}
    for d in _dirs():
        for f in sorted(os.listdir(d)):
            if f.endswith(".json") and f != "stats.json" and f not in seen:
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        t = json.load(fh)
                    s = stats.get(f, {})
                    seen[f] = {
                        "file": f,
                        "site": t.get("site"),
                        "desc": t.get("desc"),
                        "steps": len(t.get("steps", [])),
                        "params": norm_params(t.get("params"), t.get("steps")),
                        "consecutive_fails": s.get("consecutive_fails", 0),
                        "suspected": s.get("suspected", False),
                    }
                except Exception:
                    pass
    return sorted(seen.values(), key=lambda x: x["file"])


_SENSITIVE_VAR_HINTS = ("PASSWORD", "PASSWD", "PWD", "TOKEN", "SECRET",
                        "API_KEY", "CREDENTIAL", "AUTH", "COOKIE")


def is_sensitive_var(name):
    """兜底判定 $VAR 是否敏感(密码/token 等)。

    仅对"老模板/未显式声明"生效——新模板应在 params 里显式标 sensitive，
    不依赖关键词猜。关键词命中只是保险。
    """
    up = (name or "").upper()
    return any(h in up for h in _SENSITIVE_VAR_HINTS)


def norm_params(tmpl_params, steps=None):
    """把模板 params 规整成 {参数名: {"sensitive": bool}}。

    兼容两种存储形态：
      - 新: {"PASSWORD": {"sensitive": true}, "QUERY": {}} (或 {"QUERY": false})
      - 旧: ["QUERY", "USERNAME", "PASSWORD"]
    steps 给出时，先补上自动提取但未声明的参数(默认非敏感)。
    敏感标记优先级: 显式声明 > 关键词兜底(仅老数组/无声明时)。
    """
    out = {}
    if isinstance(tmpl_params, dict):
        for name, spec in tmpl_params.items():
            if isinstance(spec, dict):
                out[name] = {"sensitive": bool(spec.get("sensitive"))}
            else:  # {"PASSWORD": true} 或 {"PASSWORD": "sensitive"}
                out[name] = {"sensitive": bool(spec)}
    elif isinstance(tmpl_params, list):
        for name in tmpl_params:
            out[name] = {"sensitive": is_sensitive_var(name)}  # 老数组: 关键词兜底
    # 补自动提取但未声明的(新模板由 save 写入, 一般不会缺; 老模板缺则补)
    if steps:
        for name in _extract_vars(steps):
            if name not in out:
                out[name] = {"sensitive": is_sensitive_var(name)}
    return out


def _extract_vars(steps):
    """从模板 steps 里递归扫出所有 $VAR 占位符名，保序去重。"""
    import re
    found = []

    def rec(x):
        if isinstance(x, dict):
            for v in x.values():
                rec(v)
        elif isinstance(x, list):
            for v in x:
                rec(v)
        elif isinstance(x, str):
            for m in re.finditer(r"\$([A-Z][A-Z0-9_]*)", x):
                if m.group(1) not in found:
                    found.append(m.group(1))

    rec(steps)
    return found


def save_template(site, desc, steps, home=None, name=None, params_spec=None):
    """保存模板到用户目录，返回 {file, site, steps}。同名覆盖。

    T4 命名：site=纯网站名，文件名默认 = site_功能.json。
    name 参数给"功能"部分；不给则文件名 = site.json。
    params_spec: 显式声明参数及敏感标记，如
      {"PASSWORD": {"sensitive": true}} —— 敏感与否写死在模板里，
      关键词兜底(is_sensitive_var)只对未声明的老参数生效。
    自动从 steps 提取 $VAR 并入 params（普通参数默认非敏感）。
    存到 ~/.agent-gavel/templates/（可写、跨环境持久）。
    """
    _ensure_user_dir()
    if name:
        fname = f"{_slugify(site)}_{_slugify(name)}"
    else:
        fname = _slugify(site)
    path = os.path.join(USER_DIR, f"{fname}.json")
    auto = _extract_vars(steps)
    # 构建 params 对象：自动提取的全列上；显式 spec 覆盖标记
    params_obj = {}
    for p in auto:
        params_obj[p] = {}
    for p, spec in (params_spec or {}).items():
        if isinstance(spec, dict):
            params_obj[p] = {"sensitive": bool(spec.get("sensitive"))}
        else:
            params_obj[p] = {"sensitive": bool(spec)}
    tmpl = {
        "site": _slugify(site) or site,
        "desc": desc,
        "home": home or "",
        "params": params_obj,
        "steps": steps,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tmpl, f, ensure_ascii=False, indent=2)
    # 覆盖同名 → 清零该模板 stats
    reset_stats(os.path.basename(path))
    return {"file": os.path.basename(path), "site": tmpl["site"],
            "steps": len(steps), "params": params_obj}


def _find_in_dirs(fname):
    """在 user→pkg 目录里找确切文件名，返回 (路径, 目录) 或 (None, None)。"""
    for d in _dirs():
        p = os.path.join(d, fname)
        if os.path.isfile(p):
            return p, d
    return None, None


def load_template(name_or_file):
    """按文件名(带.json)或 site 标识找模板，返回 dict 或 None。

    T4 后 site=纯网站名，一个 site 可能多个模板文件——按 site 匹配时
    优先取"文件名 = site"的模板；没有则取该 site 的任意一个。
    user 目录优先，pkg 回退。
    """
    # 直接文件名（含 .json 或去掉扩展名）
    for cand in (name_or_file, f"{name_or_file}.json"):
        p, _ = _find_in_dirs(cand)
        if p:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    # site 名：文件名=site.json
    p, _ = _find_in_dirs(f"{name_or_file}.json")
    if p:
        with open(p, encoding="utf-8") as f:
            t = json.load(f)
        if t.get("site") == name_or_file:
            return t
    # site 匹配：任意一个
    for d in _dirs():
        for f in sorted(os.listdir(d)):
            if f.endswith(".json") and f != "stats.json":
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    t = json.load(fh)
                if t.get("site") == name_or_file:
                    return t
    return None


def load_template_file(template_file):
    """按确切模板文件名加载（供 stats 记录用）。user 优先。"""
    p, _ = _find_in_dirs(template_file)
    if p:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def resolve_template(name_or_site):
    """按文件名或 site 找到模板，返回 (文件名, 模板) 或 (None, None)。

    与 load_template 同逻辑，但返回确切文件名（stats 按文件名记录）。
    """
    # 直接文件名
    for cand in (name_or_site, f"{name_or_site}.json"):
        p, _ = _find_in_dirs(cand)
        if p:
            with open(p, encoding="utf-8") as f:
                return os.path.basename(p), json.load(f)
    # site 名：文件名=site.json
    p, _ = _find_in_dirs(f"{name_or_site}.json")
    if p:
        with open(p, encoding="utf-8") as f:
            t = json.load(f)
        if t.get("site") == name_or_site:
            return os.path.basename(p), t
    # site 匹配：任意一个
    for d in _dirs():
        for f in sorted(os.listdir(d)):
            if f.endswith(".json") and f != "stats.json":
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    t = json.load(fh)
                if t.get("site") == name_or_site:
                    return f, t
    return None, None


def _fill(template, params):
    """把模板里的 $VAR 占位符替换成 params 的实际值。"""
    import copy
    params = params or {}

    def rec(x):
        if isinstance(x, dict):
            return {k: rec(v) for k, v in x.items()}
        if isinstance(x, list):
            return [rec(v) for v in x]
        if isinstance(x, str):
            out = x
            for k, v in params.items():
                out = out.replace(f"${k}", str(v))
            return out
        return x

    return rec(template)
