import json
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
from openai import APIStatusError

from knowledge_check.definitions import McpConfiguration, definitions
from knowledge_check.foundry import (
    Binding,
    Deployment,
    FoundryAgents,
    attach_mcp,
    deploy,
    validate_target,
    verify_mcp_calls,
)
from knowledge_check.knowledge import knowledge_documents, setup_knowledge
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


def test_service_filter_error_keeps_private_category_evidence(tmp_path):
    evidence = {
        "code": "content_filter",
        "innererror": {"content_filter_result": {"jailbreak": {"filtered": True}}},
    }
    credential = Mock()
    credential.get_token.return_value = AccessToken("unit-test-not-a-credential", 9999999999)
    transport = httpx.MockTransport(lambda request: httpx.Response(400, json={"error": evidence}))
    with AIProjectClient(endpoint=ENDPOINT, credential=credential, allow_preview=True) as project:
        project.agents.get = Mock(return_value=details())
        original = project.get_openai_client
        project.get_openai_client = lambda **kwargs: original(
            **kwargs, http_client=httpx.Client(transport=transport)
        )
        with pytest.raises(APIStatusError):
            FoundryAgents(project, state()).invoke(
                "author", {}, QuestionSet, run_id="unit-run", directory=tmp_path, stage="author"
            )
    assert json.loads((tmp_path / "author-service-error.json").read_text()) == evidence
    assert not (tmp_path / "author-response.json").exists()


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


def mcp_config():
    return McpConfiguration(
        endpoint="https://unit-test.search.windows.net/knowledgebases/training-knowledge/mcp"
        "?api-version=2026-08-01-preview",
        connection_id=f"{RESOURCE}/connections/training-knowledge",
    )


def knowledge_call():
    return {
        "type": "mcp_call",
        "name": "knowledge_base_retrieve",
        "server_label": "training_knowledge",
        "status": "completed",
        "output": json.dumps(
            {
                "documents": [
                    {
                        "id": "tool-document-id",
                        "metadata": "application/json",
                        "content": json.dumps(
                            {
                                "id": "guide-v1",
                                "source_id": "guide-v1",
                                "content": "grounding",
                            }
                        ),
                    }
                ]
            }
        ),
    }


def test_knowledge_definitions_allow_only_read_only_retrieval():
    for definition in definitions("gpt-5.6-luna", mcp_config()).values():
        tool = definition.as_dict()["tools"][0]
        assert tool["type"] == "mcp"
        assert tool["allowed_tools"] == ["knowledge_base_retrieve"]
        assert tool["project_connection_id"] == mcp_config().connection_id
        assert "headers" not in tool and "authorization" not in tool
        assert "supplied source for this run" in definition.instructions


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://unit.search.windows.net/knowledgebases/demo/mcp"
        "?api-version=2026-08-01-preview&api-key=secret",
        "https://unit.search.windows.net.attacker.example/knowledgebases/demo/mcp"
        "?api-version=2026-08-01-preview",
        "http://unit.search.windows.net/knowledgebases/demo/mcp?api-version=2026-08-01-preview",
    ],
)
def test_knowledge_endpoint_rejects_embedded_secrets_and_wrong_hosts(endpoint):
    with pytest.raises(ValueError):
        McpConfiguration(endpoint=endpoint, connection_id=mcp_config().connection_id)


def test_attach_versions_existing_agents_without_replacing_identities(tmp_path):
    project = Mock()
    before = [details(), details("knowledge-review", "review-identity")]
    after = [details(), details("knowledge-review", "review-identity")]
    for remote in after:
        remote.agent_endpoint.version_selector.version_selection_rules[0].agent_version = "2"
    project.agents.get.side_effect = before
    project.agents.update_details.side_effect = after
    project.agents.create_version.return_value = SimpleNamespace(
        version="2", as_dict=lambda: {"version": "2"}
    )
    target = state()
    path = tmp_path / "foundry.json"
    attach_mcp(project, target, path, mcp_config())
    saved = Deployment.model_validate_json(path.read_text())
    assert saved.mcp == mcp_config()
    assert all(binding.version == "2" for binding in saved.agents.values())
    assert saved.agents["author"].identity_client_id == "author-identity"
    assert len(list(tmp_path.glob("foundry-before-mcp-*.json"))) == 1
    assert [c.kwargs["agent_name"] for c in project.agents.create_version.call_args_list] == [
        "knowledge-author",
        "knowledge-review",
    ]
    for call in project.agents.update_details.call_args_list:
        assert set(call.kwargs["agent_endpoint"].as_dict()) == {"version_selector"}


@pytest.mark.parametrize(
    "failure", ["missing", "tool-error", "nested-error", "empty", "wrong-tool", "incomplete"]
)
def test_missing_or_failed_knowledge_calls_do_not_pass(failure):
    call = knowledge_call()
    if failure == "tool-error":
        call["error"] = {"message": "Forbidden"}
    elif failure == "nested-error":
        call["output"] = json.dumps({"isError": True, "content": [{"text": "Forbidden"}]})
    elif failure == "empty":
        call["output"] = json.dumps({"content": []})
    elif failure == "wrong-tool":
        call["name"] = "publish"
    elif failure == "incomplete":
        call["status"] = "in_progress"
    with pytest.raises(ValueError):
        verify_mcp_calls([] if failure == "missing" else [call], "author")


def test_successful_knowledge_call_keeps_service_evidence():
    call = knowledge_call()
    assert verify_mcp_calls([call], "review") == [call]


@pytest.mark.parametrize("existing", [False, True])
def test_knowledge_setup_uses_canonical_content_and_keyless_connection(source, tmp_path, existing):
    requests = []

    def handle(request):
        requests.append(request)
        if existing and request.method == "PUT" and request.url.host != "management.azure.com":
            return httpx.Response(204)
        if request.url.path.endswith("/docs/index"):
            return httpx.Response(200, json={"value": [{"status": True}, {"status": True}]})
        if request.url.path.endswith("/retrieve"):
            return httpx.Response(
                200,
                json={
                    "references": [{"docKey": source.id}],
                    "response": [
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps([{"content": source.text}]),
                                }
                            ]
                        }
                    ],
                },
            )
        return httpx.Response(200, json={})

    credential = Mock()
    credential.get_token.return_value = AccessToken("unit-test-not-a-credential", 9999999999)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        setup_knowledge(
            client,
            credential,
            state(),
            {
                "searchEndpoint": {"value": "https://unit-test.search.windows.net"},
                "openaiEndpoint": {"value": "https://unit-test.openai.azure.com"},
            },
            source,
            tmp_path,
        )
    assert source.text in knowledge_documents(source)[0]["content"]
    connection = next(r for r in requests if r.url.host == "management.azure.com")
    properties = parse_body(connection)["properties"]
    assert properties["authType"] == "ProjectManagedIdentity"
    assert properties["audience"] == "https://search.azure.com/"
    assert "credentials" not in properties
    kb = next(r for r in requests if r.method == "PUT" and "/knowledgebases/" in r.url.path)
    definition = parse_body(kb)
    assert definition["outputMode"] == "extractiveData"
    assert definition["models"][0]["azureOpenAIParameters"]["modelName"] == "gpt-5.6-luna"
    assert (tmp_path / "knowledge-retrieval.json").exists()


@pytest.mark.parametrize("failure", ["index", "references", "grounding", "partial"])
def test_knowledge_setup_rejects_partial_or_missing_retrieval(source, tmp_path, failure):
    def handle(request):
        if request.url.path.endswith("/docs/index"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"status": True},
                        {"status": failure != "index"},
                    ]
                },
            )
        if request.url.path.endswith("/retrieve"):
            return httpx.Response(
                206 if failure == "partial" else 200,
                json={
                    "references": [] if failure == "references" else [{"docKey": source.id}],
                    "response": [
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "[]"
                                    if failure == "grounding"
                                    else json.dumps([{"content": source.text}]),
                                }
                            ]
                        }
                    ],
                },
            )
        return httpx.Response(200, json={})

    credential = Mock()
    credential.get_token.return_value = AccessToken("unit-test-not-a-credential", 9999999999)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client, pytest.raises(ValueError):
        setup_knowledge(
            client,
            credential,
            state(),
            {
                "searchEndpoint": {"value": "https://unit-test.search.windows.net"},
                "openaiEndpoint": {"value": "https://unit-test.openai.azure.com"},
            },
            source,
            tmp_path,
        )
    assert not (tmp_path / "knowledge-mcp.json").exists()


def test_connection_must_be_a_single_resource_in_the_current_project():
    with pytest.raises(ValueError, match="this Foundry project"):
        Deployment.model_validate(
            {
                **state().model_dump(),
                "mcp": {
                    **mcp_config().model_dump(),
                    "connection_id": f"{RESOURCE}/connections/../other-project",
                },
            }
        )
