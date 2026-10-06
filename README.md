# Idea Board: an AI-first, cloud-agnostic DevOps platform

A small full-stack app (React + FastAPI + PostgreSQL) used as the payload for a platform that:

- provisions identical infrastructure on **AWS (EKS + RDS)** and **GCP (GKE + Cloud SQL)** from one Terraform module contract,
- deploys the same container images with one Helm chart to either cloud,
- uses **Gemini** inside the CI/CD pipeline for ChatOps previews, environment sizing, Terraform plan review and post-deploy health judgement with automatic rollback, with guardrails so the model can advise but never act unsafely.

| | AWS | GCP |
|---|---|---|
| Live URL | _pending (account plan upgrade in progress)_ | http://104.197.197.128 |

---

## 1. Architecture

```
                        ┌──────────────── GitHub Actions ────────────────┐
 PR / push / comment ──►│ ci.yml        test · lint · scan · build → GHCR │
                        │ deploy.yml    AI sizing → plan → AI review →    │
                        │               apply → helm → AI health → rollback│
                        │ preview.yml   "/deploy-preview" ChatOps (AI)    │
                        └───────────────┬───────────────────┬─────────────┘
                         OIDC (no keys) │                   │ OIDC (no keys)
                  ┌─────────────────────▼───┐        ┌──────▼──────────────────┐
                  │ AWS                     │        │ GCP                      │
                  │  VPC + NAT              │        │  VPC + Cloud NAT         │
                  │  EKS (managed nodes)    │        │  GKE (private nodes)     │
                  │   └ Helm: idea-board    │        │   └ Helm: idea-board     │
                  │      LB → nginx(frontend)│       │      LB → nginx(frontend)│
                  │           └ /api → FastAPI│      │           └ /api → FastAPI│
                  │  RDS PostgreSQL (private)│       │  Cloud SQL PG (private IP)│
                  └─────────────────────────┘        └──────────────────────────┘
```

**Request path:** browser → cloud load balancer (a plain Kubernetes `Service type=LoadBalancer`, so no ingress controller or DNS is needed) → nginx serving the React build → `/api/*` is reverse-proxied to the FastAPI service → PostgreSQL over TLS on a private IP.

**Layers (each only knows about the one below it):**

| Layer | Path | Cloud-specific? |
|---|---|---|
| Application + Dockerfiles | `app/` | No |
| Kubernetes packaging | `charts/idea-board/` | Only 2 tiny `values/<cloud>.yaml` files |
| Pipeline logic + AI tools | `.github/workflows/`, `ai/` | Only the login + kubeconfig composite actions |
| Infrastructure | `infra/modules/*/{aws,gcp}` | Yes, behind one contract |

### Repository layout

```
app/backend           FastAPI + psycopg (non-root image, /healthz /readyz)
app/frontend          React (Vite) + nginx-unprivileged (proxies /api)
docker-compose.yml    local stack
charts/idea-board     Helm chart, values/{local,aws,gcp,profile-*}.yaml
infra/modules         network | k8s_cluster | postgres  -> each with aws/ and gcp/
infra/envs/{aws,gcp}  thin roots wiring the modules (identical variables/outputs)
infra/bootstrap       one-time: state bucket + GitHub OIDC trust, per cloud
ai/                   LLM client, 4 tools, JSON schemas, prompts, tests
.github/workflows     ci, deploy, preview, preview-cleanup, destroy
.github/actions       cloud-login, kubeconfig (the only cloud-specific pipeline code)
scripts/tf-init.sh    backend init (S3 vs GCS)
```

---

## 2. Run locally with Docker Compose

Prerequisite: Docker (Desktop, Colima, or Engine) with Compose v2.

```bash
git clone <this repo> && cd idea-board-platform
docker compose up --build          # or: make up
```

- App: http://localhost:3000 (add an idea; it persists in Postgres)
- API directly: http://localhost:8000/api/ideas · health: `/healthz`, `/readyz`
- Stop and wipe data: `docker compose down -v`

Optional: copy `.env.example` to `.env` to change the DB credentials.

Other local checks (no cloud account needed):

```bash
make test-ai        # 35 tests for the AI tools and their guardrails (no API key needed)
make tf-validate    # fmt + validate all four Terraform roots
make helm-lint      # lint the chart for local/aws/gcp
make kind-test      # install the chart into a throwaway kind cluster and call the API
make demo-ai        # run the AI tools offline with canned model responses
```

---

## 3. Deploy to a cloud with the pipeline

You do the one-time bootstrap by hand (creating trust needs admin rights). After that, everything is pipeline-driven and keyless.

### 3.1 Prerequisites
`terraform >= 1.10`, `aws` CLI, `gcloud`, `gh`; an AWS **sandbox** account and a GCP project with billing; a [Gemini API key](https://aistudio.google.com/).

### 3.2 Push to GitHub
Create the repo, push this code, and note `OWNER/REPO`.

### 3.3 Bootstrap AWS (once)
```bash
cd infra/bootstrap/aws
terraform init
terraform apply -var github_repo=OWNER/REPO -var state_bucket_name=<globally-unique-name>
# outputs: role_arn, state_bucket
```

### 3.4 Bootstrap GCP (once)
```bash
gcloud auth application-default login
cd infra/bootstrap/gcp
terraform init
terraform apply -var project_id=<PROJECT> -var github_repo=OWNER/REPO -var state_bucket_name=<globally-unique-name>
# outputs: workload_identity_provider, service_account, state_bucket
```

### 3.5 Configure GitHub
Settings → Secrets and variables → Actions:

| Kind | Name | Value |
|---|---|---|
| Secret | `GEMINI_API_KEY` | your key |
| Variable | `AWS_ROLE_ARN`, `AWS_REGION` (`us-east-1`), `AWS_STATE_BUCKET` | from bootstrap |
| Variable | `GCP_PROJECT_ID`, `GCP_REGION` (`us-central1`), `GCP_STATE_BUCKET`, `GCP_WIF_PROVIDER`, `GCP_SERVICE_ACCOUNT` | from bootstrap |
| Variable (optional) | `STAGING_NAME` (default `idea-board-staging`), `GEMINI_MODEL`, `DEFAULT_CLOUD`, `AUTO_DEPLOY=true` | |

Also create two **Environments** named `aws` and `gcp` (optionally add required reviewers for an approval gate before apply).

### 3.6 Build images
Push to `main` (or open a PR). `ci.yml` runs all checks and publishes `ghcr.io/<owner>/idea-board-{backend,frontend}:sha-<7>`.
**Then make both packages public** (GitHub profile → Packages → each package → Settings → Change visibility), or the clusters cannot pull them. If you skip this, the AI health check will detect `ImagePullBackOff` and report it, which is a good demo of the safety net.

### 3.7 Deploy
Actions → **Deploy** → Run workflow: `clouds=both`, `goal="cost-sensitive staging"`. The run:

1. AI turns the goal into validated sizing,
2. `terraform plan` per cloud (in parallel), AI-reviewed and posted to the job summary,
3. `terraform apply` (EKS/GKE take roughly 10-20 minutes the first time),
4. Helm deploy, wait for the load balancer,
5. AI health check; rolls back automatically if unhealthy,
6. the job summary shows the public URL for each cloud.

### 3.8 Previews
On any PR, a collaborator comments:

```
/deploy-preview on gcp for a day
/teardown-preview
```

Previews run in a `pr-<N>` namespace on the staging cluster, with a throwaway in-cluster Postgres, and are removed on PR close or TTL expiry.

### 3.9 Tear down (stop the bill)
Actions → **Destroy** → choose cloud, type `destroy`.

> **Cost warning:** a running EKS/GKE cluster plus NAT and a managed database costs real money every hour. Deploy, capture the URLs, and destroy when you are done.

---

## 4. AI integration

### What was built

All four tools live in `ai/`, share one tiny Gemini client (`llm.py`), and each has a JSON Schema in `ai/schemas/` and a prompt in `ai/prompts/`.

| # | Tool | Trigger | Input → Output | Value |
|---|---|---|---|---|
| 1 | `deploy_plan.py` | PR comment `/deploy-preview …` | free text → `{action, cloud, profile, ttl}` → exact `helm` commands rendered by code | Devs ask for environments in plain English; reviewers get a live URL per PR, auto-expired |
| 2 | `env_profile.py` | `deploy.yml` input `goal` | "HA production" → Terraform vars + Helm values | Sizing decisions become a sentence instead of YAML archaeology; budget caps are enforced |
| 3 | `plan_review.py` | every `terraform plan` | plan JSON → risk + plain-English summary | Reviewers see "this replaces the database" before it happens |
| 4 | `health_judge.py` | after every deploy | pods + events + logs + smoke test → verdict → rollback signal | Catches problems that readiness probes miss, and explains them |

### Design choices

**The model produces data, never actions.** Every output is constrained by a response schema (Gemini structured output) and re-validated with `jsonschema`. Nothing the model returns is executed. Code maps validated fields to allow-listed operations:
- the namespace and release name come from the PR number only (a PR can never touch another PR's resources),
- preview commands are rendered by `render_commands()` from validated fields,
- previews always deploy the PR head, ignoring any ref in the comment,
- branch names are regex-checked and hostile inputs (`; rm -rf /`, `$(curl …)`, `../`) are rejected (tested).

**Deterministic first, AI second.**
- `health_judge`: hard gates (pod readiness, CrashLoopBackOff, OOMKilled, image pull errors, restart count, end-to-end HTTP smoke test) are authoritative. The model **cannot** override a failed gate (tested). It only judges the grey zone: error bursts in logs, DB trouble, latency. Outcomes: *healthy* (exit 0), *unhealthy* → automatic `helm rollback` (exit 1, only for hard failures or AI confidence ≥ 0.8), *uncertain* → keep the release and ask a human (exit 2).
- `plan_review`: deterministic checks set a **risk floor** (replace/delete of DB/cluster/network, world-open security rules). Final risk = max(floor, model). The model can raise risk, never lower it.
- `env_profile`: cross-field rules the schema cannot express (HA requires ≥ 2 nodes and replicas, min ≤ max, node budget cap `AI_MAX_NODES`) are enforced after the model answers.

**Graceful degradation.** If Gemini is down or returns invalid output twice, every tool has a deterministic fallback (regex parser, static profiles, rule-based summary). A good deployment is never rolled back just because the AI API is unavailable, and a broken one is still caught by the gates.

**Prompt-injection hygiene.** Comments, logs and plan names are untrusted. Prompts say so, secrets are redacted from logs before they reach the model, and, more importantly, the architecture means a successful injection can at worst change a summary, not run a command or lower a safety gate.

**Operational choices.** Plain REST (no SDK) so CI needs one pip package; `temperature=0`; one automatic repair retry on invalid output; offline mock mode (`AI_MOCK_RESPONSE`) so everything is testable without a key; the model name is configurable (`GEMINI_MODEL`, default chain `gemini-flash-latest,gemini-3.1-flash-lite`: the first model, then a fallback when it is overloaded or retired; comma-separated)).

### Tangible value across the lifecycle

| Stage | Without AI | With this platform |
|---|---|---|
| Request an environment | Edit tfvars/values, know instance types | One sentence |
| Review infra change | Read a 400-line plan | 3-line risk-rated summary with hard-coded safety floor |
| Deploy verification | Probes say "Ready" | Logs, events and latency are also judged; automatic rollback |
| Incident handoff | "It's broken" | Report with evidence, suspected cause, and what was rolled back |

Try it offline: `make demo-ai`.

---

## 5. Cloud-agnostic approach

**Principle: one contract, many implementations; keep cloud knowledge in the smallest possible places.**

1. **Module contract.** `network`, `k8s_cluster` and `postgres` each exist as `modules/<name>/aws` and `modules/<name>/gcp` with identical variables and outputs.
   - Sizing is abstract: `node_size` and `db_size` are `small|medium|large`; each implementation owns its own map (`small` = `t3.medium` / `e2-medium`, and so on).
   - Both roots (`infra/envs/aws|gcp`) expose the same variable surface and the **same outputs** (`cluster_name`, `cluster_endpoint`, `database_url`, …), so the pipeline reads them cloud-blind.
   - Why not one module with `if cloud == aws`? Terraform providers are statically configured: one module would need both providers and both sets of credentials.
2. **Change a few variables, not code:** `region`, `node_count`, `node_size`, `db_size`, `high_availability`, plus `project_id` on GCP (see `terraform.tfvars.example` in each env).
3. **The app layer never mentions a cloud.** One Helm chart; `values/aws.yaml` and `values/gcp.yaml` only hold load-balancer annotations. Images live on GHCR, not ECR or GCR.
4. **Pipeline:** a matrix over `[aws, gcp]` runs the same steps. The only cloud-specific code is two composite actions (`cloud-login`, `kubeconfig`) and `scripts/tf-init.sh` (S3 vs GCS backend).
5. **Keyless auth everywhere:** GitHub OIDC → IAM role (AWS) / Workload Identity Federation (GCP), restricted to this repository.
6. **Security equivalence:** private DB (no public IP), TLS enforced, encrypted storage, private nodes (GKE) / private subnets (EKS), non-root containers with dropped capabilities and seccomp, Trivy scans (IaC, dependencies, secrets, images).

### Adding a third cloud (e.g. Azure)

1. Add `modules/{network,k8s_cluster,postgres}/azure` implementing the same variables and outputs.
2. Add `infra/envs/azure` (copy `aws/variables.tf` and `outputs.tf`, change the provider and modules).
3. Add `infra/bootstrap/azure` (state storage + federated credential).
4. Add an `azure` branch to `cloud-login`, `kubeconfig` and `tf-init.sh`, add `charts/idea-board/values/azure.yaml`, and add `azure` to the matrix.

No change to the app, chart templates, AI tools, or deployment logic.

---

## 6. Security and quality notes

- **State:** remote, versioned, encrypted buckets; no state or tfvars in git (`.gitignore`).
- **Preview workflow:** runs only default-branch code with cloud credentials; PR code is never executed with credentials (PR images are built by CI, which has none). Fork PRs and non-collaborators are rejected.
- **Known trade-offs (deliberate, documented in `.trivyignore`):**
  - The Kubernetes API endpoint is public (IAM-authenticated) so GitHub-hosted runners can deploy. Narrow it with `api_allowed_cidrs` if you use static egress IPs.
  - The pipeline roles are broad (admin) for a demo; scope them down and add permission boundaries for production.
  - One NAT gateway (cost) instead of one per AZ.
- **Quality gates in CI:** pytest (API against real Postgres), `terraform fmt/validate`, tflint, `helm lint`, Trivy (IaC / deps / secrets / images), actionlint-clean workflows.

## 7. What was verified

- **GCP, live:** the pipeline provisioned VPC, Cloud NAT, private Cloud SQL (PostgreSQL 16, TLS enforced) and a private-node GKE cluster, then deployed the app. The AI health check passed; an idea posted through the public load balancer is stored in Cloud SQL and read back.
- Docker Compose stack: end-to-end create/list through the nginx proxy; 5 backend tests pass; both images run as non-root.
- Helm chart: installed on a real kind cluster.
- AI tools, against the real Gemini API: sizing, ChatOps planning (prompt-injection attempt had no effect), plan review, and health judgement (the model caught slow queries and pool exhaustion that every deterministic gate missed).
- AI health judge live path: verified on kind with a healthy release and with the database scaled to zero (unhealthy, exit 1).
- CI: all jobs green (tests, terraform fmt/validate, tflint, helm lint, Trivy IaC/deps/secrets/images).
- **AWS:** the same code validates and plans cleanly, but a real apply needs an account that is allowed to use EC2/EKS/RDS (see lessons below).

### Lessons from the first real deployments (and what changed)

| Problem found in production-like conditions | Fix |
|---|---|
| Pinned Gemini model (`gemini-2.5-flash`) was retired for new users | Default is a fallback chain `gemini-flash-latest,gemini-3.1-flash-lite`; overloaded (503) or retired (404) models fall through to the next |
| Pinned Kubernetes `1.31` was rejected by GKE | No pin by default; GKE uses the REGULAR release channel; pinning is an opt-in variable |
| The AI chose 1 small node for "cost-sensitive"; GKE system pods filled it and the backend could not schedule | New deterministic capacity floor in `env_profile.enforce()` (small nodes need at least 2) plus a test |
| Trivy found a CRITICAL OpenSSL CVE in the nginx base image | Frontend image runs `apk upgrade`; the CI image gate stays on |
| `.gitignore` rule `kubeconfig*` also hid `.github/actions/kubeconfig/` | Rule narrowed to `/kubeconfig*` |
| AWS Free-plan accounts block EC2/EKS/RDS/IAM-OIDC via organization SCPs | Upgrade to the Paid plan (credits are kept) |
