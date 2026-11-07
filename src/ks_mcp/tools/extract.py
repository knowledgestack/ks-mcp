"""Schema-shaped extraction via the KS agent (``POST /v1/agent/extract``)."""

from typing import Annotated, Any
from uuid import UUID

import ksapi
from ksapi.api.agent_api import AgentApi
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ks_mcp.client import DEFAULT_AGENT_TIMEOUT_S, get_api_client
from ks_mcp.errors import rest_to_mcp
from ks_mcp.schema import ExtractResult


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def extract(
        prompt: Annotated[
            str,
            Field(
                description=(
                    "What to pull out of the knowledge base, in plain language — e.g. "
                    "'the payment terms and renewal date from the Acme MSA'."
                ),
                min_length=1,
                max_length=8000,
            ),
        ],
        output_schema: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "JSON Schema (as an object) describing the shape to return. "
                    "Provide this OR `schema_path_part_id`, not both."
                )
            ),
        ] = None,
        schema_path_part_id: Annotated[
            UUID | None,
            Field(
                description=(
                    "path_part_id of a KS document whose body *is* a JSON Schema — use this "
                    "to reuse a schema your tenant already stores. Provide this OR "
                    "`output_schema`, not both."
                )
            ),
        ] = None,
        timeout_s: Annotated[
            float,
            Field(
                description="How long to wait for the agent to finish (seconds).",
                ge=10.0,
                le=600.0,
            ),
        ] = DEFAULT_AGENT_TIMEOUT_S,
    ) -> ExtractResult:
        """Extract structured, schema-conforming JSON from the knowledge base.

        Runs the KS agent in extraction mode: it retrieves the relevant chunks
        and returns an object matching your JSON Schema, so you get typed
        fields instead of prose to re-parse. Use this for populating records,
        filling forms, or building tables; use ``ask`` when you want a written
        answer with citations.

        Exactly one of ``output_schema`` / ``schema_path_part_id`` is required.
        The call blocks until the agent finishes and consumes one message of
        tenant quota.
        """
        if (output_schema is None) == (schema_path_part_id is None):
            raise ValueError(
                "Provide exactly one of `output_schema` (inline JSON Schema) or "
                "`schema_path_part_id` (a KS document holding the schema)."
            )

        api = AgentApi(get_api_client())
        request = ksapi.ExtractRequest(
            prompt=prompt,
            output_schema=output_schema,
            schema_path_part_id=schema_path_part_id,
        )
        try:
            # This route blocks until the agent workflow completes, so it
            # needs a far longer timeout than the default KS_TIMEOUT_S.
            response = await api.agent_extract(
                extract_request=request, _request_timeout=timeout_s
            )
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc
        return ExtractResult(structured=response.structured or {})
