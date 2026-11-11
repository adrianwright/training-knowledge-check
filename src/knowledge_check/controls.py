from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from azure.core.credentials import TokenCredential

ACCOUNT_API = "2026-03-15-preview"
SECURITY_API = "2026-07-01"
ALERT_API = "2022-01-01"
JAILBREAK_ALERTS = {
    "AI.Azure_Jailbreak.ContentFiltering.BlockedAttempt",
    "AI.Azure_Jailbreak.ContentFiltering.DetectedAttempt",
}


class ArmReader:
    def __init__(self, credential: TokenCredential):
        self.credential = credential

    def get(self, resource_id: str, api_version: str) -> dict[str, Any]:
        if not resource_id.startswith("/subscriptions/") or "?" in resource_id:
            raise ValueError("Expected an ARM resource ID.")
        return self.get_url(f"https://management.azure.com{resource_id}?api-version={api_version}")

    def get_url(self, url: str) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "management.azure.com":
            raise ValueError("Refusing to send ARM credentials to a different host.")
        token = self.credential.get_token("https://management.azure.com/.default")
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            response = client.get(url, headers={"Authorization": f"Bearer {token.token}"})
            response.raise_for_status()
            return response.json()


def account_id(project_resource_id: str) -> str:
    return project_resource_id.rsplit("/projects/", 1)[0]


def check_a365(arm: ArmReader, project_resource_id: str) -> dict[str, Any]:
    properties = arm.get(account_id(project_resource_id), ACCOUNT_API)["properties"]
    if properties.get("a365LoggingEnabled") is not True:
        raise ValueError("Foundry resource a365LoggingEnabled must be true.")
    if properties.get("a365Status") != "Enabled":
        raise ValueError(
            f"A365 status is {properties.get('a365Status')!r}, not Enabled. "
            "An administrator must verify tenant licensing, activation and accepted terms."
        )
    return {"a365LoggingEnabled": True, "a365Status": "Enabled"}


def check_defender(arm: ArmReader, project_resource_id: str, model: str) -> dict[str, Any]:
    resource = account_id(project_resource_id)
    subscription = "/".join(resource.split("/")[:3])
    pricing = arm.get(f"{subscription}/providers/Microsoft.Security/pricings/AI", SECURITY_API)
    properties = pricing["properties"]
    if properties.get("pricingTier") != "Standard":
        raise ValueError("Enable the paid Defender for AI Services plan before the security test.")
    if not any(
        e.get("name") == "AIPromptEvidence" and e.get("isEnabled") == "True"
        for e in properties.get("extensions", [])
    ):
        raise ValueError("Enable AIPromptEvidence explicitly for exact-run alert readback.")
    if not model or "/" in model:
        raise ValueError("Invalid model deployment name.")
    deployment = arm.get(f"{resource}/deployments/{model}", ACCOUNT_API)
    policy_name = deployment["properties"].get("raiPolicyName")
    if not policy_name or "/" in policy_name:
        raise ValueError("Cannot verify the deployment's content filter; configure Prompt Shields.")
    policy = arm.get(f"{resource}/raiPolicies/{policy_name}", ACCOUNT_API)
    if not any(
        f.get("name") == "Jailbreak"
        and f.get("source") == "Prompt"
        and f.get("enabled") is True
        and f.get("blocking") is True
        for f in policy["properties"].get("contentFilters", [])
    ):
        raise ValueError("Enable and block Prompt Shields user prompt attacks on this deployment.")
    return {"plan": "Standard", "prompt_evidence": True, "prompt_shields_blocking": True}


def matching_alert(alert: dict[str, Any], resource_id: str, run_id: str, since: datetime) -> bool:
    properties = alert.get("properties", {})
    if properties.get("alertType") not in JAILBREAK_ALERTS:
        return False
    if properties.get("isTest") is True:
        return False
    identifiers = properties.get("resourceIdentifiers", [])
    target = resource_id.casefold().rstrip("/")
    if not any(
        str(i.get("azureResourceId", "")).casefold().rstrip("/") == target
        or str(i.get("azureResourceId", "")).casefold().startswith(target + "/")
        for i in identifiers
    ):
        return False
    evidence = properties.get("extendedProperties", {})
    # Require the canary in actual alert evidence, not just a matching time window.
    import json

    if f"SYNTHETIC-DEMO-{run_id}" not in json.dumps(evidence):
        return False
    timestamp = properties.get("startTimeUtc")
    if not isinstance(timestamp, str):
        return False
    try:
        start = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    return start.tzinfo is not None and start >= since


def find_alert(
    arm: ArmReader, project_resource_id: str, run_id: str, since: datetime
) -> dict[str, Any] | None:
    resource = account_id(project_resource_id)
    subscription = "/".join(resource.split("/")[:3])
    page = arm.get(f"{subscription}/providers/Microsoft.Security/alerts", ALERT_API)
    seen = set()
    while True:
        for alert in page.get("value", []):
            if matching_alert(alert, resource, run_id, since):
                return alert
        url = page.get("nextLink")
        if not url:
            return None
        if url in seen:
            raise ValueError("Defender returned a repeated pagination link.")
        seen.add(url)
        page = arm.get_url(url)
