# Security checklist: pre-launch, ongoing, and incident response

Status: 2026-11-22. Use it as the gate for staging → public demo (S6) and as input to the IEC 81001-5-1 / FDA 524B security file.
Tick each item with the date, who checked it, and the evidence (link to run, report or screenshot).

## 1. Pre-launch gate (all must be ticked before real users)

### 1.1 Infrastructure (verify on the applied staging project, not only in code)

- [ ] `terraform plan` shows no drift. `fmt`, `validate`, `tflint` and `checkov` are green in CI (`deploy-infra.yml`).
- [ ] No service account keys exist: `gcloud iam service-accounts keys list --iam-account=... --managed-by=user` is empty for every SA.
- [ ] Buckets `uploads`, `results`, `access-logs`:
  - public access prevention `enforced`, uniform access, CMEK key shown;
  - soft delete `0`, versioning off;
  - lifecycle 1 d / 7 d / 180 d.
- [ ] An anonymous `curl` of an object URL returns 401/403 on every non-viewer bucket.
- [ ] Cloud SQL: no public IP, `ssl_mode=ENCRYPTED_ONLY`, backups + PITR on, backup location asia-south1, deletion protection on (prod).
- [ ] The worker has no internet. Run a test job that tries `curl https://example.com`: it must fail. `curl https://storage.googleapis.com` must succeed.
- [ ] The API `run.app` URL is not reachable from the internet (ingress = internal + LB). The API answers only via the LB domain.
- [ ] Cloud Armor:
  - OWASP rules out of preview (`waf_preview = false`) after 1–2 weeks of tuning on staging logs;
  - rate limits tested (429 returned);
  - Adaptive Protection on.
- [ ] TLS: SSL Labs grade A or better on both domains; HTTP redirects to HTTPS; HSTS present.
- [ ] CSP moved from report-only to enforcing after the viewer runs cleanly (edge module `viewer_response_headers`).
- [ ] Logs:
  - security bucket in asia-south1 with ≥ 180 days retention;
  - `_Default` sink disabled or default location set (MEITY_AND_RESIDENCY.md §3);
  - Data Access audit logs on;
  - prod security bucket **locked**.
- [ ] Alerts arrive: trigger each security alert once (e.g. add and remove a harmless IAM binding on a test SA) and confirm the email.
- [ ] Budget alerts configured (25/50/75/90 % of ₹30,000, credits excluded). Monthly budget for after expiry set.
- [ ] Identity Platform: self sign-up off, MFA mandatory (TOTP), authorized domains = the site domain only, password policy set in the console (min length 12, if available).
- [ ] Org policy suggestions applied if an organization exists (`infra/modules/org_policy`).
- [ ] GitHub:
  - environments `staging` and `production` with required reviewers;
  - `main` protected (reviews, status checks);
  - Actions pinned by commit SHA (Dependabot keeps them current);
  - secret scanning and push protection on.

### 1.2 Application (S4 service and S3 viewer)

OWASP ASVS **5.0 Level 2** items that apply to this service, grouped by ASVS chapter. Verify by test or code review and record the evidence.

| ASVS 5.0 chapter | What to verify here |
|---|---|
| V1 Encoding and Sanitization | No user-controlled string reaches a shell or SQL (parameterised queries only); filenames never used as paths |
| V2 Validation and Business Logic | Job state machine enforced server-side (upload → queued → running → done/failed → deleted); per-account job quota; idempotent start |
| V3 Web Frontend Security | CSP, `X-Content-Type-Options`, frame-ancestors none, COOP/COEP; no tokens in localStorage; Subresource Integrity not needed (same-origin assets) |
| V4 API and Web Service | Only documented methods; JSON content-type enforced; CORS allows the site origin only; `Cache-Control: no-store` on API responses |
| V5 File Handling | Uploads only via signed URL with size range; NIfTI magic/header check; voxel-count and dimension caps **before** allocation; decompressed-size limit (gzip bomb); no DICOM/zip accepted server-side |
| V6 Authentication | Identity Platform with MFA; no local passwords in the app; account lockout/rate limiting via Identity Platform + Armor |
| V7 Session Management | Short ID-token lifetime (1 h, refresh via SDK); sign-out revokes refresh tokens for sensitive roles |
| V8 Authorization | Every object access checks ownership in SQL; clinician-only actions (sign-off) check a role claim set by an admin, not by the user |
| V9 Self-contained Tokens | JWT signature, `iss`, `aud`, `exp`, `iat` validated; `alg` pinned to RS256; JWKS cached with rotation |
| V10 OAuth and OIDC | Redirect URIs restricted to the authorized domain; state/nonce handled by the Identity Platform SDK |
| V11 Cryptography | No custom crypto; Google-managed TLS; CMEK at rest; job IDs from a CSPRNG with ≥ 128 bits |
| V12 Secure Communication | TLS 1.2+ only (SSL policy MODERN); Cloud SQL TLS required; internal calls to Google APIs over TLS |
| V13 Configuration | No secrets in images or env files; `DATABASE_URL` from Secret Manager; debug off; dependency versions pinned (lock files) |
| V14 Data Protection | Logs carry job IDs, never file names, DICOM tags or emails; deletion receipts; retention 24 h / 7 d implemented in code and backstopped in IaC |
| V15 Secure Coding and Architecture | SBOM per image; SOUP register updated (`docs/ct_platform/device/SOUP_register*.md`); third-party models' licences checked |
| V16 Security Logging and Error Handling | Security events logged (sign-in, job create/delete, sign-off, authorization failures) with UTC timestamps; generic error messages to clients |

### 1.3 Penetration test (external, before S6)

- **Tester:** a CERT-In empanelled auditor is advisable for Indian buyers and hospitals.
- **Environment:** staging, with public data only, configured identically to prod. Prod is excluded from intrusive testing.
- **Google Cloud rules:** no need to notify Google, but the tester must follow the Cloud Acceptable Use Policy and the Terms of Service. No DoS, no testing of other tenants or of Google infrastructure.
- **In scope:**
  1. The viewer domain and the API domain through the load balancer (black box, plus grey box with two test accounts per role).
  2. Authentication and authorisation: IDOR across accounts, role escalation to clinician, JWT tampering, MFA bypass, invite-only bypass.
  3. Upload path: signed-URL misuse (other object names, size bypass, reuse after expiry), malicious NIfTI (gzip bomb, huge dimensions, malformed headers), DICOM smuggling.
  4. Queue and worker:
     - a crafted job message, if the tester can reach publish, in a white-box variant with a test SA;
     - worker egress test (exfiltration attempts from a job).
  5. Storage: bucket enumeration, public access, signed URL leakage via Referer, deletion guarantees (object gone after "delete now" and after 24 h).
  6. Cloud configuration review (read-only SA): IAM, firewall, logging, KMS, org policies, against the CIS Google Cloud Foundations Benchmark.
  7. Browser de-identification: identifiers left in output NIfTI for a set of public DICOM test files with synthetic PHI in standard, private and burned-in fields.
- **Out of scope:** volumetric DDoS, social engineering, Google-managed infrastructure.
- **Deliverables:** report with CVSS scores. All critical/high findings fixed and retested before launch; medium findings fixed or risk-accepted in the risk file (ISO 14971).

## 2. Ongoing: dependency and image scanning

| What | Tool | When | Gate |
|---|---|---|---|
| Terraform misconfiguration | checkov, tflint | Every PR touching `infra/` | Fail on any failed check (justified skips are inline with a reason) |
| Container images | Trivy (vulnerabilities, secrets, misconfig) | Every PR (scan only) and every build (before push) | Fail on fixable HIGH/CRITICAL |
| Registry continuous scanning | Artifact Analysis (enabled on the repository) | Continuous, new CVEs against stored images | Review weekly; rebuild when a fix lands |
| SBOM | syft (SPDX + CycloneDX), attached to the image (cosign attest) and the GitHub release | Every release | Required for the device file (FDA 524B, IEC 81001-5-1) |
| Image signatures | cosign keyless (GitHub OIDC, Sigstore transparency log) | Every push; verified before promotion to prod | Prod deploys only verified digests |
| Python dependencies | `pip-audit` on `requirements-*.txt` (add to the S4 CI) | Every PR | Fail on known-exploited or HIGH |
| npm (viewer) | `npm audit --omit=dev --audit-level=high` | Every viewer deploy | Fail on HIGH |
| GitHub Actions and Terraform providers | Dependabot | Weekly | Review and merge |
| Cloud posture | Security Command Center (Standard tier, free) findings | Weekly | Triage |

**Patch timelines** (proposal for the security plan):
- critical: 7 days;
- high: 30 days;
- medium: 90 days.

A known-exploited vulnerability (CISA KEV) is treated as critical.

## 3. Incident response (including CERT-In 6-hour reporting)

Roles:
- **Incident lead:** the owner (data controller).
- **Technical responder:** whoever is on call.
- **Point of contact registered with CERT-In:** the owner. CERT-In Directions require each entity to designate a POC and share it with CERT-In.

### 3.1 Detect

Sources:
- the Cloud Monitoring security alerts (IAM change, KMS destroy, sink change, SA key created, bucket made public);
- dead-letter / failure alerts;
- Cloud Armor and Adaptive Protection alerts;
- Security Command Center findings;
- user reports (publish `security@<domain>` and a `/.well-known/security.txt`);
- the pen-tester.

**Record the time the incident was noticed in UTC and IST.** The CERT-In clock starts here.

### 3.2 Triage (within 1 hour)

1. Open an incident record (private GitHub issue in a separate private repo, or a document) with: time noticed, who noticed, what was seen, systems affected.
2. Decide whether it is a CERT-In reportable incident. The Directions of 28 April 2022 list incident types, among others:
   - targeted scanning;
   - compromise of critical systems or information;
   - unauthorised access to IT systems or data;
   - website defacement;
   - malicious code;
   - attacks on servers, databases or cloud;
   - identity theft and phishing;
   - DoS/DDoS;
   - **data breach and data leak**;
   - attacks on applications.

   **If in doubt, report.**
3. Decide whether personal data is affected. If yes, the DPDP breach steps (3.5) apply too.

### 3.3 Report to CERT-In: within 6 hours of noticing

- Send to **incident@cert-in.org.in**, using the incident reporting form from cert-in.org.in. Phone and fax are listed there too.
- Content:
  - entity name and POC;
  - time of occurrence and of noticing;
  - type of incident;
  - affected systems (Google Cloud project, service, region asia-south1);
  - symptoms;
  - indicators (IPs, log excerpts);
  - actions taken.
- Send what is known at 6 hours. Follow-up information can come later.
- Keep a copy of the email and the form in the incident record.
- Do not delete or alter logs. The security log bucket keeps 180 days (CERT-In also requires logs within Indian jurisdiction for 180 days, and that they be provided on request).

### 3.4 Contain, eradicate, recover

| Situation | First actions |
|---|---|
| Suspected credential or SA compromise | Disable the SA (`gcloud iam service-accounts disable`), remove WIF bindings, rotate the `database-url` secret version, review audit logs for that principal |
| Data exposure (bucket public, signed URL leak) | Re-enforce public access prevention, delete exposed objects, list access from the storage access logs and Data Access audit logs |
| Compromised image or dependency | Roll back Cloud Run to the last verified digest (DEPLOY_RUNBOOK.md "Rollback"), block the digest, rebuild from a clean base |
| Abuse / DoS / credit burn | Set API `max_instances` to 0–1, tighten Armor rate limits or turn on the geo block list, pause the Eventarc trigger (delete it via Terraform with `deploy_workloads` or `gcloud eventarc triggers delete`) |
| Worker exploited by a crafted file | Remove the dispatcher trigger, keep the failing input object (do not delete, it is evidence) in a quarantine prefix, patch the parser |

Recovery:
- redeploy from Terraform and signed images;
- verify with the pre-launch checks above;
- restore the database by PITR if data was altered.

### 3.5 Personal-data breach (DPDP)

Under the DPDP Rules 2025, whose core duties apply from **13 May 2027**:
- the Data Fiduciary (the owner) informs each affected Data Principal **without delay**;
- it informs the **Data Protection Board** without delay, and sends a detailed report within **72 hours** (or longer if the Board allows).

Before that date, follow the same practice as good conduct and per contracts. Details and templates: [DATA_PROTECTION.md](DATA_PROTECTION.md) §7. **A lawyer must confirm the timelines and the content.**

### 3.6 Close

- Root cause and CAPA in the QMS (ISO 13485 CAPA; IEC 81001-5-1 vulnerability handling).
- Update this threat model and checklist.
- Notify Google Cloud support if the cause is on Google's side.
- Notify hospital partners per their contracts.

### 3.7 Practise

Run one tabletop exercise before launch (for example: "a bucket became public at 02:00 IST"). Time the 6-hour report.
