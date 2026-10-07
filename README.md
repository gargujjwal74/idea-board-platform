# Idea Board: an AI-first, cloud-agnostic DevOps platform

A small full-stack app (React + FastAPI + PostgreSQL) used as the payload for a platform that

- provisions the same infrastructure on **AWS (EKS + RDS)** and **GCP (GKE + Cloud SQL)** from one Terraform module contract,
- deploys the same container images to either cloud with a single Helm chart,
- uses **Gemini** inside the CI/CD pipeline to size environments, review Terraform plans, run ChatOps previews, and judge deployment health with automatic rollback, with guardrails so the model can advise but never act unsafely.

## Live deployments

| Cloud | URL | Stack |
|---|---|---|
| **AWS** | http://a3041d6aec20840438cf872de02d88aa-459230198.us-east-1.elb.amazonaws.com | VPC, NAT, EKS, RDS PostgreSQL 16 |
| **GCP** | http://34.58.27.241 | VPC, Cloud NAT, GKE (private nodes), Cloud SQL PostgreSQL 16 |

Both were deployed by the pipeline described in section 3 (`Deploy` workflow, `clouds=both`).

---

## 1. Architecture

```
                       ┌────────────────────── GitHub Actions ──────────────────────┐
 push / PR / comment ─►│ ci.yml       test, lint, scan, build images, push to GHCR    │
                       │ deploy.yml   AI sizing → plan → AI review → apply → helm →   │
                       │              AI health check → automatic rollback            │
                       │ preview.yml  "/deploy-preview" ChatOps (AI-planned)          │
                       │ destroy.yml  guarded teardown                                │
                       └──────────────┬───────────────────────────┬──────────────────┘
                       keyless OIDC   │                           │   keyless OIDC
                        ┌─────────────▼───────────┐       ┌───────▼───────────────────┐
                        │ AWS                     │       │ GCP                        │
                        │  VPC + NAT              │       │  VPC + Cloud NAT           │
                        │  EKS (managed nodes)    │       │  GKE (private nodes)       │
                        │   └ Helm: idea-board    │       │   └ Helm: idea-board       │
                        │  RDS PostgreSQL         │       │  Cloud SQL PostgreSQL      │
                        │  (private, TLS)         │       │  (private IP, TLS only)    │
                        └─────────────────────────┘       └────────────────────────────┘
```

**Request path:** browser → cloud load balancer (a plain Kubernetes `Service type=LoadBalancer`, so no ingress controller or DNS is required) → nginx, which serves the React build and reverse-proxies `/api/*` → FastAPI → PostgreSQL over TLS on a private address.

**Layers.** Each layer only knows about the one below it:

| Layer | Path | Cloud-specific? |
|---|---|---|
| Application and Dockerfiles | `app/` | No |
| Kubernetes packaging | `charts/idea-board/` | Only two small `values/<cloud>.yaml` files |
| Pipeline logic and AI tools | `.github/workflows/`, `ai/` | Only the login and kubeconfig composite actions |
| Infrastructure | `infra/modules/*/{aws,gcp}` | Yes, behind one contract |

**Repository layout**

```
app/backend           FastAPI + psycopg (non-root image, /healthz and /readyz)
app/frontend          React (Vite) + nginx-unprivileged (proxies /api)
docker-compose.yml    local stack
charts/idea-board     Helm chart; values/{local,aws,gcp,profile-*}.yaml
infra/modules         network | k8s_cluster | postgres, each with aws/ and gcp/
infra/envs/{aws,gcp}  thin roots wiring the modules (identical variables and outputs)
infra/bootstrap       one-time per cloud: state bucket + GitHub OIDC trust
ai/                   Gemini client, four tools, JSON schemas, prompts, tests
.github/workflows     ci, deploy, preview, preview-cleanup, destroy
.github/actions       cloud-login, kubeconfig (the only cloud-specific pipeline code)
scripts/tf-init.sh    Terraform backend init (S3 vs GCS)
```

---

## 2. Run locally with Docker Compose

Prerequisite: Docker with Compose v2 (Docker Desktop, Colima, or Engine).

1. Clone the repo and enter it:
   ```bash
   git clone https://github.com/gargujjwal74/idea-board-platform.git
   cd idea-board-platform
   ```
2. (Optional) Change database credentials: `cp .env.example .env` and edit it. The defaults work as is.
3. Start everything (frontend, backend, PostgreSQL):
   ```bash
   docker compose up --build        # or: make up
   ```
4. Use it:
   - App: http://localhost:3000. Add an idea; it is stored in PostgreSQL and survives a refresh.
   - API: http://localhost:8000/api/ideas (`GET` lists, `POST {"content": "..."}` creates)
   - Health: http://localhost:8000/healthz (liveness) and `/readyz` (checks the database)
5. Stop and remove data: `docker compose down -v` (or `make down`).

The database has a healthcheck and the backend waits for it. The backend creates the `ideas(id, content, created_at)` table at startup.

**Other local checks (no cloud account needed)**

```bash
make test-ai        # 46 unit tests for the AI tools and their guardrails (no API key needed)
make tf-validate    # terraform fmt + validate for all four roots
make helm-lint      # lint the chart for local, aws and gcp
make kind-test      # install the chart into a throwaway kind cluster and call the API
make demo-ai        # run the AI tools offline against canned model responses
```

---

## 3. Deploy to a cloud with the pipeline

The one-time bootstrap (state bucket and GitHub trust) needs admin rights, so you run it by hand once per cloud. After that, everything is pipeline-driven and keyless: there are no stored cloud credentials.

### 3.1 Prerequisites
- `terraform >= 1.10`, `gh`, and the CLI of each cloud you deploy to (`aws`, `gcloud`)
- A GitHub repo with this code (fork or copy it) and a [Gemini API key](https://aistudio.google.com/)
- **AWS:** an account allowed to use EC2, EKS, RDS and IAM OIDC providers (accounts on AWS's restricted "Free plan" cannot; use a paid-plan account). vCPU quota of at least 4 for the default sizing.
- **GCP:** a project with billing enabled

### 3.2 Bootstrap AWS (once)
```bash
cd infra/bootstrap/aws
# If you use `aws login`, export short-lived credentials for Terraform:
eval "$(aws configure export-credentials --format env)"
terraform init
terraform apply -var github_repo=OWNER/REPO -var state_bucket_name=<globally-unique-bucket>
# outputs: role_arn, state_bucket
```
Newer GitHub repositories issue *immutable subject* OIDC claims (`repo:OWNER@ID/REPO@ID:...`). Check yours and, if the value contains `@`, also pass it so the IAM trust matches:
```bash
gh api repos/OWNER/REPO/actions/oidc/customization/sub --jq .sub_claim_prefix
terraform apply ... -var 'github_extra_sub_patterns=["<sub_claim_prefix>:*"]'
```

### 3.3 Bootstrap GCP (once)
```bash
cd infra/bootstrap/gcp
export GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"   # or: gcloud auth application-default login
terraform init
terraform apply -var project_id=<PROJECT> -var github_repo=OWNER/REPO -var state_bucket_name=<globally-unique-bucket>
# outputs: workload_identity_provider, service_account, state_bucket
```

### 3.4 Configure GitHub
```bash
gh secret set GEMINI_API_KEY -R OWNER/REPO

# AWS (from the bootstrap outputs)
gh variable set AWS_ROLE_ARN        -R OWNER/REPO --body "<role_arn>"
gh variable set AWS_REGION          -R OWNER/REPO --body us-east-1
gh variable set AWS_STATE_BUCKET    -R OWNER/REPO --body "<state_bucket>"

# GCP (from the bootstrap outputs)
gh variable set GCP_PROJECT_ID      -R OWNER/REPO --body "<PROJECT>"
gh variable set GCP_REGION          -R OWNER/REPO --body us-central1
gh variable set GCP_STATE_BUCKET    -R OWNER/REPO --body "<state_bucket>"
gh variable set GCP_WIF_PROVIDER    -R OWNER/REPO --body "<workload_identity_provider>"
gh variable set GCP_SERVICE_ACCOUNT -R OWNER/REPO --body "<service_account>"
```
Optional variables: `STAGING_NAME` (environment name, default `idea-board-staging`), `GEMINI_MODEL` (comma-separated fallback chain), `DEFAULT_CLOUD`, `AUTO_DEPLOY`.
Optional: add required reviewers to the `aws` and `gcp` GitHub Environments for an approval gate before apply.

### 3.5 Build and deploy
1. Push to `main`. `ci.yml` runs all checks and publishes `ghcr.io/<owner>/idea-board-{backend,frontend}:sha-<commit>`. The packages of a public repository are public; confirm anonymous pulls work, or set the packages to public.
2. Deploy, either:
   - **Manually:** Actions → **Deploy** → Run workflow (`clouds=both`, `goal="cost-sensitive staging"`), or
     `gh workflow run Deploy -f clouds=both -f goal="cost-sensitive staging"`
   - **Automatically:** set the repository variable `AUTO_DEPLOY=true`; every successful CI run on `main` then deploys that commit's images to both clouds.
3. What a deploy does, per cloud in parallel:
   1. Gemini turns the goal into validated Terraform and Helm sizing
   2. `terraform plan`, then an AI risk review (a "block" recommendation stops the run before apply)
   3. `terraform apply` of that exact plan (EKS and GKE take roughly 10 to 20 minutes the first time)
   4. Helm deploy, wait for the load balancer
   5. AI health check; automatic `helm rollback` if the release is unhealthy
   6. The job summary shows the plan review, the health report and the **public URL**
4. Open the URL, or `curl http://<url>/api/ideas`.

### 3.6 ChatOps previews
A collaborator comments on a pull request:
```
/deploy-preview on gcp for a day
/teardown-preview
```
The preview runs in a `pr-<N>` namespace on the staging cluster with a throwaway Postgres and is removed when the PR closes or its TTL expires (`preview-cleanup.yml`). The workflow must exist on the default branch for comment events to trigger it.

### 3.7 Tear down
Actions → **Destroy** → choose the cloud, the environment name, and type `destroy`. The workflow first deletes Kubernetes Services (cloud load balancers are not in Terraform state and would block VPC deletion), then runs `terraform destroy` with retries.

> **Cost:** a running EKS or GKE cluster plus NAT and a managed database costs real money every hour. Destroy environments you are not using.

### 3.8 Operational notes
- **GCP peering after Cloud SQL deletion:** Google releases the Service Networking peering with a delay, so the first destroy attempts can fail with "Producer services are still using this connection". The workflow retries; if it persists, delete the peering directly: `gcloud compute networks peerings delete servicenetworking-googleapis-com --network <env-name>`, then re-run Destroy.
- **Cloud SQL names** are reserved for about a week after deletion; the module appends a random suffix so redeploys never collide.
- **Gemini quota:** if the model is rate-limited or unavailable, every AI step falls back to a deterministic path (see section 4), so deployments are never blocked by the AI.

---

## 4. AI integration

### What was built
Four tools live in `ai/`. They share one small Gemini client (`ai/llm.py`), and each has a JSON Schema in `ai/schemas/` and a prompt in `ai/prompts/`.

| # | Tool | Trigger | Input → output | Value |
|---|---|---|---|---|
| 1 | `deploy_plan.py` | PR comment `/deploy-preview ...` | free text → `{action, cloud, profile, ttl}` → exact `helm` commands rendered by code | Developers request environments in plain English; reviewers get a live URL per PR that expires on its own |
| 2 | `env_profile.py` | `deploy.yml` input `goal` | "HA production" → Terraform variables + Helm values | Sizing becomes one sentence instead of instance-type archaeology; budget caps and capacity rules are enforced |
| 3 | `plan_review.py` | every `terraform plan` | plan JSON → risk rating + plain-English summary | Reviewers see "this replaces the database" before it happens |
| 4 | `health_judge.py` | after every deploy | pods + events + logs + smoke test → verdict → rollback signal | Catches problems that readiness probes miss, and explains them |

### Design choices

**The model produces data, never actions.** Every output is constrained by a response schema (Gemini structured output) and re-validated locally with `jsonschema`. Nothing the model returns is executed; code maps validated fields to allow-listed operations:
- the namespace and release name are derived from the PR number only, so one PR can never touch another's resources;
- preview commands are rendered by `render_commands()` from validated fields;
- previews always deploy the PR head, ignoring any ref in the comment;
- branch names are regex-checked, and hostile input (`; rm -rf /`, `$(curl ...)`, `../`) is rejected (covered by tests).

**Deterministic first, AI second.**
- `health_judge`: hard gates (pod readiness, CrashLoopBackOff, OOMKilled, image pull errors, restart count, an end-to-end HTTP smoke test) are authoritative, and the model **cannot** override a failed gate. The model judges the grey zone: error bursts in logs, database trouble, latency. Outcomes: *healthy* (exit 0); *unhealthy*, which triggers an automatic `helm rollback` (exit 1; hard failures, or AI confidence of at least 0.8); *uncertain*, which keeps the release and asks a human (exit 2). The smoke test warms up first (a brand-new load balancer is slow on the first requests) and tolerates a single stalled sample out of ten, so a cold start or one dropped connection is not mistaken for a bad release, while a real outage still fails the gate.
- `plan_review`: deterministic checks set a **risk floor** (replace or delete of a database, cluster or network; world-open security rules). The final risk is the maximum of the floor and the model's rating, so the model can raise risk but never lower it.
- `env_profile`: cross-field rules the schema cannot express are enforced after the model answers: HA needs at least 2 nodes and 2 replicas, min replicas cannot exceed max, a node budget cap, and a **minimum node count per size** (Kubernetes system pods fill a single small node, so the app would not schedule).

**Noise is filtered before the model sees it.** A public endpoint is probed by internet scanners within hours (`/wp-login.php`, `/.env`, ...), and a single-page-app server answers 200 to unknown paths, so those requests look "successful". A real deployment once had its logs read as a compromise because of them. Known scanner traffic and HPA metrics warm-up events are now removed deterministically and only counted, the prompt states the same rules, and unit tests cover that real errors are never filtered.

**Graceful degradation.** The Gemini client tries a chain of models (`gemini-3.1-flash-lite`, then `gemini-flash-latest`) with retries and one repair attempt for invalid output. If the AI is still unavailable, each tool has a deterministic fallback (regex parser, static profiles, rule-based summary). A good deployment is never rolled back because the AI API is down, and a broken one is still caught by the gates.

**Prompt-injection hygiene.** Comments, logs and plan names are untrusted. Prompts say so, secrets are redacted from logs before they reach the model, and the architecture means a successful injection can at worst change a summary, never run a command or lower a safety gate.

**Operational choices.** Plain REST (no SDK), so CI needs one pip package; `temperature=0`; an offline mock mode (`AI_MOCK_RESPONSE`) so everything is testable without a key.

### Tangible value across the DevOps lifecycle

| Stage | Without AI | With this platform |
|---|---|---|
| Request an environment | Edit tfvars and values, know instance types | One sentence |
| Review an infrastructure change | Read a 400-line plan | A short, risk-rated summary backed by hard-coded safety floors |
| Verify a deployment | Probes say "Ready" | Logs, events and latency are also judged; automatic rollback |
| Incident handoff | "It's broken" | A report with evidence, suspected cause, and what was rolled back |

---

## 5. Cloud-agnostic approach

**Principle: one contract, many implementations; keep cloud knowledge in the smallest possible places.**

1. **Terraform module contract.** `network`, `k8s_cluster` and `postgres` each exist as `infra/modules/<name>/aws` and `infra/modules/<name>/gcp` with identical variables and outputs.
   - Sizing is abstract: `node_size` and `db_size` are `small|medium|large`; each implementation owns its own map (`small` = `t3.medium` on AWS, `e2-medium` on GCP).
   - Both roots (`infra/envs/aws`, `infra/envs/gcp`) expose the same variable surface and the **same outputs** (`cluster_name`, `cluster_endpoint`, `database_url`, ...), so the pipeline reads them without knowing the cloud.
   - Why not one module with `if cloud == "aws"`? Terraform providers are statically configured: a single module would need both providers and both sets of credentials.
2. **Switch clouds by changing a few variables, not code:** `region`, `node_count`, `node_size`, `db_size`, `high_availability` (plus `project_id` on GCP). See `terraform.tfvars.example` in each env.
3. **The application layer never mentions a cloud.** One Helm chart; `values/aws.yaml` and `values/gcp.yaml` only hold load-balancer settings, and `values/profile-*.yaml` are sizing presets. Images live on GHCR rather than a cloud registry, and the same immutable image (tagged with the commit SHA) is deployed everywhere.
4. **The pipeline is a matrix over clouds.** The same steps run for `[aws, gcp]`. The only cloud-specific code is two small composite actions (`cloud-login`, `kubeconfig`) and `scripts/tf-init.sh` (S3 vs GCS state backend).
5. **Keyless authentication everywhere:** GitHub OIDC is exchanged for an IAM role (AWS) or via Workload Identity Federation (GCP), restricted to this repository.
6. **Equivalent security posture:** private databases with TLS enforced and encrypted storage, private subnets (EKS) or private nodes (GKE), non-root containers with dropped capabilities and seccomp, and Trivy scans of IaC, dependencies, secrets and images.

### Adding a third cloud (for example Azure)
1. Add `infra/modules/{network,k8s_cluster,postgres}/azure` implementing the same variables and outputs.
2. Add `infra/envs/azure` (copy `aws/variables.tf` and `outputs.tf`, change the provider and module sources) and `infra/bootstrap/azure` (state storage and a federated credential).
3. Add an `azure` branch to the `cloud-login` and `kubeconfig` actions and to `scripts/tf-init.sh`, add `charts/idea-board/values/azure.yaml`, and add `azure` to the workflow matrix.

The application, chart templates, AI tools and deployment logic do not change.

---

## 6. Security, quality and testing

- **Secrets and state:** no credentials in git; Terraform state is remote, versioned and encrypted; `.gitignore` excludes state, tfvars and `.env`.
- **Preview workflow:** runs only default-branch code with cloud credentials; PR code is never executed with credentials (PR images are built by CI, which has none). Fork PRs and non-collaborators are rejected.
- **CI gates:** backend tests against a real PostgreSQL, frontend build, 46 AI-guardrail tests, `terraform fmt`/`validate`, `tflint`, `helm lint` (every cloud x profile), Trivy for IaC, dependencies, secrets and images, with `actionlint`-clean workflows.
- **Documented trade-offs** (`.trivyignore`): the Kubernetes API endpoint is public but IAM-authenticated so hosted CI runners can deploy (narrow it with `api_allowed_cidrs`); pipeline roles are broad for a self-contained demo and should be scoped down with permission boundaries for production; a single NAT gateway is used for cost.
