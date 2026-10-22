"""V1.5.x（方案 v2 / P2）：/api/providers/status、联合清单、E_MODEL_UNAVAILABLE 四象限与 meta.selected。"""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.question_scorer import QuestionScorer
from app.core.retrieval_scorer import RetrievalScorer
from app.core.retriever import HybridRetriever
from app.models.schemas import E_MODEL_UNAVAILABLE
from app.providers.base import AllProvidersFailedError
from app.routers import chat as chat_router
from app.routers import health as health_router
from app.routers.chat import build_model_unavailable_payload
from app.services.session_store import SessionStore


# ---------------- 诊断四象限 ----------------

class FakeStatusGateway:
    """只暴露 status_report 的诊断网关。"""

    def __init__(self, providers, bindings=None, enabled=True):
        self._report = {"enabled": enabled, "providers": providers,
                        "bindings": bindings or {}}

    def status_report(self):
        return self._report


def _provider(name, ptype, available, reason):
    return {"name": name, "type": ptype, "available": available,
            "reason": reason, "detail": "", "installed_models": [], "selected_for": []}


def test_diagnosis_local_down_cloud_missing():
    gw = FakeStatusGateway([_provider("ollama", "local", False, "service_unreachable")])
    payload = build_model_unavailable_payload(SimpleNamespace(gateway=gw))
    assert payload["code"] == E_MODEL_UNAVAILABLE
    assert payload["message"] == "本地 Ollama 不可用，在线模型未配置"
    assert "DEEPSEEK_API_KEY" in payload["suggestion"]
    assert "backend/.env" in payload["suggestion"]
    assert payload["diagnosis"][0]["reason"] == "service_unreachable"


def test_diagnosis_no_chat_models():
    gw = FakeStatusGateway([_provider("ollama", "local", False, "no_chat_models")])
    payload = build_model_unavailable_payload(SimpleNamespace(gateway=gw))
    assert payload["message"] == "Ollama 在线，但未安装任何对话模型"
    assert "ollama pull" in payload["suggestion"]
    # 不点名具体型号
    assert "qwen2.5" not in payload["message"] + payload["suggestion"]
    assert "llama3" not in payload["message"] + payload["suggestion"]


def test_diagnosis_cloud_auth_failed():
    gw = FakeStatusGateway([
        _provider("ollama", "local", False, "service_unreachable"),
        _provider("deepseek", "cloud", False, "auth_failed"),
    ])
    payload = build_model_unavailable_payload(SimpleNamespace(gateway=gw))
    assert payload["message"] == "云端凭据无效"
    assert "API Key" in payload["suggestion"]


def test_diagnosis_all_unavailable_with_configured_cloud():
    gw = FakeStatusGateway([
        _provider("ollama", "local", False, "timeout"),
        _provider("deepseek", "cloud", False, "service_unreachable"),
    ])
    payload = build_model_unavailable_payload(SimpleNamespace(gateway=gw))
    assert payload["message"] == "无可用模型，请检查网络与服务状态"


def test_diagnosis_shape_and_auto_echo():
    gw = FakeStatusGateway(
        [_provider("ollama", "local", False, "service_unreachable")],
        bindings={"generate": {"provider": "ollama", "model": "llama3.1:8b", "reason": "fallback"}},
    )
    payload = build_model_unavailable_payload(SimpleNamespace(gateway=gw))
    assert set(payload) >= {"code", "message", "suggestion", "diagnosis", "tried_auto_models"}
    # 允许复述实际兜底选中的模型（非引导性文案，只出现在机器层字段）
    assert payload["tried_auto_models"][0]["model"] == "llama3.1:8b"
    # 引导文案仍无型号
    assert "llama3.1:8b" not in payload["message"] + payload["suggestion"]


def test_diagnosis_tolerates_gateway_without_status_report():
    payload = build_model_unavailable_payload(SimpleNamespace(gateway=object()))
    assert payload["code"] == E_MODEL_UNAVAILABLE
    assert payload["message"] == "本地 Ollama 不可用，在线模型未配置"


# ---------------- 端点 ----------------

class ApiGateway:
    def __init__(self):
        self.refreshed = False
        self.probed = False

    async def available_models(self, refresh: bool = False):
        self.refreshed = refresh
        return [
            {"id": "ollama:qwen2.5:7b", "name": "qwen2.5:7b", "provider": "ollama",
             "type": "local", "available": True, "source": "config",
             "reason": "ok", "parameter_size": 7.0, "family": "qwen2.5",
             "missing_models": None},
            {"id": "ollama:llama3.1:8b", "name": "llama3.1:8b", "provider": "ollama",
             "type": "local", "available": True, "source": "discovered",
             "reason": "ok", "parameter_size": 8.0, "family": "llama3.1",
             "missing_models": None},
        ]

    async def probe_all(self):
        self.probed = True

    def status_report(self):
        return {
            "enabled": True,
            "providers": [
                {"name": "ollama", "type": "local", "available": True, "reason": "ok",
                 "detail": "",
                 "installed_models": [{"name": "qwen2.5:7b", "parameter_size": 7.0,
                                       "family": "qwen2.5", "size_bytes": 1}],
                 "selected_for": []},
            ],
            "bindings": {"generate": None},
        }

    def selected_payload(self):
        return {"provider": "ollama", "model": "qwen2.5:7b", "auto": True}


def _api_app(gateway) -> TestClient:
    app = FastAPI()
    app.include_router(health_router.router)
    app.state.core = SimpleNamespace(gateway=gateway)
    return TestClient(app)


def test_models_endpoint_includes_union_fields():
    gw = ApiGateway()
    client = _api_app(gw)
    resp = client.get("/api/models")
    assert resp.status_code == 200
    models = resp.json()
    assert models[0]["source"] == "config"
    assert models[0]["reason"] == "ok"
    discovered = [m for m in models if m["source"] == "discovered"]
    assert discovered and discovered[0]["parameter_size"] == 8.0
    assert gw.refreshed is False
    assert gw.probed is False


def test_models_endpoint_refresh_flag():
    gw = ApiGateway()
    client = _api_app(gw)
    resp = client.get("/api/models", params={"refresh": "true"})
    assert resp.status_code == 200
    assert gw.refreshed is True


def test_providers_status_endpoint():
    gw = ApiGateway()
    client = _api_app(gw)
    resp = client.get("/api/providers/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["enabled"] is True
    assert payload["providers"][0]["installed_models"][0]["name"] == "qwen2.5:7b"
    assert "bindings" in payload
    assert gw.probed is False
    resp2 = client.get("/api/providers/status", params={"refresh": "true"})
    assert resp2.status_code == 200
    assert gw.probed is True


# ---------------- SSE 映射与 meta.selected ----------------

class _MockVS:
    def __init__(self, results=None):
        self.results = results or []

    def query(self, query, top_k, **kwargs):
        return self.results[:top_k]


class _MockBM25:
    def __init__(self, results=None):
        self.results = results or []

    def search(self, query, top_k):
        return self.results[:top_k]


class AllFailGateway:
    """全部任务都无可用模型。"""

    current_model = None
    current_provider = None

    def __init__(self):
        self.default_selection = None
        self.last_selection = None

    async def chat(self, messages, task_type="generate", stream=False, model_id=None, **kw):
        raise AllProvidersFailedError("所有模型均不可用（任务: %s）最后错误: x" % task_type)


CITED_ANSWER = "PersistentClient 用于持久化存储。[来源1]"


class WorkingGateway(AllFailGateway):
    """可用网关 happy path（带 selected_payload）。"""

    current_model = "fake-model"
    current_provider = "fake"

    def __init__(self):
        super().__init__()

    def selected_payload(self):
        return {"provider": self.current_provider, "model": self.current_model, "auto": False}

    async def chat(self, messages, task_type="generate", stream=False, model_id=None, **kw):
        if stream:
            async def _gen():
                for token in ["这是", "一段", "模拟回答。"]:
                    yield token
            return _gen()
        if task_type == "rewrite":
            return "如何配置 bge-reranker 的 batch size？"
        return CITED_ANSWER


def _chunk(i: int) -> dict:
    return {
        "id": f"x.md#{i}",
        "text": "PersistentClient 用于持久化存储。" if i == 0 else "混合检索结合向量与 BM25。",
        "metadata": {"file_path": "notes/a.md", "heading_path": "Chroma 入门"},
        "score": 0.9 - i * 0.1,
    }


def _chat_client(tmp_path, gateway) -> TestClient:
    core = SimpleNamespace(
        session_store=SessionStore(tmp_path / "s.db"),
        question_scorer=QuestionScorer(domain_terms={"chromadb", "persistentclient"}),
        retrieval_scorer=RetrievalScorer({"ChromaDB": 30, "PersistentClient": 30}),
        retriever=HybridRetriever(_MockVS([_chunk(0), _chunk(1)]), _MockBM25([])),
        gateway=gateway,
        metadata=None,
        chroma=None,
    )
    app = FastAPI()
    app.include_router(chat_router.router)
    app.state.core = core
    return TestClient(app)


def _parse_sse(text: str):
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        events.append((event, data))
    return events


def test_sse_all_providers_failed_maps_to_model_unavailable(tmp_path):
    client = _chat_client(tmp_path, AllFailGateway())
    resp = client.post("/api/chat/stream", json={"question": "这个怎么弄？"})
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    types = [e for e, _ in events]
    assert "error" in types and types[-1] == "done"
    error_event = next(d for e, d in events if e == "error")
    assert error_event["code"] == E_MODEL_UNAVAILABLE
    assert all(d.get("code") != "E_INTERNAL" for e, d in events if e == "error")
    assert "本地 Ollama 不可用" in error_event["message"]
    assert "backend/.env" in error_event["suggestion"]
    done = events[-1][1]
    assert done["answer_status"] == "error"


def test_sse_meta_contains_selected_payload(tmp_path):
    client = _chat_client(tmp_path, WorkingGateway())
    resp = client.post("/api/chat/stream", json={"question": "ChromaDB PersistentClient 怎么配置？"})
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    meta = next(d for e, d in events if e == "meta")
    assert meta["selected"] == {"provider": "fake", "model": "fake-model", "auto": False}


def test_sse_meta_selected_none_for_legacy_gateway(tmp_path):
    class LegacyGateway(WorkingGateway):
        def selected_payload(self):
            raise AttributeError("legacy stub does not support selected")

    client = _chat_client(tmp_path, LegacyGateway())
    resp = client.post("/api/chat/stream", json={"question": "ChromaDB PersistentClient 怎么配置？"})
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    meta = next(d for e, d in events if e == "meta")
    assert meta["selected"] is None
