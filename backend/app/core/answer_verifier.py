"""答案逐句校验（PRD 附录 E.6.2）。

规则引擎实现：引用存在性 / 引用必要性 / 关键实体一致 / 过度断言 /
退化检测 / 高信号字段冲突。不调用 LLM，避免校验本身产生幻觉。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.evidence import EvidencePack, build_evidence_pack, extract_entities

CITATION_RE = re.compile(r"\[(?:来源\s*)?(\d{1,2})\]")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])(?![\s]*(?:\[来源|\[\d{1,2}\]))")
VERSION_RE = re.compile(r"(?<![\w.])[vV]?\d+(?:\.\d+){1,3}(?![\w.])")
PORT_RE = re.compile(r"(?:localhost|127\.0\.0\.1)?:\d{2,5}\b")
OVERCLAIM_WORDS = ("官方", "最新", "一定", "必须", "绝对", "唯一")
COMPARISON_WORDS = ("对比", "区别", "分别", "差异", "相比")
LIST_MARKER_RE = re.compile(r"^[-*+]\s+|^\d+[.)、]\s+")
WORD_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")


@dataclass
class VerificationResult:
    passed: bool
    illegal_citations: list[int] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    severe: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unsupported_ratio: float = 0.0
    degenerate: bool = False
    factual_count: int = 0

    def repair_brief(self) -> str:
        lines = []
        if self.severe:
            lines.append("严重未支撑句子：\n" + "\n".join(f"- {s}" for s in self.severe))
        remaining = [s for s in self.unsupported if s not in self.severe]
        if remaining:
            lines.append("未支撑句子：\n" + "\n".join(f"- {s}" for s in remaining))
        if self.illegal_citations:
            lines.append("非法引用编号：" + "、".join(str(i) for i in self.illegal_citations))
        return "\n".join(lines)


def split_sentences(answer: str) -> list[str]:
    """按句末标点/换行切分，代码块整体保留，避免切断代码。"""
    units: list[str] = []
    buffer: list[str] = []
    in_code = False

    def flush_plain() -> None:
        if buffer:
            text = " ".join(buffer).strip()
            if text:
                units.append(text)
            buffer.clear()

    for line in (answer or "").split("\n"):
        if line.strip().startswith("```"):
            if in_code:
                buffer.append(line)
                units.append("\n".join(buffer).strip())
                buffer.clear()
                in_code = False
            else:
                flush_plain()
                buffer.append(line)
                in_code = True
            continue

        if in_code:
            buffer.append(line)
            continue

        parts = SENTENCE_SPLIT_RE.split(line)
        for part in parts:
            part = part.strip()
            if part:
                units.append(part)

    flush_plain()
    if in_code and buffer:
        units.append("\n".join(buffer).strip())
    return units


def _clean_sentence(sentence: str) -> str:
    return LIST_MARKER_RE.sub("", sentence).strip()


def _is_heading(sentence: str) -> bool:
    stripped = sentence.lstrip()
    return stripped.startswith("#") or set(stripped) <= {"-", "=", " "}


def _is_code_block(sentence: str) -> bool:
    stripped = sentence.strip()
    return stripped.startswith("```") or (
        "\n" in stripped and stripped.rstrip().endswith("```")
    )


def _meaningful_length(sentence: str) -> int:
    return len(WORD_RE.findall(CITATION_RE.sub("", sentence)))


def _is_non_factual(sentence: str) -> bool:
    text = _clean_sentence(sentence)
    if not text or _is_heading(text):
        return True
    if text in ("注意", "说明", "总结", "总之", "另外", "此外", "例如", "如下", "结论"):
        return True
    return _meaningful_length(text) < 4


def _has_degeneration(answer: str) -> bool:
    text = (answer or "").strip()
    if _meaningful_length(text) < 10:
        return True
    if text.count("```") % 2 != 0:
        return True
    # 只有引用没有正文
    without_citations = CITATION_RE.sub("", text)
    if not WORD_RE.search(without_citations):
        return True
    # 重复 n-gram：6 字窗口在足够长的文本中反复出现
    compact = re.sub(r"\s+", "", without_citations)
    if len(compact) >= 60:
        counts: dict[str, int] = {}
        for i in range(len(compact) - 5):
            gram = compact[i : i + 6]
            counts[gram] = counts.get(gram, 0) + 1
        if counts and max(counts.values()) >= 6:
            return True
    return False


def _source_label(item) -> str:
    meta = item.metadata or {}
    return str(meta.get("title") or meta.get("file_path") or f"来源{item.index}")


def detect_conflicts(evidence: EvidencePack, question: str) -> list[str]:
    """高信号字段冲突：版本号 / 端口（对比类问题除外）。"""
    if not evidence.items or any(word in question for word in COMPARISON_WORDS):
        return []

    conflicts: list[str] = []

    def collect(pattern: re.Pattern, label: str, trigger: bool) -> None:
        if not trigger:
            return
        values: dict[str, list[int]] = {}
        for item in evidence.items:
            for match in pattern.findall(item.text):
                values.setdefault(match, [])
                if item.index not in values[match]:
                    values[match].append(item.index)
        if len(values) >= 2:
            detail = "；".join(
                f"{value}（来源{','.join(str(i) for i in indices)}）"
                for value, indices in sorted(values.items())
            )
            conflicts.append(f"资料存在冲突：{label} {detail}")

    collect(VERSION_RE, "版本", "版本" in question)
    collect(PORT_RE, "端口", "端口" in question or "port" in question.lower())
    return conflicts


def verify_answer(
    answer: str,
    evidence: EvidencePack | list[dict],
    *,
    critical_entity_check: bool = True,
    max_unsupported_ratio: float = 0.10,
    question: str = "",
) -> VerificationResult:
    """逐句校验，通过标准见 PRD E.6.2。"""
    pack = evidence if isinstance(evidence, EvidencePack) else build_evidence_pack(evidence)
    result = VerificationResult(passed=False)

    if not (answer or "").strip():
        result.degenerate = True
        result.issues.append("模型输出为空")
        return result

    if _has_degeneration(answer):
        result.degenerate = True
        result.issues.append("模型输出退化（空/重复/代码块未闭合/只有引用）")

    sentences = split_sentences(answer)
    for raw in sentences:
        sentence = _clean_sentence(raw)
        if _is_non_factual(sentence):
            continue
        result.factual_count += 1
        is_code = _is_code_block(sentence)

        citations = [int(n) for n in CITATION_RE.findall(sentence)]
        illegal = [n for n in citations if n < 1 or n > pack.max_index]
        valid = [n for n in citations if 1 <= n <= pack.max_index]
        if illegal:
            result.illegal_citations.extend(n for n in illegal if n not in result.illegal_citations)

        unsupported_reasons: list[str] = []
        if illegal:
            unsupported_reasons.append("引用编号越界")
        if not is_code and not citations:
            unsupported_reasons.append("事实句缺少引用")
        elif is_code and not citations:
            # 代码块不强制逐句引用，但无法核验的代码实体按严重处理
            pass

        cited_text = "\n".join(pack.text(n) for n in valid)
        entities = extract_entities(CITATION_RE.sub("", sentence))
        if critical_entity_check:
            missing = [e for e in entities if e not in cited_text]
            if missing and entities:
                if not valid:
                    result.severe.append(f"{sentence}（关键实体无可用引用：{'、'.join(missing)}）")
                else:
                    result.severe.append(
                        f"{sentence}（关键实体未在引用片段出现：{'、'.join(missing)}）"
                    )

        if any(word in sentence for word in OVERCLAIM_WORDS):
            if not any(word in cited_text for word in OVERCLAIM_WORDS):
                unsupported_reasons.append("过度断言缺少证据原文支持")

        if unsupported_reasons:
            reason = "；".join(unsupported_reasons)
            if sentence not in result.unsupported:
                result.unsupported.append(f"{sentence}（{reason}）")

    if result.factual_count:
        result.unsupported_ratio = len(result.unsupported) / result.factual_count
    result.conflicts = detect_conflicts(pack, question)

    severe_in_unsupported = [s for s in result.severe if s not in result.unsupported]
    result.unsupported.extend(severe_in_unsupported)
    if result.factual_count and severe_in_unsupported:
        result.unsupported_ratio = len(result.unsupported) / result.factual_count

    result.passed = (
        not result.degenerate
        and not result.illegal_citations
        and not result.severe
        and not result.conflicts
        and result.unsupported_ratio <= max_unsupported_ratio
    )
    return result
