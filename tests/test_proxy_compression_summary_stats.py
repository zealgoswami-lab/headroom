"""Precise /stats compression_summary and token field semantics."""

from __future__ import annotations

import asyncio

import pytest

from headroom.proxy.cost import build_compression_summary


def test_build_compression_summary_layers_are_distinct() -> None:
    summary = build_compression_summary(
        proxy_compression_tokens=400,
        proxy_total_before_compression=1000,
        forwarded_tokens=600,
        cli_tokens_avoided=50,
        total_tokens_before=1050,
        all_layers_tokens_saved=450,
        attempted_input_tokens=800,
        cli_filtering_tool="rtk",
        cli_filtering_label="RTK",
        display_session={
            "requests": 3,
            "tokens_saved": 120,
            "total_input_tokens": 380,
            "savings_percent": 24.0,
            "compression_savings_usd": 0.01,
            "started_at": "2026-01-01T00:00:00+00:00",
            "last_activity_at": "2026-01-01T00:05:00+00:00",
        },
    )

    assert summary["proxy"]["received_tokens"] == 1000
    assert summary["proxy"]["forwarded_tokens"] == 600
    assert summary["proxy"]["tokens_saved"] == 400
    assert summary["proxy"]["savings_percent_of_received"] == 40.0
    assert summary["proxy"]["active_savings_percent_of_attempted"] == 50.0

    assert summary["cli_filtering"]["tokens_saved"] == 50

    assert summary["all_layers"]["received_tokens"] == 1050
    assert summary["all_layers"]["tokens_saved"] == 450
    assert summary["all_layers"]["savings_percent_of_received"] == pytest.approx(42.86, rel=1e-3)

    assert summary["display_session"]["received_tokens"] == 500
    assert summary["display_session"]["forwarded_tokens"] == 380
    assert summary["display_session"]["tokens_saved"] == 120


def test_stats_exposes_compression_summary_and_precise_aliases(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    try:
        import headroom.proxy.server as server
        from headroom.proxy.outcome import RequestOutcome, emit_request_outcome
        from headroom.proxy.server import ProxyConfig, create_app
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.skip(f"headroom._core not available (Rust extension not compiled): {exc}")

    monkeypatch.setenv("HEADROOM_SAVINGS_PATH", str(tmp_path / "savings.json"))

    monkeypatch.setattr(
        server,
        "_get_context_tool_stats",
        lambda: {
            "tool": "rtk",
            "label": "RTK",
            "installed": True,
            "tokens_saved": 25,
        },
    )

    config = ProxyConfig(cache_enabled=False, rate_limit_enabled=False, log_requests=False)

    with TestClient(create_app(config)) as client:
        proxy = client.app.state.proxy
        asyncio.run(
            emit_request_outcome(
                proxy,
                RequestOutcome(
                    request_id="req-1",
                    provider="openai",
                    model="gpt-4o",
                    original_tokens=1000,
                    optimized_tokens=700,
                    output_tokens=10,
                    tokens_saved=300,
                    attempted_input_tokens=900,
                ),
            )
        )

        stats = client.get("/stats").json()

    cs = stats["compression_summary"]
    assert cs["proxy"]["received_tokens"] == 1000
    assert cs["proxy"]["forwarded_tokens"] == 700
    assert cs["proxy"]["tokens_saved"] == 300
    assert cs["cli_filtering"]["tokens_saved"] == 25
    assert cs["all_layers"]["tokens_saved"] == 325
    assert cs["all_layers"]["received_tokens"] == 1025

    tokens = stats["tokens"]
    assert tokens["received_tokens"] == 1000
    assert tokens["forwarded_tokens"] == 700
    assert tokens["proxy_tokens_saved"] == 300
    assert tokens["all_layers_tokens_saved"] == 325
    assert tokens["total_saved_tokens"] == 325
    assert tokens["total_saved_percent"] == cs["all_layers"]["savings_percent_of_received"]

    compression = stats["savings"]["by_layer"]["compression"]
    assert compression["proxy_tokens_saved"] == 300
    assert compression["total_saved_tokens"] == 325
    assert compression["proxy"]["tokens_saved"] == 300
    assert compression["all_layers"]["tokens_saved"] == 325

    # Legacy flat keys remain for older consumers.
    assert tokens["saved"] == 325
    assert tokens["proxy_compression_saved"] == 300
    assert tokens["input"] == 700
