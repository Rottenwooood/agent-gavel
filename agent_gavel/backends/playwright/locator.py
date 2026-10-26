"""locator 解析：target 归一、strict 检查与候选样例。"""

from ...core.errors import GavelError
from .frames import frame_root

_TARGET_KEYS = ("role", "label", "placeholder", "text", "testid", "css", "xpath")


class LocatorMixin:
    @staticmethod
    def _normalize_target(target):
        if not isinstance(target, dict):
            raise GavelError("bad_target", "target 必须是对象", hint='如 {"by":"css","value":"#q"}')
        by = target.get("by")
        value = target.get("value")
        if by is None:
            for k in _TARGET_KEYS:
                if k in target:
                    by, value = k, target[k]
                    break
        if by is None or value is None:
            raise GavelError(
                "bad_target",
                f"target 缺少 by/value：{target}",
                hint="by 取 role|label|placeholder|text|testid|css|xpath",
            )
        return by, value

    async def resolve(self, page, target):
        by, value = self._normalize_target(target)
        name = target.get("name")
        exact = target.get("exact")
        strict = target.get("strict", True)
        nth = target.get("nth")
        has_text = target.get("has_text")
        root = frame_root(page, target.get("frame"))
        try:
            if by == "role":
                loc = root.get_by_role(value, name=name, exact=exact)
            elif by == "label":
                loc = root.get_by_label(value, exact=exact)
            elif by == "placeholder":
                loc = root.get_by_placeholder(value, exact=exact)
            elif by == "text":
                loc = root.get_by_text(value, exact=exact)
            elif by == "testid":
                loc = root.get_by_test_id(value)
            elif by == "css":
                loc = root.locator(value)
            elif by == "xpath":
                loc = root.locator("xpath=" + value)
            else:
                raise GavelError("bad_target", f"未知 by：{by}")
        except GavelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise GavelError("bad_target", f"非法 locator：{e}") from e
        if has_text:
            loc = loc.filter(has_text=has_text)
        if nth is not None:
            return loc.nth(nth)
        if strict:
            n = await loc.count()
            if n == 0:
                raise GavelError(
                    "locator_not_found",
                    f"locator 无命中：{by}={value}",
                    detail={"target": target},
                    retryable=True,
                    hint="用 page_explore 找可用锚点",
                )
            if n > 1:
                samples = await self._samples(loc)
                raise GavelError(
                    "locator_not_unique",
                    f"locator 命中 {n} 个（strict）：{by}={value}",
                    detail={"count": n, "samples": samples, "target": target},
                    hint="收窄：加 nth / has_text / 更精确的 by",
                )
        return loc

    async def _samples(self, loc, limit=5):
        out = []
        try:
            texts = await loc.all_inner_texts()
            for t in texts[:limit]:
                out.append((t or "").strip()[:60])
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程
        return out
