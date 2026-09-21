"""
Thin LLM client (Anthropic Messages API).

Design rules enforced here:
  * The LLM is only ever asked for PERCEPTION tasks - classify this document,
    read these fields, phrase this explanation.
  * It is never asked for a verdict. `decision.py` owns the verdict.
  * Every call is optional. If no API key is configured, or the call fails,
    the caller falls back to the deterministic path so the system still runs
    end-to-end. The run record always states which path was used.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

import requests

from app.config import LLM_API_KEY, LLM_ENABLED, LLM_MODEL, LLM_TIMEOUT_S

API_URL = "https://api.anthropic.com/v1/messages"


def available() -> bool:
    if LLM_ENABLED == "off":
        return False
    return bool(LLM_API_KEY)


def complete(system: str, user: str, max_tokens: int = 1500) -> Optional[str]:
    if not available():
        return None
    try:
        resp = requests.post(
            API_URL,
            headers={"x-api-key": LLM_API_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": LLM_MODEL, "max_tokens": max_tokens,
                  "system": system, "temperature": 0,
                  "messages": [{"role": "user", "content": user}]},
            timeout=LLM_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        return "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text").strip()
    except Exception:  # network, auth, rate limit - never break the pipeline
        return None


def complete_json(system: str, user: str, max_tokens: int = 1500) -> Optional[Dict[str, Any]]:
    raw = complete(system, user, max_tokens)
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