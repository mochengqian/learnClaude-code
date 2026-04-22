import io
import json
import os
import ssl
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from repo_task_runtime import (
    ModelClientConfig,
    ModelClientError,
    OpenAICompatibleModelClient,
    create_model_client_from_env,
)


class _FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ModelClientTest(unittest.TestCase):
    def _make_client(self, **overrides):
        config = ModelClientConfig(
            base_url="https://right.codes/codex/v1",
            api_key="test-key",
            model="gpt-5.4-mini-test",
            timeout_seconds=15,
            max_retries=1,
            retry_backoff_milliseconds=0,
            **overrides,
        )
        return OpenAICompatibleModelClient(config)

    def test_complete_retries_transient_ssl_eof_once_and_succeeds(self):
        client = self._make_client()
        success_response = _FakeHTTPResponse(
            {
                "model": "gpt-5.4-mini-test",
                "choices": [{"message": {"content": '{"summary":"done","action":"finish"}'}}],
                "usage": {"total_tokens": 22},
            }
        )

        with patch(
            "repo_task_runtime.model_client.urlopen",
            side_effect=[
                URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol")),
                success_response,
            ],
        ) as urlopen_mock:
            response = client.complete(system_prompt="sys", user_prompt="user")

        self.assertEqual(2, urlopen_mock.call_count)
        self.assertEqual('{"summary":"done","action":"finish"}', response.text)

    def test_complete_raises_after_transient_retries_are_exhausted(self):
        client = self._make_client()
        eof_error = URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol"))

        with patch(
            "repo_task_runtime.model_client.urlopen",
            side_effect=[eof_error, eof_error],
        ) as urlopen_mock:
            with self.assertRaises(ModelClientError) as ctx:
                client.complete(system_prompt="sys", user_prompt="user")

        self.assertEqual(2, urlopen_mock.call_count)
        self.assertIn("after 2 attempts", str(ctx.exception))
        self.assertIn("EOF occurred in violation of protocol", str(ctx.exception))

    def test_complete_does_not_retry_non_retryable_http_error(self):
        client = self._make_client()
        http_error = HTTPError(
            url="https://right.codes/codex/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"bad key"}'),
        )

        with patch(
            "repo_task_runtime.model_client.urlopen",
            side_effect=http_error,
        ) as urlopen_mock:
            with self.assertRaises(ModelClientError) as ctx:
                client.complete(system_prompt="sys", user_prompt="user")

        self.assertEqual(1, urlopen_mock.call_count)
        self.assertEqual(
            'Model request failed: HTTP 401: {"error":"bad key"}',
            str(ctx.exception),
        )

    def test_create_model_client_from_env_reads_retry_settings(self):
        with patch.dict(
            os.environ,
            {
                "REPO_TASK_MODEL_API_KEY": "test-key",
                "REPO_TASK_MODEL_BASE_URL": "https://right.codes/codex/v1",
                "REPO_TASK_MODEL_NAME": "gpt-5.4-mini",
                "REPO_TASK_MODEL_TIMEOUT_SECONDS": "45",
                "REPO_TASK_MODEL_MAX_RETRIES": "2",
                "REPO_TASK_MODEL_RETRY_BACKOFF_MILLISECONDS": "125",
            },
            clear=False,
        ):
            client = create_model_client_from_env()

        self.assertIsNotNone(client)
        if client is None:
            self.fail("Expected a configured model client.")
        self.assertEqual(45, client.config.timeout_seconds)
        self.assertEqual(2, client.config.max_retries)
        self.assertEqual(125, client.config.retry_backoff_milliseconds)
