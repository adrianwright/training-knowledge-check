# Evidence and honest success criteria

| Claim | Required evidence |
|---|---|
| Fresh infrastructure exists | Successful Bicep deployment and ARM outputs |
| Two real prompt agents exist | Saved create/details receipts with `kind: prompt`, distinct native identities and version routing |
| Agents ran | Actual Responses IDs, completed statuses and service response payloads |
| Reviewer caught ambiguity | Actual review finding against the deliberately seeded second question |
| Repair worked | New author invocation plus independent rereview with no outstanding findings |
| Human approved | Separately created approved artifact with a nonblank reviewer attestation |
| A365 prerequisites satisfied | Account readback: logging enabled and status Enabled |
| Activity appears in A365 | Actual Agent 365 portal readback for both identities and the run window |
| Defender detected this run | A non-test Defender alert with the correct resource, time, type and unique canary |

`results\<batch>\manifest.json` tracks scenarios and results. Individual run directories contain request/response payloads, response IDs, timestamps, identity/version receipts and validated artifacts. These may contain sensitive administrative metadata even though the guide is fictional; they are gitignored.

The clean scenario can still legitimately receive review findings: language-model generation and review are not deterministic. `ready_for_human` is not automatic approval. Exact citation checking establishes substring provenance, not semantic entailment; the independent reviewer and human remain responsible for content quality.

For A365 verification, locate both native identities from `.local\foundry.json` in the Agent 365 administration experience. Refresh the activity view after its ingestion delay and compare the batch window with real invocations. Save a private screenshot/export and record what was observed. Do not change `a365_readback_verified` to true merely because invocation receipts exist.

For Defender verification, `defender-readback.json` lists verified alert IDs and pending runs. Full matching alerts are saved privately as `defender-alert.json`. Masked evidence, aggregation, unsupported detections or an expired wait window can prevent exact correlation; the script then fails rather than declaring success. A portal-only observation should be described explicitly as manual evidence.

Unit tests and CI do not contact Azure and do not prove tenant licensing, service access, cloud deployment, native telemetry ingestion or security detection.
