from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from azure.ai.projects import AIProjectClient
from azure.core.credentials import TokenCredential

from knowledge_check.foundry import Deployment, check_route, save_json


def publish_requests(state: Deployment, app_version: str) -> dict[str, dict]:
    state.require_complete()
    if not re.fullmatch(r"[1-9]\d*\.(0|[1-9]\d*)\.(0|[1-9]\d*)", app_version):
        raise ValueError(
            "App version must have three numeric parts and a nonzero major, for example 1.0.0."
        )
    return {
        role: {
            "agentDisplayName": binding.name,
            "performBotRegistration": True,
            "publishScope": "Shared",
            "publishAsAutopilot": False,
            "appVersion": app_version,
            "shortDescription": f"Fictional training knowledge-check {role} demo.",
            "fullDescription": (
                f"A Foundry prompt agent for the {role} stage of a synthetic workplace "
                "training demo. Requires structured workflow input and human approval; "
                "not for real assessments, learner records or high-stakes scoring."
            ),
            "developerName": "Training knowledge check demo",
            "developerWebsiteUrl": "https://github.com/adrianwright/training-knowledge-check",
            "privacyUrl": (
                "https://github.com/adrianwright/training-knowledge-check/"
                "blob/main/docs/security.md#demo-data-handling"
            ),
            "termsOfUseUrl": (
                "https://github.com/adrianwright/training-knowledge-check/"
                "blob/main/docs/security.md#demo-use"
            ),
        }
        for role, binding in state.agents.items()
    }


def publish_m365(
    client: httpx.Client,
    credential: TokenCredential,
    project: AIProjectClient,
    state: Deployment,
    directory: Path,
    app_version: str,
) -> dict[str, dict]:
    requests = publish_requests(state, app_version)
    before = {}
    for role, binding in state.agents.items():
        details = project.agents.get(agent_name=binding.name)
        check_route(details, binding.version)
        if not details.instance_identity or (
            details.instance_identity.client_id != binding.identity_client_id
        ):
            raise ValueError("Native agent identity changed; refusing publication.")
        before[role] = details.as_dict()
    attempt = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    results = {}
    for role, binding in state.agents.items():
        prefix = directory / f"{binding.name}-{attempt}"
        save_json(prefix.with_name(prefix.name + "-before.json"), before[role])
        save_json(prefix.with_name(prefix.name + "-request.json"), requests[role])
        published = None
        # A previous accepted submission can survive a failed readback or a partial batch.
        for path in sorted(directory.glob(f"{binding.name}-*-response.json"), reverse=True):
            receipt = json.loads(path.read_text(encoding="utf-8"))
            stem = path.name.removesuffix("-response.json")
            request = json.loads((directory / f"{stem}-request.json").read_text(encoding="utf-8"))
            snapshot = json.loads((directory / f"{stem}-before.json").read_text(encoding="utf-8"))
            if (
                receipt["http_status"] == 200
                and request == requests[role]
                and snapshot.get("instance_identity", {}).get("client_id")
                == binding.identity_client_id
            ):
                published = json.loads(receipt["body"])
                break
        if published is None:
            token = credential.get_token("https://ai.azure.com/.default")
            response = client.post(
                f"{state.project_endpoint}/agents/{quote(binding.name, safe='')}"
                "/microsoft365/publish?api-version=v1",
                headers={"Authorization": f"Bearer {token.token}"},
                json=requests[role],
            )
            save_json(
                prefix.with_name(prefix.name + "-response.json"),
                {"http_status": response.status_code, "body": response.text},
            )
            response.raise_for_status()
            published = response.json()
        if not isinstance(published, dict) or not published.get("titleId"):
            raise ValueError(f"{binding.name}: publication returned no titleId.")
        details = project.agents.get(agent_name=binding.name)
        after = details.as_dict()
        save_json(prefix.with_name(prefix.name + "-after.json"), after)
        check_route(details, binding.version)
        if not details.instance_identity or (
            details.instance_identity.client_id != binding.identity_client_id
        ):
            raise ValueError("Publication changed the native agent identity.")
        for identity in ("instance_identity", "blueprint"):
            if any(
                after.get(identity, {}).get(field) != before[role].get(identity, {}).get(field)
                for field in ("client_id", "principal_id")
            ):
                raise ValueError("Publication changed the native identity or blueprint.")
        endpoint = after.get("agent_endpoint", {})
        protocols = endpoint.get("protocol_configuration", {})
        schemes = {item["type"] for item in endpoint.get("authorization_schemes", [])}
        if (
            "activity" not in protocols
            or not schemes.intersection({"Entra", "AzureRbac"})
            or "BotServiceRbac" not in schemes
            or "BotServiceTenant" in schemes
        ):
            raise ValueError(f"{binding.name}: restricted publication readback failed.")
        results[role] = {
            "title_id": published["titleId"],
            "app_version": app_version,
            "submission_accepted": True,
            "catalog_visibility_verified": False,
            "publish_scope": "Shared",
            "publish_approval_status": endpoint.get("publish_approval_status"),
            "identity_client_id": binding.identity_client_id,
            "agent_version": binding.version,
        }
        save_json(directory / "published.json", results)
    return results
