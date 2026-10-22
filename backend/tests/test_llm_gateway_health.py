"""V1.5.x（方案 v2）：Provider 健康探测、本地模型发现与自动绑定测试。

覆盖：
- 域模型解析（parameter_size / family / chatable 过滤 / list() 兼容 dict 与对象）
- Ollama / OpenAI 兼容 Provider 的 probe()（不可达 / 超时 / 模型发现 / 鉴权失败）
- 网关：probe_all 归一化、TTL 零网络、失效重探、候选链过滤、调用超时降级
- fit_binding 家族匹配 / 大小启发式 / 字典序 / 配置可用时释放 / 恢复配置优先
- 自动兜底命中（last_selection.auto）、model_id 手动选择优先级
- available_models 联合清单 / status_report
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

from app.providers.base import AllProvidersFailedError, LLMProvider
from app.providers.health import (
    DEFAULT_EXCLUDE_KEYWORDS,
    LocalModelInfo,
    ProviderStatus,
    Reason,
    is_chatable,
    model_family,
    parse_installed_models,
    parse_parameter_size,
)
from app.providers.ollama_provider import OllamaProvider
from app.providers.openai_provider import OpenAICompatibleProvider
from app.services.llm_gateway import LLMGateway


# ---------------- 工具 ----------------

def _fresh(status: ProviderStatus) -> ProviderStatus:
    status.checked_at = time.monotonic()
    return status


def _ok_local(models=None) -> ProviderStatus:
    return ProviderStatus(
        name="ollama", type="local", available=True, reason=Reason.OK.value,
        installed_models=models if models is not None else [
            LocalModelInfo(name="qwen2.5:7b", parameter_size=7.0, family="qwen2.5"),
            LocalModelInfo(name="qwen2.5:3b", parameter_size=3.0, family="qwen2.5"),
        ],
    )


def _cloud(reason: str = Reason.OK.value, available: bool = True) -> ProviderStatus:
    return ProviderStatus(name="deepseek", type="cloud", available=available, reason=reason)


def _down(reason: str = Reason.SERVICE_UNREACHABLE.value, ptype: str = "cloud") -> ProviderStatus:
    return ProviderStatus(name="_", type=ptype, available=False, reason=reason)


class ProbeProvider(LLMProvider):
    """探测画像可编程的假 Provider（不覆写 probe 之外的替代路径）。"""

    def __init__(self, name: str, status: ProviderStatus | None = None,
                 reply: str = "ok", fail: bool = False, chat_delay: float = 0.0):
        self.name = name
        self.provider_type = "local" if name == "ollama" else "cloud"
        self._status = status
        self._reply = reply
        self._fail = fail
        self._chat_delay = chat_delay
        self.probe_calls = 0
        self.chat_calls = 0

    async def probe(self, timeout: float) -> ProviderStatus:
        self.probe_calls += 1
        if self._status is not None:
            return self._status
        return ProviderStatus(
            name=self.name, type=self.provider_type, available=True,
            reason=Reason.OK.value,
        )

    async def is_available(self) -> bool:
        return self._status.available if self._status is not None else True

    async def chat(self, messages, model=None, stream=False, **kw):
        self.chat_calls += 1
        if self._chat_delay:
            await asyncio.sleep(self._chat_delay)
        if self._fail:
            raise RuntimeError(f"{self.name} exploded")
        if stream:
            async def _gen():
                for token in self._reply:
                    yield token
            return _gen()
        return self._reply


def _gateway(providers, routing, **kw) -> LLMGateway:
    kw.setdefault("probe_enabled", True)
    kw.setdefault("health_ttl", 30.0)
    kw.setdefault("llm_timeout", 5.0)
    kw.setdefault("probe_timeout", 1.0)
    return LLMGateway(providers=providers, routing=routing, **kw)


# ---------------- 域模型解析 ----------------

def test_parse_parameter_size_from_details_and_name():
    assert parse_parameter_size("7.6B") == 7.6
    assert parse_parameter_size("14B") == 14.0
    assert parse_parameter_size("334M") == pytest.approx(0.334, abs=1e-6)
    assert parse_parameter_size(None, "qwen2.5:7b") == 7.0
    assert parse_parameter_size(None, "phi3:3.8b") == 3.8
    assert parse_parameter_size(None, "tinyllama-1.1b") == 1.1
    assert parse_parameter_size(None, "mistral-small") is None


def test_model_family_normalized():
    assert model_family("qwen2.5:7b") == "qwen2.5"
    assert model_family("LLAMA3.1:8B") == "llama3.1"
    assert model_family("mistral-small") == "mistral"
    assert model_family("") == ""


def test_is_chatable_excludes_non_chat_models():
    assert is_chatable("llama3.1:8b") is True
    assert is_chatable("nomic-embed-text") is False
    assert is_chatable("bge-reranker-v2-m3") is False
    assert is_chatable("llama-guard-3") is False
    assert is_chatable("mxbai-embed-large") is False
    assert is_chatable("all-minilm:latest") is False
    # 自定义关键词覆盖
    assert is_chatable("special-test-model", ("special",)) is False
    assert is_chatable("special-test-model", DEFAULT_EXCLUDE_KEYWORDS) is True


def test_parse_installed_models_dict_and_object_forms():
    listing = {
        "models": [
            {"name": "qwen2.5:7b", "size": 123, "modified_at": "2024-01-01",
             "details": {"parameter_size": "7.6B", "family": "qwen2"}},
            {"name": "nomic-embed-text", "size": 1, "details": {"parameter_size": "0.1B"}},
        ]
    }
    infos = parse_installed_models(listing)
    assert [i.name for i in infos] == ["qwen2.5:7b", "nomic-embed-text"]
    assert infos[0].parameter_size == 7.6
    assert infos[0].family == "qwen2.5"
    assert infos[0].size_bytes == 123
    assert infos[0].chatable is True
    assert infos[1].chatable is False

    # pydantic 风格对象（SimpleNamespace 模拟）：字段名相同
    listing_obj = SimpleNamespace(
        models=[
            SimpleNamespace(name="llama3.1:8b", model=None, size=None, modified_at=None,
                            details=SimpleNamespace(parameter_size="8B", family="llama"))
        ]
    )
    infos2 = parse_installed_models(listing_obj)
    assert len(infos2) == 1
    assert infos2[0].parameter_size == 8.0
    assert infos2[0].family == "llama3.1"


# ---------------- Provider probe ----------------

class _StubOllamaClient:
    def __init__(self, result=None, exc: Exception | None = None, delay: float = 0.0):
        self._result = result
        self._exc = exc
        self._delay = delay

    async def list(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc:
            raise self._exc
        return self._result


_WIN_OLLAMA_LISTING = {
    "models": [
        {"name": "qwen2.5:7b", "size": 99, "modified_at": "2024-01-01",
         "details": {"parameter_size": "7.6B", "family": "qwen2"}},
        {"name": "nomic-embed-text", "size": 1,
         "details": {"parameter_size": "0.1B"}},
    ]
}


def test_ollama_probe_ok_discovers_models():
    provider = OllamaProvider(host="http://x", default_model="anything")
    provider.client = _StubOllamaClient(result=_WIN_OLLAMA_LISTING)
    status = asyncio.run(provider.probe(0.5))
    assert status.available is True
    assert status.reason == Reason.OK.value
    assert status.type == "local"
    assert len(status.installed_models) == 2
    chatable = status.chatable_models()
    assert [m.name for m in chatable] == ["qwen2.5:7b"]
    assert chatable[0].parameter_size == 7.6


def test_ollama_probe_service_unreachable_and_timeout():
    provider = OllamaProvider(host="http://x", default_model="anything")
    provider.client = _StubOllamaClient(exc=ConnectionError("refused"))
    status = asyncio.run(provider.probe(0.3))
    assert status.available is False
    assert status.reason == Reason.SERVICE_UNREACHABLE.value
    assert status.installed_models == []

    slow = OllamaProvider(host="http://x", default_model="anything")
    slow.client = _StubOllamaClient(result=_WIN_OLLAMA_LISTING, delay=1.0)
    status2 = asyncio.run(slow.probe(0.02))
    assert status2.available is False
    assert status2.reason == Reason.TIMEOUT.value


def test_ollama_probe_no_chat_models():
    provider = OllamaProvider(host="http://x", default_model="anything")
    provider.client = _StubOllamaClient(result={
        "models": [{"name": "nomic-embed-text", "size": 1, "details": {}}]
    })
    status = asyncio.run(provider.probe(0.3))
    assert status.available is False
    assert status.reason == Reason.NO_CHAT_MODELS.value


class _StubOpenAIModels:
    def __init__(self, exc: Exception | None = None, delay: float = 0.0):
        self._exc = exc
        self._delay = delay

    async def list(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc:
            raise self._exc
        return []


class _StubOpenAIClient:
    def __init__(self, exc: Exception | None = None, delay: float = 0.0):
        self.models = _StubOpenAIModels(exc, delay)


class _AuthError(Exception):
    status_code = 401


def test_openai_probe_ok_and_auth_failed():
    provider = OpenAICompatibleProvider(name="deepseek", base_url="http://x",
                                        api_key="sk-test", default_model="m")
    provider.client = _StubOpenAIClient()
    status = asyncio.run(provider.probe(0.3))
    assert status.available is True
    assert status.reason == Reason.OK.value
    assert status.type == "cloud"

    provider2 = OpenAICompatibleProvider(name="deepseek", base_url="http://x",
                                         api_key="sk-bad", default_model="m")
    provider2.client = _StubOpenAIClient(exc=_AuthError())
    status2 = asyncio.run(provider2.probe(0.3))
    assert status2.available is False
    assert status2.reason == Reason.AUTH_FAILED.value

    provider3 = OpenAICompatibleProvider(name="deepseek", base_url="http://x",
                                         api_key="sk-bad", default_model="m")
    provider3.client = _StubOpenAIClient(exc=ConnectionError("offline"))
    status3 = asyncio.run(provider3.probe(0.3))
    assert status3.available is False
    assert status3.reason == Reason.SERVICE_UNREACHABLE.value


def test_base_probe_delegates_to_legacy_is_available():
    """旧式 mock（未实现 probe，只有 is_available）经基类默认实现仍可用。"""

    class Legacy(LLMProvider):
        name = "legacy"

        async def chat(self, messages, model=None, stream=False, **kw):
            return "ok"

        async def is_available(self) -> bool:
            return self.flag

    legacy = Legacy()
    legacy.flag = True
    status = asyncio.run(legacy.probe(0.5))
    assert status.available is True
    legacy.flag = False
    status2 = asyncio.run(legacy.probe(0.5))
    assert status2.available is False


# ---------------- 网关探测与缓存 ----------------

@pytest.mark.asyncio
async def test_probe_all_populates_and_survives_probe_exception():
    ok = ProbeProvider("ollama", status=_ok_local())
    down = ProbeProvider("deepseek", status=_down())

    class Broken(LLMProvider):
        name = "broken"
        provider_type = "cloud"

        async def probe(self, timeout):
            raise RuntimeError("boom")

        async def chat(self, messages, model=None, stream=False, **kw):
            raise RuntimeError("no chat")

        async def is_available(self):
            return False

    broken = Broken()
    gw = _gateway({"ollama": ok, "broken": broken, "deepseek": down},
                  {"generate": [{"provider": "ollama", "model": "m"}]})
    statuses = await gw.probe_all()
    assert set(statuses) == {"ollama", "broken", "deepseek"}
    assert statuses["ollama"].available is True
    assert statuses["broken"].available is False
    assert statuses["broken"].reason == Reason.ERROR.value
    assert statuses["deepseek"].reason == Reason.SERVICE_UNREACHABLE.value
    assert statuses["ollama"].installed_models  # 发现清单已入缓存


@pytest.mark.asyncio
async def test_probe_disabled_keeps_legacy_path():
    legacy_unused = ProbeProvider("deepseek", status=_down())  # is_available 依 status → False
    gw = _gateway({"deepseek": legacy_unused},
                  {"generate": [{"provider": "deepseek", "model": "m"}]},
                  probe_enabled=False)
    models = await gw.available_models()
    assert models[0]["available"] is False
    assert legacy_unused.probe_calls == 0  # 关闭探测 → 不走 probe
    with pytest.raises(AllProvidersFailedError):
        await gw.chat([{"role": "user", "content": "x"}])
    assert legacy_unused.chat_calls == 0   # 旧路径 is_available=false → 不调用 chat


@pytest.mark.asyncio
async def test_candidates_filter_by_fresh_cache_no_extra_probe():
    local_down = ProbeProvider("ollama", status=_down(ptype="local"))
    cloud_ok = ProbeProvider("deepseek", status=_fresh(_cloud()))
    gw = _gateway({"ollama": local_down, "deepseek": cloud_ok},
                  {"generate": [{"provider": "ollama", "model": "m1"},
                                 {"provider": "deepseek", "model": "m2"}]})
    await gw.probe_all()
    local_down.probe_calls = 0
    result = await gw.chat([{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert local_down.probe_calls == 0, "新鲜缓存下剔除不可用项时不得重复探测"
    assert local_down.chat_calls == 0
    assert gw.current_provider == "deepseek"


@pytest.mark.asyncio
async def test_stale_cache_reprobe_discovers_recovery():
    stale_ok = _ok_local([LocalModelInfo(name="m1", parameter_size=1.0, family="m1")])
    stale_ok.checked_at = 0.0  # 失效
    local = ProbeProvider("ollama", status=stale_ok, reply="first-local")
    cloud_down = ProbeProvider("deepseek", status=_down())
    gw = _gateway({"ollama": local, "deepseek": cloud_down},
                  {"generate": [{"provider": "ollama", "model": "m1"},
                                 {"provider": "deepseek", "model": "m2"}]})
    result = await gw.chat([{"role": "user", "content": "hi"}])
    assert result == "first-local"
    assert local.probe_calls == 1  # 过期 → 现场重探一次发现恢复


@pytest.mark.asyncio
async def test_ttl_fresh_cache_means_zero_probe_during_chat():
    ok = ProbeProvider("ollama", status=_ok_local())
    gw = _gateway({"ollama": ok}, {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"}]})
    await gw.probe_all()
    ok.probe_calls = 0
    await gw.chat([{"role": "user", "content": "1"}])
    await gw.chat([{"role": "user", "content": "2"}])
    assert ok.probe_calls == 0, "TTL 内对话不得进行额外探测"
    assert ok.chat_calls == 2


@pytest.mark.asyncio
async def test_invalidate_on_chat_failure():
    fail_local = ProbeProvider("ollama", status=_ok_local(), fail=True)
    cloud_ok = ProbeProvider("deepseek", status=None, reply="deep")
    gw = _gateway({"ollama": fail_local, "deepseek": cloud_ok},
                  {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"},
                                 {"provider": "deepseek", "model": "m2"}]})
    await gw.probe_all()
    assert gw._health["ollama"].checked_at > 0
    result = await gw.chat([{"role": "user", "content": "hi"}])
    assert result == "deep"
    assert fail_local.chat_calls == 1
    assert gw._health["ollama"].checked_at == 0.0, "调用失败必须失效该 Provider 缓存"


@pytest.mark.asyncio
async def test_chat_timeout_degrades_to_next_provider():
    slow = ProbeProvider("ollama", status=_ok_local(), reply="slow", chat_delay=0.2)
    fast = ProbeProvider("deepseek", status=None, reply="fast")
    gw = _gateway({"ollama": slow, "deepseek": fast},
                  {"generate": [{"provider": "ollama", "model": "m1"},
                                 {"provider": "deepseek", "model": "m2"}]},
                  llm_timeout=0.05)
    result = await gw.chat([{"role": "user", "content": "hi"}])
    assert result == "fast"
    assert gw.current_provider == "deepseek"


# ---------------- 自动绑定 ----------------

@pytest.mark.asyncio
async def test_fit_binding_family_match_and_used_in_chat():
    ok_ollama = ProbeProvider(
        "ollama",
        status=_ok_local([
            LocalModelInfo(name="qwen2.5:14b", parameter_size=14.0, family="qwen2.5"),
            LocalModelInfo(name="llama3.1:8b", parameter_size=8.0, family="llama3.1"),
        ]),
        reply="auto-answer",
    )
    down_cloud = ProbeProvider("deepseek", status=_down())
    gw = _gateway({"ollama": ok_ollama, "deepseek": down_cloud},
                  {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"},
                                 {"provider": "deepseek", "model": "deepseek-chat"}]})
    await gw.probe_all()
    bindings = gw.bind_defaults()
    binding = bindings["generate"]
    assert binding is not None
    assert binding.provider == "ollama"
    assert binding.model == "qwen2.5:14b", "应家族匹配 qwen2.5 而非随意挑最大/首项"
    assert binding.reason == "family_match"

    result = await gw.chat([{"role": "user", "content": "hi"}], task_type="generate")
    assert result == "auto-answer"
    assert gw.current_model == "qwen2.5:14b"
    assert gw.last_selection["auto"] is True
    assert gw.last_selection["via"] == "auto_binding"


@pytest.mark.asyncio
async def test_fit_binding_size_heuristic_and_fallback():
    installed = [
        LocalModelInfo(name="llama3.1:8b", parameter_size=8.0, family="llama3.1"),
        LocalModelInfo(name="mistral:12b", parameter_size=12.0, family="mistral"),
    ]
    ok_ollama = ProbeProvider("ollama", status=_ok_local(installed))
    gw = _gateway({"ollama": ok_ollama},
                  {"generate": [{"provider": "ollama", "model": "whatever:20b"}],
                   "rewrite": [{"provider": "ollama", "model": "whatever:1b"}]})
    await gw.probe_all()
    bindings = gw.bind_defaults()
    assert bindings["generate"].model == "mistral:12b"
    assert bindings["generate"].reason == "size_max"
    assert bindings["rewrite"].model == "llama3.1:8b"
    assert bindings["rewrite"].reason == "size_min"

    # 参数量未知 → 名称字典序，reason=fallback
    unknown = [
        LocalModelInfo(name="zz-model", family="zz"),
        LocalModelInfo(name="aa-model", family="aa"),
    ]
    ok2 = ProbeProvider("ollama", status=_ok_local(unknown))
    gw2 = _gateway({"ollama": ok2}, {"generate": [{"provider": "ollama", "model": "x"}]})
    await gw2.probe_all()
    binding2 = gw2.bind_defaults()["generate"]
    assert binding2.model == "aa-model"
    assert binding2.reason == "fallback"


@pytest.mark.asyncio
async def test_binding_released_when_configured_model_installed():
    # 第一步：配置模型未装 → 绑定兜底
    ok_ollama = ProbeProvider(
        "ollama", status=_ok_local([
            LocalModelInfo(name="llama3.1:8b", parameter_size=8.0, family="llama3.1")
        ])
    )
    gw = _gateway({"ollama": ok_ollama},
                  {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"}]})
    await gw.probe_all()
    assert gw.bind_defaults()["generate"] is not None
    # 第二步：配置模型装上了 → 绑定释放（配置优先）
    ok_ollama._status = _ok_local([
        LocalModelInfo(name="qwen2.5:7b", parameter_size=7.0, family="qwen2.5"),
        LocalModelInfo(name="llama3.1:8b", parameter_size=8.0, family="llama3.1"),
    ])
    await gw.probe_all()
    assert gw.bind_defaults()["generate"] is None


@pytest.mark.asyncio
async def test_fit_binding_skips_when_config_candidate_available():
    ok = ProbeProvider("ollama", status=_ok_local())
    gw = _gateway({"ollama": ok}, {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"}]})
    await gw.probe_all()
    assert gw.bind_defaults()["generate"] is None
    # 未探测（无健康记录）→ 不猜
    no_probe = ProbeProvider("deepseek")  # status None → 默认可用，但从未 probe
    gw2 = _gateway({"deepseek": no_probe}, {"generate": [{"provider": "deepseek", "model": "m"}]})
    assert gw2.fit_binding("generate") is None


@pytest.mark.asyncio
async def test_choose_default_and_selected_payload():
    ok_ollama = ProbeProvider(
        "ollama",
        status=_ok_local([LocalModelInfo(name="llama3.1:8b", parameter_size=8.0, family="llama3.1")]),
    )
    manual = ProbeProvider("manual-cloud", status=None, reply="m")  # 从未探测也可用
    down_cloud = ProbeProvider("deepseek", status=_down())
    gw = _gateway({"ollama": ok_ollama, "manual-cloud": manual, "deepseek": down_cloud},
                  {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"}]})
    await gw.probe_all()
    gw.bind_defaults()
    gw.choose_default()
    assert (gw.current_provider, gw.current_model) == ("ollama", "llama3.1:8b")
    selected = gw.selected_payload()
    assert selected == {"provider": "ollama", "model": "llama3.1:8b", "auto": True}

    # 手动选择成功后 → last_selection 优先且 auto=False
    await gw.chat([{"role": "user", "content": "x"}], model_id="manual-cloud:some")
    selected2 = gw.selected_payload()
    assert selected2 == {"provider": "manual-cloud", "model": "some", "auto": False}


@pytest.mark.asyncio
async def test_manual_model_id_preferred_and_hard_fails_when_unavailable():
    ok_local = ProbeProvider("ollama", status=_ok_local())
    down = ProbeProvider("deepseek", status=_down())
    gw = _gateway({"ollama": ok_local, "deepseek": down},
                  {"generate": [{"provider": "ollama", "model": "m1"}]})
    await gw.probe_all()
    await gw.chat([{"role": "user", "content": "x"}], model_id="ollama:qwen2.5:3b")
    assert ok_local.chat_calls == 1
    assert gw.last_selection["via"] == "user"
    assert gw.last_selection["auto"] is False

    # 手动选中的模型不可用 → 不静默降级（用户意图最高，报结构化引导错误）
    with pytest.raises(AllProvidersFailedError):
        await gw.chat([{"role": "user", "content": "x"}], model_id="deepseek:m")


# ---------------- available_models / status_report ----------------

@pytest.mark.asyncio
async def test_available_models_union_config_and_discovered():
    ok_ollama = ProbeProvider(
        "ollama", status=_ok_local([
            LocalModelInfo(name="qwen2.5:7b", parameter_size=7.0, family="qwen2.5"),
            LocalModelInfo(name="llama3.1:8b", parameter_size=8.0, family="llama3.1"),
            LocalModelInfo(name="nomic-embed-text", family="nomic", chatable=False),
        ])
    )
    ok_cloud = ProbeProvider("deepseek", status=None)
    gw = _gateway({"ollama": ok_ollama, "deepseek": ok_cloud},
                  {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"},
                                 {"provider": "deepseek", "model": "deepseek-chat"}],
                   "rewrite": [{"provider": "ollama", "model": "qwen2.5:3b"}]})
    await gw.probe_all()
    models = await gw.available_models()
    by_id = {m["id"]: m for m in models}

    assert by_id["ollama:qwen2.5:7b"]["available"] is True
    assert by_id["ollama:qwen2.5:7b"]["source"] == "config"
    # 服务在线但 rewrite 声明的模型未安装 → 标注说明（非错误态）
    assert by_id["ollama:qwen2.5:3b"]["available"] is False
    assert by_id["ollama:qwen2.5:3b"]["reason"] == Reason.MODEL_NOT_PULLED.value
    assert by_id["ollama:qwen2.5:3b"]["missing_models"] == ["qwen2.5:3b"]
    # 云端可用
    assert by_id["deepseek:deepseek-chat"]["available"] is True
    # 本地发现模型并入清单且可用、带元数据
    discovered = by_id["ollama:llama3.1:8b"]
    assert discovered["available"] is True and discovered["source"] == "discovered"
    assert discovered["parameter_size"] == 8.0
    # 非对话模型不出现在清单里
    assert "ollama:nomic-embed-text" not in by_id
    # 可用项应排在前
    assert models[0]["available"] is True


@pytest.mark.asyncio
async def test_available_models_ghost_provider_marks_no_api_key():
    gw = _gateway({}, {"generate": [{"provider": "deepseek", "model": "deepseek-chat"}]})
    models = await gw.available_models()
    assert models[0]["available"] is False
    assert models[0]["reason"] == Reason.NO_API_KEY.value
    assert models[0]["type"] == "cloud"


@pytest.mark.asyncio
async def test_available_models_refresh_forces_reprobe():
    ok = ProbeProvider("ollama", status=_ok_local())
    gw = _gateway({"ollama": ok}, {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"}]})
    await gw.probe_all()
    ok.probe_calls = 0
    await gw.available_models()             # TTL 内 → 零网络
    assert ok.probe_calls == 0
    await gw.available_models(refresh=True)  # 显式刷新 → 全量重探
    assert ok.probe_calls == 1


@pytest.mark.asyncio
async def test_status_report_shape_and_bindings():
    ok_ollama = ProbeProvider(
        "ollama", status=_ok_local([
            LocalModelInfo(name="llama3.1:8b", parameter_size=8.0, family="llama3.1"),
            LocalModelInfo(name="nomic-embed-text", family="nomic", chatable=False),
        ])
    )
    down = ProbeProvider("deepseek", status=_down())
    gw = _gateway({"ollama": ok_ollama, "deepseek": down},
                  {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"}],
                   "rewrite": [{"provider": "ollama", "model": "qwen2.5:3b"}]})
    await gw.probe_all()
    gw.bind_defaults()
    report = gw.status_report()
    by_name = {p["name"]: p for p in report["providers"]}
    assert by_name["ollama"]["available"] is True
    assert len(by_name["ollama"]["installed_models"]) == 1, "embedding 模型不应进对话候选"
    assert by_name["deepseek"]["available"] is False
    assert by_name["deepseek"]["reason"] == Reason.SERVICE_UNREACHABLE.value
    assert by_name["deepseek"]["type"] == "cloud"
    assert report["bindings"]["generate"]["model"] == "llama3.1:8b"
    assert report["bindings"]["rewrite"]["model"] == "llama3.1:8b"
    assert "generate" in by_name["ollama"]["selected_for"]
