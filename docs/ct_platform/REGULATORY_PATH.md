# Regulatory path: from software to a surgical medical device

Status: roadmap of 2026-11-22. Not legal advice; every class and timeline below must be confirmed by an Indian device regulatory consultant and, for the US, at an FDA Pre-Submission.
Evidence: [research/device/](research/device/):
- [india.md](research/device/india.md);
- [global.md](research/device/global.md);
- [engineering.md](research/device/engineering.md);
- [critique.md](research/device/critique.md), the verified synthesis, which wins where the reports disagree.

## 1. The goal and the short answer

**Goal (owner, 2026-11-21):** a regulated medical device that surgeons and doctors use **before surgery** (planning) and **during surgery** (on-screen reference), give feedback on, and that grows toward wider surgical use.

**Yes, this is achievable, and there is close precedent.** The FDA cleared two very similar products in 2025:

| Product | 510(k) | What it does | Decided | Model updates |
|---|---|---|---|---|
| **Axial3D Insight** | K250369 | Cloud AI segmentation of CT into 3D models for treatment planning | 18 Sep 2025, about 7 months after submission | Predetermined Change Control Plan (PCCP) |
| **InferOperate Suite** | K250237 | AI CT segmentation for "preoperative surgical planning and intraoperative image display" | 15 Sep 2025 | PCCP |

Both are FDA product code QIH. A PCCP is the regulated way to let doctor feedback improve the model after clearance.

**What is not allowed:** doctors using it on real patients' care *before* a licence, outside a formal study.

**What is allowed:** doctors working with it before the licence through:
- ethics-approved studies on old scans;
- "shadow" studies on new patients, where the treating team does not see the output before deciding;
- a CDSCO-permitted clinical investigation, where surgeons really use it for planning.

That third route is the legal form of "doctors use it and give feedback".

## 2. First intended use (narrow on purpose)

Draft, modelled on the Axial3D clearance:

> A cloud-based software service that converts DICOM CT images of adult patients into 3D surface models of **bony anatomy**: pelvis/acetabulum, spine, shoulder girdle, proximal femur and humerus. After review, editing and approval by a qualified clinician, the models support **pre-operative planning in orthopaedic and trauma surgery**. To be used with the source images and clinical judgment. Not for diagnosis, lesion or tumour detection, measurement-based treatment decisions, children, MR or cone-beam CT input, intra-operative navigation, or registration to the patient.

Why start here:
- It has the strongest predicate (K250369).
- It has the lowest plausible Indian class: B if CDSCO agrees, otherwise C. Plan for **C** (central licence).
- These bones are covered by TotalSegmentator's Apache-licensed `total` task.

Intra-operative use comes next, as its own approval step (section 3).

**Expansion order:**
1. Intra-op **read-only display** of the same signed-off models, on an offline OR screen (InferOperate precedent).
2. 3D-printed anatomical models.
3. Thoracic and abdominal organs and vessels.
4. Hands, feet, lower legs and cranio-maxillofacial (CMF), once those weights are commercially licensed.
5. CT angiography (CTA) / cardiovascular.
6. Tracked navigation or AR overlay. This is a separate, hardware-partnered product (21 CFR 882.4560).

## 3. Stages: what doctors can do at each one

| Stage | What doctors do | Legal basis | Rough time | Rough cost |
|---|---|---|---|---|
| **R0 Foundation** | Interviews and mock-ups only | Contracts | 0–3 months | ₹10–20 L |
| **R1 Retrospective reader study** | Rate and edit 3D models made from archived, de-identified CTs; structured feedback | Hospital ethics committee (consent waiver) + data use agreement; get CDSCO's view in writing | 2–9 months | ₹8–20 L |
| **R2 Shadow prospective** | Software runs on new patients; output hidden from the treating team until after decisions | Ethics committee, CTRI registration advisable | 6–12 months | ₹10–25 L |
| **R3 India pilot investigation** | **Surgeons use the models for pre-op planning** (intra-op display if the protocol allows) on consented patients | MDR 2017: MD-12→MD-13 test licence, MD-22→MD-23 permission (≤90 days), registered ethics committee, CTRI, compensation cover | 9–20 months | ₹20–50 L |
| **R4 FDA 510(k)** (in parallel) | After clearance, US surgeons use it clinically pre-op | 510(k) under QIH with PCCP; US Agent; cybersecurity (524B) | 12–30 months | $150–400k incl. FY2027 fee ($7,163 small business) |
| **R5 India pivotal + licence** | Routine Indian clinical pre-op use | MD-22 pivotal study, then MD-7→MD-9 (Class C) or MD-3→MD-5 (Class B) | 18–33 months | ₹40 L–1.2 Cr |
| **R6 Intra-op reference display** | Signed-off models on an OR screen during surgery | New 510(k) (K250237 precedent); India: new investigational use or change approval | +9–18 months | ₹30–80 L |
| **R7 Navigation / AR** | Registered overlay, instrument guidance | Navigation-device rules; with a hardware partner | 2+ years | partner-led |

**Total to first licences:** about 2–3 years and roughly ₹1–3 crore (estimate).

**Data law:** DPDP Rules 2025 core duties apply from **13 May 2027**. Hospital-sourced data needs agreements that allow commercial device training, not just ethics approval.

## 4. What changes in engineering, starting now

- **Quality system from day one:**
  - **ISO 13485** for the quality system;
  - **IEC 62304** for the software lifecycle (plan for software safety class B or C);
  - **ISO 14971** for risk;
  - **IEC 62366-1** for usability, with summative tests of at least 15 users per group;
  - **IEC 81001-5-1** for security.
- **Design records to start now:** intended use, user needs, requirements with traceability to tests, risk file, SOUP and licence register (every library and model weight), verification protocols, signed and versioned releases, and a locked model registry with hashes.
- **Model licences for a device:**

  | Model | Status in the device |
  |---|---|
  | TotalSegmentator Apache `total` task | Device path |
  | MOOSE | Needs written commercial terms first (paid Zenta version exists; some training data is non-commercial) |
  | NVIDIA NV-Segment-CT | Needs written permission (card says "not for clinical usage") |
  | CADS `open` | Needs a share-alike review |

  Until those clear, MOOSE, NVIDIA and CADS stay in a research-only build, never in the device build.
- **Hazards to test for in CI:** left/right flip, wrong scale, wrong patient or series, organ truncated by the scan edge, stale model version, silent model change.
- **Clinician sign-off is mandatory:** the product shows models as "not approved" until a qualified clinician reviews, edits and approves them. Both predicates have this.
- **Intra-op readiness:**
  - an offline-capable, read-only viewer;
  - no dependence on the network in the OR;
  - large touch targets, with touchless control as a later option.
- **Accuracy acceptance (proposal, to be fixed at the FDA Pre-Sub):** bone mean surface distance ≤ 1 mm, HD95 ≤ 2–3 mm, laterality 100%, on about 150–300 independent patients from at least 3 sites, including Indian data.
- **Research vs device builds stay separate:** the public research demo must not make clinical claims, and the device build must not include research-only weights. Once clinical claims are made, a research disclaimer no longer protects.

## 5. Next 30 days

1. Engage an Indian medical-device regulatory consultant with software experience. Get a quote for R0–R3. *(owner)*
2. Freeze intended use v1 (section 2) and its excluded uses.
3. Draft the CDSCO classification request with that text. *(consultant confirms which portal)*
4. Get the final CDSCO software guidance (CDSCO/MD/GD/MDSW/01/2026, a scanned PDF) read by the consultant, especially its classification table.
5. Set up the QMS skeleton: document control, IEC 62304 development plan, risk plan, SOUP/licence register, complaints and feedback (CAPA) procedure.
6. Lock the device build to TotalSegmentator `total` only, with usage statistics off. Email the TotalSegmentator authors (training-data rights), Zenta (MOOSE commercial terms) and NVIDIA (clinical use).
7. Approach two hospital partners with registered ethics committees, ideally one government hospital (MD-22 fee waiver). Draft the R1 retrospective protocol and data use agreement. *(owner)*
8. Write the annotation manual and ground-truth plan. Plan an India-region (asia-south1) Google Cloud project with an audit trail. *(needs owner's Google Cloud permission when we get there)*
9. Start the hazard analysis and add a phantom geometry test suite to CI.
10. Outline an FDA Pre-Submission (predicate K250369, test plan, PCCP scope).

## 6. Open questions for the consultant or lawyer

1. Exact CDSCO class (B or C) for the section 2 text, and whether any Indian predicate exists; if none, a new-device permission (MD-26/27) is needed.
2. Whether the R1 and R2 studies need CDSCO permission or only ethics approval.
3. Which portal to file on.
4. Who is fiduciary and who is processor under DPDP for hospital scans.
5. Training-data rights behind the model weights.
6. Compensation and insurance for investigations.
7. Small-business status and a US Agent for an Indian company.
8. CERT-In incident-reporting duties for the cloud service.
