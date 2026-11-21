# CT-to-3D as a surgical medical device: verified synthesis (2026-11-21)

**How this was checked.** I re-verified the claims the roadmap depends on most this run, using FDA databases, the CDSCO and MeitY-derived texts, the NVIDIA licence, the HF card, the MOOSE README and the FDA guidance page. Anything I could only confirm from secondary sources is marked as such. **UNVERIFIED** means I could not confirm it this run. I did not read 01_clinical_CT/, results/ or data/, and I modified no files. Costs are estimates. Currency conversion assumes ₹88 = $1 (UNVERIFIED rate). This is not legal advice.

---

## 1. Re-verification of the claims the roadmap depends on

| # | Claim | Verdict | Source |
|---|---|---|---|
| 1 | **India class:** standalone software is classed A–D using a "situation × significance" matrix; software that drives hardware takes the hardware's class | **Confirmed in outline (secondary).** The individual cells are **disputed**. One summary maps "drives × serious" to **B**. Another reports **C**. Read Table 2 in the PDF before relying on any cell | https://www.lexorbis.com/cdsco-issues-guidance-on-medical-device-software-under-the-medical-devices-rules-2017/ ; https://www.lexology.com/library/detail.aspx?g=79f45643-4003-411a-85d7-7ea945a8c974 ; https://meddeviceguide.com/blog/india-cdsco-medical-device-software-mdsw-guidance-2026-guide |
| 2 | **Licensing authority:** Class A/B by the State (MD-3→MD-5); Class C/D by the Central authority (MD-7→MD-9); clinical investigation permission always Central | **Confirmed** against the MDR text | https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2022/m_device/Medical%20Devices%20Rules,%202017.pdf |
| 2a | **New point:** a circular of 23 Oct 2025 reportedly says the Central authority must classify a device before a State authority can license it | Secondary; UNVERIFIED. **Even a Class B route therefore starts with a central classification** | https://www.imarcengineering.com/news/medical-devices-rules-amendments-licensing-compliance |
| 3 | **Clinical investigation rules (rules 49–55):** MD-22→MD-23, decision within 90 days, registered ethics committee, CTRI registration, compensation; MD-12→MD-13 test licence to manufacture the investigational device | **Confirmed.** CDSCO's device page describes MD-22 and MD-12 | MDR PDF above ; https://cdsco.gov.in/opencms/opencms/en/Medical-Device-Diagnostics/Medical-Device-Diagnostics/ |
| 3a | **r.51(3) academic study exemption** | **Confirmed, with two conditions:** the device must be **licensed**, and the data must not support any investigational device. It does not apply before a licence | MDR PDF ; https://cdsco.gov.in/opencms/export/sites/CDSCO_WEB/Pdf-documents/MDfAq24.pdf |
| 3b | **r.63(1) fourth proviso** (waiver of Indian clinical investigation data) | **CORRECTED.** The list of approving jurisdictions was UK, US, Australia, Canada and Japan. **G.S.R. 744(E)** (notified 14 Aug 2026, in force 19 Aug 2026) added **"European Union countries"**. The conditions still apply: 2 years of marketing there, CLA satisfaction, and a post-market investigation undertaking. Secondary source; Gazette not read | https://www.pureglobal.com/news/india-gsr-744e-eu-rule-63-class-a-qms-2026 |
| 4 | **Final CDSCO software guidance:** CDSCO/MD/GD/MDSW/01/2026, dated 21 Jul 2026 | **Exists** (the PDF is on cdsco.gov.in). The date is **21 Jul per most reports; one says 23 Jul.** The PDF is scanned, so its contents are verified only through secondary sources | https://cdsco.gov.in/opencms/export/sites/CDSCO_WEB/Pdf-documents/Guidance-document-on-Medical-Device-Software-under-MDR-2017.pdf ; https://www.lexology.com/library/detail.aspx?g=0049fff8-5f9c-469c-8701-0a30b009a4c5 ; https://asiaactual.com/blog/india-medical-device-software-guidance/ |
| 5 | **DPDP Rules:** G.S.R. 846(E) of 13 Nov 2025; Rule 4 from 13 Nov 2026; Rules 3, 5–16, 22 and 23 from **13 May 2027** | **Confirmed (secondary).** The Gazette is dated 13 Nov, but PIB and some outlets say 14 Nov. Dates are computed from Gazette publication, so 13 Nov governs. MeitY PDF not read | https://www.bitscore.in/instruments/dpdp-rules-2025 ; https://dpdprules.org/rules/1 ; https://en.wikipedia.org/wiki/Digital_Personal_Data_Protection_Rules,_2025 |
| 6 | **FDA QIH product code** (21 CFR 892.2050, Class II) | **Confirmed from the FDA classification page.** It is **not GMP-exempt**, **"Adaptive AI algorithms are not within scope"**, and it is **NOT eligible for third-party review** | https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD/classification.cfm?ID=QIH |
| 6a | **K250369 Axial3D Insight** | **Confirmed:** QIH, decided 18 Sep 2025, PCCP authorised, no third-party review. **Date received 10 Feb 2025**, so the review took about 7 months, not 1 | https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMN/pmn.cfm?ID=K250369 |
| 6b | **K250237 InferOperate Suite** | **Confirmed:** QIH, cleared 15 Sep 2025, PCCP authorised. Its indications cover "preoperative surgical planning and intraoperative image display" | https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMN/pmn.cfm?ID=K250237 ; https://fda.innolitics.com/submissions/RA/subpart-b%E2%80%94diagnostic-devices/QIH/K250237 |
| 7 | **FDA PCCP guidance** | **Confirmed Final**, docket FDA-2022-D-2628, covering 510(k), De Novo and PMA. First issued as final 4 Dec 2024; the FDA page now shows "August 2025", content current as of 18 Aug 2025 | https://www.fda.gov/regulatory-information/search-fda-guidance-documents/marketing-submission-recommendations-predetermined-change-control-plan-artificial-intelligence ; https://www.federalregister.gov/documents/2024/12/04/2024-28361/marketing-submission-recommendations-for-a-predetermined-change-control-plan-for-artificial |
| 8 | **NV-Segment-CT clinical-use wording** | **Confirmed verbatim** on the model card: "This model is for research purposes and not for clinical usage." The licence (v. 24 Oct 2025) says "Models are commercially usable" and has **no medical or clinical clause**. However, §6 makes the user "solely responsible for reviewing Model documentation, including any Special-Purpose Model limitations", and §2.1 terminates rights if guardrails are bypassed. **The verdict stands: do not ship it without written permission from NVIDIA** | https://huggingface.co/nvidia/NV-Segment-CT ; https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/ |
| 9 | **MOOSE terms** | **Confirmed:** model CC BY 4.0, code Apache 2.0, and a "Commercial Version: Contact Zenta". No clinical restriction is stated. Dataset licensing is "per site", and AutoPET is CC BY-NC per the DATA_CARD. **Get a commercial licence or written permission before putting it in a device** | https://raw.githubusercontent.com/ENHANCE-PET/MOOSE/main/README.md ; https://raw.githubusercontent.com/ENHANCE-PET/MOOSE/main/DATA_CARD.md |
| 10 | **EU AI Act Omnibus,** Reg. (EU) 2026/1744: Annex I obligations from 2 Aug 2028 | **Still secondary only;** EUR-Lex not checked. Treat as UNVERIFIED | https://www.pureglobal.com/news/eu-ai-act-omnibus-high-risk-dates-2028 |
| 11 | **CDSCO: AI cancer-detection software on X-ray/CT is Class C** | Confirmed in news coverage (oncology list of 77 devices). Notification text and number UNVERIFIED | https://www.insightsonindia.com/2026/01/07/centre-imposes-norms-for-ai-based-cancer-detection/ ; https://medicaldialogues.in/news/health/ai-based-cancer-diagnostic-software-to-come-under-centres-regulatory-oversight-162267 |
| 12 | **Draft MDR amendment** shortening Class B and C/D licensing windows | **Draft only** (G.S.R. 515(E) of 23 Jun 2026 or 270(E), reports conflict). Not in force; keep the current statutory timelines | https://www.imarcengineering.com/news/medical-devices-rules-amendments-licensing-compliance |

---

## 2. Contradictions resolved

1. **India class for pre-op planning (B or C).**
   - Engineering reads "drives × serious" as B. One secondary source reports C for that cell.
   - **Resolution:** budget and plan for **Class C (central licence)**. Treat B as an upside if the claim is narrowed to orthopaedic and trauma bone and CDSCO confirms it in writing.
   - Intra-op display: **C**. Oncology, cardiac or neuro claims: **C**, possibly D if quantitative outputs are framed as diagnostic.
2. **Which CDSCO portal.**
   - The India report says SUGAM is outdated for devices. CDSCO's own device page still points MD-22 applicants to Sugam.
   - **Resolution:** unresolved. The consultant should confirm, choosing between Sugam, cdscomdonline.gov.in and NSWS (for MD-12).
3. **"A third-party 510(k) review means no FDA fee."**
   - This does **not apply**, because QIH is not eligible for third-party review.
   - Budget the FY2027 fee: $28,653 standard or $7,163 small business (https://www.federalregister.gov/documents/2026/07/30/2026-15335/medical-device-user-fee-rates-for-fiscal-year-2027).
4. **"Axial3D cleared in about 1 month."** Wrong. FDA received it on 10 Feb 2025 and decided on 18 Sep 2025. Plan for 6–9 months, including additional-information holds.
5. **Validation size: 12 cases (predicate) vs 150–300 patients (engineering).**
   - Predicate summaries show the floor FDA accepted, not what it will expect from a new sponsor.
   - **Resolution:** plan for about 150–300 independent patients per indication from at least 3 sites, including Indian and US-representative data, plus a reader study. Fix the number at the FDA Pre-Sub.
6. **Google Cloud MeitY empanelment.**
   - Engineering cites Google's own page (https://cloud.google.com/security/compliance/meity-india). The India report marked it UNVERIFIED.
   - **Resolution:** accept that vendor page as the source, but re-check the empanelment's validity date before filing.
7. **Indian clinical evidence vs foreign approval.**
   - For an Indian-developed device without a predicate, an Indian investigation is required from the pilot stage (Seventh Schedule).
   - The r.63 proviso can waive clinical data only after 2 years of marketing in the US, UK, EU, Canada, Australia or Japan.
   - **Resolution:** the waiver is too slow to rely on for Indian launch. Run the Indian investigation and the FDA 510(k) in parallel. Keep the waiver as a fallback.
8. **What changed from the earlier plan.**
   - The earlier PLAN.md stance ("research and education only, no clinical wording") was a deliberate **stage 1 positioning**, not a finding that the product cannot be a device.
   - Both the device route and the research route are lawful. They must not be mixed: once clinical claims are made, the research disclaimer no longer protects (https://fda.gov/media/87374/download).

---

## 3. Final deliverable

### A. Plain answer to the founder

Yes. This can become a regulated medical device that surgeons use before surgery, and later during surgery as an on-screen reference. The FDA has already cleared two very similar products in 2025:
- **Axial3D Insight** (K250369): cloud AI CT segmentation that produces 3D models for treatment planning.
- **InferOperate** (K250237): AI CT segmentation for "preoperative surgical planning and intraoperative image display".

Both are cleared with a pre-approved plan that lets the AI model be updated from new data, which is the regulated form of "doctors give feedback and the model improves" (https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMN/pmn.cfm?ID=K250369, https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMN/pmn.cfm?ID=K250237).

How you get there:
1. Write one narrow intended use.
2. Build the quality system: ISO 13485, IEC 62304, ISO 14971, IEC 62366-1 and IEC 81001-5-1.
3. Let doctors work with it **legally before the licence**:
   - first on old, de-identified scans with ethics committee approval;
   - then in "shadow" mode on new patients, where the treating team does not see the output before deciding;
   - then inside a CDSCO-permitted clinical investigation (MD-12/13 test licence, MD-22/23 permission, ethics approval, CTRI registration, insurance), where surgeons actually use it pre-op and, if the protocol allows, as an intra-op display.
4. File for a CDSCO licence (likely Class C, central) and an FDA 510(k).

Plan for about 2–3 years and roughly ₹1–3 crore to the first licences (estimate). Intra-op use should start as a read-only display of models a clinician has already signed off. Anything that registers the model to the patient or tracks instruments is a navigation device under 21 CFR 882.4560, which means a hardware partner and a separate product.

### B. Intended use

**First intended-use statement (draft; narrow and predicate-backed, modelled on Axial3D K250369):**

> "[Product] is a cloud-based software service and image segmentation framework intended to transfer DICOM CT imaging information of adult patients (≥18 years) to an output file containing a 3D surface model of bony anatomy of the pelvis/acetabulum, spine, shoulder girdle and proximal femur/humerus. The output, after review, editing and approval by a qualified clinician, is intended to support pre-operative treatment planning in orthopaedic and trauma surgery. It is intended to be used in conjunction with the source images, other diagnostic tools and expert clinical judgment. It is not intended for diagnosis, lesion or tumour detection, measurement-based treatment decisions, paediatric patients, MR or CBCT input, intra-operative navigation or registration to the patient."

Why this scope:
- Its strongest predicate is K250369 (cloud, ML, QIH, PCCP).
- It gives the lowest plausible Indian class (B, if CDSCO accepts "serious, not critical").
- The anatomy fits the Apache-licensed TotalSegmentator `total` task, so it avoids the licensed `appendicular_bones` and the commercial MOOSE weights (https://raw.githubusercontent.com/wasserth/TotalSegmentator/master/README.md). Confirm the exact label list against the model.
- Include mandatory clinician review, which both predicates have.
- Include a PCCP covering retraining within the same structures and population.

**Expansion sequence toward intra-op use** (each step is a new intended use: in the US a new 510(k) with your own device as predicate; in India a new investigational use under r.3(x) needing an MD-22 study, or a change approval):

| Step | Expansion | Precedent or route | Notes |
|---|---|---|---|
| 1 | **Intra-op reference display** of the same signed-off models: read-only, offline edge viewer, no registration | K250237 precedent | India: C |
| 2 | **3D-printed anatomical models** "for diagnostic use" with validated printer/material combinations | K173619, K250369, K220205 | |
| 3 | **Thoracic and abdominal organs and vessels** for oncologic or hepatobiliary planning, including intra-op display | K250237, K212896 | India C, possibly D if quantitative |
| 4 | **Distal limbs and CMF** | after a commercial licence for MOOSE or TotalSegmentator `appendicular_bones`, or retraining on licensed data | |
| 5 | **CTA / cardiovascular** | Mimics Enlight K190874 | |
| 6 | **Tracked navigation or AR overlay** | 882.4560 (HAW/OLO/SBF) via a navigation-hardware partner, ASTM F2554 accuracy testing; K221087, K252054 | separate product |

### C. Staged roadmap

| Stage | What doctors can do | Legal basis | Key deliverables | Duration | Rough cost |
|---|---|---|---|---|---|
| **S0 Foundation** | Interviews, usability mock-ups. **No patient data affects care** | None needed beyond contracts | Company entity; intended use v1; regulatory strategy; written CDSCO classification request; QMS (about 25 SOPs), IEC 62304 plan, risk plan, SOUP and licence register; locked model registry; separate device repo; research demo firewalled | 0–3 mo | ₹10–20 L ($11–23k) |
| **S1 Retrospective reader study** | Surgeons and radiologists rate and edit 3D models from archived, de-identified CTs and give structured feedback | Ethics committee approval with consent waiver (ICMR 2017/2023); data use agreement. CDSCO permission arguably not needed (data-only); **get CDSCO's view in writing** | Annotation manual, ground truth (2 readers + adjudicator), Dice/HD95, formative usability, India-region hosting | 2–9 mo | ₹8–20 L ($9–23k) |
| **S2 Shadow prospective** | Software runs on new patients; output is hidden from the treating team until after decisions, or reviewed by a non-treating panel | Ethics committee; CTRI registration advisable; CDSCO view (UNVERIFIED that MD-22 is not needed) | Failure-mode log, workflow and timing data, draft clinical evaluation plan | 6–12 mo | ₹10–25 L ($11–28k) |
| **S3 India pilot investigation** | **Surgeons use the models for pre-op planning** (and an intra-op display if the protocol allows) on consented patients | MDR r.31 MD-12→MD-13 (₹500/device); r.51–53 MD-22→MD-23 (₹1 L, decision ≤90 days); registered ethics committee; CTRI; compensation per r.55; device labelled "investigational" | Locked version, protocol, insurance, monitoring, SAE process, pilot report | 9–20 mo | ₹20–50 L ($23–57k) |
| **S4 FDA 510(k) (parallel)** | After clearance, **US surgeons use it clinically pre-op** | 510(k), QIH, with PCCP; QMSR (21 CFR 820); 524B cyber; US Agent required for a foreign establishment (21 CFR 807.40, https://www.ecfr.gov/current/title-21/chapter-I/subchapter-H/part-807/subpart-B/section-807.40) | Pre-Sub; standalone study (about 150–300 patients, ≥3 sites, US-representative); reader study; IEC 62366 summative test (≥15 per user group); SBOM and penetration test; PCCP | 12–30 mo | $150–400k (₹1.3–3.5 Cr) including FY2027 fee of $7,163–28,653 |
| **S5 India pivotal + licence** | After the licence, **routine Indian clinical pre-op use**; academic studies on new uses under r.51(3) | MD-22 pivotal; MD-26→MD-27 if no predicate (₹50k; 120+30 days); MD-7→MD-9 for C (inspection ≤60 days, grant ≤45 days) or MD-3→MD-5 for B | Multicentre Indian data, ISO 13485 certificate, technical file, PSUR plan | 18–33 mo | ₹40 L–1.2 Cr ($45–136k) |
| **S6 Intra-op reference display** | Read-only signed-off models on an OR monitor or edge box during surgery | New US 510(k) (K250237 precedent); India new investigational use (MD-22) or change approval | Offline edge viewer, OR usability, network-loss risk controls | +9–18 mo after S4/S5 | ₹30–80 L ($34–91k) |
| **S7 Navigation / AR** | Overlay registered to the patient, instrument guidance | 21 CFR 882.4560; India takes the hardware's class; EU IIb or higher | Hardware partner, ASTM F2554, new predicates | 2+ yrs, partner-led | partner-dependent |
| **EU (optional, later)** | Clinical use in the EU | MDR Rule 11, IIb, notified body; AI Act Annex I (2 Aug 2028, UNVERIFIED) | Authorised Representative, clinical evaluation, PMCF | 18–30 mo | €150–400k (estimate) |

**Exit criteria:**
- **S0:** intended use frozen; classification request filed; QMS SOPs approved.
- **S1:** pre-specified acceptance criteria met (e.g. bone MSD ≤1 mm and HD95 ≤2–3 mm, a proposal); laterality 100%.
- **S2:** no unresolved high-severity failure modes; usability issues closed.
- **S3:** pilot endpoints met; CDSCO accepts the pivotal design.
- **S4:** substantial-equivalence letter.
- **S5:** MD-9 (or MD-5) granted.
- **S6:** clearance or approval of the new indication.

### D. Next 10 actions (30 days)

1. Hire an Indian device regulatory consultant (CDSCO software experience) and get a scoped quote for S0–S3.
2. Freeze intended-use v1 (§B) and an "excluded uses" list. Rewrite the PLAN.md header and §2 accordingly. Keep the research demo under separate branding.
3. Draft the CDSCO classification request with the exact intended-use text, to file through the portal the consultant confirms.
4. Download the final MDSW guidance PDF and get it OCR'd or read by the consultant; extract Table 2 and the documentation checklist.
5. Stand up the QMS skeleton: document control, design and development plan (IEC 62304 §5.1), risk management plan, SOUP and licence register, CAPA and complaints procedures (feedback route).
6. Lock the weights allowlist to the TotalSegmentator Apache `total` task. Map the first-claim bones to its labels. Turn off usage statistics. Email Wasserthal about training-data rights, Zenta about MOOSE commercial terms, and NVIDIA about clinical use. Do not use the excluded models.
7. Approach 2 hospital partners (one government-funded, because r.51(2) waives the MD-22 fee for government institutions) with registered ethics committees. Draft the S1 retrospective protocol and data use agreement.
8. Write the annotation manual and ground-truth plan, and set up an India-region (asia-south1) single-tenant project with an audit trail. Confirm GCP's MeitY empanelment validity.
9. Start the hazard analysis (laterality, scale, wrong patient, truncation, stale model) and add a phantom geometry test suite to CI.
10. Prepare an FDA Pre-Sub outline (predicate K250369, testing plan, PCCP scope) and price the US Agent and small-business determination.

### E. Risks and open questions for a consultant or lawyer

- **Class:** the exact CDSCO class for the §B text, B or C, and whether "drives × serious" is B. Whether **any Indian predicate** exists (e.g. an imported Mimics or Axial3D licence); if not, MD-26/27 is needed.
- **CDSCO scope of studies:** whether S1 and S2 need MD-22; whether a pure-SaaS "manufacturing site" is inspected, and how.
- **Portal:** which portal handles the classification request and MD-22 (Sugam, cdscomdonline or NSWS).
- **Pending amendment:** the effect of the draft MDR amendment (G.S.R. 515(E)/270(E)) on timelines.
- **Data roles:** under DPDP, who is fiduciary and who is processor for hospital-sourced scans. The research exemption under Rule 16 / Second Schedule applies from 13 May 2027. Consent for scans reused for training and commercial models. Children excluded.
- **Data rights for training:** hospital data use agreements must allow commercial device training and validation. Ethics approval alone is not enough.
- **Model licences:**
  - TotalSegmentator v2 non-public training data;
  - MOOSE `clin_ct_*` training sources (AutoPET is CC BY-NC);
  - whether NVIDIA's "not for clinical usage" card line is a binding Special-Purpose limitation;
  - CC BY-SA terms on locked device weights (CADS).
- **Clinical investigation liability:** compensation under r.55 / Drugs and Cosmetics Rules 122DAB, insurance availability, and indemnity in hospital agreements.
- **US filings:** small-business status for an Indian firm; US Agent; the current 524B guidance version (June 2025 vs Feb 2026, UNVERIFIED); whether the Pre-Sub will accept mixed Indian and US data.
- **EU:** confirm 2026/1744 on EUR-Lex; notified-body capacity.
- **Product liability and professional indemnity** for intra-op use; human-factors risk of reliance under time pressure.
- **Cyber and incident reporting:** CERT-In 6-hour reporting and 180-day log retention apply to the cloud service (UNVERIFIED this run).

### F. Corrections

1. **India report:** the r.63(1) fourth proviso now also covers **EU countries**, via G.S.R. 744(E) (14/19 Aug 2026; secondary). Its "UK, US, Australia, Canada, Japan" list is out of date.
2. **US/EU report:** the "third-party review pays no FDA user fee" route **does not apply**, because QIH is not eligible for third-party review (FDA classification page).
3. **US/EU report:** Axial3D K250369 was **received 10 Feb 2025**, not 19 Aug 2025. The review took about 7 months, not about 1.
4. **India and engineering reports:** the MDSW matrix cells are **contested among secondary sources** ("drives × serious" is reported as B in one and C in another). Do not present either as settled.
5. **India report:** "SUGAM is outdated for devices" is contradicted by CDSCO's own page, which lists Sugam for MD-22. Unresolved.
6. **India report:** add that a 23 Oct 2025 circular reportedly requires central classification before any State licence (secondary). This applies to the Class B route too.
7. **DPDP:** the Gazette is dated 13 Nov 2025, but PIB and Wikipedia say 14 Nov. Keep the 13 Nov-based dates (13 Nov 2026 and 13 May 2027) and confirm against the Gazette PDF.
8. **Engineering report:** it attributes MD-12/MD-22 to the CDSCO guidance. They come from **MDR 2017 r.31 and r.51**; the guidance only references them.
9. **PCCP:** both reports are consistent once stated precisely: final on 4 Dec 2024, page re-issued in August 2025, content current as of 18 Aug 2025, Final.
10. **NVIDIA:** the licence has **no clinical-use clause**. The restriction sits on the model card, and the licence's §6 places the burden of honouring card limitations on the user. "Not for clinical usage" is therefore more than a bare disclaimer for compliance purposes, and the recommendation to get written permission stands.
11. **MOOSE:** the README states no clinical restriction. The risk comes from per-site training-data licences and the existence of a paid commercial version (Zenta), not from the CC BY 4.0 grant itself.
12. **India timeline:** the proposed shorter licensing windows (Class B notified-body audit 90→30 days, C/D 45→30 days) are **draft only**. Keep the current statutory timelines.
13. **US/EU Option B** (organs with intra-op display from day one): valid as a US precedent, but it raises the Indian class to C, possibly D for oncology (CDSCO lists AI cancer-detection software on CT as Class C). It is not the lowest-risk first claim, so the bone-first claim in §B is preferred.

**Files read:** `docs/ct_platform/research/host.md` (§4). The K250369 PDF could not be text-extracted.