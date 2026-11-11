"""locator 解析：target 归一、strict 检查与候选样例。"""

from ...core.errors import GavelError
from ...core.targets import normalize
from .frames import frame_root


class LocatorMixin:
    _normalize_target = staticmethod(normalize)

    async def resolve(self, handle, target):
        page = handle.page
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
                    hint="用 page(op=explore) 找可用锚点",
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
