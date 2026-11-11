from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from azure.ai.projects import AIProjectClient
from azure.core.exceptions import AzureError
from azure.identity import AzureCliCredential
from openai import OpenAIError

from knowledge_check.controls import ArmReader, check_a365, check_defender, find_alert
from knowledge_check.definitions import KNOWLEDGE_TOOL, McpConfiguration, definitions
from knowledge_check.foundry import Deployment, FoundryAgents, attach_mcp, deploy, save_json
from knowledge_check.knowledge import setup_knowledge
from knowledge_check.models import Artifact, Source
from knowledge_check.publishing import publish_m365, publish_requests
from knowledge_check.workflow import approve, execute


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Two real Foundry prompt agents; no offline provider."
    )
    commands = root.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "deploy", help="Preview prompt definitions; --apply creates agents."
    )
    create.add_argument("--endpoint", required=True)
    create.add_argument("--project-resource-id", required=True)
    create.add_argument("--model", required=True)
    create.add_argument("--prefix", default="knowledge")
    create.add_argument("--state", type=Path, default=Path(".local/foundry.json"))
    create.add_argument("--apply", action="store_true")
    tools = commands.add_parser("attach-mcp", help="Version existing agents with Foundry IQ.")
    tools.add_argument("--state", type=Path, default=Path(".local/foundry.json"))
    tools.add_argument("--url", required=True)
    tools.add_argument("--connection", required=True)
    tools.add_argument("--apply", action="store_true")
    kb = commands.add_parser(
        "setup-knowledge", help="Seed and verify the Foundry IQ knowledge base."
    )
    kb.add_argument("--state", type=Path, default=Path(".local/foundry.json"))
    kb.add_argument("--outputs", type=Path, default=Path(".local/knowledge-outputs.json"))
    kb.add_argument("--source", type=Path, default=Path("data/guide.json"))
    kb.add_argument("--apply", action="store_true")
    publish = commands.add_parser(
        "publish-m365", help="Publish both existing agents with Just you visibility."
    )
    publish.add_argument("--state", type=Path, default=Path(".local/foundry.json"))
    publish.add_argument("--app-version", default="1.0.0")
    publish.add_argument("--output", type=Path, default=Path(".local/m365-publishing"))
    publish.add_argument("--apply", action="store_true")
    for command in ["generate", "preflight", "verify-defender"]:
        child = commands.add_parser(command)
        child.add_argument("--state", type=Path, default=Path(".local/foundry.json"))
        if command == "generate":
            child.add_argument("--source", type=Path, default=Path("data/guide.json"))
            child.add_argument(
                "--scenario",
                choices=["mixed", "clean", "ambiguous", "repaired", "security"],
                default="mixed",
            )
            child.add_argument("--count", type=int, choices=range(1, 21), default=3)
            child.add_argument("--output", type=Path, required=True)
        if command == "preflight":
            child.add_argument("--security", action="store_true")
        if command == "verify-defender":
            child.add_argument("--manifest", type=Path, required=True)
            child.add_argument("--wait-seconds", type=int, default=600)
    human = commands.add_parser("approve")
    human.add_argument("--input", type=Path, required=True)
    human.add_argument("--output", type=Path, required=True)
    human.add_argument("--human", required=True)
    human.add_argument("--confirm-reviewed", action="store_true", required=True)
    return root


def generate(args, state: Deployment, credential: AzureCliCredential) -> None:
    if args.output.exists():
        raise FileExistsError(
            "Use a new output directory; previous evidence will not be overwritten."
        )
    arm = ArmReader(credential)
    controls = check_a365(arm, state.project_resource_id)
    if args.scenario == "security":
        controls["defender"] = check_defender(arm, state.project_resource_id, state.model)
    source = Source.model_validate_json(args.source.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True)
    manifest = {
        "started_at": datetime.now(UTC).isoformat(),
        "project_resource_id": state.project_resource_id,
        "agents": {role: binding.model_dump() for role, binding in state.agents.items()},
        "controls": controls,
        "a365_readback_verified": False,
        "mcp": state.mcp.model_dump() if state.mcp else None,
        "runs": [],
    }
    manifest_path = args.output / "manifest.json"
    save_json(manifest_path, manifest)
    with AIProjectClient(
        endpoint=state.project_endpoint, credential=credential, allow_preview=True
    ) as project:
        agents = FoundryAgents(project, state)
        for index in range(args.count):
            scenario = (
                ["clean", "ambiguous", "repaired"][index % 3]
                if args.scenario == "mixed"
                else args.scenario
            )
            run_id = str(uuid4())
            entry = {
                "run_id": run_id,
                "scenario": scenario,
                "started_at": datetime.now(UTC).isoformat(),
                "status": "in_progress",
            }
            manifest["runs"].append(entry)
            save_json(manifest_path, manifest)
            print(f"{index + 1}/{args.count}: {scenario}, run {run_id}", flush=True)
            try:
                artifact = execute(agents, source, scenario, run_id, args.output / run_id)
            except (ValueError, OpenAIError, AzureError):
                entry["status"] = "failed"
                save_json(manifest_path, manifest)
                raise
            entry["status"] = artifact.status if artifact else "blocked_by_service"
            save_json(manifest_path, manifest)
            print(entry["status"], flush=True)
    print(f"Evidence: {manifest_path}. A365 portal readback is still required.")


def verify_defender(args, state: Deployment, arm: ArmReader) -> None:
    if not 0 <= args.wait_seconds <= 1800:
        raise ValueError("--wait-seconds must be between 0 and 1800.")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest["project_resource_id"] != state.project_resource_id:
        raise ValueError("Manifest belongs to a different Foundry project.")
    runs = [r for r in manifest["runs"] if r["scenario"] == "security"]
    if not runs:
        raise ValueError("Manifest contains no security runs.")
    deadline = time.monotonic() + args.wait_seconds
    pending = {r["run_id"]: r for r in runs}
    verified = {}
    while pending:
        for run_id, run in list(pending.items()):
            alert = find_alert(
                arm,
                state.project_resource_id,
                run_id,
                datetime.fromisoformat(run["started_at"]),
            )
            if alert:
                save_json(args.manifest.parent / run_id / "defender-alert.json", alert)
                verified[run_id] = alert["id"]
                del pending[run_id]
        save_json(
            args.manifest.parent / "defender-readback.json",
            {
                "verified": verified,
                "unverified": list(pending),
            },
        )
        if not pending:
            print(f"Verified actual Defender alerts for {len(verified)} security run(s).")
            return
        if time.monotonic() >= deadline:
            raise ValueError(
                "No exact-run Defender alert for: "
                + ", ".join(pending)
                + ". Detection is unverified, not passed. "
                "Inspect masked/aggregated alerts manually."
            )
        print(f"Awaiting Defender readback for {len(pending)} run(s)...", flush=True)
        time.sleep(min(30, max(0, deadline - time.monotonic())))


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "approve":
            if args.output.exists() or args.input.resolve() == args.output.resolve():
                raise FileExistsError("Approval must use a new output file.")
            artifact = Artifact.model_validate_json(args.input.read_text(encoding="utf-8"))
            save_json(args.output, approve(artifact, args.human))
            print("Human approval recorded locally; nothing was published.")
            return 0
        if args.command == "deploy":
            state = Deployment(
                project_endpoint=args.endpoint.rstrip("/"),
                project_resource_id=args.project_resource_id,
                model=args.model,
            )
            plan = {
                "project_endpoint": state.project_endpoint,
                "agents": {
                    f"{args.prefix}-{role}": definition.as_dict()
                    for role, definition in definitions(state.model).items()
                },
            }
            if not args.apply:
                print(json.dumps(plan, indent=2))
                print("Preview only. --apply creates two prompt agents and sets version routing.")
                return 0
            with (
                AzureCliCredential() as credential,
                AIProjectClient(
                    endpoint=state.project_endpoint, credential=credential, allow_preview=True
                ) as project,
            ):
                deploy(project, state, args.state, args.prefix)
            print(f"Created both Foundry prompt agents. Native identities saved in {args.state}.")
            return 0
        state = Deployment.model_validate_json(args.state.read_text(encoding="utf-8"))
        state.require_complete()
        if args.command == "setup-knowledge":
            if not args.apply:
                print(
                    "Preview: seed guide/rubric, create knowledge source/base "
                    "and keyless connection."
                )
                return 0
            outputs = json.loads(args.outputs.read_text(encoding="utf-8-sig"))
            source = Source.model_validate_json(args.source.read_text(encoding="utf-8"))
            with AzureCliCredential() as credential, httpx.Client(timeout=180) as client:
                mcp = setup_knowledge(
                    client, credential, state, outputs, source, args.outputs.parent
                )
            print(f"Knowledge retrieval verified. MCP endpoint: {mcp.endpoint}")
            return 0
        if args.command == "attach-mcp":
            mcp = McpConfiguration(endpoint=args.url, connection_id=args.connection)
            if not args.apply:
                print(
                    json.dumps(
                        {
                            "mcp": mcp.model_dump(),
                            "tools": {"author": KNOWLEDGE_TOOL, "review": KNOWLEDGE_TOOL},
                            "action": "Version agents; preserve identities; update routing.",
                        },
                        indent=2,
                    )
                )
                return 0
            with (
                AzureCliCredential() as credential,
                AIProjectClient(
                    endpoint=state.project_endpoint, credential=credential, allow_preview=True
                ) as project,
            ):
                attach_mcp(project, state, args.state, mcp)
            print("Foundry IQ attached to both prompt agents; native identities preserved.")
            return 0
        with AzureCliCredential() as credential:
            if args.command == "publish-m365":
                requests = publish_requests(state, args.app_version)
                if not args.apply:
                    print(json.dumps(requests, indent=2))
                    print("Preview only. --apply publishes with Just you visibility.")
                    return 0
                with (
                    AIProjectClient(
                        endpoint=state.project_endpoint, credential=credential, allow_preview=True
                    ) as project,
                    httpx.Client(timeout=120, follow_redirects=False) as client,
                ):
                    result = publish_m365(
                        client, credential, project, state, args.output, args.app_version
                    )
                print(json.dumps(result, indent=2))
                print(
                    "M365 submissions accepted; scoped endpoints verified. "
                    "Catalog visibility and A365/Defender ingestion remain unverified."
                )
                return 0
            arm = ArmReader(credential)
            if args.command == "generate":
                generate(args, state, credential)
            elif args.command == "preflight":
                print(json.dumps(check_a365(arm, state.project_resource_id), indent=2))
                if args.security:
                    print(
                        json.dumps(
                            check_defender(arm, state.project_resource_id, state.model), indent=2
                        )
                    )
            elif args.command == "verify-defender":
                verify_defender(args, state, arm)
        return 0
    except (ValueError, OSError, OpenAIError, AzureError, httpx.HTTPError) as error:
        # Service exception bodies can contain prompt content. Keep those in private service logs.
        if isinstance(error, (OpenAIError, AzureError, httpx.HTTPError)):
            print(
                f"ERROR: {type(error).__name__}; check permissions/service logs.", file=sys.stderr
            )
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
