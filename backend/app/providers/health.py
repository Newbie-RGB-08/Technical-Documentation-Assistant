"""Provider 健康状态域模型：启动自检与本地模型自动发现（方案 v2 / D1-D2）。

- Reason：探测结论的机器可读原因码（启动日志、错误诊断与前端横幅消费）
- LocalModelInfo：从 Ollama ``list()`` 解析出的单个已安装模型（含参数量/家族元数据）
- ProviderStatus：一次 ``probe(timeout)`` 的完整快照
- ``parse_installed_models`` 等解析工具对 dict 与 pydantic 风格返回都兼容

设计要点：不绑定任何具体模型型号——"配置里的模型没安装"不是错误态，
由网关的自动绑定（fit_binding）从已装模型里挑选兜底。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Reason(str, Enum):
    OK = "ok"
    SERVICE_UNREACHABLE = "service_unreachable"   # Ollama 连不上 / 云端网络不可达
    NO_CHAT_MODELS = "no_chat_models"             # 服务在线，但没有任何可对话的模型（仅本地）
    NO_API_KEY = "no_api_key"                     # 云端未配置 Key（Provider 未构建）
    AUTH_FAILED = "auth_failed"                   # 云端凭据无效（401/403）
    TIMEOUT = "timeout"
    ERROR = "error"
    # 仅用于 /api/models 条目的说明码：服务在线、但该条目声明的模型未安装。
    # 非故障态——自动绑定会优先消化该情况（方案 v2 取消其"错误态"定位）。
    MODEL_NOT_PULLED = "model_not_pulled"


# 默认按名称关键词过滤非对话模型（可在 config: local_model_exclude_keywords 覆盖）
DEFAULT_EXCLUDE_KEYWORDS: tuple[str, ...] = (
    "embed", "bge", "nomic", "minilm", "mxbai", "rerank", "guard", "whisper", "gte", "sentence",
)

_PARAM_SEG = re.compile(r"(\d+(?:\.\d+)?)(b|m)")


def model_family(name: str) -> str:
    """从模型名取"基础家族"（冒号/连字符前的小写基础名），两侧（配置与已装）统一口径。

    例：``"qwen2.5:7b"`` → ``"qwen2.5"``；``"llama3.1:8b"`` → ``"llama3.1"``。
    """
    base = str(name or "").strip().casefold()
    if not base:
        return ""
    if ":" in base:
        return base.split(":", 1)[0]
    return base.split("-", 1)[0] if "-" in base else base


def parse_parameter_size(detail: object, model_name: str = "") -> float | None:
    """解析参数量（单位 B，返回浮点）。

    优先用 Ollama ``details.parameter_size``（如 ``"7.6B"`` / ``"334M"``）；
    缺失时从模型名回退解析（``"qwen2.5:7b"`` → 7.0）。解析不出返回 None。
    """

    def _norm(value: float, text: str) -> float:
        return value / 1000.0 if text.lower().endswith("m") else value

    text = str(detail or "").strip()
    match = re.match(r"(\d+(?:\.\d+)?)", text)
    if match:
        return _norm(float(match.group(1)), text)
    # 回退：按分隔符切段，找形如 "7b" / "0.5b" / "22B" 的段
    for seg in re.split(r"[\s_:/\-]+", str(model_name or "").lower()):
        seg_match = _PARAM_SEG.fullmatch(seg)
        if seg_match:
            return float(seg_match.group(1))
    return None


def is_chatable(name: str, exclude_keywords: tuple[str, ...] = DEFAULT_EXCLUDE_KEYWORDS) -> bool:
    """名称启发式过滤非对话模型（embedding / reranker / guard 等）。"""
    lowered = str(name or "").casefold()
    return not any(marker in lowered for marker in exclude_keywords)


def _field(item: object, key: str) -> object:
    """兼容 dict 与 pydantic 实例两种 ``list()`` 返回形态的字段读取。"""
    value = getattr(item, key, None)
    if value is None and isinstance(item, dict):
        value = item.get(key)
    return value


def _details_of(item: object) -> object:
    return _field(item, "details")


@dataclass
class LocalModelInfo:
    """Ollama ``list()`` 中的一个已安装模型。"""

    name: str                       # 如 "llama3.1:8b"（候选/选择器用的原始名）
    size_bytes: int = 0
    parameter_size: float | None = None
    family: str = ""                # 基础家族（model_family 口径）
    modified_at: str = ""
    chatable: bool = True           # 非对话模型（embedding 等）为 False，不进候选/选择器


@dataclass
class ProviderStatus:
    """一次探测的快照。"""

    name: str
    type: str                       # "local" | "cloud"
    available: bool
    reason: str = Reason.ERROR.value
    detail: str = ""                # 面向人类的中文说明（不得出现具体型号的引导）
    installed_models: list[LocalModelInfo] = field(default_factory=list)  # 仅 local
    checked_at: float = 0.0         # monotonic 时间戳，供 TTL 判定；0 表示已失效

    def chatable_models(self) -> list[LocalModelInfo]:
        return [m for m in self.installed_models if m.chatable]


@dataclass
class AutoBinding:
    """自动绑定兜底：配置链全部不可用时选中的本地已装模型（运行时，不落盘）。"""

    provider: str
    model: str
    reason: str                     # family_match | size_max | size_min | fallback


def parse_installed_models(
    listing: object,
    exclude_keywords: tuple[str, ...] = DEFAULT_EXCLUDE_KEYWORDS,
) -> list[LocalModelInfo]:
    """把 Ollama ``list()``（dict 或 pydantic 对象）解析为 LocalModelInfo 列表。"""
    raw: object
    if isinstance(listing, dict):
        raw = listing.get("models")
    else:
        raw = getattr(listing, "models", None)
    infos: list[LocalModelInfo] = []
    for item in (raw or []):
        name = str(_field(item, "name") or _field(item, "model") or "").strip()
        if not name:
            continue
        details = _details_of(item)
        infos.append(
            LocalModelInfo(
                name=name,
                size_bytes=int(_field(item, "size") or 0),
                parameter_size=parse_parameter_size(
                    _field(details, "parameter_size") if details else None, name
                ),
                family=model_family(name),
                modified_at=str(_field(item, "modified_at") or ""),
                chatable=is_chatable(name, exclude_keywords),
            )
        )
    return infos
