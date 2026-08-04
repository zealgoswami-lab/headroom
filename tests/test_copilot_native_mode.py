"""`wrap copilot --native`: route Copilot's own API through Headroom.

BYOK replaces Copilot's model routing and rejects Enterprise aliases like
``claude-sonnet-5``. Native mode redirects the CLI's GitHub-authenticated API
surface instead (via ``COPILOT_API_URL``), leaving Copilot's routing intact.
"""

from __future__ import annotations

import pytest

from headroom.providers.copilot.wrap import (
    COPILOT_BYOK_ENV_VARS,
    COPILOT_NATIVE_API_URL_ENV,
    build_launch_env,
    build_native_launch_env,
    native_api_url_supported,
)


def test_native_env_redirects_the_cli_api_surface() -> None:
    env, display = build_native_launch_env(port=8890, environ={}, project=None)
    assert env[COPILOT_NATIVE_API_URL_ENV] == "http://127.0.0.1:8890"
    assert any(COPILOT_NATIVE_API_URL_ENV in line for line in display)


def test_native_env_clears_every_byok_variable() -> None:
    seeded = dict.fromkeys(COPILOT_BYOK_ENV_VARS, "leftover")
    env, _ = build_native_launch_env(port=8890, environ=seeded, project=None)
    still_set = [v for v in COPILOT_BYOK_ENV_VARS if v in env]
    assert not still_set, f"BYOK vars survived into native mode: {still_set}"


def test_native_env_keeps_the_project_prefix() -> None:
    env, _ = build_native_launch_env(port=8890, environ={}, project="myproj")
    assert env[COPILOT_NATIVE_API_URL_ENV] == "http://127.0.0.1:8890/p/myproj"


def test_native_env_preserves_unrelated_environment() -> None:
    env, _ = build_native_launch_env(
        port=8890, environ={"PATH": "/usr/bin", "MY_VAR": "keep"}, project=None
    )
    assert env["PATH"] == "/usr/bin"
    assert env["MY_VAR"] == "keep"


def test_native_support_probe_is_tri_state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = {"LOCALAPPDATA": str(tmp_path / "none")}
    assert native_api_url_supported(environ=empty, home=str(tmp_path / "none")) is None

    pkg = tmp_path / "copilot" / "pkg" / "win32-x64" / "1.0.0"
    pkg.mkdir(parents=True)
    (pkg / "app.js").write_text("var x = 1;  // no override support\n", encoding="utf-8")
    assert (
        native_api_url_supported(
            environ={"LOCALAPPDATA": str(tmp_path)}, home=str(tmp_path / "none")
        )
        is False
    )

    (pkg / "app.js").write_text(
        "function qc(){return process.env.COPILOT_API_URL?1:2}\n", encoding="utf-8"
    )
    assert (
        native_api_url_supported(
            environ={"LOCALAPPDATA": str(tmp_path)}, home=str(tmp_path / "none")
        )
        is True
    )


def test_byok_env_builder_is_unchanged_by_native_mode() -> None:
    env, display = build_launch_env(
        port=8787, provider_type="openai", wire_api="responses", environ={}, project=None
    )
    assert env["COPILOT_PROVIDER_TYPE"] == "openai"
    assert env["COPILOT_PROVIDER_BASE_URL"] == "http://127.0.0.1:8787/v1"
    assert env["COPILOT_PROVIDER_WIRE_API"] == "responses"
    assert COPILOT_NATIVE_API_URL_ENV not in env
    assert any("COPILOT_PROVIDER_BASE_URL" in line for line in display)


def test_byok_and_native_are_mutually_exclusive_shapes() -> None:
    byok, _ = build_launch_env(
        port=8787, provider_type="openai", wire_api=None, environ={}, project=None
    )
    native, _ = build_native_launch_env(port=8787, environ={}, project=None)
    assert "COPILOT_PROVIDER_BASE_URL" in byok
    assert COPILOT_NATIVE_API_URL_ENV not in byok
    assert COPILOT_NATIVE_API_URL_ENV in native
    assert "COPILOT_PROVIDER_BASE_URL" not in native


def _invoke_copilot(monkeypatch: pytest.MonkeyPatch, args: list[str]):
    """Run `wrap copilot` with the launch intercepted, returning what it would do."""
    import shutil

    from click.testing import CliRunner

    from headroom.cli import wrap as wrap_mod
    from headroom.cli.main import main

    captured: dict[str, object] = {}

    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/copilot")
    monkeypatch.setattr(wrap_mod, "_check_proxy", lambda _p: False)

    class _Res:
        token = "gho_test"
        api_url = "https://api.githubcopilot.com"
        refresh_oauth_token = None
        api_token_expires_at = None

    monkeypatch.setattr(wrap_mod, "_require_copilot_subscription_resolution", lambda: _Res())
    monkeypatch.setattr(wrap_mod, "resolve_client_bearer_token", lambda: "gho_test")
    monkeypatch.setattr(wrap_mod, "_native_api_url_supported", lambda **_k: True)

    def _fake_launch(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(wrap_mod, "_launch_tool", _fake_launch)
    result = CliRunner().invoke(main, ["wrap", "copilot", *args])
    return result, captured


def test_native_flag_sets_the_override_and_no_byok_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    result, captured = _invoke_copilot(monkeypatch, ["--native", "--port", "8890"])
    assert result.exit_code == 0, result.output
    env = captured["env"]
    assert isinstance(env, dict)
    assert env[COPILOT_NATIVE_API_URL_ENV].startswith("http://127.0.0.1:8890")
    assert "COPILOT_PROVIDER_BASE_URL" not in env


def test_native_points_the_anthropic_upstream_at_copilot(monkeypatch: pytest.MonkeyPatch) -> None:
    _result, captured = _invoke_copilot(monkeypatch, ["--native", "--port", "8890"])
    assert captured["anthropic_api_url"] == "https://api.githubcopilot.com"
    assert captured["openai_api_url"] == "https://api.githubcopilot.com"


def test_byok_launch_leaves_the_anthropic_upstream_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    _result, captured = _invoke_copilot(
        monkeypatch, ["--subscription", "--port", "8890", "--", "--model", "gpt-5.4"]
    )
    assert captured["anthropic_api_url"] is None
    env = captured["env"]
    assert isinstance(env, dict)
    assert "COPILOT_PROVIDER_BASE_URL" in env
    assert COPILOT_NATIVE_API_URL_ENV not in env


def test_native_implies_subscription_so_no_model_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, captured = _invoke_copilot(monkeypatch, ["--native", "--port", "8890"])
    assert result.exit_code == 0, result.output
    assert "requires a model" not in result.output
    assert COPILOT_NATIVE_API_URL_ENV in captured["env"]


def test_native_rejects_wire_api(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _captured = _invoke_copilot(
        monkeypatch, ["--native", "--wire-api", "responses", "--port", "8890"]
    )
    assert result.exit_code != 0
    assert "--wire-api" in result.output


def test_native_rejects_anthropic_provider_type(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _captured = _invoke_copilot(
        monkeypatch, ["--native", "--provider-type", "anthropic", "--port", "8890"]
    )
    assert result.exit_code != 0
    assert "provider-type anthropic" in result.output


def test_native_rejects_no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _captured = _invoke_copilot(monkeypatch, ["--native", "--no-proxy", "--port", "8890"])
    assert result.exit_code != 0
    assert "--no-proxy" in result.output


def test_native_warns_when_cli_lacks_support(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    from click.testing import CliRunner

    from headroom.cli import wrap as wrap_mod
    from headroom.cli.main import main

    class _Res:
        token = "gho_test"
        api_url = "https://api.githubcopilot.com"
        refresh_oauth_token = None
        api_token_expires_at = None

    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/copilot")
    monkeypatch.setattr(wrap_mod, "_check_proxy", lambda _p: False)
    monkeypatch.setattr(wrap_mod, "_require_copilot_subscription_resolution", lambda: _Res())
    monkeypatch.setattr(wrap_mod, "_launch_tool", lambda **_k: None)
    monkeypatch.setattr(wrap_mod, "_native_api_url_supported", lambda **_k: False)

    result = CliRunner().invoke(main, ["wrap", "copilot", "--native", "--port", "8890"])
    assert result.exit_code == 0, result.output
    assert "COPILOT_API_URL" in result.output
    assert "silently get no compression" in result.output
    assert "without --native" in result.output


def test_anthropic_upstream_mismatch_predicate() -> None:
    from headroom.cli.wrap import _proxy_anthropic_upstream_mismatch

    native_proxy = {"anthropic_api_url": "https://api.githubcopilot.com"}
    assert _proxy_anthropic_upstream_mismatch(native_proxy, None) is True


def test_plain_proxy_is_still_reusable_for_claude() -> None:
    from headroom.cli.wrap import _proxy_anthropic_upstream_mismatch

    plain_proxy = {"anthropic_api_url": None}
    assert _proxy_anthropic_upstream_mismatch(plain_proxy, None) is False
    assert _proxy_anthropic_upstream_mismatch({}, None) is False


def test_native_proxy_is_reusable_for_the_same_native_upstream() -> None:
    from headroom.cli.wrap import _proxy_anthropic_upstream_mismatch

    native_proxy = {"anthropic_api_url": "https://api.githubcopilot.com"}
    assert (
        _proxy_anthropic_upstream_mismatch(native_proxy, "https://api.githubcopilot.com") is False
    )
    assert _proxy_anthropic_upstream_mismatch(native_proxy, "https://api.anthropic.com") is True


def test_probe_finds_a_needle_split_across_a_read_boundary(tmp_path) -> None:
    pkg = tmp_path / "copilot" / "pkg" / "win32-x64" / "1.0.77"
    pkg.mkdir(parents=True)
    (pkg / "app.js").write_text(
        "x" * ((1 << 20) - 8) + COPILOT_NATIVE_API_URL_ENV + "y" * 40, encoding="utf-8"
    )
    assert (
        native_api_url_supported(
            environ={"LOCALAPPDATA": str(tmp_path)}, home=str(tmp_path / "none")
        )
        is True
    )


def test_probe_answers_from_the_newest_bundle_only(tmp_path) -> None:
    root = tmp_path / "copilot" / "pkg" / "win32-x64"
    (root / "1.0.39").mkdir(parents=True)
    (root / "1.0.39" / "app.js").write_text(
        f"var a = process.env.{COPILOT_NATIVE_API_URL_ENV};", encoding="utf-8"
    )
    (root / "1.0.78").mkdir(parents=True)
    (root / "1.0.78" / "app.js").write_text("var a = 1; // variable removed", encoding="utf-8")
    assert (
        native_api_url_supported(
            environ={"LOCALAPPDATA": str(tmp_path)}, home=str(tmp_path / "none")
        )
        is False
    )


def test_probe_returns_unknown_for_an_empty_root(tmp_path) -> None:
    (tmp_path / "copilot" / "pkg").mkdir(parents=True)
    assert (
        native_api_url_supported(
            environ={"LOCALAPPDATA": str(tmp_path)}, home=str(tmp_path / "none")
        )
        is None
    )
