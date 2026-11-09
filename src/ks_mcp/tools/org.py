"""Tenant-context tools: organisation info + current datetime."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import ksapi
from ksapi.api.tenants_api import TenantsApi
from ksapi.api.users_api import UsersApi
from mcp.server.fastmcp import FastMCP

from ks_mcp.client import get_api_client
from ks_mcp.errors import rest_to_mcp
from ks_mcp.middleware import current_credentials, set_resolved_tenant
from ks_mcp.schema import CurrentDateTime, OrganizationInfo

# Single-tenant fallback cache, used only when no request credentials are
# bound. In gateway mode (#16) the resolved tenant is stored on the request's
# credentials instead — a module global here would serve tenant A's name and
# timezone to tenant B.
_ambient_tenant: OrganizationInfo | None = None


def _enum_value(value: object, default: str) -> str:
    """Unwrap a ksapi enum (or plain string) to its wire value."""
    if value is None:
        return default
    return str(getattr(value, "value", None) or value) or default


async def _fetch_tenant() -> OrganizationInfo:
    """Resolve the caller's tenant.

    There is no ``/v1/tenants/me`` route, so the API-key identity is resolved
    via ``GET /v1/users/me`` (which reports ``current_tenant_id``) and then the
    tenant itself is fetched by id.
    """
    global _ambient_tenant

    creds = current_credentials()
    if creds is not None:
        if creds.tenant is not None:
            return creds.tenant
    elif _ambient_tenant is not None:
        return _ambient_tenant

    client = get_api_client()
    try:
        me = await UsersApi(client).get_me()
        tenant: ksapi.TenantResponse = await TenantsApi(client).get_tenant(
            tenant_id=me.current_tenant_id
        )
    except ksapi.ApiException as exc:
        raise rest_to_mcp(exc) from exc

    settings = tenant.settings
    resolved = OrganizationInfo(
        tenant_id=tenant.id,
        name=tenant.name or "",
        default_language=_enum_value(getattr(settings, "language", None), "en"),
        timezone=_enum_value(getattr(settings, "timezone", None), "UTC"),
    )
    if creds is not None:
        # Cached for this request only.
        set_resolved_tenant(resolved)
    else:
        _ambient_tenant = resolved
    return resolved


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_organization_info() -> OrganizationInfo:
        """Return the caller's tenant metadata: id, name, default language, timezone.

        Cached per process. Useful as the first call when the user's question
        depends on locale ("translate this to our default language") or the
        tenant's name ("write an internal email at <tenant>").
        """
        return await _fetch_tenant()

    @mcp.tool()
    async def get_current_datetime() -> CurrentDateTime:
        """Return current date/time in both UTC and the tenant's timezone.

        Use whenever the user's query is relative ("yesterday's notes", "this
        quarter's results"). The tool consults ``get_organization_info`` for
        the tenant's IANA timezone, falling back to UTC if unset.
        """
        info = await _fetch_tenant()
        now_utc = datetime.now(UTC)
        try:
            zone = ZoneInfo(info.timezone)
        except Exception:  # noqa: BLE001 - any bad tz string falls back to UTC
            zone = UTC
        return CurrentDateTime(
            iso_utc=now_utc.isoformat(),
            iso_local=now_utc.astimezone(zone).isoformat(),
            timezone=str(zone),
        )
