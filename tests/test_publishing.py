import json
from unittest.mock import Mock

import httpx
import pytest
from azure.core.credentials import AccessToken
from test_foundry import details, state

from knowledge_check.publishing import publish_m365, publish_requests


def published_details(name="knowledge-author", identity="author-identity"):
    value = details(name, identity).as_dict()
    value["agent_endpoint"].update(
        protocol_configuration={"responses": {}, "activity": {}},
        authorization_schemes=[{"type": "Entra"}, {"type": "BotServiceRbac"}],
        publish_approval_status="published",
    )
    remote = Mock()
    remote.agent_endpoint = details().agent_endpoint
    remote.instance_identity.client_id = identity
    remote.as_dict.return_value = value
    return remote


@pytest.mark.parametrize(
    "failure", [None, "tenant", "no-title", "http", "route", "principal", "blueprint"]
)
@pytest.mark.parametrize("approval", ["published", "not_published"])
def test_publication_requires_personal_scope_and_preserves_bindings(tmp_path, failure, approval):
    requests = []
    after_author = published_details()
    after_author.as_dict.return_value["agent_endpoint"]["publish_approval_status"] = approval
    if failure == "tenant":
        after_author.as_dict.return_value["agent_endpoint"]["authorization_schemes"].append(
            {"type": "BotServiceTenant"}
        )
    if failure == "route":
        after_author.agent_endpoint.version_selector.version_selection_rules[0].agent_version = "2"
    if failure in ("principal", "blueprint"):
        key = "instance_identity" if failure == "principal" else "blueprint"
        after_author.as_dict.return_value[key]["principal_id"] = "changed-principal"
    project = Mock()
    project.agents.get.side_effect = [
        details(),
        details("knowledge-review", "review-identity"),
        after_author,
        published_details("knowledge-review", "review-identity"),
    ]
    credential = Mock()
    credential.get_token.return_value = AccessToken("unit-test-not-a-credential", 9999999999)

    def handle(request):
        requests.append(request)
        return httpx.Response(
            403 if failure == "http" else 200,
            json={} if failure == "no-title" else {"titleId": "unit-test-title"},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        if failure:
            with pytest.raises((ValueError, httpx.HTTPStatusError)):
                publish_m365(client, credential, project, state(), tmp_path, "1.0.0")
            assert not (tmp_path / "published.json").exists()
            return
        result = publish_m365(client, credential, project, state(), tmp_path, "1.0.0")
    assert set(result) == {"author", "review"}
    assert len(requests) == 2
    assert requests[0].url.path.endswith("/agents/knowledge-author/microsoft365/publish")
    for request in requests:
        body = json.loads(request.content)
        assert body["publishScope"] == "Shared"
        assert body["performBotRegistration"] is True
        assert body["publishAsAutopilot"] is False
        assert body["privacyUrl"].endswith("docs/security.md#demo-data-handling")
        assert body["termsOfUseUrl"].endswith("docs/security.md#demo-use")
    assert result["author"]["identity_client_id"] == "author-identity"
    assert result["author"]["publish_approval_status"] == approval
    assert result["author"]["catalog_visibility_verified"] is False
    assert (tmp_path / "published.json").exists()
    after_review = published_details("knowledge-review", "review-identity")
    project.agents.get.side_effect = [after_author, after_review, after_author, after_review]
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        publish_m365(client, credential, project, state(), tmp_path, "1.0.0")
    assert len(requests) == 2


def test_publication_refuses_identity_drift_before_any_post(tmp_path):
    project = Mock()
    project.agents.get.return_value = details(identity="different-identity")
    client = Mock()
    with pytest.raises(ValueError, match="identity changed"):
        publish_m365(client, Mock(), project, state(), tmp_path, "1.0.0")
    client.post.assert_not_called()


@pytest.mark.parametrize("version", ["latest", "0.1.0", "1.01.0"])
def test_publication_requires_explicit_valid_app_version(version):
    with pytest.raises(ValueError, match="three numeric parts"):
        publish_requests(state(), version)
