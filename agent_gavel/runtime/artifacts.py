"""产物存储（docs/tech-plan.md §5.9）。

下载/截图/trace 等产物统一存到 artifact 目录，用 id 引用；模型拿不到任意
绝对路径，要落盘必须走 export（受文件白名单约束）。
M1 提供基础存储与清理；下载捕获、MIME/文本抽取在 M3 补全。
"""

import hashlib
import json
import os
import shutil
import tempfile
import time

from .errors import GavelError

_DATA_ROOT = os.environ.get(
    "AGENT_GAVEL_DATA_DIR",
    os.path.join(tempfile.gettempdir(), "agent-gavel"),
)
ARTIFACT_DIR = os.environ.get(
    "AGENT_GAVEL_ARTIFACT_DIR", os.path.join(_DATA_ROOT, "artifacts"))


def _now():
    return time.time()


class ArtifactStore:
    def __init__(self, root: str = ARTIFACT_DIR):
        self.root = root
        self._index_path = os.path.join(root, "index.json")
        self._index = None

    # ---- index ----
    def _load(self):
        if self._index is not None:
            return self._index
        os.makedirs(self.root, exist_ok=True)
        try:
            with open(self._index_path, encoding="utf-8") as f:
                self._index = json.load(f)
        except Exception:
            self._index = {}
        return self._index

    def _save(self):
        os.makedirs(self.root, exist_ok=True)
        tmp = self._index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._index or {}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._index_path)

    def _next_id(self):
        idx = self._load()
        n = len(idx) + 1
        while f"artifact_{n:03d}" in idx:
            n += 1
        return f"artifact_{n:03d}"

    # ---- 写入 ----
    def save_bytes(self, data: bytes, *, filename=None, kind="file",
                   mime_type=None, source_url=None, session_id=None,
                   page_id=None, meta=None) -> dict:
        aid = self._next_id()
        dest = os.path.join(self.root, aid)
        with open(dest, "wb") as f:
            f.write(data)
        info = {
            "artifact_id": aid,
            "kind": kind,
            "suggested_filename": filename or aid,
            "mime_type": mime_type,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "source_url": source_url,
            "session_id": session_id,
            "page_id": page_id,
            "path": dest,
            "created_at": _now(),
        }
        if meta:
            info.update(meta)
        self._load()[aid] = info
        self._save()
        return dict(info)

    def register_file(self, src_path: str, *, filename=None, kind="file",
                      move=True, **kw) -> dict:
        if not os.path.isfile(src_path):
            raise GavelError("artifact_missing", f"文件不存在：{src_path}")
        aid = self._next_id()
        dest = os.path.join(self.root, aid)
        if move:
            shutil.move(src_path, dest)
        else:
            shutil.copy2(src_path, dest)
        size = os.path.getsize(dest)
        h = hashlib.sha256()
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        info = {
            "artifact_id": aid,
            "kind": kind,
            "suggested_filename": filename or os.path.basename(src_path) or aid,
            "mime_type": kw.pop("mime_type", None),
            "size_bytes": size,
            "sha256": h.hexdigest(),
            "path": dest,
            "created_at": _now(),
        }
        info.update(kw)
        self._load()[aid] = info
        self._save()
        return dict(info)

    # ---- 读 ----
    def get(self, artifact_id: str) -> dict:
        info = self._load().get(artifact_id)
        if not info:
            raise GavelError("artifact_not_found", f"未知 artifact：{artifact_id}")
        return dict(info)

    def list(self, *, kind=None, run_id=None, session_id=None) -> list:
        out = []
        for info in self._load().values():
            if kind and info.get("kind") != kind:
                continue
            if run_id and info.get("run_id") != run_id:
                continue
            if session_id and info.get("session_id") != session_id:
                continue
            out.append(dict(info))
        return sorted(out, key=lambda x: x.get("created_at", 0))

    def export(self, artifact_id: str, dest_dir: str, filename=None,
               *, policies=None) -> dict:
        info = self.get(artifact_id)
        if policies is not None:
            policies.check_local_path(dest_dir, purpose="导出")
        os.makedirs(dest_dir, exist_ok=True)
        name = filename or info.get("suggested_filename") or artifact_id
        dest = os.path.join(dest_dir, name)
        base, ext = os.path.splitext(dest)
        i = 1
        while os.path.exists(dest):
            dest = f"{base}_{i}{ext}"
            i += 1
        shutil.copy2(info["path"], dest)
        return {"artifact_id": artifact_id, "exported_to": dest,
                "size_bytes": info.get("size_bytes")}

    # ---- 清理 ----
    def cleanup(self, *, ttl_days=None, max_bytes=None) -> dict:
        idx = self._load()
        removed = []
        now = _now()
        if ttl_days:
            cutoff = now - ttl_days * 86400
            for aid, info in list(idx.items()):
                if info.get("created_at", now) < cutoff:
                    self._remove(aid, idx)
                    removed.append(aid)
        if max_bytes:
            items = sorted(idx.items(), key=lambda kv: kv[1].get("created_at", 0))
            total = sum(i.get("size_bytes", 0) for _, i in items)
            for aid, info in items:
                if total <= max_bytes:
                    break
                total -= info.get("size_bytes", 0)
                self._remove(aid, idx)
                removed.append(aid)
        if removed:
            self._save()
        return {"removed": removed, "count": len(removed)}

    def _remove(self, aid, idx):
        info = idx.pop(aid, None)
        if info and info.get("path") and os.path.isfile(info["path"]):
            try:
                os.remove(info["path"])
            except OSError:
                pass
