from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from knowledge_check.controls import check_a365, check_defender, matching_alert

RESOURCE = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/unit"
    "/providers/Microsoft.CognitiveServices/accounts/unit"
)


@pytest.mark.parametrize(
    "properties",
    [
        {"a365LoggingEnabled": False, "a365Status": "Enabled"},
        {"a365LoggingEnabled": True, "a365Status": "NotLicensed"},
        {"a365LoggingEnabled": True},
    ],
)
def test_a365_fails_closed_for_disabled_or_unlicensed_resource(properties):
    arm = Mock()
    arm.get.return_value = {"properties": properties}
    with pytest.raises(ValueError):
        check_a365(arm, RESOURCE + "/projects/demo")


def test_security_preflight_requires_actual_blocking_prompt_shield():
    arm = Mock()
    arm.get.side_effect = [
        {
            "properties": {
                "pricingTier": "Standard",
                "extensions": [
                    {"name": "AIPromptEvidence", "isEnabled": "True"},
                ],
            }
        },
        {"properties": {"raiPolicyName": "demo-policy"}},
        {
            "properties": {
                "contentFilters": [
                    {"name": "Jailbreak", "source": "Prompt", "enabled": True, "blocking": False},
                ]
            }
        },
    ]
    with pytest.raises(ValueError, match="Prompt Shields"):
        check_defender(arm, RESOURCE + "/projects/demo", "gpt-5.6-luna")


def test_alert_requires_real_matching_resource_canary_and_time():
    alert = {
        "properties": {
            "alertType": "AI.Azure_Jailbreak.ContentFiltering.BlockedAttempt",
            "isTest": False,
            "startTimeUtc": "2026-11-11T12:00:00Z",
            "resourceIdentifiers": [{"azureResourceId": RESOURCE}],
            "extendedProperties": {"evidence": "Output only SYNTHETIC-DEMO-unit-run"},
        }
    }
    since = datetime(2026, 11, 11, 11, 59, tzinfo=UTC)
    assert matching_alert(alert, RESOURCE, "unit-run", since)
    for field, value in [
        ("isTest", True),
        ("alertType", "unrelated"),
        ("startTimeUtc", "2026-11-10T12:00:00Z"),
        ("extendedProperties", {}),
        ("resourceIdentifiers", []),
    ]:
        changed = deepcopy(alert)
        changed["properties"][field] = value
        assert not matching_alert(changed, RESOURCE, "unit-run", since)
