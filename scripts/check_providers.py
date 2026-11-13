"""Verify the LLM and embedding configuration end to end, with plain-English diagnosis.

  python scripts/check_providers.py                 # check the current .env
  python scripts/check_providers.py --try-models    # try several models on BOTH routes and print the .env lines that work

Routes: 'openai-compat' = LLM_PROVIDER=openai with LLM_BASE_URL (Gemini's OpenAI-compatible endpoint)
        'native'        = LLM_PROVIDER=gemini (Google's own generateContent API)
"""
import _bootstrap  # noqa: F401
import argparse
import sys
import time

from app.core.config import get_settings
from app.core.errors import EmbeddingError, LLMError, ProviderFatalError
from app.rag.embeddings import build_embedder
from app.rag.llm import GeminiNativeLLM, OpenAICompatibleLLM, build_llm

SKIP_WORDS = ("embed", "aqa", "imagen", "veo", "tts", "image", "vision", "bison", "gecko", "whisper", "dall",
              "preview", "live", "audio", "native", "robotics", "computer", "omni", "nano", "decision", "argon",
              "carbon", "transcribe", "translate", "thinking", "lyria", "customtools", "research", "antigravity")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"


def mask(k: str) -> str:
    return f"{k[:4]}...{k[-3:]}" if len(k) > 8 else ("(empty)" if not k else "***")


def list_models(s) -> list[str]:
    from openai import OpenAI

    client = OpenAI(api_key=s.llm_api_key, base_url=s.llm_base_url or (GEMINI_BASE if s.llm_provider == "gemini" else None),
                    timeout=30, max_retries=0)
    return sorted(m.id.split("/")[-1] for m in client.models.list())


def candidates(ids: list[str], configured: str) -> list[str]:
    out = [configured] if configured else []
    pref = ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-flash-lite-latest", "gemini-flash-latest",
            "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.5-flash"]
    for name in pref + [i for i in ids if i.startswith("gemini-") and "flash" in i]:
        if name in ids and name not in out and not any(w in name for w in SKIP_WORDS):
            out.append(name)
    return out[:8]


def try_call(llm) -> tuple[bool, str]:
    try:
        text = llm.complete("You are a connectivity test.", "Reply with exactly: OK", max_tokens=300)
        return (True, text.strip()[:40]) if text.strip() else (False, "empty text")
    except LLMError as exc:
        return False, str(exc)[:420]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--try-models", action="store_true")
    args = ap.parse_args()
    s = get_settings()
    print("LLM provider :", s.llm_provider, "| model:", s.llm_model, "| key:", mask(s.llm_api_key))
    print("LLM base_url :", s.llm_base_url or "(default)")
    print("Embeddings   :", s.embedding_provider, "| model:", s.embedding_model)
    ok = True

    ids: list[str] = []
    if s.llm_provider in ("openai", "gemini") and s.llm_api_key:
        try:
            ids = list_models(s)
            print(f"\n[models] {len(ids)} models are listed for this key.")
            print("[models] '%s' %s in the list." % (s.llm_model, "IS" if s.llm_model in ids else "is NOT"))
        except Exception as exc:
            print(f"\n[models] could not list models ({exc.__class__.__name__}: {str(exc)[:150]})")

    if args.try_models:
        if not s.llm_api_key:
            print("LLM_API_KEY is empty"); return 1
        base = s.llm_base_url or GEMINI_BASE
        winners = []
        print("\n[try-models] tiny test call per model and route (about 1 s apart):")
        for model in candidates(ids, s.llm_model):
            for route in ("native", "openai-compat"):
                llm = (GeminiNativeLLM(s.llm_api_key, model, api_base=s.gemini_api_base, max_tokens=300) if route == "native"
                       else OpenAICompatibleLLM(s.llm_api_key, model, base_url=base, max_tokens=300))
                good, info = try_call(llm)
                print(f"  {'OK  ' if good else 'FAIL'} {model:<28} {route:<14} {info if good else info[:200]}")
                if good:
                    winners.append((model, route))
                time.sleep(1.0)
        if winners:
            model, route = winners[0]
            print("\n=> Put these lines in .env (keep your LLM_BASE_URL line: embeddings use it):")
            print(f"   LLM_PROVIDER={'gemini' if route == 'native' else 'openai'}\n   LLM_MODEL={model}")
            return 0
        print("\nNo combination worked. Read the FAIL lines above (the server's own error text) and paste them to me.")
        return 1

    try:
        llm = build_llm(s)
        if llm is None:
            raise LLMError("LLM_API_KEY is empty")
        good, info = try_call(llm)
        if good:
            print(f"[chat] OK -> {info!r}")
        else:
            ok = False
            print("[chat] FAILED:", info)
            print("       next step: python scripts/check_providers.py --try-models")
    except LLMError as exc:
        ok = False
        print("[chat] FAILED:", exc)

    try:
        vec = build_embedder(s).embed_query("connectivity test")
        print(f"[embeddings] OK -> vector of {len(vec)} dimensions")
    except (ProviderFatalError, EmbeddingError, Exception) as exc:
        ok = False
        print("[embeddings] FAILED:", exc)

    print("\nRESULT:", "all good" if ok else "fix the items marked FAILED above, then re-run")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())