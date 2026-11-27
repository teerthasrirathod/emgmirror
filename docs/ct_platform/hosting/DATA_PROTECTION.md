# Data protection (DPDP Act 2023 and DPDP Rules 2025)

Status: 2026-11-22. **Not legal advice.** Items marked ⚖ need an Indian privacy lawyer before real users. The DPDP Rules' core duties apply from **13 May 2027**. Building to them now is cheaper than retrofitting.
Data Fiduciary (controller): **the owner** (PLAN owner decision). Processors: Google Cloud (hosting) and, for alerts only, the owner's email provider.
This document covers the **opt-in upload tier**. The default browser tier processes nothing on the server.

## 1. Data map

| # | Data | Source | Purpose | Where stored (all India unless noted) | Retention | Deleted by |
|---|---|---|---|---|---|---|
| D1 | De-identified CT volume (`input.nii.gz`) | User's browser, after de-identification | Run segmentation and meshing for that user | GCS `uploads/<job_id>/`, CMEK | **24 h** after upload (contract); lifecycle backstop 1 day + up to 24 h lag | Cleanup job at 24 h; "delete now"; bucket lifecycle |
| D2 | Results: label map, meshes (GLB/STL/3MF), DICOM-SEG, per-structure JSON | Worker | Return the result to the user | GCS `results/<job_id>/`, CMEK | **7 days** | Cleanup job; "delete now"; lifecycle |
| D3 | Job record: job ID, account ID, status, timestamps, model version, input hash, de-identification manifest | API, worker | Operate the service; traceability (device design controls) | Cloud SQL, CMEK | While the account exists; a tombstone (job ID, hashes, deletion time) is kept as the deletion receipt ⚖ | Account deletion; erasure request |
| D4 | Account: email, display name, password hash, TOTP secret | User | Authentication | **Identity Platform (global, may be outside India)** | Until account deletion | Account deletion (Identity Platform user delete) |
| D5 | Profile and role (clinician registration number, institution) | User / admin | Authorisation for clinician sign-off | Cloud SQL | Until account deletion + retention for device records ⚖ | Account deletion |
| D6 | Consent records (see §4) | User actions | Prove consent; withdrawal | Cloud SQL (append-only) | Life of the account + limitation period ⚖ | Scheduled purge after the period |
| D7 | Security logs: LB/Armor requests (IP, path, user agent), audit logs (principal, method), Identity Platform logs, VPC flow logs | Google Cloud | Security, abuse prevention, CERT-In | Cloud Logging bucket `*-security`, CMEK | 180 days in staging, **400 days in prod** (CERT-In ≥ 180 days; DPDP Rules: logs one year ⚖) | Bucket retention |
| D8 | Application logs (job IDs, error codes, **no file names, no emails, no DICOM tags**) | API, worker | Operations | Cloud Logging bucket `*-app` | 30 days | Bucket retention |
| D9 | Storage access logs | GCS | Security, audit | GCS `access-logs` | 180 days | Lifecycle |
| D10 | Database backups (contain D3, D5, D6) | Cloud SQL | Recovery | Cloud SQL backups in asia-south1 | 14 daily backups + 7 days PITR | Automatic expiry |

Not collected by the server: names, dates of birth, hospital IDs, DICOM headers, raw DICOM, face surfaces. The browser removes or crops them before upload ([THREAT_MODEL.md](THREAT_MODEL.md) §2.2).

⚖ **Is de-identified CT still "personal data"?**
- Under DPDP, data that can identify a person is personal data.
- De-identified volumes are pseudonymous at best: body habitus and implants can identify someone, and the job is linked to an account.
- **Treat D1/D2 as personal data** (this design does). Health data has no separate "sensitive" class in DPDP, but contracts and ethics committees will treat it as such.

## 2. Notice and consent (DPDP s.5–6; Rules: notice requirements)

Shown before the first upload and versioned:
- an itemised list of what is processed (D1–D9 in plain language), for what purpose, and for how long;
- that processing happens on Google Cloud in Mumbai, India, and that account data (D4) may be stored by Google outside India;
- research use only, no diagnosis (until licensed, [REGULATORY_PATH.md](../REGULATORY_PATH.md));
- how to withdraw consent (as easy as giving it), and how to delete a job or the account;
- how to complain: the grievance officer contact, then the Data Protection Board of India;
- available in English and in at least one other language of the Eighth Schedule if offered to the public ⚖ (which languages).

**Children (under 18):** verifiable parental consent is required. Until a verified mechanism exists, **block uploads for users who declare they are under 18**. Also show the "not validated for children" banner already planned. ⚖ Which verification method is acceptable.

**Clinicians uploading patient scans:** in R1–R3 studies the hospital or the investigator is likely the fiduciary for the patient's data, and the owner may be a processor or a joint fiduciary. ⚖ This changes the consent chain: patient consent under the ethics protocol, and a data processing agreement with the hospital. Until those agreements exist, **public users may only upload their own scans**.

## 3. Rights of the Data Principal and how the service meets them

| Right | Mechanism | Timeline |
|---|---|---|
| Access (summary of data and processing) | "My data" page listing jobs, consents and account data; export as JSON | Immediate (self-service) |
| Correction / update | Profile edit; jobs are immutable (re-run instead) | Immediate |
| **Erasure** | "Delete now" per job: GCS objects deleted (no versioning, **no soft delete**), job row replaced by a tombstone, receipt shown. Account deletion removes Identity Platform user, profile and jobs | Immediate for objects. **Backups keep DB rows up to 14 days** (disclose in the notice). Logs keep only job IDs until their retention ends ⚖ (justify as a legal obligation / security) |
| Withdraw consent | Same screen; stops new processing and triggers erasure of D1/D2 | Immediate |
| Grievance redressal | Grievance contact on the site; ticket log | Respond within the period set by the Rules (90 days at most) ⚖ |
| Nomination | Record a nominee in the profile | — |

⚖ **Possible conflict to resolve: DPDP Rules on minimum retention of logs and data.**
- The Rules contain provisions on keeping logs (and, in one reading, the personal data and traffic data of processing) for **one year** for specified purposes and for security safeguards.
- If they apply to the content (D1/D2), they would conflict with the 24 h / 7 day promise.
- This design keeps **logs** for 400 days in prod and the **content** for 24 h / 7 days. A lawyer must confirm this reading before launch.

## 4. Consent records (D6)

Append-only table in Cloud SQL, never updated in place:

| Field | Example |
|---|---|
| `consent_id` | UUID |
| `account_id` | Identity Platform UID |
| `notice_version` and `notice_sha256` | `2027-01-v3`, hash of the exact text shown |
| `purposes` | `["process_upload", "store_results_7d"]` (no bundled consent; optional research-use consent separate and off by default) |
| `language` | `en` |
| `event` | `given` / `withdrawn` |
| `timestamp_utc` | server time |
| `channel` | `web` |
| `age_declared_18_plus` | true |
| `ip_truncated` | /24 or omitted ⚖ (minimisation vs proof) |

Consent managers (registered under the Rules) are not needed for a first-party web flow. Re-check if hospitals integrate.

## 5. Security safeguards (DPDP s.8(5); Rules: reasonable security safeguards)

Implemented in `infra/`:
- **Encryption at rest:** CMEK on buckets, database, secrets, Pub/Sub, Artifact Registry, logs and Workflows.
- **Encryption in transit:** TLS 1.2+, TLS on Cloud SQL.
- **Access control:** least-privilege service accounts, no keys, WIF for CI, MFA for users.
- **Visibility:** audit logs, Data Access logs, security alerts, 180/400-day log retention.
- **Continuity:** backups, PITR, deletion protection.
- **Network isolation:** private IP database, worker without internet.
- **Development:** threat model, scanning, signing, pen test ([SECURITY_CHECKLIST.md](SECURITY_CHECKLIST.md)).

Still to write as procedures:
- access reviews (quarterly);
- an access log of who in the team accessed production data (there should be nobody in normal operation);
- staff confidentiality undertakings.

## 6. Processors and contracts

| Processor | What they process | Contract to have in place | Notes |
|---|---|---|---|
| Google Cloud (Google Cloud India Pvt Ltd / Google LLC per the billing account) | All server-side data | Google Cloud Terms of Service + **Cloud Data Processing Addendum (CDPA)**: accept it in the console (Account → Privacy & Security), record the version and date | ⚖ Check that the CDPA's controller-processor terms satisfy DPDP s.8(2) (processing under a valid contract). Record subprocessors (Google publishes a list). Configure the Privacy & Security contacts in the console |
| Email provider (alert emails) | Alert text (no personal data by design) | Standard terms | Keep alerts free of personal data |
| GitHub | Source code, CI logs (no personal data) | GitHub terms | Never put uploads or production data in CI |
| Hospitals (P2) | — | **Data processing / data use agreement** that allows device development use (REGULATORY_PATH §3) ⚖ | Defines fiduciary/processor roles |

## 7. Breach notification (DPDP; Rules: intimation of personal data breach)

From 13 May 2027 (follow it earlier as practice):
1. **Without delay**, inform each affected Data Principal in plain language. Include:
   - what happened;
   - when;
   - likely consequences;
   - what the owner is doing;
   - what they can do (e.g. reset password);
   - a contact.
2. **Without delay**, inform the **Data Protection Board** with a short description.
3. **Within 72 hours** of becoming aware (or longer if the Board allows), send the Board a detailed report covering:
   - facts, circumstances and reasons;
   - mitigation;
   - findings about the person responsible, if known;
   - remedial measures;
   - the notifications sent to Data Principals.
4. In parallel: **CERT-In within 6 hours** ([SECURITY_CHECKLIST.md](SECURITY_CHECKLIST.md) §3.3). These are two different regulators and two different clocks.
5. Hospital partners: as their contracts require (often 24–72 h).

Templates for the user email and the Board report go into the QMS document set. ⚖ Final wording.

## 8. DPIA and other paperwork before the public demo (S6)

- **DPIA:** not legally required unless designated a Significant Data Fiduciary, but planned (PLAN §4) and expected by hospitals and ethics committees. Use this data map as its core.
- Privacy notice, terms of use (research only), NOTICE page with model licences, cookie notice (the viewer sets no tracking cookies; keep it so).
- Grievance officer named on the site.
- Record of processing (this document, versioned).

## 9. Questions for the lawyer (one list)

1. Is de-identified CT plus an account link personal data? Any extra duties for health data (contracts, ICMR / ethics guidance)?
2. Identity Platform storing account data outside India: acceptable under s.16 and sector rules? Is notice wording enough?
3. Rules on one-year retention of logs (and possibly data of processing): does this apply to the CT content? Can 24 h / 7 day deletion stand?
4. Tombstones and consent records after erasure: what may be kept, and for how long?
5. Children: acceptable verifiable parental consent method, or block under-18s entirely?
6. Google CDPA sufficiency for DPDP s.8(2); need for an India-specific addendum.
7. Fiduciary/processor roles in clinician uploads and hospital studies (R1–R3).
8. Grievance timelines and the languages required for notices.
9. Breach notification content and timelines; interplay with CERT-In.
