"""阶段 4 测试：LLM 网关（mock Provider 降级链）与问题改写。"""
import pytest

from app.core.query_rewriter import rewrite_query
from app.providers.base import AllProvidersFailedError, LLMProvider
from app.services.llm_gateway import LLMGateway, resolve_env_placeholders


class FakeProvider(LLMProvider):
    def __init__(self, name: str, available: bool = True, reply: str = "ok", fail: bool = False):
        self.name = name
        self._available = available
        self._reply = reply
        self._fail = fail
        self.calls: list[dict] = []

    async def chat(self, messages, model=None, stream=False, **kwargs):
        self.calls.append({"messages": messages, "model": model, "stream": stream})
        if self._fail:
            raise RuntimeError(f"{self.name} exploded")
        if stream:
            async def _gen():
                for token in self._reply:
                    yield token
            return _gen()
        return self._reply

    async def is_available(self) -> bool:
        return self._available


def _gateway(providers, routing):
    return LLMGateway(providers=providers, routing=routing)


@pytest.mark.asyncio
async def test_env_placeholder_resolution(monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-123")
    assert resolve_env_placeholders("${MY_KEY}") == "sk-123"
    assert resolve_env_placeholders("${MISSING_KEY:default-val}") == "default-val"
    assert resolve_env_placeholders("${MISSING_KEY}") == ""
    assert resolve_env_placeholders("https://${HOST:localhost}:8000") == "https://localhost:8000"


@pytest.mark.asyncio
async def test_primary_provider_used():
    p1 = FakeProvider("ollama", reply="from-ollama")
    p2 = FakeProvider("deepseek", reply="from-deepseek")
    gw = _gateway(
        {"ollama": p1, "deepseek": p2},
        {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"},
                       {"provider": "deepseek", "model": "deepseek-chat"}]},
    )
    result = await gw.chat([{"role": "user", "content": "hi"}], task_type="generate")
    assert result == "from-ollama"
    assert gw.current_provider == "ollama"
    assert gw.current_model == "qwen2.5:7b"


@pytest.mark.asyncio
async def test_fallback_on_unavailable():
    p1 = FakeProvider("ollama", available=False)
    p2 = FakeProvider("deepseek", reply="from-deepseek")
    gw = _gateway(
        {"ollama": p1, "deepseek": p2},
        {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"},
                       {"provider": "deepseek", "model": "deepseek-chat"}]},
    )
    result = await gw.chat([{"role": "user", "content": "hi"}])
    assert result == "from-deepseek"
    assert gw.current_provider == "deepseek"


@pytest.mark.asyncio
async def test_fallback_on_exception():
    p1 = FakeProvider("ollama", fail=True)
    p2 = FakeProvider("deepseek", reply="fallback-answer")
    gw = _gateway(
        {"ollama": p1, "deepseek": p2},
        {"generate": [{"provider": "ollama", "model": "m1"},
                       {"provider": "deepseek", "model": "m2"}]},
    )
    result = await gw.chat([{"role": "user", "content": "hi"}])
    assert result == "fallback-answer"


@pytest.mark.asyncio
async def test_all_providers_failed():
    p1 = FakeProvider("ollama", available=False)
    p2 = FakeProvider("deepseek", fail=True)
    gw = _gateway(
        {"ollama": p1, "deepseek": p2},
        {"generate": [{"provider": "ollama", "model": "m1"},
                       {"provider": "deepseek", "model": "m2"}]},
    )
    with pytest.raises(AllProvidersFailedError):
        await gw.chat([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_task_type_routing():
    heavy = FakeProvider("ollama", reply="heavy")
    light = FakeProvider("deepseek", reply="light")
    gw = _gateway(
        {"ollama": heavy, "deepseek": light},
        {
            "generate": [{"provider": "ollama", "model": "qwen2.5:7b"}],
            "rewrite": [{"provider": "deepseek", "model": "deepseek-chat"}],
        },
    )
    assert await gw.chat([{"role": "user", "content": "x"}], task_type="rewrite") == "light"
    assert light.calls[0]["model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_stream_passthrough():
    p1 = FakeProvider("ollama", reply="流式输出")
    gw = _gateway({"ollama": p1}, {"generate": [{"provider": "ollama", "model": "m"}]})
    tokens = [t async for t in await gw.chat([{"role": "user", "content": "x"}], stream=True)]
    assert "".join(tokens) == "流式输出"


@pytest.mark.asyncio
async def test_model_id_override():
    p1 = FakeProvider("ollama")
    p2 = FakeProvider("deepseek")
    gw = _gateway(
        {"ollama": p1, "deepseek": p2},
        {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"}]},
    )
    await gw.chat([{"role": "user", "content": "x"}], model_id="deepseek:deepseek-chat")
    assert p2.calls[0]["model"] == "deepseek-chat"
    assert gw.current_provider == "deepseek"


@pytest.mark.asyncio
async def test_available_models_marks_unavailable():
    p1 = FakeProvider("ollama", available=True)
    gw = _gateway(
        {"ollama": p1},
        {"generate": [{"provider": "ollama", "model": "qwen2.5:7b"},
                       {"provider": "ghost", "model": "nope"}]},
    )
    models = await gw.available_models()
    by_id = {m["id"]: m for m in models}
    assert by_id["ollama:qwen2.5:7b"]["available"] is True
    assert by_id["ghost:nope"]["available"] is False


# ---- 问题改写 ----
class RewriteGateway:
    def __init__(self):
        self.captured = None

    async def chat(self, messages, task_type="general", **kwargs):
        self.captured = {"messages": messages, "task_type": task_type}
        return "  如何配置 bge-reranker 的 batch size？  "


@pytest.mark.asyncio
async def test_rewrite_no_history_passthrough():
    gw = RewriteGateway()
    result = await rewrite_query(gw, "这个怎么配置？", history=None)
    assert result == "这个怎么配置？"
    assert gw.captured is None


@pytest.mark.asyncio
async def test_rewrite_with_history():
    gw = RewriteGateway()
    history = [
        {"role": "user", "content": "bge-reranker-v2-m3 是什么？"},
        {"role": "assistant", "content": "是一个 Cross-Encoder 重排序模型。"},
    ]
    result = await rewrite_query(gw, "这个怎么配置？", history=history)
    assert result == "如何配置 bge-reranker 的 batch size？"
    assert gw.captured["task_type"] == "rewrite"
    prompt = gw.captured["messages"][0]["content"]
    assert "bge-reranker-v2-m3 是什么？" in prompt
    assert "最新问题：这个怎么配置？" in prompt


@pytest.mark.asyncio
async def test_rewrite_pair_format_history():
    gw = RewriteGateway()
    history = [{"user": "问题A", "assistant": "回答A"}]
    await rewrite_query(gw, "那它呢？", history=history)
    assert "用户：问题A" in gw.captured["messages"][0]["content"]
