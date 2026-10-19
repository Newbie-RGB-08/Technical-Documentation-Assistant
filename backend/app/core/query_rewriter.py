"""多轮问题改写（PRD 3.3.2 / 4.5）。

基于最近 3 轮历史，将指代性问题改写为独立完整查询；无历史直通。
"""
from __future__ import annotations

REWRITE_PROMPT = """基于以下对话历史，将用户的最新问题改写为一个独立、完整的查询语句。
要求：保留原始意图，补全指代和省略的信息，只输出改写后的查询语句，不要任何解释。

对话历史：
{history}

最新问题：{question}
改写后的查询："""


def _format_history(history: list[dict], turns: int = 3) -> str:
    """兼容 [{role, content}] 与 [{user, assistant}] 两种历史格式。"""
    lines: list[str] = []
    for h in history[-turns:]:
        if "user" in h:
            lines.append(f"用户：{h['user']}\n助手：{str(h.get('assistant', ''))[:200]}")
        else:
            role = "用户" if h.get("role") == "user" else "助手"
            lines.append(f"{role}：{h.get('content', '')[:200]}")
    return "\n".join(lines)


async def rewrite_query(
    gateway, question: str, history: list[dict] | None, force_llm: bool = False
) -> str:
    """默认无历史直通；有历史或强制改写时走轻量模型（task_type=rewrite）。"""
    if not history and not force_llm:
        return question
    history_text = _format_history(history) if history else "（无对话历史）"
    prompt = REWRITE_PROMPT.format(history=history_text, question=question)
    response = await gateway.chat(
        messages=[{"role": "user", "content": prompt}], task_type="rewrite"
    )
    return response.strip()
