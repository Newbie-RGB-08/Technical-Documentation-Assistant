"""LLM 统一网关（PRD 3.4 / 4.8）。

- 统一接口 chat()/embed()/rerank()，业务层不感知底层差异
- config/models.yaml 驱动任务路由链，链上靠前失败自动降级
- ${VAR:default} 环境变量解析；API Key 为空时对应 Provider 不启用
- model_id 覆盖（"provider:model"）支持前端手动切换模型

V1.5.x 新增（启动自检与本地模型自动发现，方案 v2）：
- probe_all()：启动期并发探测，一次快照同时拿到健康与已装模型清单
- 健康缓存（TTL）：chat() 读缓存跳过已知不可用的 Provider，缓存过期现场重探
- 自动绑定：配置链全部不可用时，从本地已装模型里挑兜底（运行时，不落盘）
- llm_timeout_seconds 真正接入：单 Provider 调用超时即降级
- available_models() 联合"配置声明 + 本地发现"两张清单，TTL 内零网络调用
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path

import yaml

from app.config import get_settings
from app.providers.base import AllProvidersFailedError, LLMProvider
from app.providers.health import (
    AutoBinding,
    Reason,
    ProviderStatus,
    model_family,
)
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
        *,
        # 探测参数可注入覆盖（测试用）；缺省读全局配置
        probe_enabled: bool | None = None,
        probe_timeout: float | None = None,
        health_ttl: float | None = None,
        llm_timeout: float | None = None,
    ):
        if providers is not None and routing is not None:
            # 测试注入路径
            self.providers = providers
            self.routing = routing
        else:
            config = load_models_config(config_path)
            self.providers = build_providers(config.get("providers", {}))
            self.routing = config.get("routing", {})

        settings = get_settings()
        self.probe_enabled: bool = (
            probe_enabled if probe_enabled is not None else settings.provider_probe_enabled
        )
        self.probe_timeout: float = float(
            probe_timeout if probe_timeout is not None else settings.provider_probe_timeout_seconds
        )
        self.health_ttl: float = float(
            health_ttl if health_ttl is not None else settings.provider_health_ttl_seconds
        )
        self.llm_timeout: float = float(
            llm_timeout if llm_timeout is not None else settings.llm_timeout_seconds
        )

        self.current_model: str | None = None
        self.current_provider: str | None = None
        # 健康快照：name -> ProviderStatus（checked_at 为 0 表示已失效）
        self._health: dict[str, ProviderStatus] = {}
        # 自动绑定兜底：task_type -> AutoBinding | None（bind_defaults 填充）
        self.auto_bindings: dict[str, AutoBinding | None] = {}
        # 启动期预选（供 meta 事件展示）；last_selection 为最近一次成功调用
        self.default_selection: dict | None = None
        self.last_selection: dict | None = None

    # ---- 探测与发现 ----
    def _provider_type(self, name: str, provider: LLMProvider | None) -> str:
        if provider is not None:
            return getattr(provider, "provider_type", "cloud")
        return "local" if name == "ollama" else "cloud"

    def _is_fresh(self, status: ProviderStatus) -> bool:
        return (
            status.checked_at > 0
            and (time.monotonic() - status.checked_at) <= self.health_ttl
        )

    async def _probe_one(
        self, provider: LLMProvider, timeout: float | None = None
    ) -> ProviderStatus | None:
        """探测单个 Provider 并写缓存；探测自身异常归一为 ERROR，绝不抛出。"""
        name = getattr(provider, "name", "?")
        effective_timeout = float(timeout) if timeout is not None else self.probe_timeout
        try:
            status = await provider.probe(effective_timeout)
        except Exception as exc:  # noqa: BLE001
            status = ProviderStatus(
                name=name,
                type=self._provider_type(name, provider),
                available=False,
                reason=Reason.ERROR.value,
                detail=str(exc) or type(exc).__name__,
            )
        status.checked_at = time.monotonic()
        self._health[name] = status
        return status

    def invalidate(self, name: str) -> None:
        """调用失败即失效缓存，下一候选/下一轮会现场重探。"""
        status = self._health.get(name)
        if status is not None:
            status.checked_at = 0.0

    async def probe_all(self, timeout: float | None = None) -> dict[str, ProviderStatus]:
        """并发探测所有 Provider；返回快照。探测关闭时返回空操作。"""
        if not self.probe_enabled:
            return dict(self._health)
        per_timeout = float(timeout) if timeout is not None else self.probe_timeout
        await asyncio.gather(
            *(self._probe_one(p, per_timeout) for p in list(self.providers.values()))
        )
        return dict(self._health)

    async def _status_now(self, provider: LLMProvider) -> ProviderStatus | None:
        """缓存新鲜则直读，否则现场重探（TTL 快捷路径 + 失效重探）。"""
        status = self._health.get(provider.name)
        if status is not None and self._is_fresh(status):
            return status
        return await self._probe_one(provider)

    def installed_chat_models(self) -> list[dict]:
        """跨本地 Provider 汇总 detected 对话模型（供联合清单与诊断）。"""
        rows: list[dict] = []
        for name, status in self._health.items():
            for info in status.chatable_models():
                rows.append(
                    {
                        "provider": name,
                        "name": info.name,
                        "parameter_size": info.parameter_size,
                        "family": info.family,
                        "size_bytes": info.size_bytes,
                        "modified_at": info.modified_at,
                    }
                )
        return rows

    # ---- 自动绑定（方案 v2 / D3 fit_binding） ----
    def _raw_candidates(self, task_type: str) -> list[dict]:
        return self.routing.get(task_type) or self.routing.get("generate") or []

    def _chat_task_types(self) -> list[str]:
        return [
            tt for tt, entries in self.routing.items()
            if any((entry or {}).get("model") for entry in (entries or []))
        ]

    def fit_binding(self, task_type: str) -> AutoBinding | None:
        """从本地已装模型里为任务挑兜底（纯输入输出，可单测）。

        规则：
        - 配置链上任一候选可用 → 不绑定（配置优先，不越位）；
        - 候选 Provider 构建了但无健康记录（未探测）→ 不绑定（未知即不猜）；
        - 家族匹配（配置模型名冒号前的小写基础名 == 已装模型 family）优先；
        - generate 取参数量最大，rewrite 取最小；参数量未知时按名称字典序。
        """
        if not self.probe_enabled:
            return None
        raw = self._raw_candidates(task_type)
        if not raw:
            return None
        for candidate in raw:
            name, model = candidate.get("provider"), candidate.get("model")
            if not name or not model:
                continue
            provider = self.providers.get(name)
            if provider is None:
                continue  # 未构建（如云端无 Key）→ 视作不可用，继续判定
            status = self._health.get(name)
            if status is None:
                return None  # 存在但状态未知 → 不猜
            if not status.available:
                continue
            if status.type == "local":
                # 本地：服务在线但该配置模型未装 → 该候选无效，继续看下家
                installed_names = {m.name for m in status.chatable_models()}
                if model in installed_names:
                    return None  # 配置候选可用 → 无需兜底
                continue
            return None  # 云端候选可用 → 无需兜底

        want_families = {model_family(c.get("model")) for c in raw if c.get("model")}
        for provider in self.providers.values():
            status = self._health.get(provider.name)
            if status is None or not status.available or status.type != "local":
                continue
            installed = status.chatable_models()
            if not installed:
                continue
            family_hits = [m for m in installed if m.family in want_families]
            pool = family_hits or installed
            want_small = task_type == "rewrite"
            sized = [m for m in pool if m.parameter_size is not None]
            if sized:
                if want_small:
                    chosen = min(sized, key=lambda m: (m.parameter_size, m.name))
                else:
                    chosen = max(sized, key=lambda m: (m.parameter_size, m.name))
                reason = "family_match" if family_hits else ("size_min" if want_small else "size_max")
            else:
                chosen = sorted(pool, key=lambda m: m.name)[0]
                reason = "family_match" if family_hits else "fallback"
            return AutoBinding(provider=provider.name, model=chosen.name, reason=reason)
        return None

    def bind_defaults(self) -> dict[str, AutoBinding | None]:
        """对全部含"模型"的聊天任务计算一次兜底绑定（generate/rewrite）。"""
        self.auto_bindings = {tt: self.fit_binding(tt) for tt in self._chat_task_types()}
        return self.auto_bindings

    def choose_default(self) -> None:
        """启动探测后预选 generate 默认模型，供 meta 事件与前端头部药丸展示。"""
        binding = self.auto_bindings.get("generate")
        if binding is not None:
            self.current_provider, self.current_model = binding.provider, binding.model
            self.default_selection = {
                "provider": binding.provider, "model": binding.model, "auto": True,
            }
            return
        for candidate in self._raw_candidates("generate"):
            name, model = candidate.get("provider"), candidate.get("model")
            if not name or not model:
                continue
            status = self._health.get(name)
            if status is not None and status.available:
                self.current_provider, self.current_model = name, model
                self.default_selection = {"provider": name, "model": model, "auto": False}
                return

    # ---- 路由 ----
    def _candidates(self, task_type: str) -> list[dict]:
        """任务候选链：新鲜缓存剔除已知不可用项与"模型未装"项，末位追加自动绑定兜底。"""
        candidates: list[dict] = []
        for candidate in self._raw_candidates(task_type):
            name, model = candidate.get("provider"), candidate.get("model")
            if not name or not model:
                continue
            status = self._health.get(name)
            if status is not None and self._is_fresh(status):
                if not status.available:
                    continue  # 新鲜且不可用 → 剔除（省一次探活）；过期进入循环时现场重探
                if status.type == "local":
                    installed_names = {m.name for m in status.chatable_models()}
                    if installed_names and model not in installed_names:
                        # 服务在线但配置模型未安装 → 跳过（省一次 404），交给兜底绑定
                        continue
            candidates.append(candidate)
        binding = self.auto_bindings.get(task_type) if self.auto_bindings else None
        if binding is not None:
            candidates.append(
                {"provider": binding.provider, "model": binding.model, "auto": True}
            )
        return candidates

    @staticmethod
    def _parse_model_id(model_id: str) -> tuple[str | None, str]:
        if ":" in model_id:
            provider, _, model = model_id.partition(":")
            return provider, model
        return None, model_id

    async def _provider_ready(self, provider: LLMProvider) -> tuple[bool, ProviderStatus | None]:
        """调用前可用性：探测开启走健康快照，关闭则退回旧 is_available()。"""
        if not self.probe_enabled:
            try:
                return bool(await provider.is_available()), None
            except Exception:  # noqa: BLE001
                return False, None
        status = await self._status_now(provider)
        if status is not None and not status.available:
            logger.info(
                "Provider '%s' 健康未通过（%s），降级", provider.name, status.reason
            )
            return False, status
        return True, status

    async def chat(
        self,
        messages: list[dict],
        task_type: str = "generate",
        stream: bool = False,
        model_id: str | None = None,
        **kwargs,
    ):
        """按路由链依次尝试，失败自动降级；全链失败抛 AllProvidersFailedError。

        - 每个候选调用受 ``llm_timeout_seconds`` 保护，超时即降级并失效该缓存
        - 用户 model_id（手动选择）优先级最高，不参与健康过滤与自动绑定
        """
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
                ready, _status = await self._provider_ready(provider)
                if not ready:
                    continue
                result = await asyncio.wait_for(
                    provider.chat(messages, model=model, stream=stream, **kwargs),
                    timeout=self.llm_timeout,
                )
                self.current_model = model
                self.current_provider = name
                self.last_selection = {
                    "provider": name,
                    "model": model,
                    "task_type": task_type,
                    "auto": bool(candidate.get("auto")),
                    "via": ("user" if model_id
                            else ("auto_binding" if candidate.get("auto") else "route_chain")),
                }
                return result
            except asyncio.TimeoutError as exc:
                last_error = exc
                logger.warning("Provider '%s' 调用超时（%.1fs），降级", name, self.llm_timeout)
                self.invalidate(name)
            except Exception as exc:  # noqa: BLE001 - 单个 Provider 失败继续降级
                last_error = exc
                logger.warning("Provider '%s' 调用失败，降级: %s", name, exc)
                self.invalidate(name)

        raise AllProvidersFailedError(
            f"所有模型均不可用（任务: {task_type}）最后错误: {last_error}"
        )

    # ---- 模型清单（前端选择器） ----
    def _single_option(self, name: str, status: ProviderStatus | None, model: str, source: str) -> dict:
        ptype = self._provider_type(name, self.providers.get(name))
        option: dict = {
            "id": f"{name}:{model}",
            "name": model,
            "provider": name,
            "type": ptype,
            "source": source,
            "missing_models": None,
        }
        if status is None:
            option["available"] = False
            option["reason"] = (
                Reason.NO_API_KEY.value if ptype == "cloud" else Reason.SERVICE_UNREACHABLE.value
            )
            return option
        if not status.available:
            option["available"] = False
            option["reason"] = status.reason
            return option
        if ptype == "local":
            installed_names = {m.name for m in status.chatable_models()}
            if model in installed_names:
                option["available"] = True
                option["reason"] = Reason.OK.value
            else:
                # 服务在线但该配置声明的模型未安装（自动绑定已消化，纯说明性标记）
                option["available"] = False
                option["reason"] = Reason.MODEL_NOT_PULLED.value
                option["missing_models"] = [model]
        else:
            option["available"] = True
            option["reason"] = Reason.OK.value
        return option

    async def available_models(self, *, refresh: bool = False) -> list[dict]:
        """联合清单：配置声明项 + 本地发现项；TTL 内零网络调用（refresh=True 强制重探）。"""
        statuses: dict[str, ProviderStatus | None] = {}

        async def _status_for(name: str, provider: LLMProvider | None) -> ProviderStatus | None:
            if name in statuses:
                return statuses[name]
            if provider is None:
                statuses[name] = None
                return None
            if not self.probe_enabled:
                statuses[name] = self._health.get(name)
                return statuses[name]
            status = self._health.get(name)
            if status is None or not self._is_fresh(status):
                status = await self._probe_one(provider)
            statuses[name] = status
            return status

        if refresh and self.probe_enabled:
            await self.probe_all()

        options: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for task_candidates in self.routing.values():
            for candidate in task_candidates:
                name, model = candidate.get("provider"), candidate.get("model")
                if not name or not model or (name, model) in seen:
                    continue
                seen.add((name, model))
                if not self.probe_enabled and self.providers.get(name) is not None:
                    # 关闭探测时保持旧口径：实时 is_available
                    provider = self.providers[name]
                    try:
                        live_ok = bool(await provider.is_available())
                    except Exception:  # noqa: BLE001
                        live_ok = False
                    options.append(
                        {
                            "id": f"{name}:{model}",
                            "name": model,
                            "provider": name,
                            "type": self._provider_type(name, provider),
                            "source": "config",
                            "available": live_ok,
                            "reason": Reason.OK.value if live_ok else Reason.ERROR.value,
                            "missing_models": None,
                        }
                    )
                    continue
                option = self._single_option(
                    name, await _status_for(name, self.providers.get(name)), model, "config"
                )
                options.append(option)

        # 本地发现清单：未被配置声明覆盖的已装模型追加为可用项
        for row in self.installed_chat_models():
            key = (row["provider"], row["name"])
            if key in seen:
                continue
            seen.add(key)
            options.append(
                {
                    "id": f"{row['provider']}:{row['name']}",
                    "name": row["name"],
                    "provider": row["provider"],
                    "type": "local",
                    "source": "discovered",
                    "available": True,
                    "reason": Reason.OK.value,
                    "parameter_size": row["parameter_size"],
                    "family": row["family"],
                    "missing_models": None,
                }
            )

        # 可用项在前（稳定排序，组内保持配置/发现先后）
        options.sort(key=lambda item: item["available"] is not True)
        return options

    # ---- 诊断快照（/api/providers/status 消费） ----
    def status_report(self) -> dict:
        rows = []
        for name, status in self._health.items():
            rows.append(
                {
                    "name": name,
                    "type": status.type,
                    "available": status.available,
                    "reason": status.reason,
                    "detail": status.detail,
                    "installed_models": [
                        {
                            "name": info.name,
                            "parameter_size": info.parameter_size,
                            "family": info.family,
                            "size_bytes": info.size_bytes,
                        }
                        for info in status.chatable_models()
                    ],
                    "selected_for": sorted(
                        tt for tt, binding in self.auto_bindings.items()
                        if binding is not None and binding.provider == name
                    ),
                }
            )
        bindings = {
            tt: (
                {"provider": b.provider, "model": b.model, "reason": b.reason}
                if b is not None else None
            )
            for tt, b in self.auto_bindings.items()
        }
        return {"enabled": self.probe_enabled, "providers": rows, "bindings": bindings}

    # ---- meta 事件展示口径 ----
    def selected_payload(self) -> dict | None:
        """当前"将被用于回答"的模型选择（provider/model/auto）。"""
        if self.last_selection and self.last_selection.get("provider"):
            return {
                "provider": self.last_selection["provider"],
                "model": self.last_selection["model"],
                "auto": bool(self.last_selection.get("auto")),
            }
        if self.default_selection:
            return dict(self.default_selection)
        if self.current_provider and self.current_model:
            return {"provider": self.current_provider, "model": self.current_model, "auto": False}
        return None
