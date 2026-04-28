import logging
from langchain_ollama import ChatOllama
from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL, LLM_TIMEOUT_SECONDS, GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger("pa.llm")


def get_llm() -> ChatOllama:
    return ChatOllama(
        base_url=OLLAMA_BASE_URL,
        model=OLLAMA_MODEL,
        temperature=0.3,
        keep_alive="2m",
        num_ctx=2048,
        timeout=LLM_TIMEOUT_SECONDS,
    )


def get_gemini_llm():
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GEMINI_API_KEY,
        temperature=0.3,
    )


def get_smart_llm():
    if GEMINI_API_KEY:
        return get_gemini_llm()
    return get_llm()


def llm_with_fallback():
    ollama = get_llm()
    if not GEMINI_API_KEY:
        return ollama
    gemini = get_gemini_llm()
    return ollama.with_fallbacks([gemini])
