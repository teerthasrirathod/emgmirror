# Hosting (S5: hosting readiness)

Google Cloud, asia-south1 (Mumbai). The owner is the data controller. Nothing has been applied to any cloud. Infrastructure as code is in [`infra/`](../../../infra/), and CI/CD in `.github/workflows/deploy-*.yml`.

| Document | What it answers |
|---|---|
| [GPU_DECISION.md](GPU_DECISION.md) | Which GPU setup, why, cost per CT and against the ₹30,000 credits, quota to request |
| [DEPLOY_RUNBOOK.md](DEPLOY_RUNBOOK.md) | **§A: the one list of grants to ask the owner for**, then bootstrap → staging, rollback, cost checks, teardown |
| [THREAT_MODEL.md](THREAT_MODEL.md) | STRIDE over upload, de-identification, worker, storage, viewer, auth |
| [SECURITY_CHECKLIST.md](SECURITY_CHECKLIST.md) | Pre-launch gate, pen-test scope, OWASP ASVS 5.0 L2 items, scanning, incident response with the CERT-In 6-hour step |
| [DATA_PROTECTION.md](DATA_PROTECTION.md) | DPDP data map, retention, erasure, consent records, processors, breach notification, questions for the lawyer |
| [MEITY_AND_RESIDENCY.md](MEITY_AND_RESIDENCY.md) | MeitY empanelment status (with re-check date) and where each service keeps data |
| RUNTIME_CONTRACT.md | The api/worker interface. Owned by the S4 service milestone |

## Architecture in one picture

```
            DNS: DOMAIN, API_DOMAIN ──▶ global external HTTPS LB (managed cert, TLS 1.2+)
                                         ├─ DOMAIN     ─▶ Cloud CDN ─▶ GCS viewer (public, static)
                                         └─ API_DOMAIN ─▶ Cloud Armor (OWASP CRS, per-IP limits)
                                                          ─▶ Cloud Run "api" (ingress: LB only)
 asia-south1 ──────────────────────────────────────────────────────────────────────────────
   Cloud Run api ──▶ Cloud SQL Postgres (private IP, CMEK, backups + PITR)
        │        ──▶ GCS uploads (24 h) / results (7 d): CMEK, no public access, no soft delete
        └──▶ Pub/Sub jobs (India-only storage) ──Eventarc──▶ Workflows dispatch
                                       └─ dead-letter topic ◀── bad or failed jobs
                                                       ──▶ Cloud Run job, 1 × L4 GPU
                                                           Direct VPC egress, no internet
   Secret Manager (DATABASE_URL) · KMS (7 keys) · Artifact Registry (signed images)
   Logging: security bucket 180 d (prod 400 d), CMEK · Monitoring: uptime + alerts
 global ── Identity Platform (OIDC, TOTP MFA, invite-only) · budget ₹30,000 (credits excluded)
```
