# The Daily Edition publisher

This directory adds a private Kubernetes publisher to the existing Astro newspaper.
It does not change the website, edition TypeScript type, existing editions, or Pages workflow.
The CronJob ships **suspended**. Do not unsuspend it until the live acceptance gate below passes.

## Execution and trust boundaries

1. `initialize` clones the latest public `main`, determines America/Vancouver's date,
   detects an already published edition, and prepares the prompt/schema. Repository
   files are data only; no repository scripts, hooks, dependencies or instructions run.
2. `research` runs subscription-authenticated `codex exec` with live web search,
   read-only sandbox, command tools disabled, no user configuration/plugins, an explicit
   output schema, and a 30-minute timeout. It receives no GitHub credential. A writable
   authentication PVC preserves Codex's own token refresh. An advisory lock prevents
   overlapping research processes on that PVC; use one serialized runner per login.
3. `publish` has only GitHub authentication. It clones current `main` again, validates
   the generated envelope, commits only `content/editions/YYYY-MM-DD.json`, pushes
   without force, fetches from GitHub, and compares the entire committed JSON.
   A normal fast-forward push rejects races. Up to three attempts handle concurrent
   unrelated changes; a concurrent published edition always wins. Failed/ambiguous
   pushes are fetched and checked before retry. Drafts may be replaced; published
   editions are never replaced. Jobs crossing Vancouver midnight fail.
4. Pages workflow completion is verified for the exact commit by default. A failed,
   cancelled or missing deployment is a failed Job, even if the edition was committed.
   A rerun checks the existing edition/deployment rather than generating another one.

The envelope contains the unchanged public edition plus private source evidence,
four dated forecasts and explicit omissions. Only the edition enters Git. Validation
requires typed fields, date/status, evidence for every substantive item, source dates,
recent verification timestamps, future event dates/venues/registration URLs, four
matching Celsius forecasts, market coverage or explicit omissions, and public HTTPS
sources that return HTTP 200. It rejects duplicated JSON keys, extra fields, banned
holdings, placeholders, HTML, implausible temperatures and recent repeated story URLs.
All substantive updates belong in sourced sections; `watchers` is retained as an empty
array to prevent an unsourced publication channel. News sources must be <=7 days old;
equity sessions <=5 days; macro releases may be older and are explicitly dated.

A reachable URL and model-supplied evidence cannot prove a claim's truth. These checks
catch structural and obvious freshness failures; factual accuracy still depends on
research. Paywalls, bot blocks and source outages fail closed. The prompt tells Codex
to omit unverified items rather than fill sections. Public product identities must be
configured before promotion opportunities are included.

## Prerequisites

- Existing cluster with CronJob `spec.timeZone` support (Kubernetes 1.27+), a default
  StorageClass, and an enforcing NetworkPolicy CNI for the supplied egress policy.
- Docker/image builder, registry accessible to the cluster, `kubectl`, Python 3.10+.
- Your existing Codex CLI image with Python 3.10+, tzdata and TLS certificates.
  It must support the flags used in `publisher.py`; verify this through the manual run.
  Pin both resulting images by digest. Set imagePullSecrets if your registry requires it.
- Subscription login enabled for your account. No AI API keys are used; API-key auth
  is rejected. Subscription quotas, revocation and expired login can still stop a run.
- Fine-grained GitHub PAT limited to `malykhin/daily_edition`: Contents read/write,
  Actions read, and implicit Metadata read. No Workflows write, administration or
  organization permissions. Branch rules must permit this actor's direct main push.
  A dedicated GitHub App with those permissions and externally renewed installation
  token is an alternative. This script reads the token file at each Git operation.
  A PAT/App push triggers the existing Pages workflow; do not use an Actions
  `GITHUB_TOKEN` for this external publisher. Rotate expiring credentials before expiry.

OpenAI's [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
documents `exec`, JSON events and structured output. Its
[managed authentication guide](https://learn.chatgpt.com/docs/auth/ci-cd-auth)
documents preserving refreshed `auth.json`, and explicitly limits the advanced pattern
to trusted private automation, advising against its use for public/open-source repository
workflows. Keep this orchestrator and its credentials on private trusted infrastructure;
the public newspaper is only fetched as data and receives the single validated JSON.
Do not run this credentialed workflow from public Actions, PR code, or repository scripts.
This implementation does not establish an exception to OpenAI's documented limitation.
[Kubernetes CronJob documentation](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/)
describes time zones, concurrency and missed-run deadlines.

## Build and configure

Run from the repository root. Replace registry/image placeholders with your values:

```bash
export EDITION_REGISTRY=registry.example.net/personal
export EDITION_TAG=publisher-v1
export EDITION_CODEX_BASE='your-existing-codex-image@sha256:YOUR_DIGEST'
docker build -f publisher/Dockerfile -t "$EDITION_REGISTRY/daily-edition-runtime:$EDITION_TAG" .
docker build -f publisher/Dockerfile.research --build-arg CODEX_BASE="$EDITION_CODEX_BASE" -t "$EDITION_REGISTRY/daily-edition-research:$EDITION_TAG" .
docker push "$EDITION_REGISTRY/daily-edition-runtime:$EDITION_TAG"
docker push "$EDITION_REGISTRY/daily-edition-research:$EDITION_TAG"
```

Edit image references in `cronjob.yaml`, `auth-maintenance.yaml`, and `fault-jobs.yaml`
to the resulting immutable digests. Review the namespace, PVC storage class/size, UID
1000 compatibility and resource limits for your cluster. No Kubernetes RBAC permissions
are needed by the containers. Configure Kubernetes Secret encryption at rest and restrict
namespace exec/Secret access. The authentication PVC contains password-equivalent tokens.

```bash
kubectl config current-context
kubectl apply --dry-run=server -f publisher/k8s/cronjob.yaml
kubectl apply -f publisher/k8s/cronjob.yaml
kubectl -n daily-edition create configmap daily-edition-products --from-file=products.md=publisher/products.example.md
```

Replace `products.example.md` with approved public URLs/descriptions for Murmur,
Nekoni and Embedible first if available. Do not add private context. To update later:

```bash
kubectl -n daily-edition create configmap daily-edition-products --from-file=products.md=publisher/products.example.md --dry-run=client -o yaml | kubectl apply -f -
```

Create the GitHub Secret from a protected local file supplied by your secret manager.
The file must contain only the PAT/App installation token. Never paste the token into
commands, manifests, shell history or chat. No Secret values are in this repository.

```bash
kubectl -n daily-edition create secret generic daily-edition-github --from-file=token=/secure/path/github-token
```

Apply `network-policy.yaml` after adapting its DNS selector for your cluster. It permits
public IPv4 HTTPS and CoreDNS only, blocks ingress and access to private/metadata IPs,
and adds protection against source URL DNS rebinding. NodeLocal DNS needs a specific
DNS exception. IPv6-only internet access requires an equivalent IPv6 public-only rule.

```bash
kubectl apply -f publisher/k8s/network-policy.yaml
```

## Initialize subscription authentication once

No Jobs should be active during authentication maintenance. Log in directly on the
private runner so this token stream is not shared with a laptop or another runner:

```bash
kubectl apply -f publisher/k8s/auth-maintenance.yaml
kubectl -n daily-edition wait --for=condition=Ready pod/codex-auth-maintenance --timeout=180s
kubectl -n daily-edition exec -it codex-auth-maintenance -- codex -c 'cli_auth_credentials_store="file"' login --device-auth
kubectl -n daily-edition exec codex-auth-maintenance -- codex login status
kubectl -n daily-edition exec codex-auth-maintenance -- chmod 700 /auth
kubectl -n daily-edition exec codex-auth-maintenance -- chmod 600 /auth/auth.json
kubectl -n daily-edition delete pod codex-auth-maintenance
```

If device login is unavailable, use the official browser login/cache-copy procedure
into this private PVC. Seed once; do not restore an old Secret over refreshed tokens
each day. Reseed after revocation or an unrecoverable refresh error. Do not inspect
`auth.json` with `cat` or copy it into diagnostic logs.

## Acceptance: manual first, schedule last

Local regression suite (no AI/GitHub/cluster credentials, no public publication):

```bash
python3 -m unittest discover -s publisher/tests -v
```

Run an immediate manual Job while the CronJob remains suspended:

```bash
export EDITION_FIRST_JOB="daily-edition-manual-$(date -u +%Y%m%d%H%M%S)"
kubectl -n daily-edition create job "$EDITION_FIRST_JOB" --from=cronjob/daily-edition
kubectl -n daily-edition get pods -l job-name="$EDITION_FIRST_JOB" -w
```

Use the logs below while running. Once complete:

```bash
kubectl -n daily-edition wait --for=condition=Complete "job/$EDITION_FIRST_JOB" --timeout=4200s
kubectl -n daily-edition logs "job/$EDITION_FIRST_JOB" -c research
kubectl -n daily-edition logs "job/$EDITION_FIRST_JOB" -c publish
```

The first full acceptance run must log live `research:success`, `validation:success`,
`publishing:push_success`, `verification:success`, Pages conclusion `success`, and
`overall:success` with result `published` and a commit SHA. Verify the website at
https://malykhin.github.io/daily_edition/ and the commit's Actions run linked in the log.
The existing `.github/workflows/deploy.yml` is preserved and triggers on every main push.

**Today's edition already exists:** the October 20, 2026 reference is published.
An immediate run correctly returns `already_published` without using Codex. This checks
idempotency but does not count as a full generated-edition acceptance run. Do not delete,
unpublish, change the date of, or overwrite it to force a test. Repeat the full manual
run on the next Vancouver date with no published edition; keep the schedule suspended
until that run and the gate pass. There is intentionally no force/date-override flag.

After a successful new publication, run a second distinct Job on the same date:

```bash
export EDITION_SECOND_JOB="daily-edition-repeat-$(date -u +%Y%m%d%H%M%S)"
kubectl -n daily-edition create job "$EDITION_SECOND_JOB" --from=cronjob/daily-edition
kubectl -n daily-edition wait --for=condition=Complete "job/$EDITION_SECOND_JOB" --timeout=4200s
kubectl -n daily-edition logs "job/$EDITION_SECOND_JOB" -c publish
```

Expect `already_published`, the same main SHA and no new commit. Never launch two
manual publisher Jobs simultaneously; `Forbid` only applies to CronJob-managed Jobs.
The auth lock prevents token races, but the second Job will visibly fail instead of wait.

Negative tests deliberately fail Kubernetes Jobs. They use synthetic fixtures and
local temporary bare Git remotes, mount neither production credential, and cannot
modify GitHub or existing editions. Invalid JSON exercises validation failure; the
push test uses an actual Git pre-receive rejection after successful validation.

```bash
kubectl apply -f publisher/k8s/fault-jobs.yaml
kubectl -n daily-edition wait --for=condition=Failed job/daily-edition-invalid-json --timeout=180s
kubectl -n daily-edition wait --for=condition=Failed job/daily-edition-push-error --timeout=180s
kubectl -n daily-edition logs job/daily-edition-invalid-json -c test
kubectl -n daily-edition logs job/daily-edition-push-error -c test
```

Each must show `overall:failure`, exit code 1 and a final `negative_test` record with
`expected_failure:true` and `remote_unchanged:true`. To repeat these named tests, delete
only those two test Jobs first, then reapply their manifest.

The gate checks real Kubernetes conditions and container logs: the first run actually
researched/validated/published and deployed Pages, the second made no duplicate, and
both negative Jobs failed without remote damage. It writes `acceptance-result.json`.
Only `--enable` changes the CronJob; omit that flag to inspect evidence first.

```bash
python3 publisher/acceptance.py --first "$EDITION_FIRST_JOB" --second "$EDITION_SECOND_JOB" --invalid daily-edition-invalid-json --push-error daily-edition-push-error --enable
kubectl -n daily-edition get cronjob daily-edition
```

Once enabled: 07:00 America/Vancouver, DST-aware, concurrency `Forbid`, no automatic
Job retries, 30-minute missed-start window and 70-minute total Job deadline. Reapplying
the original manifest suspends scheduling again; preserve the desired suspend state
in your private deployment configuration after acceptance.

## Diagnose failures

```bash
kubectl -n daily-edition get cronjob,jobs,pods
kubectl -n daily-edition describe job "$EDITION_FIRST_JOB"
kubectl -n daily-edition get pods -l job-name="$EDITION_FIRST_JOB" -o wide
kubectl -n daily-edition logs "job/$EDITION_FIRST_JOB" -c initialize --timestamps
kubectl -n daily-edition logs "job/$EDITION_FIRST_JOB" -c research --timestamps
kubectl -n daily-edition logs "job/$EDITION_FIRST_JOB" -c publish --timestamps
kubectl -n daily-edition get pods -l job-name="$EDITION_FIRST_JOB" -o jsonpath='{range .items[*]}{.status.initContainerStatuses}{"\n"}{.status.containerStatuses}{"\n"}{end}'
kubectl -n daily-edition get events --sort-by=.lastTimestamp
```

Every normal stage emits JSONL with execution ID, UTC timestamp, stage and status.
Separate `/work/logs/{initialize,research,validation,publishing,verification,overall}.jsonl`
files mirror stdout. `/work/state.json` and the container termination message contain
start/end, result, exit code, failed stage and committed SHA. Research logs include
Codex event counts and redacted error diagnostics; raw reasoning is not retained.
Git errors include redacted stderr. An init-container failure prevents publishing and
makes the Job Failed. OOM/deadline/node failures can prevent a final application log;
container termination reasons and Job conditions are authoritative in that case.

The work volume is ephemeral and its files disappear with the Pod. Kubernetes container
logs are the diagnostic interface after exit; retain/collect them before deleting Jobs.
History retains 7 successful and 14 failed scheduled Jobs, not a guaranteed number of
days. Configure your cluster's log retention/collector for longer history. Never collect
the auth PVC or generated prompt as public artifacts. Failed manual Jobs are retained
until you delete them. Monitor Failed Jobs and missed CronJob runs with your cluster's
existing monitoring.

Typical causes: pending PVC/image pull/UID mismatch; auth revoked/quota exhausted;
unsupported Codex flags; blocked source URLs; stale/missing evidence; Git credential
expiry or protected branch rules; a failed Pages workflow. Fix the cause, then create
a new manual Job. If push succeeded but verification failed, the next Job preserves
the edition and rechecks deployment. Inspect the recorded SHA before retrying anything.
To pause scheduling without interrupting a currently running Job:

```bash
kubectl -n daily-edition patch cronjob daily-edition --type=merge -p '{"spec":{"suspend":true}}'
```
