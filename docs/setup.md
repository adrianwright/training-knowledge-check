# Setup and permissions

## Infrastructure

The checked-in subscription-scope Bicep creates a **new** resource group and a new Foundry account/project. It does not discover or reuse a pre-existing Foundry project. The model defaults to `gpt-5.6-luna`, version `2026-07-09`, GlobalStandard, capacity 30. Check regional model availability and quota before provisioning.

The deployer needs resource creation and role-assignment permission, for example subscription Owner or an appropriately scoped combination of Contributor and User Access Administrator. Bicep grants the supplied operator **Foundry User** at the new project only. This is a data-plane role; subscription ownership alone is not a substitute. Role propagation may take several minutes.

`Provision-Demo.ps1 -PrincipalId '<object-id>'` can supply another operator; the default resolves the signed-in user. For service principals, invoke Bicep directly with `principalType=ServicePrincipal` and arrange a supported credential flow. The application currently authenticates with `AzureCliCredential`.

The account has system-assigned identity, local keys disabled and Entra-authenticated public access. Both agents get their **own native Entra agent identities and blueprints** from Foundry's current agent model. No client secret, Graph application write grant, extra A365 registration or custom telemetry ingestion permission is required by this application. Creating agent identities may require your tenant's Foundry/Entra administrative configuration.

The Agents API preview used by the pinned SDK is enabled explicitly with `allow_preview=True`. Definitions use structured output and no external tools. Each agent endpoint is configured to route 100% to the created version. Do not concurrently change endpoint routing during a demonstration.

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

## Official references

- [Current Foundry agent identity model](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-identity)
- [Migration from legacy Agent Applications](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/migrate-agent-applications)
- [Agent 365 integration](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-365-integration)
- [Configure Agent 365 data collection](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/configure-agent-365-data-collection)
