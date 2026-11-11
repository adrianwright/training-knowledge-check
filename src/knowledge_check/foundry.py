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

from knowledge_check.definitions import definitions
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

    @model_validator(mode="after")
    def target_matches(self) -> Deployment:
        validate_target(self.project_endpoint, self.project_resource_id)
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
    for role, definition in definitions(state.model).items():
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
        # The schema validates structure; grounding and semantic checks happen separately.
        return contract.model_validate_json(response.output_text)
