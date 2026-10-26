"""工作流录制缓冲（运行时级）。

录制归属以 context 为准：page 属于 context，session 是租约，不是页面的固有属性。
"""


class Recording:
    def __init__(self, context_id=None, page_id=None):
        self.context_id = context_id
        self.page_id = page_id
        self.steps = []


class Recorder:
    def __init__(self):
        self.active = False
        self.recording = None

    def start(self, context_id=None, page_id=None):
        self.recording = Recording(context_id, page_id)
        self.active = True
        return {"context_id": context_id, "page_id": page_id, "steps": 0}

    def stop(self):
        rec = self.recording
        self.active = False
        self.recording = None
        return rec

    def record(self, handle, action, url_before=None, url_after=None,
               checkpoint=None):
        """动作成功后调用；只录同一 context 的动作。"""
        if not self.active or self.recording is None:
            return
        if self.recording.context_id \
                and handle.context_id != self.recording.context_id:
            return
        step = {"action": action, "page_id": handle.page_id,
                "url_before": url_before, "url_after": url_after}
        if checkpoint:
            step["checkpoint"] = checkpoint
        self.recording.steps.append(step)
