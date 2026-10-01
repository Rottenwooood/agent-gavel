"""Session 资源（docs/tech-plan.md §5.4）。

会话级隔离与配置。模式：
  ephemeral  共享默认浏览器上 new_context；关闭即弃
  persistent launch_persistent_context(user_data_dir)；独占进程、状态落盘
  clone      从 storage_state 起新 context

并发决策 B：workflow 执行时独占 session（acquire/release），同 session 再来一条
返回 session_busy；不同 session 可并行。
"""

import asyncio
import os
import tempfile

from .errors import GavelError

_DATA_ROOT = os.environ.get(
    "AGENT_GAVEL_DATA_DIR",
    os.path.join(tempfile.gettempdir(), "agent-gavel"),
)
PROFILE_DIR = os.environ.get(
    "AGENT_GAVEL_PROFILE_DIR", os.path.join(_DATA_ROOT, "profiles"))


class Session:
    def __init__(self, session_id, mode, config, handle):
        self.session_id = session_id
        self.mode = mode
        self.config = dict(config or {})
        self.handle = handle
        self.context_ids = []
        self.storage_state = self.config.get("storage_state")
        self.created_at = None
        self._busy_owner = None
        self._busy_lock = asyncio.Lock()

    # ---- 独占（并发决策 B）----
    async def acquire(self, run_id: str):
        async with self._busy_lock:
            if self._busy_owner is not None and self._busy_owner != run_id:
                raise GavelError(
                    "session_busy",
                    f"session {self.session_id} 正被 {self._busy_owner} 占用",
                    detail={"owner": self._busy_owner},
                    hint="等它跑完，或换一个 session",
                )
            self._busy_owner = run_id

    def release(self, run_id: str):
        if self._busy_owner == run_id:
            self._busy_owner = None

    @property
    def busy_owner(self):
        return self._busy_owner

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "mode": self.mode,
            "context_ids": list(self.context_ids),
            "process_id": self.handle.process_id if self.handle else None,
            "busy_owner": self._busy_owner,
            "config": {k: v for k, v in self.config.items()
                       if k not in ("storage_state",)},
            "has_storage_state": bool(self.storage_state),
        }


class SessionManager:
    """会话创建/关闭。进程分配规则见 docs/tech-plan.md §3.2/§5.4。"""

    _LAUNCH_KEYS = ("headless", "channel", "proxy", "slow_mo", "downloads_path")

    def __init__(self, runtime):
        self.runtime = runtime

    def get(self, session_id):
        s = self.runtime.sessions.get(session_id)
        if s is None:
            raise GavelError("session_not_found", f"未知 session：{session_id}",
                             hint="用 session_create 新建，或 session_list 查看")
        return s

    def _launch_opts(self, config):
        return {k: config[k] for k in self._LAUNCH_KEYS if config.get(k) is not None}

    async def create(self, mode="ephemeral", **config):
        if mode not in ("ephemeral", "persistent", "clone"):
            raise GavelError("bad_mode", f"未知 session 模式：{mode}",
                             hint="mode 取 ephemeral|persistent|clone")
        sid = self.runtime.next_id("sess")
        opts = self._launch_opts(config)
        if mode == "persistent":
            udd = config.get("user_data_dir") or os.path.join(PROFILE_DIR, sid)
            handle = await self.runtime.processes.launch_persistent(udd, **opts)
            session = Session(sid, mode, config, handle)
            self.runtime.sessions[sid] = session
            await self.runtime.context_mgr.adopt(
                session, handle.persistent_context)
        else:
            # 只有显式给了 launch 级参数（proxy/channel/headless）才独占进程，
            # 否则复用共享默认浏览器（headless=None 不应触发独占）。
            needs_dedicated = bool(opts.get("proxy") or opts.get("channel")
                                   or config.get("headless") is not None)
            if needs_dedicated:
                handle = await self.runtime.processes.launch(**opts)
            else:
                handle = await self.runtime.ensure_default_browser()
            session = Session(sid, mode, config, handle)
            self.runtime.sessions[sid] = session
            storage_state = config.get("storage_state") if mode == "clone" else None
            await self.runtime.context_mgr.create(session, config,
                                               storage_state=storage_state)
        return session

    async def close(self, session_id):
        session = self.get(session_id)
        for cid in list(session.context_ids):
            await self.runtime.context_mgr.close(cid)
        handle = session.handle
        # 独占进程的 session 才关浏览器；共享默认浏览器的留到 shutdown
        if handle is not None and self.runtime.default_handle is not handle:
            await self.runtime.processes.close(handle)
        self.runtime.sessions.pop(session_id, None)
        return {"session_id": session_id, "closed": True}

    async def reset(self, session_id):
        session = self.get(session_id)
        config = dict(session.config)
        mode = session.mode
        await self.close(session_id)
        new = await self.create(mode=mode, **config)
        return new
