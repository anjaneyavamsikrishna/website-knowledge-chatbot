import json

import pytest

from app.core.errors import LLMError
from app.rag.llm import GeminiNativeLLM, build_llm, extract_json


class Resp:
    def __init__(self, status=200, data=None, text=None):
        self.status_code = status
        self._data = data
        self.text = text if text is not None else json.dumps(data or {})

    def json(self):
        return self._data


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "body": json})
        return self.responses.pop(0)


def ok(text):
    return Resp(200, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]})


def make(responses, **kw):
    sess = Session(responses)
    return GeminiNativeLLM("SECRET-KEY-123", "models/gemini-2.5-flash-lite", session=sess, sleep=lambda s: None, **kw), sess


def test_success_request_shape_and_key_in_header_only():
    llm, sess = make([ok('{"a": 1}')])
    assert extract_json(llm.complete("sys", "hello", json_mode=True)) == {"a": 1}
    call = sess.calls[0]
    assert call["url"].endswith("/models/gemini-2.5-flash-lite:generateContent")   # 'models/' prefix removed
    assert call["headers"]["x-goog-api-key"] == "SECRET-KEY-123" and "SECRET-KEY" not in call["url"]
    body = call["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert body["contents"][0]["parts"][0]["text"] == "hello"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


def test_unsupported_thinking_option_is_dropped_and_retried():
    llm, sess = make([Resp(400, text="Thinking budget is not supported for this model"), ok("fine")])
    assert llm.complete("s", "u") == "fine"
    assert "thinkingConfig" not in sess.calls[1]["body"]["generationConfig"]


def test_unsupported_json_mode_is_dropped_and_retried():
    llm, sess = make([Resp(400, text="response mime type application/json is unsupported"), ok("{}")])
    llm.complete("s", "u", json_mode=True)
    assert "responseMimeType" not in sess.calls[1]["body"]["generationConfig"]


def test_404_message_contains_server_text_and_model_but_not_key():
    llm, _ = make([Resp(404, text='{"error":{"message":"models/x is not found"}}')])
    with pytest.raises(LLMError) as e:
        llm.complete("s", "u")
    assert "models/x is not found" in str(e.value) and "gemini-2.5-flash-lite" in str(e.value) and "SECRET-KEY" not in str(e.value)


def test_rate_limit_is_retried_then_succeeds():
    llm, sess = make([Resp(429, text="quota"), ok("done")])
    assert llm.complete("s", "u") == "done" and len(sess.calls) == 2


def test_auth_error_is_clear():
    llm, _ = make([Resp(403, text="API key not valid")])
    with pytest.raises(LLMError) as e:
        llm.complete("s", "u")
    assert "rejected" in str(e.value)


def test_blocked_and_empty_answers_raise_clear_errors():
    blocked, _ = make([Resp(200, {"promptFeedback": {"blockReason": "SAFETY"}})])
    with pytest.raises(LLMError) as e:
        blocked.complete("s", "u")
    assert "SAFETY" in str(e.value)
    empty, _ = make([Resp(200, {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]})])
    with pytest.raises(LLMError) as e2:
        empty.complete("s", "u")
    assert "MAX_TOKENS" in str(e2.value)


def test_thought_parts_are_ignored():
    llm, _ = make([Resp(200, {"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": "ANSWER"}]}}]})])
    assert llm.complete("s", "u") == "ANSWER"


def test_build_llm_selects_native_provider(settings):
    s = settings.model_copy(update={"llm_provider": "gemini", "llm_api_key": "k", "llm_model": "gemini-2.5-flash-lite"})
    assert isinstance(build_llm(s), GeminiNativeLLM)