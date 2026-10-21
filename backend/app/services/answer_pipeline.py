"""严格答案管道与通用流式管道（PRD 附录 E.6 / E.7）。

严格管道：缓冲生成 → 逐句校验 → 修复一次 → 摘录式兜底。
通用管道：仅宽泛概念问题使用；固定免责声明 + 轻量守卫 + 标记未验证。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import AsyncIterator

from app.config import get_settings
from app.core.answer_verifier import CITATION_RE, VerificationResult, verify_answer
from app.core.confidence import DISCLAIMER
from app.core.evidence import EvidencePack, build_evidence_pack
from app.core.fallback_ladder import (
    FallbackLevel,
    fallback_message,
)
from app.core.input_guard import wrap_untrusted_web_content

SPECIFIC_OPERATION_RE = re.compile(
    r"(如何|怎么|怎样|为啥|为什么).{0,8}(配置|安装|部署|排查|修复|升级|连接|调用|设置|启动)"
    r"|报错|错误码|Traceback|Exception|命令行|--[A-Za-z]"
)
PSEUDO_CITATION_RE = re.compile(r"\[(?:来源\s*)?\d+\]|根据(?:文档|资料|知识库)(?:显示|所述|记载)")
GENERAL_SPECIFIC_RE = re.compile(
    r"(?<![\w.])[vV]?\d+(?:\.\d+){1,3}(?![\w.])|(?<![\w]):\d{2,5}\b|--[A-Za-z][\w-]*"
)
STRICT_SYSTEM_PROMPT = """你是一个技术文档研究助手。只能使用下列编号证据回答，禁止使用模型记忆。

规则：
1. 每个包含事实的句子都必须紧跟在句末标注引用，如 [来源1]
2. 数字、版本号、端口、错误码、API 名、命令、配置键必须与引用片段逐字一致
3. 不得引用未提供的编号；不得编造文档、网页或历史对话中的内容
4. 不要使用“官方/最新/一定/必须”等强断言，除非证据原文明确支持
5. 证据不足时直接说明“现有资料未覆盖”，不要补全
6. 回答使用 Markdown；代码块用 ``` 闭合

证据快照：
{context}
"""

GENERAL_SYSTEM_PROMPT = """你是一个技术概念讲解助手。当前没有可用的本地知识库证据。
只解释概念、原理和名词，不给出具体版本号、端口、默认值、命令或 API 调用细节。
禁止输出任何 [来源N] 或“根据文档/资料显示”等引用表述。
如果问题涉及具体配置或排错，请说明需要查阅官方文档。
"""


@dataclass
class AnswerResult:
    text: str
    status: str
    verified: bool = False
    fallback_level: int = int(FallbackLevel.L0_NORMAL)
    used_indices: list[int] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def _context_for(evidence: EvidencePack) -> str:
    parts: list[str] = []
    for item in evidence.items:
        meta = item.metadata or {}
        src = meta.get("source_url") or ""
        src_line = f" | 网页来源：{src}" if src else ""
        text = item.text
        if item.origin == "web_search":
            text = wrap_untrusted_web_content(text)
        parts.append(
            f"[来源{item.index}] 文件：{meta.get('file_path', '未知')} | "
            f"章节：{meta.get('heading_path', '')}{src_line}\n{text}"
        )
    return "\n\n".join(parts)


def _citations_in(text: str) -> list[int]:
    values = [int(n) for n in CITATION_RE.findall(text or "")]
    return sorted({n for n in values if n > 0})


def _strip_heading_lines(text: str) -> str:
    lines = [line for line in text.split("\n") if not line.lstrip().startswith("#")]
    return "\n".join(lines).strip()


def build_extractive_answer(
    evidence: EvidencePack,
    *,
    max_items: int = 3,
    excerpt_chars: int = 220,
) -> AnswerResult:
    """摘录式兜底：只取证据原文，不改写、不总结、不扩写。"""
    bullets: list[str] = []
    used: list[int] = []
    for item in evidence.items:
        if len(bullets) >= max_items:
            break
        text = _strip_heading_lines(item.text)
        if not text:
            continue
        excerpt = text[:excerpt_chars].strip()
        if excerpt and excerpt not in [b.split(" [来源")[0] for b in bullets]:
            bullets.append(f"- {excerpt} [来源{item.index}]")
            used.append(item.index)
    if not bullets:
        return AnswerResult(
            text=fallback_message(FallbackLevel.L4_REFUSED),
            status="refused",
            fallback_level=int(FallbackLevel.L4_REFUSED),
        )
    text = fallback_message(FallbackLevel.L2_EXTRACTIVE) + "\n\n" + "\n".join(bullets)
    text += "\n\n其余部分现有资料未覆盖。"
    return AnswerResult(
        text=text,
        status="extractive",
        verified=False,
        fallback_level=int(FallbackLevel.L2_EXTRACTIVE),
        used_indices=used,
    )


def build_conflict_answer(evidence: EvidencePack, conflicts: list[str]) -> AnswerResult:
    lines = ["资料存在冲突，暂不合并结论：", ""]
    for conflict in conflicts:
        lines.append(f"- {conflict}")
    lines.append("")
    lines.append("请核对官方文档或补充限定条件后再判断。")
    used = sorted({i for i in range(1, evidence.max_index + 1)})
    return AnswerResult(
        text="\n".join(lines),
        status="partial",
        verified=False,
        fallback_level=int(FallbackLevel.L3_PARTIAL),
        used_indices=used,
        conflicts=list(conflicts),
    )


async def _generate(gateway, messages: list[dict], model_id: str | None) -> str:
    answer = await gateway.chat(messages=messages, task_type="generate", model_id=model_id)
    return str(answer or "")


def _strict_messages(query: str, evidence: EvidencePack) -> list[dict]:
    return [
        {"role": "system", "content": STRICT_SYSTEM_PROMPT.format(context=_context_for(evidence))},
        {"role": "user", "content": query},
    ]


def _repair_messages(
    query: str,
    evidence: EvidencePack,
    answer: str,
    verification: VerificationResult,
) -> list[dict]:
    return [
        {"role": "system", "content": STRICT_SYSTEM_PROMPT.format(context=_context_for(evidence))},
        {
            "role": "user",
            "content": (
                f"问题：{query}\n\n原回答：\n{answer}\n\n"
                f"校验发现以下问题：\n{verification.repair_brief()}\n\n"
                "请删除或改写未被证据支撑的句子；只能使用上述证据，不得新增事实，"
                "并保留正确的 [来源N] 引用。"
            ),
        },
    ]


async def run_strict_pipeline(
    gateway,
    query: str,
    evidence: EvidencePack | list[dict],
    model_id: str | None = None,
    on_status=None,
) -> AnswerResult:
    """严格管道入口：生成 → 校验 → 修复一次 → 摘录兜底。"""
    settings = get_settings()
    pack = evidence if isinstance(evidence, EvidencePack) else build_evidence_pack(evidence)
    if not pack.items:
        return AnswerResult(
            text=fallback_message(FallbackLevel.L4_REFUSED),
            status="refused",
            fallback_level=int(FallbackLevel.L4_REFUSED),
        )

    try:
        answer = await _generate(gateway, _strict_messages(query, pack), model_id)
    except Exception:  # noqa: BLE001 - 全链失败直接摘录
        return build_extractive_answer(pack)

    answer = answer[: settings.max_answer_chars]
    verification = verify_answer(
        answer,
        pack,
        critical_entity_check=settings.critical_entity_check,
        max_unsupported_ratio=settings.max_unsupported_claim_ratio,
        question=query,
    )

    if verification.conflicts:
        return build_conflict_answer(pack, verification.conflicts)

    if verification.passed:
        return AnswerResult(
            text=answer,
            status="verified",
            verified=True,
            fallback_level=int(FallbackLevel.L0_NORMAL),
            used_indices=_citations_in(answer),
        )

    # 严重实体错误 / 退化输出不再重试，直接摘录（PRD E.6.3）
    repair_allowed = (
        settings.verification_max_repair_retries > 0
        and not verification.degenerate
        and not verification.severe
    )
    if repair_allowed:
        if on_status is not None:
            try:
                on_status("repairing")
            except Exception:  # noqa: BLE001 - 状态回调失败不影响主流程
                pass
        try:
            repaired = await _generate(
                gateway, _repair_messages(query, pack, answer, verification), model_id
            )
        except Exception:  # noqa: BLE001
            return build_extractive_answer(pack)

        repaired = repaired[: settings.max_answer_chars]
        second = verify_answer(
            repaired,
            pack,
            critical_entity_check=settings.critical_entity_check,
            max_unsupported_ratio=settings.max_unsupported_claim_ratio,
            question=query,
        )
        if second.conflicts:
            return build_conflict_answer(pack, second.conflicts)
        if second.passed:
            return AnswerResult(
                text=repaired,
                status="verified",
                verified=True,
                fallback_level=int(FallbackLevel.L1_REPAIRED),
                used_indices=_citations_in(repaired),
            )

    if settings.extractive_fallback_enabled:
        return build_extractive_answer(pack)
    return AnswerResult(
        text=fallback_message(FallbackLevel.L4_REFUSED),
        status="refused",
        fallback_level=int(FallbackLevel.L4_REFUSED),
    )


def classify_specificity(question: str) -> str:
    """区分“宽泛概念型”与“具体 API/版本/命令/操作型”问题（PRD E.4.2）。"""
    from app.core.retrieval_scorer import CAMEL_CASE_RE, CAMEL_SNAKE_RE

    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", question)
    for token in tokens:
        if CAMEL_SNAKE_RE.match(token) or CAMEL_CASE_RE.search(token):
            return "specific"
    if re.search(r"(?<![\w.])[vV]?\d+(?:\.\d+){1,3}(?![\w.])", question):
        return "specific"
    if re.search(r"(?:localhost|127\.0\.0\.1)?:\d{2,5}\b|--[A-Za-z]", question):
        return "specific"
    if SPECIFIC_OPERATION_RE.search(question):
        return "specific"
    return "concept"


def guard_general_answer(answer: str) -> tuple[bool, list[str]]:
    """通用流式轻量守卫：伪引用、具体断言、强断言。"""
    reasons: list[str] = []
    if PSEUDO_CITATION_RE.search(answer or ""):
        reasons.append("检测到伪引用")
    if GENERAL_SPECIFIC_RE.search(answer or ""):
        reasons.append("检测到具体版本/端口/命令断言")
    if any(word in (answer or "") for word in ("官方宣布", "最新版本", "一定", "绝对")):
        reasons.append("检测到强断言")
    return bool(reasons), reasons


GENERAL_CORRECTION = (
    "\n\n> 更正提示：以上为未基于本地知识库的通用回答，"
    "其中的具体版本/端口/命令/默认值请以官方文档为准。"
)


def _general_messages(question: str) -> list[dict]:
    return [
        {"role": "system", "content": GENERAL_SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]


async def run_general_stream(
    gateway,
    question: str,
    model_id: str | None = None,
) -> AsyncIterator[dict]:
    """通用流式管道：先免责声明，再流式输出，最后轻量守卫。

    yield 事件：{"event":"token","text":...} / {"event":"done","result":AnswerResult}
    """
    collected = DISCLAIMER + "\n\n"
    yield {"event": "token", "text": DISCLAIMER + "\n\n"}

    try:
        stream = await gateway.chat(
            _general_messages(question), task_type="generate", stream=True, model_id=model_id
        )
        async for token in stream:
            token = str(token or "")
            if not token:
                continue
            collected += token
            yield {"event": "token", "text": token}
    except Exception:  # noqa: BLE001 - 不向用户暴露原始异常
        unavailable = "（模型服务暂不可用，请稍后再试）"
        yield {"event": "token", "text": unavailable}
        yield {
            "event": "done",
            "result": AnswerResult(
                text=DISCLAIMER + "\n\n" + unavailable,
                status="refused",
                verified=False,
                fallback_level=int(FallbackLevel.L4_REFUSED),
            ),
        }
        return

    flagged, _reasons = guard_general_answer(collected)
    status = "unverified_general_flagged" if flagged else "unverified_general"
    if flagged:
        collected += GENERAL_CORRECTION
        yield {"event": "token", "text": GENERAL_CORRECTION}
    yield {
        "event": "done",
        "result": AnswerResult(
            text=collected,
            status=status,
            verified=False,
            fallback_level=int(FallbackLevel.L0_NORMAL),
        ),
    }
