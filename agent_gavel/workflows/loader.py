"""工作流模板存取（docs/tech-plan.md §5.15.5）。

双层目录：用户层 ~/.config/agent-gavel/workflows/（可写、优先）+ 包内层。
同 template_id 再存 = version+1，stats 写进模板本体。旧 schema 只读迁移。
"""

import datetime
import json
import os
import re

from ..runtime.errors import GavelError
from . import schema as _schema

PKG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _config_root():
    if os.name == "nt":
        return os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))


# 目录隔离别动 XDG_CONFIG_HOME——实测把它指到空目录会让 Chrome 后续外网导航
# net::ERR_NETWORK_CHANGED / 超时（Chrome 会读该变量）。专用覆盖变量更安全。
USER_DIR = (os.environ.get("AGENT_GAVEL_WORKFLOWS_DIR")
            or os.path.join(_config_root(), "agent-gavel", "workflows"))


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def _is_safe_id(name) -> bool:
    """template_id 只允许小写字母/数字/下划线，防止拼进路径时穿越。"""
    return isinstance(name, str) and bool(_ID_RE.match(name))


def _require_safe_id(name):
    if not _is_safe_id(name):
        raise GavelError("bad_template_id", f"非法 template_id：{name!r}",
                         hint="只允许小写字母/数字/下划线，如 bing_search")
    return name


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def ensure_dirs():
    os.makedirs(USER_DIR, exist_ok=True)


def _dirs():
    return [d for d in (USER_DIR, PKG_DIR) if os.path.isdir(d)]


def _path_for(template_id, directory=USER_DIR):
    return os.path.join(directory, f"{template_id}.json")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def migrate_old(old):
    """旧 schema（action 字符串 + selectors + expect）→ 新 schema。"""
    if old.get("schema_version") == _schema.SCHEMA_VERSION:
        return old
    steps = []
    for i, st in enumerate(old.get("steps", [])):
        sels = st.get("selectors", {}) or {}
        action = st.get("action")
        act = {"type": action}
        if "input" in sels:
            act["target"] = {"by": "css", "value": sels["input"]}
        if "target" in sels:
            act["target"] = {"by": "css", "value": sels["target"]}
        if "value" in sels:
            act["value"] = sels["value"]
        if "keys" in sels:
            act["keys"] = sels["keys"]
        if action == "navigate":
            act["url"] = st.get("url") or sels.get("url")
        if action == "press_enter":
            act = {"type": "press", "keys": "Enter",
                   "target": act.get("target")}
        step = {"id": f"step{i + 1}", "action": act}
        if st.get("expected_feature"):
            step["checkpoint"] = {"checks": st["expected_feature"]}
        steps.append(step)
    t = {"schema_version": _schema.SCHEMA_VERSION,
         "template_id": slugify(old.get("site", "legacy")) + "_migrated",
         "version": 1, "site": old.get("site", ""), "desc": old.get("desc", ""),
         "parameters": old.get("params") or {}, "steps": steps}
    return _schema.validate(t)[0]


def save(template):
    """保存模板到用户层；同 id 则 version+1 并保留 stats。返回 info。"""
    ensure_dirs()
    tid = _require_safe_id(template["template_id"])
    path = _path_for(tid)
    prev = None
    if os.path.isfile(path):
        try:
            prev = _read(path)
        except Exception:
            prev = None
    if prev:
        template["version"] = int(prev.get("version", 1)) + 1
        # stats 由运行时管理：覆盖保存时以已存在的 stats 为准，
        # 避免"修复后重发"把 record_run 刚写的统计冲回旧值。
        if prev.get("stats"):
            template["stats"] = prev["stats"]
    else:
        template.setdefault("version", 1)
        template.setdefault("stats", {"success_count": 0, "fail_count": 0,
                                      "last_status": None, "last_at": None})
    template.setdefault("saved_at",
                        datetime.datetime.now().isoformat(timespec="seconds"))
    _write(path, template)
    return {"file": f"{tid}.json", "template_id": tid,
            "version": template["version"], "steps": len(template["steps"]),
            "parameters": template.get("parameters", {})}


def get(name_or_id):
    """按 template_id 或 site 加载（用户层优先）。返回 (template, path)。"""
    ensure_dirs()
    if _is_safe_id(name_or_id):
        for d in _dirs():
            p = _path_for(name_or_id, d)
            if os.path.isfile(p):
                return _read(p), p
    # 按 site 匹配（name_or_id 不合法时也走到这里，不拼路径）
    for d in _dirs():
        for f in sorted(os.listdir(d)):
            if f.endswith(".json") and f != "stats.json":
                t = _read(os.path.join(d, f))
                if t.get("site") == name_or_id:
                    return t, os.path.join(d, f)
    raise GavelError("workflow_not_found", f"未知 workflow：{name_or_id}",
                     hint="用 workflow_list 查看")


def list_workflows():
    ensure_dirs()
    seen = {}
    for d in _dirs():
        for f in sorted(os.listdir(d)):
            if not f.endswith(".json") or f == "stats.json" or f in seen:
                continue
            try:
                t = _read(os.path.join(d, f))
            except Exception:
                continue
            stats = t.get("stats") or {}
            seen[f] = {
                "file": f, "template_id": t.get("template_id"),
                "site": t.get("site"), "desc": t.get("desc"),
                "version": t.get("version"),
                "steps": len(t.get("steps", [])),
                "parameters": t.get("parameters", {}),
                "success_count": stats.get("success_count", 0),
                "fail_count": stats.get("fail_count", 0),
                "last_status": stats.get("last_status"),
                "layer": "user" if d == USER_DIR else "package",
            }
    return sorted(seen.values(), key=lambda x: x["file"])


def publish(template_id, out_dir=None):
    """导出干净 JSON 供 PR（不改远端）。"""
    t, path = get(template_id)
    t = dict(t)
    t.pop("saved_at", None)
    tid = _require_safe_id(t.get("template_id"))
    if out_dir:
        dest = os.path.join(out_dir, f"{tid}.json")
        _write(dest, t)
        return {"template_id": tid, "path": dest}
    return {"template_id": tid, "template": t}


def record_run(template_id, status):
    """更新模板 stats（写回文件；用户层优先，包内层不可写则跳过）。"""
    try:
        t, path = get(template_id)
    except GavelError:
        return None
    if path.startswith(PKG_DIR):
        return None
    stats = t.setdefault("stats", {"success_count": 0, "fail_count": 0,
                                   "last_status": None, "last_at": None})
    if status == "pass":
        stats["success_count"] = stats.get("success_count", 0) + 1
    else:
        stats["fail_count"] = stats.get("fail_count", 0) + 1
    stats["last_status"] = status
    stats["last_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _write(path, t)
    return {"template_id": template_id, **stats}


def stats():
    return {w["template_id"]: {"success_count": w["success_count"],
                               "fail_count": w["fail_count"],
                               "last_status": w["last_status"],
                               "version": w["version"]}
            for w in list_workflows()}
