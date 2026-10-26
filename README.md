# OpenAI ADK Middleware Template

This template deploys an independently named Cloud Run gateway and persistence
worker for Google ADK agents that call OpenAI models through ADK's LiteLLM
integration. The gateway authenticates FlutterFlow/Firebase users, reserves
prepaid wallet credit, proxies buffered or SSE chat to the agent's Cloud Run
service, and publishes completed turns. The worker owns durable transcripts,
usage ledger records, wallet settlement, and reservation reconciliation.

The central Billing API remains the only Stripe/payment integration. This
middleware shares its configured customer-wallet, wallet-transaction, and
billing-period collections; turn reservations, transcripts, and agent ledgers
remain stack-specific. Collection names are Terraform-configured, not inferred
from this repository name. Review the deploy output before applying.

## Billing preflight invariant — keep this order

Treat this ordering as a hard billing and side-effect boundary:

- Authenticate and validate existing thread/session ownership using server-owned state only.
- Reserve wallet credit. If reservation fails (including HTTP 402), return without creating a
  provider-side session or sending agent/model input.
- Only after reservation succeeds may the gateway create a provider-side session or submit
  agent/model input.
- If session creation fails after reservation, release the reservation idempotently.
- Keep buffered and streaming paths aligned; test that zero-credit requests return 402 with no
  provider-session or agent calls.

Purely local ID generation may happen before reservation only when it has no provider-side side effects.

## Important: billing compatibility gate

The template intentionally fails closed on incomplete provider usage: it does
not invent a cache-write count or silently choose a fallback price. OpenAI
reports cached-input and cache-write tokens as separate usage fields. ADK's
typed `usage_metadata` schema does not include the cache-write field, so the
companion `adkOpenAI` example uses an adapter to preserve it in the event's
`custom_metadata.openai_usage.cache_write_tokens`; this middleware merges that
integer into its normal usage record.

This adapter is **not automatic for other ADK agents**. Deploy the updated
upstream agent image that includes the adapter (or implement an equivalent),
then verify a real streamed event carries
`customMetadata.openai_usage.cache_write_tokens`, including an explicit zero.
If a cache-eligible turn arrives without that count, the middleware records a
known-cost lower bound and marks `estimated_cost_usd` unavailable. The worker
releases the reservation and retries usage lookup from that exact backend
invocation's persisted session events; it does not hold wallet credit while waiting. A
recovered cost is debited from then-available credit, and any unpaid remainder
is recorded as a shortfall and retried after later top-ups. After the configured
retention period, unresolved usage is marked for manual review rather than
inventing a charge. Missing usage is not an admission lock: the gateway may
reject a request only when the wallet lacks enough currently available credit
for the configured reservation. Keep this rule in place if billing reconciliation
is changed. The default worker schedule is once per hour (at minute 0), with provider lookups
starting after 5 minutes, exponentially backing off to 6 hours, and retrying for up
to 7 days. These values are configurable in Terraform. The agent Cloud Run
service must grant the worker identity permission to read its ADK session endpoint;
for Agent Runtime agents, grant the worker identity permission to read session
events as well. Do not enable live prepaid enforcement until the deployed agent
and middleware pass that end-to-end check. LiteLLM
response-result caching remains disabled for billable requests; OpenAI's
provider-side prompt caching remains enabled and is priced separately.

The `$0.40` default reservation is a one-model-call authorization estimate,
not a per-turn spend cap. A tool-using ADK turn can make multiple model calls.
Enforce a maximum call count, total prompt/output budget, and agent timeout in
the upstream agent; size per-agent reservations to that policy. The Cloud Run
middleware cannot impose an output-token limit on a separately deployed ADK
agent. External tool/API costs are not included in model-token charges.

## Pricing scope

The checked-in catalogs contain one supported model, `gpt-6-luna`, using
OpenAI's Standard/global rates. The app reports output tokens inclusive of
reasoning tokens, so reasoning is retained for audit but never added twice.
Input tokens are split into uncached, cached-hit, and cache-write portions when
the provider reports them. Long-context rates apply when a single model call's
input exceeds the configured 272,000-token threshold.

| Usage component | Up to 272,000 input tokens | Above 272,000 input tokens |
| --- | ---: | ---: |
| Uncached input, per 1M | $0.10 | $0.20 |
| Cached input, per 1M | $0.01 | $0.02 |
| Cache-write input, per 1M | $0.125 | $0.25 |
| Output including reasoning, per 1M | $0.50 | $0.75 |

Pricing is versioned in `config/billing.prod.yaml` and
`config/billing.test.yaml`. Unknown models and incomplete pricing catalogs fail
closed at startup. Verify provider rates and usage semantics before changing
either catalog. These values cover Standard/global token usage only; other
processing tiers, regions, hosted tools, and non-token fees are not supported.
See the [OpenAI GPT-6 Luna model and pricing page](https://developers.openai.com/api/docs/models/gpt-6-luna)
and [prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching).

## Before deploying

1. Use a dedicated GCP project for production when possible. Select a unique
   `MIDDLEWARE_STACK_NAME`; the scripts derive stack-specific Cloud Run,
   service-account, Pub/Sub, image, and Terraform state names from it.
   Terraform remote state requires an existing private GCS bucket, defaulting
   to `<PROJECT_ID>-tfstate`. Set `TF_STATE_BUCKET` to your approved shared
   state bucket if it has another name; the script checks access before init.
2. Confirm the production agent URL and agent ID in
   `config/agents.prod.yaml`. The development registry is selected automatically
   for development/staging deployments.
3. Configure Firebase Auth, the permitted app origins, required GCP APIs/IAM,
   Cloud Monitoring alert channels, and access to the existing shared Firestore
   wallet collections. The deploy script requires alert channels in production.
4. Create a test wallet for each test user using the supplied provisioning
   helper; never let FlutterFlow write wallet or ledger documents.
5. Keep OpenAI API keys in Secret Manager on the **agent** service. This
   middleware needs no OpenAI secret; it calls the agent service using the
   configured Cloud Run identity/auth policy.
6. Resolve the billing compatibility gate above and test the exact upstream
   usage event and settlement behavior before accepting live funds.

## Cloud Shell deployment

From the root of the cloned repository:

```bash
export PROJECT_ID="YOUR_GCP_PROJECT_ID"
export REGION="us-central1"
export MIDDLEWARE_STACK_NAME="openai-agents"
export MIDDLEWARE_IMAGE_PREFIX="openai-agents"

bash ./scripts/cloudshell_build_middleware.sh

export DEPLOYMENT_ENV="development"
export ALLOWED_ORIGINS="https://ceoappdev.flutterflow.app"
bash ./scripts/cloudshell_deploy_middleware.sh
```

The build script uses temporary Cloud Build config files and creates separate
gateway and worker images. The deploy script pins the resolved image digests,
uses `stacks/<stack>/middleware` for Terraform state, displays a plan, and
requires explicit confirmation. Do not reuse another middleware's state prefix.

For production, use the production project, set
`DEPLOYMENT_ENV=production`, set `ALLOWED_ORIGINS` to the real production web
origins, provide `ALERT_NOTIFICATION_CHANNELS_JSON` with at least one
Cloud Monitoring channel, and review `config/agents.prod.yaml` and the full
Terraform plan. Only after testing the upstream ADK usage contract, export
`OPENAI_USAGE_ACCOUNTING_REVIEWED=true`; production deployment otherwise stops.
The script also rejects the development placeholder project and known
placeholder agent URLs. Do not bypass those guards to make a plan pass.

## API

- `GET /health` — liveness check
- `GET /ready` — readiness check
- `POST /v1/agents/{agent_id}/chat` — buffered response
- `POST /v1/agents/{agent_id}/chat/stream` — Server-Sent Events response

Requests require Firebase authentication according to each registry entry.
The client supplies a message and optional session/thread metadata; user identity,
wallet reservation identity, and completed-turn event IDs are server-owned.

## Validation

Run the template tests with Python 3.11 or newer:

```bash
python -m pytest tests/ -q
```

Check deployment scripts with Bash syntax validation:

```bash
bash -n scripts/cloudshell_build_middleware.sh
bash -n scripts/cloudshell_deploy_middleware.sh
```

Terraform plans and actual GCP permissions/resources must still be checked in
the target project; passing local tests does not certify a live deployment.
