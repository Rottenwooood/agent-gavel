"""工作流模板存取。

单一目录：用户工作流目录（由 core/config 决定）。
- `resolve_id`：宽松解析（精确 id / site / 唯一后缀匹配），只读入口使用
- `get_exact` / `load`：精确读取
- `save` / `delete` / `record_run`：写入路径，只接受精确 id
"""

import datetime
import json
import os
import re

from ..core import config as gavel_config
from ..core.errors import GavelError

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def user_dir() -> str:
    return str(gavel_config.workflows_dir())


def _is_safe_id(name) -> bool:
    """template_id 只允许小写字母/数字/下划线，防止拼进路径时穿越。"""
    return isinstance(name, str) and bool(_ID_RE.match(name))


def _require_safe_id(name):
    if not _is_safe_id(name):
        raise GavelError(
            "bad_template_id",
            f"非法 template_id：{name!r}",
            hint="只允许小写字母/数字/下划线，如 bing_search",
        )
    return name


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def ensure_dirs():
    os.makedirs(user_dir(), exist_ok=True)


def _path_for(template_id):
    return os.path.join(user_dir(), f"{template_id}.json")


def _history_dir(template_id):
    return os.path.join(user_dir(), "history", template_id)


def _backup(template_id, prev):
    """写入前把旧版本留档；版本历史是"重写动作链"可回退的前提。"""
    if not prev:
        return
    ver = int(prev.get("version", 1))
    directory = _history_dir(template_id)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"v{ver}.json")
    if not os.path.isfile(path):
        _write(path, prev)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _iter_templates():
    directory = user_dir()
    if not os.path.isdir(directory):
        return
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name == "stats.json":
            continue
        try:
            yield _read(os.path.join(directory, name))
        except Exception:
            continue


def resolve_id(name_or_id) -> str:
    """把用户给的名称解析成唯一 template_id；只读入口使用。"""
    if _is_safe_id(name_or_id) and os.path.isfile(_path_for(name_or_id)):
        return name_or_id
    hits: dict = {}
    for t in _iter_templates():
        tid = t.get("template_id")
        if not tid:
            continue
        if (
            tid == name_or_id
            or t.get("site") == name_or_id
            or tid.endswith("_" + str(name_or_id))
            or str(name_or_id) in tid
        ):
            hits.setdefault(tid, True)
    if len(hits) == 1:
        return next(iter(hits))
    if len(hits) > 1:
        raise GavelError(
            "workflow_ambiguous",
            f"{name_or_id} 匹配多个模板：{sorted(hits)}",
            hint="用完整 template_id",
        )
    raise GavelError(
        "workflow_not_found", f"未知 workflow：{name_or_id}", hint="用 workflow(op=list) 查看"
    )


def get_exact(template_id):
    """按精确 id 读取；不存在即报错。返回 (template, path)。"""
    _require_safe_id(template_id)
    path = _path_for(template_id)
    if not os.path.isfile(path):
        raise GavelError(
            "workflow_not_found", f"未知 workflow：{template_id}", hint="用 workflow(op=list) 查看"
        )
    return _read(path), path


def load(name_or_id):
    """按名称解析后精确读取。"""
    return get_exact(resolve_id(name_or_id))


def save(template):
    """保存模板；同 id 则 version+1 并保留 stats。返回 info。"""
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
        _backup(tid, prev)
        template["version"] = int(prev.get("version", 1)) + 1
        # stats 由运行时管理：覆盖保存时以已存在的 stats 为准，
        # 避免"修复后重发"把 record_run 刚写的统计冲回旧值。
        if prev.get("stats"):
            template["stats"] = prev["stats"]
    else:
        template.setdefault("version", 1)
        template.setdefault(
            "stats", {"success_count": 0, "fail_count": 0, "last_status": None, "last_at": None}
        )
    template["saved_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _write(path, template)
    return {
        "file": f"{tid}.json",
        "template_id": tid,
        "version": template["version"],
        "steps": len(template["steps"]),
        "parameters": template.get("parameters", {}),
    }


def list_workflows():
    seen = {}
    for t in _iter_templates():
        tid = t.get("template_id")
        if not tid:
            continue
        stats = t.get("stats") or {}
        seen[tid] = {
            "file": f"{tid}.json",
            "template_id": tid,
            "site": t.get("site"),
            "desc": t.get("desc"),
            "version": t.get("version"),
            "steps": len(t.get("steps", [])),
            "parameters": t.get("parameters", {}),
            "status": t.get("status", "draft"),
            "login_required": bool(t.get("login_required")),
            "author": t.get("author", ""),
            "verified_at": t.get("verified_at"),
            "task": t.get("task", ""),
            "success_count": stats.get("success_count", 0),
            "fail_count": stats.get("fail_count", 0),
            "last_status": stats.get("last_status"),
        }
    return sorted(seen.values(), key=lambda x: x["file"])


def publish(template_id, out_dir=None):
    """导出干净 JSON 供 PR（不改远端）。"""
    t, path = load(template_id)
    t = dict(t)
    t.pop("saved_at", None)
    tid = _require_safe_id(t.get("template_id"))
    if out_dir:
        dest = os.path.join(out_dir, f"{tid}.json")
        _write(dest, t)
        return {"template_id": tid, "path": dest}
    return {"template_id": tid, "template": t}


def delete(template_id):
    """按精确 id 删除；不存在即报错。"""
    _require_safe_id(template_id)
    path = _path_for(template_id)
    if not os.path.isfile(path):
        raise GavelError(
            "workflow_not_found", f"未知 workflow：{template_id}", hint="用 workflow(op=list) 查看"
        )
    os.remove(path)
    return {"template_id": template_id, "deleted": True}


def record_run(template_id, status):
    """更新模板 stats（精确 id；文件不存在则跳过）。"""
    try:
        t, path = get_exact(template_id)
    except GavelError:
        return None
    stats = t.setdefault(
        "stats", {"success_count": 0, "fail_count": 0, "last_status": None, "last_at": None}
    )
    if status == "pass":
        stats["success_count"] = stats.get("success_count", 0) + 1
    else:
        stats["fail_count"] = stats.get("fail_count", 0) + 1
    stats["last_status"] = status
    stats["last_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _write(path, t)
    return {"template_id": template_id, **stats}


def stats():
    return {
        w["template_id"]: {
            "success_count": w["success_count"],
            "fail_count": w["fail_count"],
            "last_status": w["last_status"],
            "version": w["version"],
        }
        for w in list_workflows()
    }
