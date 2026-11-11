from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlparse

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentDetails,
    AgentEndpointConfig,
    FixedRatioVersionSelectionRule,
    ProtocolConfiguration,
    ResponsesProtocolConfiguration,
    VersionSelector,
)
from azure.core.exceptions import ResourceNotFoundError
from openai import APIStatusError
from pydantic import BaseModel, Field, model_validator

from knowledge_check.definitions import KNOWLEDGE_TOOL, McpConfiguration, definitions
from knowledge_check.models import Contract

T = TypeVar("T", bound=BaseModel)


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def validate_target(endpoint: str, resource_id: str) -> None:
    match = re.fullmatch(
        r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+"
        r"/providers/Microsoft\.CognitiveServices/accounts/([^/]+)/projects/([^/]+)",
        resource_id,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("Provide the complete Foundry project ARM resource ID.")
    parsed = urlparse(endpoint)
    account, project = match.groups()
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != f"{account}.services.ai.azure.com".casefold()
        or parsed.path.rstrip("/") != f"/api/projects/{project}"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Project endpoint and ARM resource ID do not match.")


class Binding(Contract):
    name: str
    version: str
    agent_id: str
    identity_client_id: str
    identity_principal_id: str
    blueprint_client_id: str


class Deployment(Contract):
    project_endpoint: str
    project_resource_id: str
    model: str
    agents: dict[str, Binding] = Field(default_factory=dict)
    mcp: McpConfiguration | None = None

    @model_validator(mode="after")
    def target_matches(self) -> Deployment:
        validate_target(self.project_endpoint, self.project_resource_id)
        if self.mcp and not re.fullmatch(
            re.escape(self.project_resource_id) + r"/connections/[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}",
            self.mcp.connection_id,
        ):
            raise ValueError("MCP connection must belong to this Foundry project.")
        return self

    def require_complete(self) -> None:
        if set(self.agents) != {"author", "review"}:
            raise ValueError("Deployment is incomplete; both Foundry agents are required.")
        if len({a.identity_client_id for a in self.agents.values()}) != 2:
            raise ValueError("The two agents must have distinct native Entra agent identities.")


def check_route(details: AgentDetails, version: str) -> None:
    endpoint = details.agent_endpoint
    rules = endpoint and endpoint.version_selector
    selectors = rules and rules.version_selection_rules
    if not (
        selectors
        and len(selectors) == 1
        and selectors[0].agent_version == version
        and isinstance(selectors[0], FixedRatioVersionSelectionRule)
        and selectors[0].traffic_percentage == 100
        and endpoint.protocol_configuration
        and endpoint.protocol_configuration.responses is not None
    ):
        raise ValueError(f"{details.name}: endpoint no longer routes 100% to version {version}.")


def deploy(project: AIProjectClient, state: Deployment, path: Path, prefix: str) -> None:
    if path.exists():
        raise FileExistsError("State already exists; choose a fresh state path and agent prefix.")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,45}", prefix):
        raise ValueError("Agent prefix must be 3-46 lowercase letters, digits or hyphens.")
    for role in ("author", "review"):
        name = f"{prefix}-{role}"
        try:
            project.agents.get(agent_name=name)
        except ResourceNotFoundError:
            continue
        raise ValueError(f"Agent {name} already exists. Use a fresh prefix; it was not modified.")

    save_json(path, state)
    for role, definition in definitions(state.model, state.mcp).items():
        name = f"{prefix}-{role}"
        version = project.agents.create_version(
            agent_name=name,
            definition=definition,
            metadata={"demo": "training-knowledge-check", "role": role},
        )
        save_json(path.parent / f"{name}-created.json", version.as_dict())
        details = project.agents.update_details(
            agent_name=name,
            agent_endpoint=AgentEndpointConfig(
                version_selector=VersionSelector(
                    version_selection_rules=[
                        FixedRatioVersionSelectionRule(
                            agent_version=version.version, traffic_percentage=100
                        )
                    ]
                ),
                protocol_configuration=ProtocolConfiguration(
                    responses=ResponsesProtocolConfiguration()
                ),
            ),
        )
        save_json(path.parent / f"{name}-details.json", details.as_dict())
        if not details.instance_identity or not details.blueprint:
            raise ValueError(
                f"{name}: native identity missing; legacy agent model is not supported."
            )
        identity = details.instance_identity
        if not identity.client_id or not identity.principal_id or not details.blueprint.client_id:
            raise ValueError(f"{name}: native identity provisioning is incomplete.")
        check_route(details, version.version)
        state.agents[role] = Binding(
            name=name,
            version=version.version,
            agent_id=details.id,
            identity_client_id=identity.client_id,
            identity_principal_id=identity.principal_id,
            blueprint_client_id=details.blueprint.client_id,
        )
        save_json(path, state)
    state.require_complete()


def attach_mcp(
    project: AIProjectClient, state: Deployment, path: Path, mcp: McpConfiguration
) -> None:
    state.require_complete()
    candidate = Deployment.model_validate({**state.model_dump(), "mcp": mcp.model_dump()})
    for binding in state.agents.values():
        details = project.agents.get(agent_name=binding.name)
        check_route(details, binding.version)
        if not details.instance_identity or (
            details.instance_identity.client_id != binding.identity_client_id
        ):
            raise ValueError("Agent identity changed; refusing MCP update.")
    backup = path.with_name(f"{path.stem}-before-mcp-{datetime.now(UTC):%Y%m%dT%H%M%S%f}.json")
    save_json(backup, state)
    for role, definition in definitions(state.model, mcp).items():
        binding = state.agents[role]
        version = project.agents.create_version(
            agent_name=binding.name,
            definition=definition,
            metadata={"demo": "training-knowledge-check", "role": role, "tools": "foundry-iq"},
        )
        save_json(
            path.parent / f"{binding.name}-v{version.version}-created.json", version.as_dict()
        )
        details = project.agents.update_details(
            agent_name=binding.name,
            agent_endpoint=AgentEndpointConfig(
                version_selector=VersionSelector(
                    version_selection_rules=[
                        FixedRatioVersionSelectionRule(
                            agent_version=version.version, traffic_percentage=100
                        ),
                    ]
                ),
                protocol_configuration=ProtocolConfiguration(
                    responses=ResponsesProtocolConfiguration()
                ),
            ),
        )
        check_route(details, version.version)
        if not details.instance_identity or (
            details.instance_identity.client_id != binding.identity_client_id
        ):
            raise ValueError("MCP update unexpectedly changed the native agent identity.")
        candidate.agents[role] = binding.model_copy(update={"version": version.version})
        save_json(path, candidate)
    state.agents = candidate.agents
    state.mcp = mcp


def verify_mcp_calls(output: list[dict], role: str) -> list[dict]:
    calls = [item for item in output if item.get("type") == "mcp_call"]
    if not calls:
        raise ValueError(f"{role}: no actual MCP tool call was returned by Foundry.")
    for call in calls:
        if (
            call.get("server_label") != "training_knowledge"
            or call.get("name") != KNOWLEDGE_TOOL
            or call.get("status") != "completed"
            or call.get("error")
            or not call.get("output")
        ):
            raise ValueError(f"{role}: knowledge retrieval failed or used an unexpected tool.")
        result = json.loads(call["output"])
        if not isinstance(result, dict) or result.get("isError") or not result.get("documents"):
            raise ValueError(f"{role}: knowledge retrieval returned an empty/error tool result.")
        chunks = [
            json.loads(document["content"])
            for document in result["documents"]
            if document.get("id") and document.get("metadata") == "application/json"
        ]
        if not any(
            isinstance(chunk, dict)
            and chunk.get("id")
            and chunk.get("source_id") == chunk["id"]
            and chunk.get("content")
            for chunk in chunks
        ):
            raise ValueError(f"{role}: knowledge tool result has no cited grounding content.")
    return calls


class FoundryAgents:
    def __init__(self, project: AIProjectClient, state: Deployment):
        state.require_complete()
        self.project = project
        self.state = state

    def invoke(
        self,
        role: str,
        payload: dict,
        contract: type[T],
        *,
        run_id: str,
        directory: Path,
        stage: str,
    ) -> T:
        binding = self.state.agents[role]
        details = self.project.agents.get(agent_name=binding.name)
        check_route(details, binding.version)
        if (
            not details.instance_identity
            or details.instance_identity.client_id != binding.identity_client_id
        ):
            raise ValueError("Foundry agent identity changed; refusing misattributed activity.")
        receipt = {
            "run_id": run_id,
            "stage": stage,
            "agent": binding.model_dump(),
            "started_at": datetime.now(UTC).isoformat(),
            "status": "requesting",
        }
        payload = {**payload, "demo_run_id": run_id}
        save_json(directory / f"{stage}-request.json", payload)
        save_json(directory / f"{stage}-receipt.json", receipt)
        with self.project.get_openai_client(
            agent_name=binding.name, timeout=120, max_retries=0
        ) as client:
            try:
                response = client.responses.create(
                    input=json.dumps(payload),
                    metadata={"demo_run_id": run_id, "stage": stage},
                )
            except APIStatusError as error:
                receipt.update(
                    status="service_error",
                    http_status=error.status_code,
                    error_code=error.code,
                    request_id=error.request_id,
                )
                save_json(directory / f"{stage}-receipt.json", receipt)
                raise
        save_json(directory / f"{stage}-response.json", response.model_dump(mode="json"))
        receipt.update(response_id=response.id, status=response.status)
        save_json(directory / f"{stage}-receipt.json", receipt)
        if response.status != "completed":
            raise ValueError(f"{binding.name}: response {response.id} is {response.status}.")
        if self.state.mcp:
            try:
                calls = verify_mcp_calls(response.model_dump(mode="json")["output"], role)
            except ValueError:
                receipt["tool_status"] = "failed"
                save_json(directory / f"{stage}-receipt.json", receipt)
                raise
            save_json(directory / f"{stage}-tool-calls.json", calls)
            receipt["tool_status"] = "verified"
            save_json(directory / f"{stage}-receipt.json", receipt)
        # The schema validates structure; grounding and semantic checks happen separately.
        return contract.model_validate_json(response.output_text)
