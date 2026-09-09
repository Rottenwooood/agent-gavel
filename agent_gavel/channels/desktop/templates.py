"""桌面(AT-SPI)模板仓库：把验证过的桌面操作流程固化成 JSON，供复用。

与 DOM 模板(dom_templates.py)对称，但每步是 AT-SPI act_and_verify 语义：
{
  "app": "wechat",                // 标识（文件/查找用）
  "desc": "发消息到文件传输助手",
  "app_id": "wechat.desktop",     // 目标应用（步骤可覆盖）
  "steps": [
    {
      "action": "click",          // click|type|press_key|activate_window|move_window
      "action_args": {...},       // 步骤动作参数（可含 $VAR）
      "assertions": [...],        // 可选：断言（act_and_verify assert 模式）
      "timeout_s": 6.0            // 可选：本步超时
    }
  ]
}
$VAR 占位符在执行时由 params 替换。
"""

import datetime
import json
import os

# 双层模板目录（与 DOM 模板仓库对称）：
#   - PKG_DIR：安装包内模板（随 wheel 提供，默认只读）
#   - USER_DIR：用户模板目录（可写，优先）。保存落这里，跨环境持久。
PKG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _config_root():
    """跨平台用户配置根目录：Windows %APPDATA%；其它 XDG_CONFIG_HOME/~/.config。"""
    if os.name == "nt":
        return os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))


USER_DIR = os.path.join(_config_root(), "agent-gavel", "desktop_templates")


def _ensure_user_dir():
    os.makedirs(USER_DIR, exist_ok=True)


def _dirs():
    dirs = []
    for d in (USER_DIR, PKG_DIR):
        if os.path.isdir(d) and d not in dirs:
            dirs.append(d)
    return dirs


def _slugify(name):
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _find_in_dirs(fname):
    for d in _dirs():
        p = os.path.join(d, fname)
        if os.path.isfile(p):
            return p, d
    return None, None


def list_templates():
    """列出模板（user + pkg 合并，user 覆盖 pkg 同名）。"""
    _ensure_user_dir()
    seen = {}
    for d in _dirs():
        for f in sorted(os.listdir(d)):
            if f.endswith(".json") and f not in seen:
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        t = json.load(fh)
                    seen[f] = {"file": f, "app": t.get("app"),
                               "desc": t.get("desc"), "steps": len(t.get("steps", []))}
                except Exception:
                    pass
    return sorted(seen.values(), key=lambda x: x["file"])


def save_template(app, desc, steps, app_id=None, name=None):
    """保存模板到用户目录，返回 {file, app, steps}。同名(按 name/app)覆盖。

    文件命名 = app_功能（name 给功能部分，不给则 = app）。
    存到 ~/.agent-gavel/desktop_templates/（可写、跨环境持久）。
    """
    _ensure_user_dir()
    if name:
        fname = f"{_slugify(app)}_{_slugify(name)}"
    else:
        fname = _slugify(app)
    path = os.path.join(USER_DIR, f"{fname}.json")
    tmpl = {
        "app": app,
        "desc": desc,
        "app_id": app_id or "",
        "steps": steps,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tmpl, f, ensure_ascii=False, indent=2)
    return {"file": os.path.basename(path), "app": app, "steps": len(steps)}


def load_template(name_or_file):
    """按文件名(带.json)或 app 标识找模板，返回 dict 或 None。user 优先。"""
    for cand in (name_or_file, f"{name_or_file}.json"):
        p, _ = _find_in_dirs(cand)
        if p:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    for d in _dirs():
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    t = json.load(fh)
                if t.get("app") == name_or_file:
                    return t
    return None


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
