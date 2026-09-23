"""
Thin LLM client (OpenAI Chat Completions API).

Design rules enforced here:
  * The LLM is only ever asked for PERCEPTION tasks - classify this document,
    read these fields, phrase this explanation.
  * It is never asked for a verdict. `decision.py` owns the verdict.
  * Every call is optional. If no API key is configured, or the call fails,
    the caller falls back to the deterministic path so the system still runs
    end-to-end. The run record always states which path was used.

Provider isolation: this module and `config.py` are the only places that know
which vendor serves the calls. Swapping providers touches nothing else.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from openai import BadRequestError, OpenAI

from app.config import (LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL,
                        LLM_REASONING_EFFORT, LLM_TIMEOUT_S)

_client: Optional[OpenAI] = None
_variant: Optional[int] = None      # which parameter dialect this model accepts


def available() -> bool:
    if LLM_ENABLED == "off":
        return False
    return bool(LLM_API_KEY)


def _client_or_none() -> Optional[OpenAI]:
    global _client
    if not available():
        return None
    if _client is None:
        _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL,
                         timeout=LLM_TIMEOUT_S, max_retries=2)
    return _client


def _call(client: OpenAI, system: str, user: str, max_tokens: int, json_mode: bool):
    """One chat completion, tolerant of the two OpenAI parameter dialects.

    Classic chat models take `temperature` + `max_tokens`. Reasoning-era models
    (gpt-5.x, gpt-6.x) reject a custom temperature and rename the budget to
    `max_completion_tokens`; they also spend part of that budget on hidden
    reasoning tokens, so the floor is raised to leave room for a real answer.
    The working dialect is remembered after the first successful call.
    """
    global _variant
    base: Dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    if json_mode:
        base["response_format"] = {"type": "json_object"}

    budget = max(max_tokens, 2000)
    variants: List[Dict[str, Any]] = [
        {"temperature": 0, "max_tokens": max_tokens},
        {"max_completion_tokens": budget, "reasoning_effort": LLM_REASONING_EFFORT},
        {"max_completion_tokens": budget},
    ]
    order = list(range(len(variants)))
    if _variant is not None:
        order = [_variant] + [i for i in order if i != _variant]

    last: Optional[Exception] = None
    for i in order:
        try:
            resp = client.chat.completions.create(**base, **variants[i])
            _variant = i
            return resp
        except BadRequestError as exc:      # unsupported parameter for this model
            last = exc
    raise last  # type: ignore[misc]


def complete(system: str, user: str, max_tokens: int = 1500,
             json_mode: bool = False) -> Optional[str]:
    try:
        client = _client_or_none()
        if client is None:
            return None
        resp = _call(client, system, user, max_tokens, json_mode)
        text = (resp.choices[0].message.content or "").strip()
        return text or None
    except Exception:  # client construction, network, auth, rate limit
        return None


def complete_json(system: str, user: str, max_tokens: int = 1500) -> Optional[Dict[str, Any]]:
    raw = complete(system, user, max_tokens, json_mode=True)
    if not raw:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None