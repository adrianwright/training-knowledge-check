from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentDetails,
    AgentEndpointConfig,
    AgentIdentity,
    FixedRatioVersionSelectionRule,
    ProtocolConfiguration,
    ResponsesProtocolConfiguration,
    VersionSelector,
)
from azure.core.credentials import AccessToken
from azure.core.exceptions import ResourceNotFoundError
from conftest import parse_body, response_json

from knowledge_check.definitions import definitions
from knowledge_check.foundry import Binding, Deployment, FoundryAgents, deploy, validate_target
from knowledge_check.models import QuestionSet

ENDPOINT = "https://unit-test.services.ai.azure.com/api/projects/demo"
RESOURCE = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/unit-test"
    "/providers/Microsoft.CognitiveServices/accounts/unit-test/projects/demo"
)


def details(name="knowledge-author", identity="author-identity"):
    return AgentDetails(
        id=f"id-{name}",
        name=name,
        instance_identity=AgentIdentity(client_id=identity, principal_id=f"principal-{identity}"),
        blueprint=AgentIdentity(client_id=f"blueprint-{identity}", principal_id="unused"),
        agent_endpoint=AgentEndpointConfig(
            version_selector=VersionSelector(
                version_selection_rules=[
                    FixedRatioVersionSelectionRule(agent_version="1", traffic_percentage=100),
                ]
            ),
            protocol_configuration=ProtocolConfiguration(
                responses=ResponsesProtocolConfiguration()
            ),
        ),
    )


def state():
    return Deployment(
        project_endpoint=ENDPOINT,
        project_resource_id=RESOURCE,
        model="gpt-5.6-luna",
        agents={
            role: Binding(
                name=f"knowledge-{role}",
                version="1",
                agent_id=f"id-{role}",
                identity_client_id=f"{role}-identity",
                identity_principal_id=f"principal-{role}",
                blueprint_client_id=f"blueprint-{role}",
            )
            for role in ["author", "review"]
        },
    )


def test_real_sdk_targets_agent_endpoint_without_overriding_prompt(questions, tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=response_json(questions.model_dump_json()))

    credential = Mock()
    credential.get_token.return_value = AccessToken("unit-test-not-a-credential", 9999999999)
    with AIProjectClient(endpoint=ENDPOINT, credential=credential, allow_preview=True) as project:
        project.agents.get = Mock(return_value=details())
        original = project.get_openai_client
        project.get_openai_client = lambda **kwargs: original(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle))
        )
        result = FoundryAgents(project, state()).invoke(
            "author",
            {"task": "unit test"},
            QuestionSet,
            run_id="unit-run",
            directory=tmp_path,
            stage="author",
        )
    assert result == questions
    assert requests[0].url.path.endswith(
        "/agents/knowledge-author/endpoint/protocols/openai/responses"
    )
    body = parse_body(requests[0])
    assert not ({"model", "instructions", "temperature"} & body.keys())
    assert body["metadata"]["demo_run_id"] == "unit-run"


@pytest.mark.parametrize("change", ["route", "identity"])
def test_changed_remote_binding_prevents_invocation(change, tmp_path):
    remote = details()
    if change == "identity":
        remote.instance_identity.client_id = "different-agent"
    else:
        remote.agent_endpoint.version_selector.version_selection_rules[0].agent_version = "2"
    project = Mock()
    project.agents.get.return_value = remote
    with pytest.raises(ValueError):
        FoundryAgents(project, state()).invoke(
            "author", {}, QuestionSet, run_id="unit-run", directory=tmp_path, stage="author"
        )
    project.get_openai_client.assert_not_called()


def test_deploy_persists_two_real_prompt_definitions_and_native_identities(tmp_path):
    project = Mock()
    project.agents.get.side_effect = ResourceNotFoundError("missing")
    project.agents.create_version.return_value = SimpleNamespace(
        version="1", as_dict=lambda: {"version": "1"}
    )
    project.agents.update_details.side_effect = [
        details(),
        details("knowledge-review", "review-identity"),
    ]
    target = Deployment(
        project_endpoint=ENDPOINT, project_resource_id=RESOURCE, model="gpt-5.6-luna"
    )
    path = tmp_path / "foundry.json"
    deploy(project, target, path, "knowledge")
    restored = Deployment.model_validate_json(path.read_text())
    restored.require_complete()
    assert (
        restored.agents["author"].identity_client_id != restored.agents["review"].identity_client_id
    )
    for call in project.agents.create_version.call_args_list:
        definition = call.kwargs["definition"].as_dict()
        assert definition["kind"] == "prompt"
        assert definition["text"]["format"]["strict"] is True
        assert definition["tools"] == []


def test_deploy_does_not_modify_an_existing_agent(tmp_path):
    project = Mock()
    with pytest.raises(ValueError, match="already exists"):
        deploy(project, state(), tmp_path / "state.json", "knowledge")
    project.agents.create_version.assert_not_called()
    project.agents.update_details.assert_not_called()


def test_strict_schema_requires_source_security_findings():
    schema = definitions("gpt-5.6-luna")["review"].as_dict()["text"]["format"]["schema"]
    assert "source_findings" in schema["required"]


def test_target_endpoint_must_match_resource_id():
    with pytest.raises(ValueError, match="do not match"):
        validate_target("https://elsewhere.services.ai.azure.com/api/projects/demo", RESOURCE)
