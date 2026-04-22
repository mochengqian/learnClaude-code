from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import socket
import ssl
import time
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModelClientError(RuntimeError):
    pass


RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
RETRYABLE_TRANSPORT_KEYWORDS = (
    "connection aborted",
    "connection reset",
    "eof occurred in violation of protocol",
    "remote end closed connection",
    "temporary failure",
    "timed out",
    "timeout",
    "unexpected eof",
)


@dataclass(frozen=True)
class ModelClientConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int = 60
    max_retries: int = 2
    retry_backoff_milliseconds: int = 200

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
        max_attempts = max(1, self.config.max_retries + 1)
        raw_body = ""

        for attempt in range(1, max_attempts + 1):
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
                break
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if self._should_retry_http_error(exc.code) and attempt < max_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                raise self._request_failure(
                    "HTTP {0}: {1}".format(exc.code, body),
                    attempt,
                ) from exc
            except URLError as exc:
                reason = self._describe_transport_error(exc)
                if self._should_retry_transport_error(exc.reason) and attempt < max_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                raise self._request_failure(reason, attempt) from exc
            except OSError as exc:
                reason = self._describe_transport_error(exc)
                if self._should_retry_transport_error(exc) and attempt < max_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                raise self._request_failure(reason, attempt) from exc

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

    def _request_failure(self, message: str, attempt: int) -> ModelClientError:
        if attempt <= 1:
            return ModelClientError("Model request failed: {0}".format(message))
        return ModelClientError(
            "Model request failed after {0} attempts: {1}".format(attempt, message)
        )

    def _should_retry_http_error(self, status_code: int) -> bool:
        return status_code in RETRYABLE_HTTP_STATUS_CODES

    def _should_retry_transport_error(self, error: object) -> bool:
        if isinstance(
            error,
            (
                ConnectionAbortedError,
                ConnectionResetError,
                BrokenPipeError,
                TimeoutError,
                socket.timeout,
                ssl.SSLError,
            ),
        ):
            return True
        message = self._describe_transport_error(error).lower()
        return any(keyword in message for keyword in RETRYABLE_TRANSPORT_KEYWORDS)

    def _describe_transport_error(self, error: object) -> str:
        if isinstance(error, URLError):
            return self._describe_transport_error(error.reason)
        return str(error)

    def _sleep_before_retry(self, attempt: int) -> None:
        if self.config.retry_backoff_milliseconds <= 0:
            return
        time.sleep((self.config.retry_backoff_milliseconds * attempt) / 1000.0)


def create_model_client_from_env() -> Optional[OpenAICompatibleModelClient]:
    api_key = os.getenv("REPO_TASK_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("REPO_TASK_MODEL_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    model = os.getenv("REPO_TASK_MODEL_NAME") or os.getenv("OPENAI_MODEL") or "gpt-5.4-mini"
    timeout_raw = os.getenv("REPO_TASK_MODEL_TIMEOUT_SECONDS", "60").strip()
    max_retries_raw = os.getenv("REPO_TASK_MODEL_MAX_RETRIES", "2").strip()
    backoff_raw = os.getenv(
        "REPO_TASK_MODEL_RETRY_BACKOFF_MILLISECONDS",
        "200",
    ).strip()

    if not api_key or not base_url:
        return None

    try:
        timeout_seconds = int(timeout_raw)
    except ValueError as exc:
        raise ModelClientError(
            "REPO_TASK_MODEL_TIMEOUT_SECONDS must be an integer."
        ) from exc
    if timeout_seconds <= 0:
        raise ModelClientError(
            "REPO_TASK_MODEL_TIMEOUT_SECONDS must be greater than 0."
        )

    try:
        max_retries = int(max_retries_raw)
    except ValueError as exc:
        raise ModelClientError("REPO_TASK_MODEL_MAX_RETRIES must be an integer.") from exc
    if max_retries < 0:
        raise ModelClientError("REPO_TASK_MODEL_MAX_RETRIES must be at least 0.")

    try:
        retry_backoff_milliseconds = int(backoff_raw)
    except ValueError as exc:
        raise ModelClientError(
            "REPO_TASK_MODEL_RETRY_BACKOFF_MILLISECONDS must be an integer."
        ) from exc
    if retry_backoff_milliseconds < 0:
        raise ModelClientError(
            "REPO_TASK_MODEL_RETRY_BACKOFF_MILLISECONDS must be at least 0."
        )

    return OpenAICompatibleModelClient(
        ModelClientConfig(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_milliseconds=retry_backoff_milliseconds,
        )
    )
