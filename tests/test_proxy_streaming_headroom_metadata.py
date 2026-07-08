"""Tests for Headroom compression metadata on streaming responses."""

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from headroom.proxy.server import HeadroomProxy


class TestStreamingHeadroomMetadata:
    """Streaming paths expose per-turn savings via headers and a trailing SSE event."""

    def _create_mock_proxy(self):
        proxy = object.__new__(HeadroomProxy)
        proxy.http_client = MagicMock(spec=httpx.AsyncClient)
        proxy.metrics = MagicMock()
        proxy.metrics.record_request = AsyncMock(return_value=None)
        proxy.metrics.record_failed = AsyncMock(return_value=None)
        proxy._config = MagicMock()
        proxy._config.memory_enabled = False
        proxy._config.ccr_inject_tool = False
        proxy._config.retry_max_attempts = 1
        proxy._config.retry_base_delay_ms = 0
        proxy._config.retry_max_delay_ms = 0
        proxy.config = proxy._config
        proxy.memory_handler = None
        proxy._parse_sse_usage_from_buffer = MagicMock(return_value=None)
        proxy._finalize_stream_response = AsyncMock(return_value=None)
        proxy._record_request_outcome = AsyncMock(return_value=None)
        return proxy

    def _create_mock_upstream_response(self, *, sse_data: bytes | None = None):
        mock_response = AsyncMock()
        mock_response.headers = httpx.Headers({"content-type": "text/event-stream"})
        mock_response.status_code = 200
        payload = sse_data or (
            b'data: {"id":"1","choices":[{"delta":{"content":"hi"}}]}\n\n'
            b"data: [DONE]\n\n"
        )

        async def aiter_bytes():
            yield payload

        mock_response.aiter_bytes = aiter_bytes
        mock_response.aclose = AsyncMock()
        return mock_response

    @pytest.mark.asyncio
    async def test_streaming_response_includes_headroom_savings_headers(self):
        proxy = self._create_mock_proxy()
        mock_response = self._create_mock_upstream_response()
        proxy.http_client.build_request = MagicMock(return_value=MagicMock())
        proxy.http_client.send = AsyncMock(return_value=mock_response)

        result = await proxy._stream_response(
            url="https://api.githubcopilot.com/chat/completions",
            headers={"authorization": "Bearer test"},
            body={
                "model": "gpt-4o",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
            provider="openai",
            model="gpt-4o",
            request_id="test-headroom-headers",
            original_tokens=1200,
            optimized_tokens=900,
            tokens_saved=300,
            transforms_applied=["json_compact"],
            tags={},
            optimization_latency=1.5,
        )

        assert result.headers.get("x-headroom-tokens-before") == "1200"
        assert result.headers.get("x-headroom-tokens-after") == "900"
        assert result.headers.get("x-headroom-tokens-saved") == "300"
        assert result.headers.get("x-headroom-model") == "gpt-4o"
        assert result.headers.get("x-headroom-transforms") == "json_compact"

    @pytest.mark.asyncio
    async def test_streaming_emits_headroom_stats_sse_event_at_end(self):
        proxy = self._create_mock_proxy()
        mock_response = self._create_mock_upstream_response()
        proxy.http_client.build_request = MagicMock(return_value=MagicMock())
        proxy.http_client.send = AsyncMock(return_value=mock_response)

        result = await proxy._stream_response(
            url="https://api.githubcopilot.com/chat/completions",
            headers={"authorization": "Bearer test"},
            body={
                "model": "gpt-4o",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
            provider="openai",
            model="gpt-4o",
            request_id="test-headroom-sse",
            original_tokens=500,
            optimized_tokens=400,
            tokens_saved=100,
            transforms_applied=[],
            tags={},
            optimization_latency=0.0,
        )

        chunks = [chunk async for chunk in result.body_iterator]
        raw = b"".join(chunks).decode("utf-8")
        assert "event: headroom_stats" in raw
        stats_payload = None
        for block in raw.split("\n\n"):
            if "event: headroom_stats" in block:
                stats_payload = json.loads(block.split("data: ", 1)[1])
                break
        assert stats_payload is not None
        assert stats_payload == {
            "type": "headroom_stats",
            "tokens_before": 500,
            "tokens_after": 400,
            "tokens_saved": 100,
            "model": "gpt-4o",
        }

    @pytest.mark.asyncio
    async def test_upstream_streaming_error_includes_headroom_headers(self):
        proxy = self._create_mock_proxy()
        mock_response = self._create_mock_upstream_response()
        mock_response.status_code = 429
        mock_response.headers = httpx.Headers(
            {
                "content-type": "application/json",
                "retry-after": "1",
            }
        )

        async def aiter_bytes():
            yield b'{"error":"rate limited"}'

        mock_response.aiter_bytes = aiter_bytes
        mock_response.aread = AsyncMock(return_value=b'{"error":"rate limited"}')

        proxy.http_client.build_request = MagicMock(return_value=MagicMock())
        proxy.http_client.send = AsyncMock(return_value=mock_response)

        result = await proxy._stream_response(
            url="https://api.githubcopilot.com/chat/completions",
            headers={"authorization": "Bearer test"},
            body={
                "model": "gpt-4o",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
            provider="openai",
            model="gpt-4o",
            request_id="test-headroom-error",
            original_tokens=80,
            optimized_tokens=60,
            tokens_saved=20,
            transforms_applied=[],
            tags={},
            optimization_latency=0.0,
        )

        assert result.status_code == 429
        assert result.headers.get("x-headroom-tokens-before") == "80"
        assert result.headers.get("x-headroom-tokens-after") == "60"
        assert result.headers.get("x-headroom-tokens-saved") == "20"
