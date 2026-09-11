# Pulp Operator UI Design

**Date:** 2026-09-11  
**Status:** Approved in conversation; pending written-spec review

## Goal

Replace script-only Pulp operations and existing `/ui/` deployment with a small internal operator console. Preserve Pulp as source of truth. Avoid separate application database, shell execution, and frontend build pipeline.

## Scope

Primary user: SRE operator.

Included workflows:

- Inspect Pulp health, versions, routing, domains, repositories, distributions, tasks, users, groups, and role assignments.
- Create tenant domains, users, groups, and domain-scoped role assignments.
- Create and inspect RPM, DEB, Python, Ansible, and Container repositories and distributions.
- Start supported content import or synchronization operations represented by current repository scripts.
- Run tenant-isolation validation workflows.
- Preview and clean up exact validation resources.
- Inspect asynchronous Pulp task state and safe error details.

Excluded from MVP:

- End-user artifact browsing or upload experience.
- OIDC/SSO.
- Per-operator Pulp credentials.
- Application database or durable application activity store.
- Bulk deletion.
- Arbitrary shell or existing-script execution.
- Automatic rollback of partially completed multi-step workflows.
- Replacing Pulp's own task scheduler.

## Current Environment

Live `tbs-dev` Pulp was inspected read-only on 2026-09-11:

- Pulp workloads and internal API are healthy.
- `pulpcore` is 3.116.0; `pulp_container` is 2.29.0.
- Previous domain-scoped container tag-pull defect is resolved.
- Domains are `default`, `dummy-alpha`, and `dummy-beta`.
- Current domain role-assignment count is zero.
- `pulp.dev.tbs.cloudeka.xyz` resolves to kgateway load balancer `103.125.100.58`, where no Pulp route exists; requests return envoy 404.
- ingress-nginx load balancer `103.125.100.50` still serves Pulp correctly.
- Existing image `wushie/pulp-ui:0.1.24` is available at `/ui/`, but uses flat `API_BASE_PATH=/pulp/api/v3/` and is not domain-aware.

Routing repair is separate infrastructure work. UI must surface routing failure, not attempt to change DNS or Gateway resources.

## Architecture

Use one FastAPI service containing:

- Server-rendered Jinja templates.
- Small vanilla JavaScript modules for forms, confirmation, and task polling.
- Basic Auth middleware for UI access.
- CSRF middleware for every state-changing request.
- One server-side Pulp REST client.
- Structured application logging.
- `/healthz` and `/readyz` probes.

Run service as a new pod in namespace `pulp`. Replace existing `pulp-ui` Deployment and Service while retaining public path `/ui/`. Kubernetes manifests remain in repository `linsa`; application source and image lifecycle remain in repository `pulp-ui`.

Browser calls only same-origin `/ui/*` pages and `/ui/api/*` endpoints. FastAPI calls internal `pulp-api-svc:24817`. Browser never receives Pulp credentials.

No application database. Pulp resources and Pulp Tasks are authoritative state. Activity view reads current-process structured activity records only; records disappear on pod restart and according to Kubernetes log retention.

## Authentication and Secrets

Two separate credentials:

1. UI Basic Auth credential protects `/ui/*` and `/ui/api/*`.
2. Existing Pulp admin credential authenticates server-side Pulp API calls.

Both come from Kubernetes Secrets through environment variables or mounted files. No plaintext credential belongs in source, image, ConfigMap, rendered HTML, browser storage, URL, exception, or log.

Pulp admin grants full control. This is accepted for MVP but must remain server-side. Future least-privilege service account or per-operator Pulp identity is outside MVP.

Store UI password as a slow password hash, not plaintext. Use constant-time credential comparison. Return the same unauthorized response for unknown user and incorrect password.

## Navigation and Pages

Use hybrid navigation: resource overview plus guided workflows.

### Overview

Show:

- Pulp API reachability and component versions.
- Public-host routing result versus internal API result.
- Counts for domains, repositories, distributions, active tasks, and failed tasks.
- Actionable warnings such as missing role assignments, failed tasks, or public route failure.
- Recent workflow runs available in current process.

Overview checks are read-only. Routing diagnostics use fixed configured targets; they do not accept arbitrary operator URLs.

### Tenants

Show domains, users, groups, and scoped roles.

Guided tenant setup stages:

1. Validate proposed domain, username, group, and role set.
2. Fetch existing matching resources.
3. Preview creates and existing-resource reuse.
4. Submit sequential Pulp API operations.
5. Stop on first failure.
6. Show completed resources and safe recovery guidance.

No automatic rollback. Retrying re-fetches state and performs only missing idempotent steps.

### Content

Support domain filtering plus plugin-specific repository/distribution forms for:

- RPM
- DEB
- Python
- Ansible
- Container

Each form maps explicit fields to documented Pulp REST endpoints. Shared fields may use shared rendering helpers; plugin endpoint paths and payload schemas remain explicit. Unsupported script behavior must not become arbitrary command input.

Import or synchronization operations return a Pulp task href when asynchronous. UI redirects to task detail and polls that href.

### Validation

Translate current tenant-isolation simulation into explicit REST operations and assertions. Display each assertion as pending, PASS, or FAIL with safe evidence.

Validation uses unique test resource names tied to one generated run identifier. Cleanup operates only on resource hrefs recorded by that run in current process. If the process loses run state, operator must locate and delete resources through resource pages; UI must not infer broad cleanup targets.

### Tasks

List and filter Pulp Tasks by domain, state, creation time, and reserved resource when supported by API.

Task detail shows:

- Task href and correlation ID.
- State and progress reports.
- Created resources.
- Safe task error summary.

A retry starts a new application workflow after revalidating current state. UI never mutates internal Pulp task state to simulate retry.

### Activity

Show in-process workflow activity with:

- Timestamp in RFC 3339 UTC.
- UI operator username.
- Action name.
- Target type and safe identifier.
- Correlation ID.
- Result.

Never log authorization headers, cookies, passwords, Secret values, request bodies containing credentials, or raw upstream exception bodies.

## API and Data Flow

State-changing flow:

1. Browser authenticates using Basic Auth.
2. Server renders form containing session-bound CSRF token.
3. Browser submits to `/ui/api/*` with CSRF token.
4. Server validates authentication, CSRF, field schema, allowlists, and current upstream state.
5. Server builds a fixed Pulp endpoint and typed JSON payload.
6. `PulpClient` sends request with server-side admin credential, timeout, and response-size limit.
7. Synchronous result is rendered directly. Asynchronous result stores task href in current-process workflow state and redirects to task detail.
8. Browser polls same-origin task endpoint. Server fetches current Pulp task state.

Domain-aware endpoint builder must encode path segments and produce `/pulp/{domain}/api/v3/...`. Flat `/pulp/api/v3/...` may be used only for endpoints confirmed to remain global, such as current role-assignment behavior, or status endpoint compatibility. Endpoint scope must be explicit in code; no free-form endpoint input.

## Destructive Operations

Deletion requires all steps:

1. Server fetches current target by exact href.
2. UI displays type, name, domain, href, and dependency warning.
3. Operator selects confirmation checkbox.
4. Server revalidates CSRF and refetches target.
5. Server deletes exact href.
6. UI records result with correlation ID.

Checkbox is approved confirmation mechanism. Disabled JavaScript must not bypass server-side confirmation validation.

No bulk delete, wildcard target, query-derived mass delete, or cascading cleanup hidden from preview.

## Input and Network Safety

Validate all browser input at trust boundary:

- Domain and resource names use explicit length and character rules compatible with Pulp.
- Plugin and action values use enumerated allowlists.
- Pulp hrefs must be same-origin internal Pulp API paths produced or fetched by server.
- External sync/import URLs permit HTTPS only.
- External hostname must match configurable allowlist.
- Resolve hostnames and reject loopback, link-local, private, multicast, unspecified, and metadata ranges unless explicitly configured as trusted internal sources.
- Revalidate resolved destination at connection time where client support allows.
- Set connect/read/write/pool timeouts.
- Limit upstream response bytes before parsing.
- Do not follow redirects to an unvalidated destination.

Configuration contains allowlisted source hosts, not arbitrary URL prefixes supplied by browser.

## Error Handling

- Pulp 4xx: map known validation fields to forms; expose only safe upstream messages.
- Pulp 5xx, malformed response, timeout, or network failure: show generic error plus correlation ID.
- Pulp task failure: show safe task error summary and link to task href.
- Partial workflow failure: stop immediately; list completed resources and failed step; provide manual recovery guidance.
- Authentication and CSRF failures: generic response without credential or token detail.
- Logging failure must not cause mutation retry or hide operation result.

Every mutation uses one generated correlation ID across application logs, activity record, and response.

## Deployment

Application image replaces `wushie/pulp-ui:0.1.24` after image build and explicit rollout approval.

`linsa` manifests must define:

- Deployment in namespace `pulp`.
- ClusterIP Service on application port.
- Existing `/ui`, `/static/pulp_ui`, and `/pulp-ui-config.json` routing removed or updated to match new application assets and routes.
- Basic Auth hash Secret reference.
- Pulp admin Secret reference.
- Non-sensitive ConfigMap values: internal Pulp base URL, public diagnostic URL, source-host allowlist, request limits.
- Read-only root filesystem where practical, non-root user, dropped Linux capabilities, seccomp RuntimeDefault, resource requests/limits, liveness/readiness probes.

Do not create Kubernetes RBAC or mount service-account tokens unless a confirmed UI feature directly calls Kubernetes API. MVP routing health should use HTTP probes, not Kubernetes API access.

Migration sequence:

1. Build and test new image.
2. Render and review manifest diff.
3. Deploy new pod under temporary internal validation path or port-forward.
4. Run read-only smoke tests.
5. Run one isolated mutation test with disposable domain resources.
6. Switch existing `/ui/` Service/Ingress target.
7. Remove obsolete UI configuration and workload only after replacement passes.

Cluster-changing execution requires separate explicit approval.

## Testing

### Unit

Cover:

- Basic Auth success/failure and constant response behavior.
- Password-hash verification.
- CSRF generation, validation, and rejection.
- Domain/resource validation and endpoint encoding.
- Plugin/action allowlists.
- URL and resolved-address SSRF rejection.
- Redirect rejection.
- Credential and sensitive-field log redaction.
- Upstream timeout, size limit, malformed JSON, and safe error mapping.
- Delete confirmation enforced server-side.

### Contract

Mock representative Pulp responses for status, domains, users, groups, roles, each supported content plugin, distributions, and tasks. Verify request method, endpoint scope, payload, task href handling, pagination, and error mapping.

### Integration

Read-only live smoke checks through port-forward:

- Login.
- Overview.
- Domain/resource lists.
- Task list/detail.
- Routing warning.

Mutation E2E uses a unique disposable domain and explicit resource inventory. Test stops before cleanup if inventory is incomplete. Cleanup deletes exact recorded hrefs and verifies each returns 404 afterward.

### Deployment Verification

- `/healthz` succeeds without Pulp dependency.
- `/readyz` fails when required configuration or internal Pulp connectivity is unavailable.
- `/ui/` requires Basic Auth.
- No credential appears in HTML, browser network response, or application logs.
- Existing `/ui/` endpoint serves replacement application after cutover.
- Pulp API, content, and registry routes remain unchanged.

## Acceptance Criteria

MVP accepted when:

1. Operator can access `/ui/` only with valid Basic Auth.
2. Overview accurately shows live internal Pulp status and current public routing failure.
3. Operator can complete tenant setup and supported content workflows without shell access.
4. Every asynchronous operation links to live Pulp task progress.
5. Isolation validation reports individual assertions and cleans only exact recorded resources.
6. Every deletion requires current-resource preview plus checked confirmation.
7. Pulp and UI credentials never reach browser output or logs.
8. Unit, contract, read-only integration, and one disposable mutation E2E pass.
9. Existing UI workload is removed only after replacement validation.
10. Pulp API, content, and registry behavior remains unchanged.

## Deliberate Simplifications

- `ponytail:` In-memory workflow/activity state limits history to pod lifetime; add PostgreSQL only when durable audit or multi-replica coordination becomes required.
- `ponytail:` Basic Auth limits identity and audit quality; replace with OIDC when shared operator access or centralized revocation becomes required.
- `ponytail:` Pulp admin credential maximizes blast radius; replace with a scoped service account or per-user delegation when Pulp permissions can cover every workflow.
- `ponytail:` One FastAPI service limits independent scaling; split frontend/backend only when measured load or release ownership requires it.
