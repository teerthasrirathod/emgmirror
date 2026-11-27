# infra: Terraform for the hosted CT-to-3D platform (milestone S5, hosting readiness)

Nothing here has been applied. The owner applies it, following
[docs/ct_platform/hosting/DEPLOY_RUNBOOK.md](../docs/ct_platform/hosting/DEPLOY_RUNBOOK.md).

```
infra/
  bootstrap/        once per project, by the owner: state bucket, GitHub WIF pool,
                    tf-plan / tf-apply service accounts, Rs 30,000 credit budget
  envs/staging/     staging project (asia-south1)
  envs/prod/        prod project (asia-south1), stricter values
  modules/
    platform/       composes everything below for one environment
    services kms network iam artifact_registry storage database secrets pubsub
    api worker edge identity logging monitoring org_policy budget github_oidc
```

Region: `asia-south1` (Mumbai) for every regional resource. The `region` variable only accepts
asia-south1 or asia-south2. Runtime interface: S4's `docs/ct_platform/hosting/RUNTIME_CONTRACT.md`
(env vars `UPLOAD_BUCKET`, `RESULTS_BUCKET`, `JOBS_TOPIC`, `DATABASE_URL`, `OIDC_ISSUER`,
`OIDC_AUDIENCE`, plus `GOOGLE_CLOUD_PROJECT` and `CT_ENV`).

## Validate locally (no credentials needed)

```bash
terraform fmt -check -recursive infra
```

```bash
for d in infra/bootstrap infra/envs/staging infra/envs/prod; do terraform -chdir=$d init -backend=false && terraform -chdir=$d validate; done
```

```bash
cd infra && tflint --init && tflint --recursive --config="$PWD/.tflint.hcl"
```

```bash
checkov -d infra --framework terraform --skip-path .terraform --compact --quiet
```

A plan against a placeholder project also works without credentials (a fresh project's plan
makes no API calls). `.github/workflows/deploy-infra.yml` does it on every PR: it adds a local
backend override, uses placeholder tfvars and a fake `GOOGLE_OAUTH_ACCESS_TOKEN`, and runs
`terraform plan -refresh=false`.

## Design notes

- **Secrets:** Terraform creates secret containers only. Values are added with gcloud (runbook C3), so
  no secret is in git or in state.
- **First apply:** run it with `deploy_workloads = false`. Cloud Run needs the `database-url` secret
  version and real images first.
- **Images:** CI deploys signed digests, and Terraform ignores `image` after creation.
- **KMS keys:** they have `prevent_destroy`, because Google Cloud cannot delete keys. Teardown removes them
  from state (runbook F2).
- **checkov skips:** each one is inline, with a reason next to the resource. They are deliberate
  (no versioning and no soft delete on personal-data buckets so erasure is final; a public viewer
  bucket; TLS-only rather than client-certificate SQL until S4 uses the connector; tf-apply's
  service-account roles behind the approval gate).
