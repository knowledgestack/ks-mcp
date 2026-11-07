import pytest


def test_env_returns_default_for_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    from ks_mcp.client import _env

    monkeypatch.delenv("OPTIONAL_VALUE", raising=False)
    assert _env("OPTIONAL_VALUE", required=False, default="fallback") == "fallback"


def test_env_raises_for_missing_required(monkeypatch: pytest.MonkeyPatch) -> None:
    from ks_mcp.client import _env

    monkeypatch.delenv("KS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="KS_API_KEY is not set"):
        _env("KS_API_KEY")


def test_get_api_client_sets_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    from ks_mcp.client import get_api_client

    get_api_client.cache_clear()
    monkeypatch.setenv("KS_API_KEY", "sk-user-custom")
    monkeypatch.setenv("KS_BASE_URL", "https://api.example.com")

    client = get_api_client()

    assert client.configuration.host == "https://api.example.com"
    assert client.default_headers["Authorization"] == "Bearer sk-user-custom"

    get_api_client.cache_clear()


def test_get_api_client_applies_ks_timeout_to_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """KS_TIMEOUT_S must reach the SDK's httpx client, not just our own calls.

    `ksapi.Configuration` has no timeout field and the SDK's lazily-built
    httpx2 client defaults to 5s, which is too tight for KS search.
    """
    from ks_mcp.client import get_api_client

    get_api_client.cache_clear()
    monkeypatch.setenv("KS_TIMEOUT_S", "42")

    client = get_api_client()

    assert client.rest_client.pool_manager is not None
    assert client.rest_client.pool_manager.timeout.read == 42.0
    get_api_client.cache_clear()


def test_get_api_client_defaults_timeout_above_sdk_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ks_mcp.client import DEFAULT_TIMEOUT_S, get_api_client

    get_api_client.cache_clear()
    monkeypatch.delenv("KS_TIMEOUT_S", raising=False)

    pool_manager = get_api_client().rest_client.pool_manager
    assert pool_manager is not None
    read_timeout = pool_manager.timeout.read
    assert read_timeout == DEFAULT_TIMEOUT_S
    assert read_timeout is not None
    assert read_timeout > 5.0  # the ksapi/httpx2 default we override
    get_api_client.cache_clear()


def test_get_api_client_is_cached() -> None:
    from ks_mcp.client import get_api_client

    get_api_client.cache_clear()
    client_a = get_api_client()
    client_b = get_api_client()

    assert client_a is client_b

    get_api_client.cache_clear()


def test_get_timeout_s_defaults_to_30(monkeypatch: pytest.MonkeyPatch) -> None:
    from ks_mcp.client import DEFAULT_TIMEOUT_S, get_timeout_s

    monkeypatch.delenv("KS_TIMEOUT_S", raising=False)
    assert get_timeout_s() == DEFAULT_TIMEOUT_S


def test_get_timeout_s_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from ks_mcp.client import get_timeout_s

    monkeypatch.setenv("KS_TIMEOUT_S", "12.5")
    assert get_timeout_s() == 12.5


@pytest.mark.parametrize("bad", ["abc", "0", "-5"])
def test_get_timeout_s_rejects_bad_values(bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from ks_mcp.client import get_timeout_s

    monkeypatch.setenv("KS_TIMEOUT_S", bad)
    with pytest.raises(RuntimeError, match="KS_TIMEOUT_S"):
        get_timeout_s()


def test_configure_logging_writes_to_stderr_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """stdout carries MCP frames on the stdio transport — logs must not go there."""
    import logging
    import sys

    import ks_mcp.client as client_module

    monkeypatch.setattr(client_module, "_LOG_CONFIGURED", False)
    monkeypatch.setenv("KS_LOG_LEVEL", "DEBUG")
    client_module.configure_logging()

    logger = logging.getLogger("ks_mcp")
    assert logger.level == logging.DEBUG
    assert logger.propagate is False
    assert [h.stream for h in logger.handlers] == [sys.stderr]  # type: ignore[attr-defined]


def test_configure_logging_falls_back_on_bad_level(monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    import ks_mcp.client as client_module

    monkeypatch.setattr(client_module, "_LOG_CONFIGURED", False)
    monkeypatch.setenv("KS_LOG_LEVEL", "LOUD")
    client_module.configure_logging()

    assert logging.getLogger("ks_mcp").level == logging.INFO


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("DEBUG", "DEBUG"),
        ("debug", "DEBUG"),
        ("  warning  ", "WARNING"),
        ("CRITICAL", "CRITICAL"),
        ("LOUD", "INFO"),
        ("", "INFO"),
    ],
)
def test_get_log_level_normalizes(
    env_value: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ks_mcp.client import get_log_level

    monkeypatch.setenv("KS_LOG_LEVEL", env_value)
    assert get_log_level() == expected


def test_get_log_level_defaults_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from ks_mcp.client import get_log_level

    monkeypatch.delenv("KS_LOG_LEVEL", raising=False)
    assert get_log_level() == "INFO"


def test_ks_log_level_reaches_fastmcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """KS_LOG_LEVEL must drive FastMCP too.

    FastMCP reconfigures root logging in its constructor, so if the level isn't
    passed through, the variable silently governs nothing the user can see.
    """
    from ks_mcp.server import build_server

    monkeypatch.setenv("KS_LOG_LEVEL", "WARNING")
    assert build_server().settings.log_level == "WARNING"


async def test_close_api_client_clears_cache() -> None:
    from ks_mcp.client import close_api_client, get_api_client

    get_api_client.cache_clear()
    get_api_client()
    assert get_api_client.cache_info().currsize == 1

    await close_api_client()
    assert get_api_client.cache_info().currsize == 0
