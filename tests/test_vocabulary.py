"""动作词表一致性（docs/architecture.md 阶段 3 的雏形）。

阶段 3 会把 schema / runner / backend 的三张表合并到 core/actions.py。
在合并之前，这里先守住"动作集合必须被完整分类"这一条：
每个动作要么需要 target，要么属于明确的 targetless 集合，不能漏。
"""

from agent_gavel.workflows import schema as S

# 与 docs/tech-plan.md 的动作语义保持一致；新增动作必须同时更新这里。
TARGETLESS = {
    "navigate", "wait_for_selector", "wait_for_url", "wait_for_text",
    "expect", "screenshot", "pdf",
}


def test_every_action_is_classified():
    assert S._TARGET_ACTIONS <= S.ACTION_TYPES
    assert S.ACTION_TYPES == (S._TARGET_ACTIONS | TARGETLESS)
    assert S._TARGET_ACTIONS.isdisjoint(TARGETLESS)


def test_action_types_are_strings():
    assert all(isinstance(t, str) and t for t in S.ACTION_TYPES)
