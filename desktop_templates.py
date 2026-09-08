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

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop_templates")


def _ensure_dir():
    os.makedirs(TEMPLATE_DIR, exist_ok=True)


def _slugify(name):
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def list_templates():
    _ensure_dir()
    out = []
    for f in sorted(os.listdir(TEMPLATE_DIR)):
        if f.endswith(".json"):
            try:
                with open(os.path.join(TEMPLATE_DIR, f), encoding="utf-8") as fh:
                    t = json.load(fh)
                out.append({"file": f, "app": t.get("app"),
                            "desc": t.get("desc"), "steps": len(t.get("steps", []))})
            except Exception:
                pass
    return out


def save_template(app, desc, steps, app_id=None, name=None):
    """保存模板，返回 {file, app, steps}。同名(按 name/app)覆盖。"""
    _ensure_dir()
    fname = (name and _slugify(name)) or _slugify(app)
    path = os.path.join(TEMPLATE_DIR, f"{fname}.json")
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
    """按文件名(带.json)或 app 标识找模板，返回 dict 或 None。"""
    _ensure_dir()
    p = os.path.join(TEMPLATE_DIR, name_or_file)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    for f in os.listdir(TEMPLATE_DIR):
        if f.endswith(".json"):
            with open(os.path.join(TEMPLATE_DIR, f), encoding="utf-8") as fh:
                t = json.load(fh)
            if t.get("app") == name_or_file:
                return t
    for f in os.listdir(TEMPLATE_DIR):
        if f == f"{name_or_file}.json":
            with open(os.path.join(TEMPLATE_DIR, f), encoding="utf-8") as fh:
                return json.load(fh)
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
