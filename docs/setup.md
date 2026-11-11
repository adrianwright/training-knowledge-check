# Setup and permissions

## Infrastructure

The checked-in subscription-scope Bicep creates a **new** resource group and a new Foundry account/project. It does not discover or reuse a pre-existing Foundry project. The model defaults to `gpt-5.6-luna`, version `2026-07-09`, GlobalStandard, capacity 30. Check regional model availability and quota before provisioning.

The deployer needs resource creation and role-assignment permission, for example subscription Owner or an appropriately scoped combination of Contributor and User Access Administrator. Bicep grants the supplied operator **Foundry User** at the new project only. This is a data-plane role; subscription ownership alone is not a substitute. Role propagation may take several minutes.

`Provision-Demo.ps1 -PrincipalId '<object-id>'` can supply another operator; the default resolves the signed-in user. For service principals, invoke Bicep directly with `principalType=ServicePrincipal` and arrange a supported credential flow. The application currently authenticates with `AzureCliCredential`.

The account has system-assigned identity, local keys disabled and Entra-authenticated public access. Both agents get their **own native Entra agent identities and blueprints** from Foundry's current agent model. No client secret, Graph application write grant, extra A365 registration or custom telemetry ingestion permission is required by this application. Creating agent identities may require your tenant's Foundry/Entra administrative configuration.

The Agents API preview used by the pinned SDK is enabled explicitly with `allow_preview=True`. Initial definitions use structured output and no external tools. `Deploy-Knowledge.ps1 -Apply` adds the Foundry IQ read-only retrieval tool to new versions of those same agents. Each agent endpoint is configured to route 100% to the saved version. Do not concurrently change endpoint routing during a demonstration.

## Foundry IQ / Basic Azure AI Search

Run `Deploy-Knowledge.ps1` to preview, then `Deploy-Knowledge.ps1 -Apply` to deploy the entire knowledge layer. `-SearchLocation eastus` is the default; choose another supported region explicitly if capacity is unavailable. The script does not silently fail over or change the model region. Basic Search is billed while provisioned. One replica/partition is a demo configuration, not a production availability design.

`infra/knowledge.bicep` assigns these scoped roles:

| Principal | Scope | Role / purpose |
|---|---|---|
| Signed-in operator | Search service | Search Service Contributor to manage index/knowledge objects |
| Signed-in operator | Search service | Search Index Data Contributor to upload fictional documents and test retrieval |
| Foundry project managed identity | Search service | Search Index Data Reader for the knowledge-base MCP connection |
| Search system-assigned identity | Existing Foundry account | Cognitive Services User to call the existing GPT-5.6 Luna planner |

Search API keys are disabled. The `RemoteTool` project connection uses `ProjectManagedIdentity` and the Search audience; no Function key, API key, Graph grant or client secret is needed. The connection is shared within the project. Downstream Search authorization is the **project identity**, not a separate per-agent or end-user document ACL. Only synthetic content belongs in this demo.

The data-plane script uses Search REST `2026-08-01-preview` for the index, knowledge source/base and agentic retrieval. It uses the documented ARM project-connection API because `ProjectManagedIdentity` is not fully represented in the local Bicep connection type catalog. Infrastructure and RBAC remain Bicep. No portal step is needed to seed or attach the KB.

If fresh RBAC has not propagated, the script fails explicitly; wait and rerun. `setup-knowledge --apply` can also be rerun separately with `uv run --no-sync knowledge-check setup-knowledge --apply`. The two tiny indexed documents are built from `data/guide.json` and the checked-in rubric; there is no duplicate guide or separate embedding service. The KB returns extracted grounding and references; the two prompt agents still author/review the structured results.

`Test-Knowledge.ps1` exercises clean, ambiguous and repaired cases against Azure, with actual retrieval-call gates. Run-specific source text remains authoritative: retrieval must not erase the security appendix or replace a fault-injected draft.

## Agent 365

An administrator must:

1. Verify the tenant's Agent 365 licensing.
2. Activate Agent 365 and accept any required terms in its administration experience.
3. Allow native Foundry integration and check the new account's `a365Status`.
4. Verify `a365LoggingEnabled=true` on this dedicated Foundry account.

Bicep sets `a365LoggingEnabled=true`. It does not accept terms, purchase licenses, bypass consent or turn a missing license into a valid configuration. The generator fails closed when readback is not enabled. Collection is account-wide, so use synthetic data only in the dedicated resource.

The Bicep type catalog may warn that the preview `a365LoggingEnabled` property is unknown. This integration property is documented by Foundry; the actual ARM API and preflight readback, not a suppressed warning, determine whether it works.

## Recovering from a partial deployment

Infrastructure deployment is repeatable. Retry the same Bicep deployment after resolving its explicit Azure error. Child account writes are serialized to avoid Azure operation conflicts.

Agent deployment deliberately will not overwrite existing names or a prior state file. Inspect private creation receipts first. For a new attempt, use a fresh `--state .local/<new-state>.json` and `--prefix <new-prefix>` with the CLI, then pass that state to subsequent CLI commands. Do not delete existing agents or discard receipts automatically.

Knowledge attachment is an explicit update path, unlike initial agent creation. It checks both existing routes/identities before making changes, saves `.local/foundry-before-mcp-*.json`, and saves state after each successfully routed version. A partial update is not silently rolled back: inspect version receipts and current routes, then rerun the scripted attachment after resolving the error. Reattachment creates new versions of the same names, not duplicate identities.

## Official references

- [Current Foundry agent identity model](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-identity)
- [Migration from legacy Agent Applications](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/migrate-agent-applications)
- [Agent 365 integration](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-365-integration)
- [Configure Agent 365 data collection](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/configure-agent-365-data-collection)
- [Connect agents to Foundry IQ](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/foundry-iq-connect)
- [Create a search index knowledge source](https://learn.microsoft.com/en-us/azure/search/agentic-knowledge-source-how-to-search-index)
- [Create a knowledge base](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-create-knowledge-base)
