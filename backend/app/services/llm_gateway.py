"""LLM 统一网关（PRD 3.4 / 4.8）。

- 统一接口 chat()/embed()/rerank()，业务层不感知底层差异
- config/models.yaml 驱动任务路由链，链上靠前失败自动降级
- ${VAR:default} 环境变量解析；API Key 为空时对应 Provider 不启用
- model_id 覆盖（"provider:model"）支持前端手动切换模型
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml

from app.config import get_settings
from app.providers.base import AllProvidersFailedError, LLMProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.openai_provider import OpenAICompatibleProvider

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def resolve_env_placeholders(value: str) -> str:
    """解析 ${VAR} / ${VAR:default} 形式的环境变量占位。"""

    def _replace(match: re.Match) -> str:
        var, default = match.group(1), match.group(2)
        return os.environ.get(var, default if default is not None else "")

    return _ENV_PATTERN.sub(_replace, value)


def _resolve_deep(obj):
    if isinstance(obj, str):
        return resolve_env_placeholders(obj)
    if isinstance(obj, dict):
        return {k: _resolve_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_deep(v) for v in obj]
    return obj


def load_models_config(config_path: str | Path | None = None) -> dict:
    path = Path(config_path or get_settings().models_config_path)
    if not path.exists():
        raise FileNotFoundError(f"模型路由配置不存在: {path}")
    with open(path, encoding="utf-8") as f:
        return _resolve_deep(yaml.safe_load(f))


def build_providers(providers_config: dict) -> dict[str, LLMProvider]:
    """按配置构建 Provider；API Key 为空的云端 Provider 跳过。"""
    providers: dict[str, LLMProvider] = {}
    for name, cfg in providers_config.items():
        try:
            if cfg["type"] == "ollama":
                providers[name] = OllamaProvider(
                    host=cfg.get("host", "http://localhost:11434"),
                    default_model=cfg.get("models", {}).get("chat", "qwen2.5:7b"),
                )
            elif cfg["type"] == "openai_compatible":
                api_key = (cfg.get("api_key") or "").strip()
                if not api_key:
                    logger.info("Provider '%s' 未配置 API Key，跳过", name)
                    continue
                providers[name] = OpenAICompatibleProvider(
                    name=name,
                    base_url=cfg["base_url"],
                    api_key=api_key,
                    default_model=cfg.get("models", {}).get("chat", ""),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Provider '%s' 初始化失败: %s", name, exc)
    return providers


class LLMGateway:
    def __init__(
        self,
        config_path: str | Path | None = None,
        providers: dict[str, LLMProvider] | None = None,
        routing: dict | None = None,
    ):
        if providers is not None and routing is not None:
            # 测试注入路径
            self.providers = providers
            self.routing = routing
        else:
            config = load_models_config(config_path)
            self.providers = build_providers(config.get("providers", {}))
            self.routing = config.get("routing", {})

        self.current_model: str | None = None
        self.current_provider: str | None = None

    # ---- 路由 ----
    def _candidates(self, task_type: str) -> list[dict]:
        return self.routing.get(task_type) or self.routing.get("generate") or []

    @staticmethod
    def _parse_model_id(model_id: str) -> tuple[str | None, str]:
        if ":" in model_id:
            provider, _, model = model_id.partition(":")
            return provider, model
        return None, model_id

    async def chat(
        self,
        messages: list[dict],
        task_type: str = "generate",
        stream: bool = False,
        model_id: str | None = None,
        **kwargs,
    ):
        """按路由链依次尝试，失败自动降级；全链失败抛 AllProvidersFailedError。"""
        if model_id:
            provider_name, model = self._parse_model_id(model_id)
            candidates = [{"provider": provider_name, "model": model}] if provider_name else [
                {"provider": p.name, "model": model} for p in self.providers.values()
            ]
        else:
            candidates = self._candidates(task_type)

        last_error: Exception | None = None
        for candidate in candidates:
            name = candidate.get("provider")
            model = candidate.get("model")
            provider = self.providers.get(name)
            if provider is None or model is None:
                continue
            try:
                if not await provider.is_available():
                    logger.info("Provider '%s' 健康检查未通过，降级", name)
                    continue
                result = await provider.chat(messages, model=model, stream=stream, **kwargs)
                self.current_model = model
                self.current_provider = name
                return result
            except Exception as exc:  # noqa: BLE001 - 单个 Provider 失败继续降级
                last_error = exc
                logger.warning("Provider '%s' 调用失败，降级: %s", name, exc)

        raise AllProvidersFailedError(
            f"所有模型均不可用（任务: {task_type}）最后错误: {last_error}"
        )

    # ---- 模型清单（前端选择器） ----
    async def available_models(self) -> list[dict]:
        options: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for task_candidates in self.routing.values():
            for candidate in task_candidates:
                name, model = candidate.get("provider"), candidate.get("model")
                if not name or not model or (name, model) in seen:
                    continue
                seen.add((name, model))
                provider = self.providers.get(name)
                if provider is None:
                    options.append(
                        {"id": f"{name}:{model}", "name": model, "provider": name,
                         "type": "local" if name == "ollama" else "cloud", "available": False}
                    )
                    continue
                try:
                    available = await provider.is_available()
                except Exception:  # noqa: BLE001
                    available = False
                options.append(
                    {"id": f"{name}:{model}", "name": model, "provider": name,
                     "type": "local" if name == "ollama" else "cloud", "available": available}
                )
        return options
