from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModelClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelClientConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int = 60

    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


@dataclass
class ModelResponse:
    text: str
    model: str
    usage: Dict[str, Any] = field(default_factory=dict)
    raw_response: Dict[str, Any] = field(default_factory=dict)


class OpenAICompatibleModelClient:
    def __init__(self, config: ModelClientConfig) -> None:
        self.config = config

    def complete(self, *, system_prompt: str, user_prompt: str) -> ModelResponse:
        payload = {
            "model": self.config.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        request = Request(
            url="{0}/chat/completions".format(self.config.normalized_base_url()),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer {0}".format(self.config.api_key),
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ModelClientError(
                "Model request failed with HTTP {0}: {1}".format(exc.code, body)
            ) from exc
        except URLError as exc:
            raise ModelClientError("Model request failed: {0}".format(exc.reason)) from exc

        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ModelClientError("Model returned invalid JSON.") from exc

        try:
            content = parsed["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelClientError("Model response did not contain assistant content.") from exc

        return ModelResponse(
            text=content,
            model=str(parsed.get("model") or self.config.model),
            usage=dict(parsed.get("usage") or {}),
            raw_response=parsed,
        )


def create_model_client_from_env() -> Optional[OpenAICompatibleModelClient]:
    api_key = os.getenv("REPO_TASK_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("REPO_TASK_MODEL_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    model = os.getenv("REPO_TASK_MODEL_NAME") or os.getenv("OPENAI_MODEL") or "gpt-5.4-mini"
    timeout_raw = os.getenv("REPO_TASK_MODEL_TIMEOUT_SECONDS", "60").strip()

    if not api_key or not base_url:
        return None

    try:
        timeout_seconds = int(timeout_raw)
    except ValueError as exc:
        raise ModelClientError(
            "REPO_TASK_MODEL_TIMEOUT_SECONDS must be an integer."
        ) from exc

    return OpenAICompatibleModelClient(
        ModelClientConfig(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    )
