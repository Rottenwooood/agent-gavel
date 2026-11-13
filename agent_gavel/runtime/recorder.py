"""工作流录制缓冲（运行时级）。

录制归属以 context 为准；按需去噪与合并，避免把试探动作、hover/scroll 噪声
或失败的断言写进模板。

页面用相对引用记录（`page0` / `new:N`），不写死 page_id —— 重放时由
runner 依据 diff 里的 `pages.opened` 重新绑定。
"""

_NOISE = {"focus", "scroll", "hover"}
_MERGE = {"fill", "type"}


class Recording:
    def __init__(self, context_id=None, page_id=None, keep_all=False):
        self.context_id = context_id
        self.page_id = page_id
        self.keep_all = keep_all
        self.steps = []
        self.root_page_id = page_id
        self.new_pages = {}

    def page_ref(self, page_id):
        """录制期页面 → 模板里的相对引用。"""
        if self.root_page_id is None:
            self.root_page_id = page_id
        if page_id == self.root_page_id:
            return "page0"
        idx = self.new_pages.get(page_id)
        if idx is None:
            idx = len(self.new_pages)
            self.new_pages[page_id] = idx
        return f"new:{idx}"


class Recorder:
    def __init__(self):
        self.active = False
        self.recording = None

    def start(self, context_id=None, page_id=None, keep_all=False):
        self.recording = Recording(context_id, page_id, keep_all)
        self.active = True
        return {"context_id": context_id, "page_id": page_id, "keep_all": keep_all, "steps": 0}

    def stop(self):
        rec = self.recording
        self.active = False
        self.recording = None
        return rec

    def record(self, handle, action, url_before=None, url_after=None, checkpoint=None, diff=None):
        """动作成功后调用；只录同一 context 的动作。"""
        if not self.active or self.recording is None:
            return
        if self.recording.context_id and handle.context_id != self.recording.context_id:
            return
        atype = action.get("type")
        if not self.recording.keep_all and atype in _NOISE:
            return

        steps = self.recording.steps
        last = steps[-1] if steps else None
        same_target = (
            last
            and last["action"].get("type") == atype
            and last["action"].get("target") == action.get("target")
        )
        if same_target:
            if atype in _MERGE:
                # 同一目标的连续输入只保留最后一次结果
                last["action"] = dict(action)
                last["url_after"] = url_after
                if checkpoint:
                    last["checkpoint"] = checkpoint
                if diff:
                    last["diff"] = diff
                return
            if checkpoint is None and action == last["action"]:
                return  # 完全重复的动作直接丢弃

        step = {
            "action": action,
            "page": self.recording.page_ref(handle.page_id),
            "url_before": url_before,
            "url_after": url_after,
        }
        if checkpoint:
            step["checkpoint"] = checkpoint
        if diff:
            step["diff"] = diff
        steps.append(step)
