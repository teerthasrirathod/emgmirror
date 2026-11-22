# Deploy runbook: from an empty Google Cloud project to staging

Status: 2026-11-22. Nothing in this repository has been applied to any cloud. Every `apply` below is run by the **owner** (or by CI after the owner approves a GitHub environment).
Placeholders: `STG` = staging project id, `PROD` = prod project id, `STG_NUM` = staging project number, `BILLING` = billing account id, `DOMAIN` = viewer host (e.g. `staging.example.in`), `API_DOMAIN` = API host (e.g. `api.staging.example.in`).

---

## A. What the owner must grant: one list to ask in one go

### A1. Billing and credits

1. **Confirm the credit type.** A ₹30,000 credit ending 13 Jan 2027 looks like the 90-day **Free Trial**. Free Trial accounts **cannot use GPUs**. Activate the full (paid) account in Billing → Overview → "Activate". Google's terms say the remaining credit carries over until it expires; confirm it on the billing page after activation.
2. Note the billing account **currency** (INR expected). The budget's `budget_currency` must match it.
3. The person who runs bootstrap needs, **on the billing account**:
   - `roles/billing.user` (link projects);
   - `roles/billing.costsManager` (create budgets).

   If the owner runs bootstrap personally as billing admin, nothing extra is needed.

### A2. Projects

4. Create **two projects**:
   - `ct-platform-stg-<suffix>` (staging, needed now);
   - `ct-platform-prod-<suffix>` (prod, create later, see GPU_DECISION.md §4).

   If the owner has a Cloud Identity / Workspace organization, create them inside it; otherwise under "No organization".
5. **Link both projects to the billing account.**
6. Send the project **ids and numbers** (Project settings page) to whoever prepares the tfvars.
7. In each project (console → IAM & Admin):
   - accept the **Cloud Data Processing Addendum** (Account settings → Privacy & Security);
   - set **Essential Contacts** (security, technical, billing, legal) to the owner's address.

### A3. IAM

8. **Bootstrap (one-time, per project):** run by the owner with their own login (`roles/owner` on the project). No other human or machine needs owner.
9. **After bootstrap:** bootstrap creates these service accounts. Nothing else gets project-wide roles.

   | Identity | Created by | Roles | Usable from |
   |---|---|---|---|
   | `tf-plan@STG` | bootstrap | read-only viewers (Run, Compute, SQL, Storage bucket metadata, IAM security reviewer, …) + state-bucket object user | GitHub **pull-request** runs of `teerthasrirathod/emgmirror` only (WIF) |
   | `tf-apply@STG` | bootstrap | `artifactregistry.admin`, `cloudkms.admin`, `cloudsql.admin`, `compute.loadBalancerAdmin`, `compute.networkAdmin`, `compute.securityAdmin`, `dns.admin`, `eventarc.admin`, `iam.roleAdmin`, `iam.serviceAccountAdmin`, `iam.serviceAccountUser`, `identityplatform.admin`, `logging.configWriter`, `monitoring.editor`, `pubsub.admin`, `resourcemanager.projectIamAdmin`, `run.admin`, `secretmanager.admin`, `servicenetworking.networksAdmin`, `serviceusage.serviceUsageAdmin`, `storage.admin`, `workflows.admin` + state-bucket object admin | GitHub environment **`staging`** only (WIF, after manual approval) |
   | `ct-stg-deployer@STG` | Terraform | `artifactregistry.writer` on the repo; `run.developer` on the API service and worker job; `serviceAccountUser` on the api/worker/cleanup SAs; `storage.objectAdmin` on the viewer bucket; custom CDN-invalidate role | GitHub environment **`staging`** only (WIF) |
   | `ct-stg-api`, `ct-stg-worker`, `ct-stg-cleanup`, `ct-stg-dispatcher`, `ct-stg-push` | Terraform | runtime, resource-scoped (see THREAT_MODEL.md §2.4) | Cloud Run / Eventarc / Pub/Sub |

   The same applies to prod with environment **`production`**.
10. **Optional (needs an organization):** `roles/orgpolicy.policyAdmin` at organization level, to apply `infra/modules/org_policy`, which restricts locations to asia-south1/asia-south2 and blocks SA keys, public SQL IPs and external VM IPs.

### A4. APIs

Bootstrap enables these:
- `cloudresourcemanager`, `serviceusage`, `iam`, `iamcredentials`, `sts`, `storage`, `cloudkms`, `billingbudgets`, `cloudbilling`, `monitoring`.

The environment Terraform enables these (all `*.googleapis.com`):
- `artifactregistry`, `cloudkms`, `cloudresourcemanager`, `compute`, `containeranalysis`, `containerscanning`, `dns`, `eventarc`, `iam`, `iamcredentials`, `identitytoolkit`, `logging`, `monitoring`, `orgpolicy`, `pubsub`, `run`, `secretmanager`, `servicenetworking`, `serviceusage`, `sqladmin`, `storage`, `sts`, `workflowexecutions`, `workflows`.

The owner can pre-enable them in one command:

```bash
gcloud services enable artifactregistry.googleapis.com cloudkms.googleapis.com cloudresourcemanager.googleapis.com compute.googleapis.com containeranalysis.googleapis.com containerscanning.googleapis.com dns.googleapis.com eventarc.googleapis.com iam.googleapis.com iamcredentials.googleapis.com identitytoolkit.googleapis.com logging.googleapis.com monitoring.googleapis.com orgpolicy.googleapis.com pubsub.googleapis.com run.googleapis.com secretmanager.googleapis.com servicenetworking.googleapis.com serviceusage.googleapis.com sqladmin.googleapis.com storage.googleapis.com sts.googleapis.com workflowexecutions.googleapis.com workflows.googleapis.com billingbudgets.googleapis.com cloudbilling.googleapis.com --project=STG
```

### A5. GPU access and quota

11. **Cloud Run GPU in asia-south1:** request access, which is invitation-only per Google's docs, via the Google Cloud account team or the request form linked from the Cloud Run GPU documentation. Ask for **NVIDIA L4 for Cloud Run jobs, without zonal redundancy, in asia-south1, project STG**.
12. After access, raise the quota "Total Nvidia L4 GPU allocation without zonal redundancy, per project per region" in asia-south1 to **4–6**. 3 are granted automatically on the first deploy.
13. **Fallback, request at the same time:** Compute Engine quotas in asia-south1:
    - `NVIDIA_L4_GPUS` = 2;
    - `PREEMPTIBLE_NVIDIA_L4_GPUS` = 4;
    - global `GPUS_ALL_REGIONS` = 4.

### A6. Domain and GitHub

14. A domain the owner controls, with the ability to add two **A records** (viewer and API) pointing to the load balancer IP.
15. On GitHub (repo Settings), the owner creates:
    - **Environments:** `staging` and `production`, each with **required reviewers = owner**, deployment branch `main` only;
    - **Repository variables** (not secrets; only ids):
      - `GCP_WIF_PROVIDER_STAGING`, `GCP_TF_PLAN_SA_STAGING`, `TF_STATE_BUCKET_STAGING`;
      - the same `_PROD` set later.
    - **Repository secrets:** `TF_VARS_STAGING`, `TF_VARS_PROD`. Each is the content of the env's `terraform.tfvars`; it holds the alert email, so it is a secret.
    - **Environment `staging` variables:**
      - `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_TF_APPLY_SA`, `GCP_DEPLOYER_SA`, `TF_STATE_BUCKET`;
      - `AR_REPO` (= `asia-south1-docker.pkg.dev/STG/ct-stg-images`);
      - `API_SERVICE` (= `ct-stg-api`), `WORKER_JOB` (= `ct-stg-worker`);
      - `VIEWER_BUCKET` (= `STG-ct-stg-viewer`), `URL_MAP` (= `ct-stg-https`).
    - **Environment `staging` secret:** `TF_VARS`.
    - The same for `production`.

---

## B. Bootstrap (owner, once per project, ~10 min)

Requires gcloud and Terraform ≥ 1.9 on the owner's machine.

B1. Log in:

```bash
gcloud auth login
```

```bash
gcloud auth application-default login
```

B2. Fill the variables (placeholders in the example; the file is git-ignored):

```bash
cd infra/bootstrap && cp terraform.tfvars.example terraform.tfvars
```

Set `project_id = "STG"` and `github_environment = "staging"`. In this one run, also set `create_budget = true`, `billing_account`, `budget_project_numbers = ["STG_NUM", "PROD_NUM"]` and `alert_email`. Use only `STG_NUM` until prod exists, then re-apply.

B3. Review and apply:

```bash
terraform init && terraform plan -out=tfplan
```

```bash
terraform apply tfplan
```

B4. Move bootstrap state into the bucket it just created:

```bash
printf 'terraform {\n  backend "gcs" {\n    prefix = "bootstrap"\n  }\n}\n' > backend.tf && terraform init -migrate-state -backend-config="bucket=$(terraform output -raw state_bucket)"
```

B5. Copy the outputs into GitHub (A6):

```bash
terraform output
```

| Output | GitHub variable |
|---|---|
| `wif_provider` | `GCP_WIF_PROVIDER_STAGING` and env `GCP_WIF_PROVIDER` |
| `tf_plan_service_account` | `GCP_TF_PLAN_SA_STAGING` |
| `tf_apply_service_account` | env `GCP_TF_APPLY_SA` |
| `state_bucket` | `TF_STATE_BUCKET_STAGING` and env `TF_STATE_BUCKET` |
| `wif_pool_name` | goes into the env tfvars |

B6. **Logging location (residency):**
- with an organization, set it before creating anything else:

  ```bash
  gcloud logging settings update --organization=ORG_ID --storage-location=asia-south1
  ```

- without one, do step C4 after the first apply.

---

## C. First deployment of staging

C1. Prepare `infra/envs/staging/terraform.tfvars` from the example, with real ids, `DOMAIN`, `API_DOMAIN`, `alert_email` and `wif_pool_name`, and **`deploy_workloads = false`**. Store the same content as GitHub secrets `TF_VARS_STAGING` and env `TF_VARS`. Keep `deploy_workloads = false` there for now.

C2. First apply (data layer, network, IAM, keys, identity, logging; no Cloud Run, no LB yet):
- **Via CI:** merge to `main` → workflow `deploy-infra` → approve `apply-staging`.
- **Or locally**, as the owner:

  ```bash
  cd infra/envs/staging && terraform init -backend-config="bucket=STG-tfstate" && terraform plan -out=tfplan
  ```

  ```bash
  terraform apply tfplan
  ```

C3. **Database credential.** Create the app user and store `DATABASE_URL` in Secret Manager without it touching disk or shell history. The URL format is the one S4's RUNTIME_CONTRACT.md defines; with the private IP from `terraform output`:

```bash
read -rs DBPASS < <(openssl rand -base64 32 | tr -d '/+=' ); gcloud sql users create ctservice --instance=ct-stg-pg --password="$DBPASS" --project=STG
```

```bash
read -r DBIP < <(terraform output -json platform | python3 -c 'import json,sys;print(json.load(sys.stdin)["database_private_ip"])'); printf 'postgresql://ctservice:%s@%s:5432/ctservice?sslmode=require' "$DBPASS" "$DBIP" | gcloud secrets versions add database-url --data-file=- --project=STG; unset DBPASS
```

The IAM database users for the api/worker/cleanup SAs already exist. If S4 adopts the Cloud SQL connector with IAM authentication, no password is needed: drop the `ctservice` user and switch `ssl_mode` to `TRUSTED_CLIENT_CERTIFICATE_REQUIRED`.

C4. Without an organization, disable the global `_Default` log sink so that logs are stored only in the asia-south1 buckets:

```bash
gcloud logging sinks update _Default --disabled --project=STG
```

C5. Push the first images:
- set `deploy_workloads = true` in the tfvars and the GitHub secrets;
- run `deploy-infra` (approve), which creates Cloud Run with placeholder images, the LB, Armor and alerts;
- run `deploy-images` (approve `staging`), which builds, scans, signs and deploys the real `api` and `worker` digests. It needs S4's `docker/api/Dockerfile` and `docker/worker/Dockerfile`; the paths are set in the workflow `env`.

C6. **DNS:** create A records `DOMAIN` and `API_DOMAIN` → the `lb_ip` output. The managed certificate becomes ACTIVE 15–60 min after DNS resolves:

```bash
gcloud compute ssl-certificates describe ct-stg-cert --global --project=STG --format='value(managed.status,managed.domainStatus)'
```

C7. **Viewer:** run `deploy-viewer` (approve `staging`).

C8. **Smoke tests** (public data only):
1. `https://DOMAIN` loads the viewer.
2. `https://API_DOMAIN/healthz` returns 200, and the `run.app` URL returns 403/404 from the internet.
3. Create an invited test user in Identity Platform (console → Users → Add) and enrol TOTP.
4. Run one job end to end with a public TotalSegmentator CT (CC BY 4.0). Watch `gcloud run jobs executions list --job=ct-stg-worker --region=asia-south1 --project=STG`.
5. Check: result downloadable, "delete now" removes the objects (`gcloud storage ls gs://STG-ct-stg-results/JOB/` is empty), the dead-letter subscription is empty.
6. Egress test from the worker (SECURITY_CHECKLIST.md §1.1).
7. Trigger one security alert and confirm the email arrives.

C9. After 1–2 weeks of clean Armor logs, set `waf_preview = false` for staging (prod already has it), and move CSP from report-only to enforcing.

---

## D. Rollback

| What | How | Time |
|---|---|---|
| API bad revision | `gcloud run services update-traffic ct-stg-api --to-revisions=PREVIOUS_REVISION=100 --region=asia-south1 --project=STG` (list revisions with `gcloud run revisions list --service=ct-stg-api --region=asia-south1`) | < 1 min |
| Worker bad image | `gcloud run jobs update ct-stg-worker --image=asia-south1-docker.pkg.dev/STG/ct-stg-images/worker@sha256:PREVIOUS --region=asia-south1 --project=STG` (digests are in the GitHub release / `device-file-worker` artifact) | < 1 min |
| Viewer | Re-run the previous successful `deploy-viewer` run (rebuilds the same commit), or restore object versions from the versioned viewer bucket, then invalidate the CDN | ~5 min |
| Terraform change | `git revert` the commit, merge, approve `apply-staging`. Never hand-edit resources: drift is caught by the next plan | ~10 min |
| Database corruption | Point-in-time clone, then repoint `DATABASE_URL`: `gcloud sql instances clone ct-stg-pg ct-stg-pg-restore --point-in-time=2027-01-01T10:00:00Z --project=STG`, then add a new `database-url` secret version and redeploy the API | 15–30 min |
| Stop all processing now (incident, cost) | `gcloud eventarc triggers delete ct-stg-worker-dispatch --location=asia-south1 --project=STG` (new jobs queue up in Pub/Sub for 24 h) and `gcloud run services update ct-stg-api --max-instances=1 --region=asia-south1 --project=STG`. Re-create the trigger by re-applying Terraform | < 2 min |

---

## E. Cost checks (weekly until the credits expire, then monthly)

1. Billing → Reports, filtered to the projects, **group by SKU**. Credits are shown separately; the budget excludes them.
2. Budget email thresholds: 25 % ≈ ₹7,500, 50 % ≈ ₹15,000, 75 % ≈ ₹22,500, 90 % ≈ ₹27,000. A GitHub issue opens 31/14/7/1 days before 13 Jan 2027.
3. GPU usage this month:

   ```bash
   gcloud run jobs executions list --job=ct-stg-worker --region=asia-south1 --project=STG --format='table(name,status.completionTime,status.succeededCount,status.failedCount)'
   ```

   Multiply by the measured minutes and ₹0.035 per second (GPU_DECISION.md §3).
4. Things that cost money while idle:
   - the two forwarding rules (~$18/month);
   - Cloud Armor (~$18/month);
   - Cloud SQL (~$12–15/month staging);
   - Artifact Registry storage.

   To park staging for weeks, set `deploy_workloads = false` and apply. This removes Cloud Run, the LB, Armor and alerts, and keeps data, keys and IAM. Then stop Cloud SQL:

   ```bash
   gcloud sql instances patch ct-stg-pg --activation-policy=NEVER --project=STG
   ```

5. **After 13 Jan 2027:** every charge is real. Reduce the monthly budget (`monthly_amount`), and decide whether prod waits for funding.

---

## F. Teardown

Staging only. **Prod teardown needs a data-retention decision first:** device records, audit logs and the locked log bucket cannot be deleted early.

1. Delete user data first and record it:

   ```bash
   gcloud storage rm --recursive "gs://STG-ct-stg-uploads/**" "gs://STG-ct-stg-results/**" --project=STG
   ```

2. KMS keys and key rings **cannot be deleted** in Google Cloud. Remove them from state so `destroy` can proceed:

   ```bash
   cd infra/envs/staging && terraform state list | grep google_kms_crypto_key | xargs -I{} terraform state rm '{}'
   ```

   Later, optionally crypto-shred by destroying all key versions (irreversible):
   `gcloud kms keys versions destroy VERSION --key=KEY --keyring=ct-stg-keys --location=asia-south1 --project=STG`.
3. Destroy. Staging has `deletion_protection = false` and `force_destroy = true`:

   ```bash
   terraform plan -destroy -out=destroy.tfplan
   ```

   ```bash
   terraform apply destroy.tfplan
   ```

4. Bootstrap last, from `infra/bootstrap` with the GCS backend. Remove its KMS key from state the same way. The state bucket has `force_destroy = false`; empty it manually after the final destroy.
5. Shut the project down. It is recoverable for 30 days, then deleted:

   ```bash
   gcloud projects delete STG
   ```

6. Remove the GitHub environments, variables and secrets, and delete the DNS records.

---

## G. Break-glass

If CI or WIF is broken and a fix is urgent:
- the owner applies locally with their own login (B1 + C2 commands);
- record the reason in the incident log;
- make the same change in git right after.

The security alert on IAM changes fires during break-glass. That is expected; acknowledge it in the incident record.
