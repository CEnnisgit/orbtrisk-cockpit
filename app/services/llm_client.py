import json
from typing import Any, Dict, List

import httpx

from app.settings import settings


class LlmError(RuntimeError):
    pass


def _system_prompt(context: Dict[str, Any]) -> str:
    context_json = json.dumps(context, indent=2, sort_keys=True)
    return (
        "You are a space object analyst assistant. Use ONLY the provided context. "
        "If a fact is not present, say 'Unknown'. Do not guess. "
        "Keep answers concise and factual. "
        "Context:\n"
        f"{context_json}"
    )


def _call_ollama(messages: List[Dict[str, str]]) -> str:
    if not settings.llm_enabled:
        raise LlmError("LLM is disabled")
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": settings.llm_temperature,
            "num_predict": settings.llm_max_tokens,
        },
    }
    url = settings.llm_base_url.rstrip("/") + "/api/chat"
    try:
        resp = httpx.post(url, json=payload, timeout=settings.llm_timeout_seconds)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise LlmError("Failed to reach LLM service") from exc
    if "message" not in data:
        raise LlmError("Malformed LLM response")
    return data["message"].get("content", "").strip()


def generate_summary(context: Dict[str, Any]) -> Dict[str, Any]:
    system = _system_prompt(context)
    user = (
        "Write a short summary (2-3 sentences) and 4 concise key facts. "
        "Return JSON with keys: summary, key_facts (array of strings)."
    )
    content = _call_ollama(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict) and "summary" in parsed:
            return parsed
    except json.JSONDecodeError:
        pass
    return {"summary": content, "key_facts": []}


def chat(context: Dict[str, Any], messages: List[Dict[str, str]]) -> str:
    system = _system_prompt(context)
    conversation = [{"role": "system", "content": system}]
    for msg in messages:
        if msg.get("role") in {"user", "assistant"}:
            conversation.append({"role": msg["role"], "content": msg.get("content", "")})
    return _call_ollama(conversation)
