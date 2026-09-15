from app.services.ollama import OllamaClient


class FakeResponse:
    def raise_for_status(self) -> None:
        return None


def test_warm_models_keeps_requested_order_and_reports_success(monkeypatch) -> None:
    calls = []

    def fake_post(url, *, json, timeout):
        calls.append((url, json["model"], timeout))
        return FakeResponse()

    monkeypatch.setattr("app.services.ollama.httpx.post", fake_post)
    results = OllamaClient("http://127.0.0.1:11434").warm_models(["model-a", "model-b"])
    assert [item["model"] for item in results] == ["model-a", "model-b"]
    assert all(item["success"] for item in results)
    assert {item[1] for item in calls} == {"model-a", "model-b"}
