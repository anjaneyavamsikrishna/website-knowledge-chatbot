"""Regression tests for the 429s seen while running the assessment against a free-tier key."""
import httpx
import openai
import pytest

from app.core.errors import LLMError
from app.rag.llm import GeminiNativeLLM, OpenAICompatibleLLM, PacedLLM
from tests.test_embedding_errors import api_error
from tests.test_gemini_native import Resp, Session, ok


class FakeChat:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def create(self, **kw):
        self.calls += 1
        out = self.outcomes.pop(0) if self.outcomes else "done"
        if isinstance(out, Exception):
            raise out
        msg = type("M", (), {"content": out})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()


def make(outcomes):
    chat = FakeChat(outcomes)
    client = type("Cl", (), {"chat": type("Ch", (), {"completions": chat})()})()
    sleeps = []
    return OpenAICompatibleLLM("k", "m", client=client, sleep=sleeps.append), chat, sleeps


def rate_limit(msg="You exceeded your current quota, please check your plan and billing details. Please retry in 7s."):
    return api_error(openai.RateLimitError, 429, msg)


def test_429_waits_for_the_hinted_time_then_succeeds():
    llm, chat, sleeps = make([rate_limit(), rate_limit()])
    assert llm.complete("s", "u") == "done"
    assert chat.calls == 3 and all(s >= 7 for s in sleeps)      # waited as long as the server asked


def test_persistent_429_gives_actionable_message():
    llm, chat, _ = make([rate_limit()] * 10)
    with pytest.raises(LLMError) as e:
        llm.complete("s", "u")
    assert "LLM_MIN_INTERVAL_S" in str(e.value) and chat.calls == 5


def test_no_credits_is_not_retried():
    llm, chat, _ = make([rate_limit("You have no credits remaining.")] * 3)
    with pytest.raises(LLMError) as e:
        llm.complete("s", "u")
    assert "no credits" in str(e.value) and chat.calls == 1


def test_connection_errors_are_retried_a_couple_of_times():
    err = openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
    llm, chat, _ = make([err, err])
    assert llm.complete("s", "u") == "done" and chat.calls == 3
    llm2, chat2, _ = make([err] * 5)
    with pytest.raises(LLMError):
        llm2.complete("s", "u")
    assert chat2.calls == 3


def test_paced_llm_spaces_calls_apart():
    clock = {"t": 0.0}
    sleeps = []

    def sleep(s):
        sleeps.append(s); clock["t"] += s

    class Inner:
        n = 0

        def complete(self, *a, **k):
            Inner.n += 1
            return "x"

    llm = PacedLLM(Inner(), 6.0, sleep=sleep, monotonic=lambda: clock["t"])
    for _ in range(3):
        llm.complete("s", "u")
    assert Inner.n == 3 and sum(sleeps) >= 12.0          # 3 calls => at least 2 gaps of 6 s


def test_native_gemini_429_honours_retry_hint():
    sess = Session([Resp(429, text='{"error":{"message":"Please retry in 9.5s."}}'), ok("fine")])
    sleeps = []
    llm = GeminiNativeLLM("k", "gemini-3.5-flash-lite", session=sess, sleep=sleeps.append)
    assert llm.complete("s", "u") == "fine" and sleeps and sleeps[0] >= 9.5


def test_build_llm_wraps_with_pacing_only_when_configured(settings):
    from app.rag.llm import build_llm
    base = settings.model_copy(update={"llm_provider": "gemini", "llm_api_key": "k", "llm_model": "m"})
    assert not isinstance(build_llm(base), PacedLLM)
    assert isinstance(build_llm(base.model_copy(update={"llm_min_interval_s": 5.0})), PacedLLM)