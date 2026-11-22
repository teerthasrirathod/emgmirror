# MeitY empanelment and data residency

Status: 2026-11-22. Not legal advice. **Re-check by 2027-03-01**, before the first non-public data, and before any CDSCO test-licence or investigation filing (MD-12 / MD-22). After that, re-check every 6 months.

## 1. Short answer

- Google states that its MeitY empanelment covers **"the Google Cloud Platform services offered from the Mumbai Region Data Center (asia-south1) and Delhi Region Data Center (asia-south2)"**. The audit was done by STQC, the testing directorate of MeitY, which reported conformance to MeitY ([Google: MeitY India](https://cloud.google.com/security/compliance/meity-india)). Google announced full Cloud Service Provider empanelment in 2021 ([Deccan Herald](https://www.deccanherald.com/business/google-cloud-achieves-full-cloud-service-provider-empanelment-eyes-public-sector-for-growth-954470.html), [Google India blog](https://blog.google/intl/en-in/products/google-companies/solving-for-indian-public-sector-with/)).
- **Not verified:**
  - the per-service list of empanelled offerings (Google's page renders client-side and could not be read here);
  - the current validity or expiry date. A legal analysis says an empanelment is valid for **three years** ([Spice Route Legal / Mondaq](https://www.mondaq.com/india/government-contracts-procurement-ppp/1233354/cloud-governance-101-public-procurement-of-cloud-services)), so a 2021 empanelment must have been renewed for it to be current. A 2026 summary counts 30 empanelled CSPs but names no dates ([secondary](https://currentaffairspulse.com/article/2026-10-29-cloud-ecosystem-india-meghraj-30-empanelled-csps)).
  - **Action:** download MeitY's current list of empanelled cloud service offerings (MeghRaj portal) and Google's empanelment letter. File both in the design history file with the date.
- Why it matters:
  - CDSCO test-licence files reportedly ask whether a SaaS device is hosted on a MeitY-empanelled cloud (secondary, see [research/device/engineering.md](../research/device/engineering.md)).
  - Hospitals and public-sector buyers expect it.
  - It is a procurement requirement for government bodies, not a legal requirement for a private device maker.

## 2. Where each service in this design keeps data

All regional resources are pinned to **asia-south1**: the `region` variable only accepts asia-south1 or asia-south2.

| Service | Location in Terraform | What it holds | Personal data? | Residency status |
|---|---|---|---|---|
| Cloud Storage `uploads`, `results` | asia-south1 (single region), CMEK | De-identified NIfTI, label maps, meshes | Pseudonymised health data | **India** |
| Cloud Storage `access-logs` | asia-south1, CMEK | Request logs (object names = job IDs) | IPs | **India** |
| Cloud Storage `viewer` | asia-south1 | Public JS/HTML, public sample (CC BY) | No | India (served globally by CDN) |
| Cloud SQL PostgreSQL | asia-south1, backups `location = asia-south1`, CMEK | Job records, consent records, audit trail | Yes (account IDs, consent) | **India** |
| Pub/Sub topics | `message_storage_policy` = asia-south1, `enforce_in_transit` | `{job_id, input_uri, profile}` | No (IDs only) | **India** |
| Secret Manager | user-managed replica asia-south1, CMEK | `DATABASE_URL` | Credential | **India** |
| Cloud KMS | asia-south1 key ring | Keys | No | **India** |
| Cloud Run API service and GPU job | asia-south1 | In-memory processing | Transient | **India** (Google: customer data of a Cloud Run resource is stored in its region) |
| Workflows and Eventarc | asia-south1, CMEK on Workflows | Dispatch arguments (job ID) | No | **India** |
| Artifact Registry | asia-south1, CMEK | Container images, model weights | No | **India** |
| Cloud Logging buckets `*-security`, `*-app` | asia-south1, CMEK, 180 / 30 days | Audit, LB, Armor, flow, app logs | IPs, account IDs | **India** |
| Cloud Logging `_Default` / `_Required` buckets | Created by Google at project creation, **global unless a default storage location is set first** | Copies of logs | IPs | **Action needed** (see §3) |
| External HTTPS LB, Cloud Armor, Cloud CDN, managed certificate | Global (edge) | TLS termination at the nearest Google front end; CDN caches only the public viewer (API responses `no-store`, CDN off for the API) | Requests in transit | Global by design; no storage of CT data |
| Identity Platform | Global service, no region option | Accounts: email, password hash, TOTP secret | **Yes** (account data, not health data) | **Outside India possible: lawyer to confirm** (see §4) |
| Cloud Monitoring, uptime checks | Global | Metrics; uptime probes from US/EU/APAC | No | Fine |
| Billing, budgets | Global | Cost data | Owner's billing details | Fine |

## 3. Residency actions for the owner (in the runbook)

1. **Logging default location:**
   - If the project is in an organization, set the org's default log storage location to asia-south1 **before** creating the projects (`gcloud logging settings update --organization=ORG_ID --storage-location=asia-south1`).
   - If there is no organization (personal account), the `_Default` bucket is created in `global`. In that case, after the first apply, disable the `_Default` sink so that only the regional sinks store logs: `gcloud logging sinks update _Default --disabled --project=PROJECT`.
   - The `_Required` bucket (Admin Activity audit logs, 400 days, free) cannot be moved or disabled. Note this in the DPIA.
2. **Organization policy `gcp.resourceLocations`:**
   - limit to `in:asia-south1-locations` and `in:asia-south2-locations` (module `infra/modules/org_policy`, off by default because it needs an organization);
   - a personal-Gmail project has no organization. Creating a free Cloud Identity organization for the owner's domain is recommended before prod.
3. **Assured Workloads:** check whether an India data-boundary package exists for these services (UNVERIFIED). It would add contractual residency guarantees.

## 4. Things a lawyer must confirm

- **Identity Platform outside India.**
  - DPDP Act 2023 s.16 allows transfers except to countries the government restricts by notification. No such notification is known as of this date.
  - Health-sector rules or a hospital contract may still demand localisation.
  - Option: keep only a pseudonymous account ID and email in Identity Platform, and keep names, registration numbers and consent in Cloud SQL (India).
- **CERT-In Directions (28 Apr 2022):**
  - logs of ICT systems are to be kept for 180 days **within Indian jurisdiction**. The security log bucket is in asia-south1, 180 days, CMEK.
  - The global `_Required` bucket holds copies of Admin Activity audit logs. Confirm this is acceptable.
- Whether CDSCO requires MeitY empanelment of the **specific services** used (Cloud Run GPU, Workflows, Identity Platform), or of the cloud provider as a whole.

## 5. Re-check list (copy into the DHF when done)

| Item | Where | Last checked | Next check |
|---|---|---|---|
| Google on MeitY list, validity date | MeitY MeghRaj empanelment list | — (not reachable from this environment) | Before first real data / CDSCO filing; by 2027-03-01 |
| Google empanelment letter and scope | cloud.google.com/security/compliance/meity-india | 2026-11-22 (scope sentence only) | Same |
| Services in scope include Cloud Run (GPU), Cloud SQL, Storage, Pub/Sub, KMS, Secret Manager, Logging, Workflows, Eventarc, Identity Platform | Google letter / list | Not verified | Same |
| Identity Platform data location | Identity Platform docs | Not verified (docs host blocked here) | Before prod |
