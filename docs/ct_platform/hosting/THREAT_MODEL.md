# Threat model (STRIDE): hosted CT-to-3D service

Status: 2026-11-22, draft for the IEC 81001-5-1 security file. Owner of the document: data controller (the owner). Review it at every architecture change and before the S6 pen test.
Scope: the Google Cloud deployment in `infra/` with the S4 service (`src/emgmirror/ctservice/`) and the S3 viewer (`web/ct-viewer/`). Out of scope: the browser-only tier (no upload) and hospital on-premises deployments (P2).

## 1. System and data flow

```
 Browser (viewer, static)                                  Google Cloud, asia-south1 unless noted
 ─────────────────────────                                  ───────────────────────────────────────
 [1] Sign in ─────────────────────────────▶ Identity Platform (global) ── ID token (JWT)
 [2] De-identify DICOM in browser (PS3.15 Annex E),
     drop scouts/secondary captures, convert to NIfTI
 [3] POST /jobs (JWT) ──▶ Global HTTPS LB ─▶ Cloud Armor ─▶ Cloud Run "api" ─▶ Cloud SQL (private IP)
                                 (TLS 1.2+, edge)            │  issues V4 signed PUT URL (15 min)
 [4] PUT input.nii.gz ──────────────────────────────────────▶ GCS uploads/<job_id>/ (CMEK, 24 h)
 [5] POST /jobs/<id>/start ─▶ api ─▶ Pub/Sub jobs topic (IDs only, India-only storage)
                                        └▶ Eventarc ─▶ Workflows "dispatch" ─▶ Cloud Run GPU job
                                                                     (no internet; Google APIs only)
                                                     worker reads uploads/<id>, writes results/<id>/
 [6] GET status / results (JWT) ─▶ api ─▶ V4 signed GET URLs (short) ─▶ GCS results (CMEK, 7 d)
 [7] "Delete now" ─▶ api deletes objects + rows, returns deletion receipt
 Static viewer files: Cloud CDN ◀─ GCS viewer bucket (public, no personal data)
```

**Trust boundaries:**
- **TB1:** internet ↔ Google edge (LB/Armor);
- **TB2:** edge ↔ API container;
- **TB3:** API ↔ data stores and queue;
- **TB4:** queue ↔ GPU worker (untrusted file parsing happens here);
- **TB5:** CI (GitHub) ↔ cloud control plane;
- **TB6:** browser ↔ uploaded file (de-identification is client-side).

**Assets:**
- A1: CT pixel data and any residual identifiers (health data, DPDP "personal data");
- A2: results (label maps, meshes);
- A3: accounts, consent records, audit trail;
- A4: model weights and images (device integrity, licences);
- A5: credentials, keys and cloud control plane;
- A6: availability and credit budget.

## 2. STRIDE by component

Legend for "Where":
- **IaC** = enforced by this Terraform;
- **S4** = service code (to verify in S4 review);
- **S3** = viewer;
- **Ops** = procedure in [SECURITY_CHECKLIST.md](SECURITY_CHECKLIST.md) or [DEPLOY_RUNBOOK.md](DEPLOY_RUNBOOK.md).

### 2.1 Upload path (steps 3–4)

| STRIDE | Threat | Mitigation | Where |
|---|---|---|---|
| S | Forged or replayed upload against another user's job | Signed URL bound to object `<job_id>/input.nii.gz`, 15 min expiry, `x-goog-content-length-range` cap; job ID unguessable (≥128-bit) and bound to the user in Cloud SQL | S4 (+ CORS limited to the site origin: IaC) |
| T | Object overwritten after the job starts | Worker records the object generation at start and refuses a changed object; the API refuses a second upload once the job is queued | S4 |
| R | User denies uploading | Data Access audit logs for Cloud Storage (who, when, object) in the 180-day India log bucket; job record with account ID | IaC + S4 |
| I | Upload of raw, identifiable DICOM | Browser de-identification before upload; the server accepts **NIfTI only** (magic and header check), refuses DICOM and zip | S3 + S4 |
| D | Huge or many uploads to burn GPU credits | Cloud Armor throttle on job creation (10 per IP per 10 min), general 300 rpm per IP with 10 min ban; signed-URL size cap; per-account quota in the API; budget alerts at 25–90 % | IaC + S4 |
| E | Path traversal via `input_uri` in the queue message | Dispatcher validates `job_id` against `^[A-Za-z0-9_-]{8,64}$`; the worker derives the path from `job_id` and ignores free-form URIs outside `gs://$UPLOAD_BUCKET/` | IaC (workflow) + S4 |

### 2.2 De-identification (browser, step 2)

| STRIDE | Threat | Mitigation | Where |
|---|---|---|---|
| I | Names and IDs remain in private tags or burned into pixels (scouts, dose screens, secondary captures) | PS3.15 Annex E basic profile with "retain longitudinal temporal information: off"; drop non-axial series, SOP classes for secondary capture / dose report; only pixel data and geometry are converted to NIfTI (NIfTI has no patient fields; `descrip` and `aux_file` blanked) | S3 / S4 |
| I | Face reconstructable from a head CT surface | Head scans: skin surface never rendered or exported; face crop before upload (no commercial-safe defacing model verified, see PLAN §4) | S3 + S4 |
| T | Malicious client skips de-identification | The server treats every upload as potentially identifiable: same encryption, 24 h deletion, access logging; NIfTI-only parsing still applies | IaC + S4 |
| R | No proof of what was removed | The browser sends a de-identification manifest (profile version, tags removed, hash of the output) stored with the job | S3 + S4 |

### 2.3 GPU worker (step 5, TB4)

| STRIDE | Threat | Mitigation | Where |
|---|---|---|---|
| S | Someone triggers jobs directly | Only the dispatcher SA has `run.jobsExecutorWithOverrides` on the job; Eventarc only from the jobs topic; only the API SA may publish | IaC |
| T | Crafted NIfTI exploits a parser (nibabel/zlib/ITK) to run code | Parse in the worker only; size and voxel caps checked **before** allocation; non-root container; no internet (Direct VPC egress `ALL_TRAFFIC`, no NAT, no default route, deny-all egress firewall except Google APIs and Cloud SQL); 30 min timeout; one job per execution (fresh sandbox per job, gVisor/gen2) | IaC + S4 |
| T | Exfiltration of other users' data after compromise | Worker SA: read-only on uploads, write on results, no list on other projects; cannot reach the internet; all access logged | IaC |
| R | Disputed results | Results carry model version, weights hash, image digest, input hash (provenance JSON) | S4 |
| I | TotalSegmentator phones home (usage stats) or downloads weights at runtime | Weights baked into the image; usage stats off; no internet anyway, so a missing switch fails loudly instead of leaking | S4 + IaC |
| D | Poison message loops or a GPU retry storm | Workflow dead-letters bad or failed jobs and exits successfully; job `max_retries = 1`; dead-letter alert | IaC |
| E | Container escape to the host | Cloud Run gen2 sandbox; no privileged mode available; no metadata server credentials beyond the worker SA | IaC (platform) |

### 2.4 Storage and database (TB3)

| STRIDE | Threat | Mitigation | Where |
|---|---|---|---|
| S | Use of a leaked service account key | No keys exist (WIF for CI, attached SAs at runtime); org policy suggestion disables key creation; alert on key creation | IaC |
| T | Tampered results or model weights | Images deployed by digest only, cosign-signed and verified before prod; immutable tags; results written once per job | IaC + CI |
| R | Admin actions not traceable | Admin Activity (always on) + Data Access audit logs for Storage, SQL, Secret Manager, KMS, IAM, Run; alerts on IAM policy change, KMS destroy, sink change | IaC |
| I | Public bucket by mistake | Uniform access + public access prevention **enforced** on uploads/results/logs; alert on `allUsers` grants; only the viewer bucket is public and holds no personal data | IaC |
| I | Data kept longer than promised | Lifecycle 1 d / 7 d as backstop; exact-time cleanup job; **soft delete off** and versioning off on personal-data buckets so deletion is final | IaC + S4 |
| I | Cloud SQL exposure | Private IP only, no public IP, TLS required, IAM database auth for SAs, CMEK, backups in India | IaC |
| D | Accidental deletion or ransomware-style destruction | Cloud SQL backups (14) + PITR (7 days), deletion protection in prod; KMS `prevent_destroy` and 30-day destroy delay | IaC |
| E | Over-broad IAM | Resource-level grants (bucket, topic, secret, service, job); project-level roles limited to telemetry, SQL client and Eventarc receiver; custom CDN invalidator role | IaC |

### 2.5 Viewer (static site)

| STRIDE | Threat | Mitigation | Where |
|---|---|---|---|
| S | Phishing look-alike site | HSTS; managed certificate on the owner's domain; publish the domain in the IFU | IaC + Ops |
| T | Tampered JS served to users (bucket write or CDN poisoning) | Only the deployer SA can write the bucket, only from an approved GitHub environment; npm `ci` with lockfile and `npm audit`; versioned bucket for rollback; CSP (report-only in staging, enforce after tuning) | IaC + CI |
| I | XSS steals the ID token | CSP `script-src 'self' 'wasm-unsafe-eval'`, no inline scripts; tokens kept in memory, not localStorage; `X-Frame-Options: DENY`, COOP/COEP | IaC headers + S3 |
| I | Signed result URLs leak via Referer or logs | `Referrer-Policy: no-referrer`; short signed URL lifetime (≤15 min) | IaC + S4 |
| D | CDN or bucket abuse | CDN caching; Armor on the API; viewer is static | IaC |

### 2.6 Authentication and authorisation

| STRIDE | Threat | Mitigation | Where |
|---|---|---|---|
| S | Credential stuffing or password spraying | Identity Platform with **mandatory TOTP MFA** (no SMS, avoiding SMS pumping); self sign-up off (invite only); Armor rate limits; Identity Platform request logging to the security log bucket | IaC |
| S | Forged JWT or wrong audience | API validates signature (Google JWKS), `iss = https://securetoken.google.com/<project>`, `aud = <project>`, `exp`, `auth_time` for sensitive actions | S4 |
| T | Insecure direct object reference (other user's job) | Every job lookup is filtered by the caller's account ID in SQL; job IDs unguessable as defence in depth | S4 |
| R | No record of clinician sign-off | Sign-off events in an append-only audit table with account ID, time and model/result hash | S4 |
| I | Account data outside India | Identity Platform is global: store minimal account data there (see MEITY_AND_RESIDENCY.md §4) | Ops / lawyer |
| E | Privilege escalation via CI | Apply SA only from protected GitHub environments with required reviewers; WIF condition accepts only this repo's PR runs and its two environments; plan SA is read-only | IaC + Ops |

## 3. Top residual risks (accepted for staging, revisit before S6)

1. **Client-side de-identification can be bypassed** by a malicious user uploading their own identifiable data. Impact is limited to that user's own data, still encrypted and deleted after 24 h. Pen test and DPIA must cover it.
2. **tf-apply is powerful** (project IAM admin). It is mitigated by the approval gate, WIF conditions and alerts on IAM changes. Separate break-glass procedure in the runbook.
3. **Identity Platform is global.** Account data may be stored outside India. Lawyer to confirm.
4. **Cloud Armor inspects only the first part of request bodies.** WAF rules do not inspect the NIfTI upload, which goes directly to GCS via signed URL anyway. Rely on server-side validation.
5. **GPU invitation in asia-south1.** If the fallback (Compute Engine VMs) is used, the VM attack surface returns: OS patching, SSH. This must be re-threat-modelled.
6. **Supply chain of model weights and Python wheels.** Pin hashes (ctseg device build), SBOM per image, Trivy gate, cosign signatures.
