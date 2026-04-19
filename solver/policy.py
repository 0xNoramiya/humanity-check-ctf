from __future__ import annotations

import base64
import json
import os

import httpx
from pydantic import ValidationError

from .actions import Action
from .prompts import SYSTEM, user_prompt


class Policy:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 30.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://verify.b01lersc.tf/",
                "X-Title": "humanitycheck-solver",
                "Content-Type": "application/json",
            },
        )
        self._model = model

    async def aclose(self) -> None:
        await self._client.aclose()

    async def decide(
        self,
        screenshot_png: bytes,
        viewport: tuple[int, int],
        score_text: str,
        history: list[str],
        signals: str = "",
        visible_text: str = "",
        capture_blank: bool = False,
    ) -> Action:
        data_url = "data:image/png;base64," + base64.b64encode(screenshot_png).decode()
        text_prompt = user_prompt(
            viewport, score_text, history, signals, visible_text, capture_blank
        )
        body = {
            "model": self._model,
            "temperature": 0.4,
            "max_tokens": 400,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }
        resp = await self._client.post("/chat/completions", json=body)
        resp.raise_for_status()
        payload = resp.json()
        content = payload["choices"][0]["message"]["content"]
        return _parse(content)


def _parse(content: str) -> Action:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return Action(type="wait", ms=400, reason="unparseable: " + content[:120])
    try:
        obj = json.loads(text[start : end + 1])
        return Action(**obj)
    except (json.JSONDecodeError, ValidationError) as e:
        return Action(type="wait", ms=400, reason="bad json: " + str(e)[:120])


def policy_from_env() -> Policy:
    key = os.environ["OPENROUTER_API_KEY"]
    model = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")
    return Policy(api_key=key, model=model)
