"""工作流录制（docs/tech-plan.md §5.15.4）。

recording 激活时，动作工具把每次成功动作追加进 Recording.steps。
"""


class Recording:
    def __init__(self, session_id=None, context_id=None, page_id=None):
        self.session_id = session_id
        self.context_id = context_id
        self.page_id = page_id
        self.steps = []


class Recorder:
    def __init__(self):
        self.active = False
        self.recording = None

    def start(self, session_id=None, context_id=None, page_id=None):
        self.recording = Recording(session_id, context_id, page_id)
        self.active = True
        return {"session_id": session_id, "context_id": context_id,
                "page_id": page_id, "steps": 0}

    def stop(self):
        rec = self.recording
        self.active = False
        self.recording = None
        return rec

    def record(self, handle, action, url_before=None, url_after=None,
               checkpoint=None):
        """动作成功后调用；只录同 session 的动作。"""
        if not self.active or self.recording is None:
            return
        if self.recording.session_id and \
                handle.session_id != self.recording.session_id:
            return
        step = {"action": action, "url_before": url_before,
                "url_after": url_after}
        if checkpoint:
            step["checkpoint"] = checkpoint
        self.recording.steps.append(step)
