"""解析器公共接口与 IR（PRD 7.3）。

解析器只负责把源文件转换成 Markdown 与资源文件，不访问数据库、不调用
向量库，也不直接读写对象存储；资源写入由调用方给定的 ``out_dir``。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


class ParserError(RuntimeError):
    """解析失败（格式合法但内容/依赖异常）。"""


class UnsupportedFormatError(ParserError):
    """扩展名不在白名单或属于首版明确拒绝的格式。"""


@dataclass
class ParsedAsset:
    kind: str                  # "image" | "table"
    name: str                  # "image_001.png"
    rel_path: str              # "assets/image_001.png"
    page_no: int | None = None
    caption: str = ""
    ocr_text: str = ""
    width: int | None = None
    height: int | None = None


@dataclass
class ParsedDocument:
    markdown: str
    assets: list[ParsedAsset] = field(default_factory=list)
    engine: str = "native"
    page_count: int | None = None
    warnings: list[str] = field(default_factory=list)


class DocumentParser(ABC):
    name: str
    supported_extensions: tuple[str, ...]

    @abstractmethod
    def available(self) -> bool:
        """依赖与配置是否满足；只表示“可以尝试解析”。"""

    @abstractmethod
    def health(self) -> dict:
        """返回 {available, status, detail, latency_ms, checked_at} 等可缓存快照。"""

    @abstractmethod
    def parse(self, src: Path, out_dir: Path, params: dict) -> ParsedDocument:
        """解析源文件；资源只能写入 out_dir。"""
