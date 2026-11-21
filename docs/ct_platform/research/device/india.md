# India pathway: CT segmentation and 3D planning software as a medical device (pre-op and intra-op)

**Scope and method.**
- Main legal text: the CDSCO consolidated copy of the Medical Devices Rules 2017 (MDR). I downloaded it and read the text: https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2022/m_device/Medical%20Devices%20Rules,%202017.pdf
- DPDP Rules: a mirror of the Gazette PDF of G.S.R. 846(E): https://dpdpa.com/DPDP_Rules_2025_English_only.pdf
- Both CDSCO software guidance PDFs (draft and final) are scanned images. I could not OCR them, so their content below comes from law-firm and consultancy summaries. Treat any table attributed to them as **UNVERIFIED against the PDF text**.
- Nothing here is legal advice. Confirm the class with CDSCO before filing (see §1.5).

---

## 0. Headline answers

| Question | Answer |
|---|---|
| Can it legally be a medical device in India? | Yes. Standalone software with a medical purpose is regulated under MDR 2017 as an "active" device, classed A–D. |
| Likely class: (a) pre-op planning | **Class C** if the claims cover critical conditions (oncology, cardiac, neuro, major vascular) and the output drives the surgical plan. **Class B** is possible with a narrow claim, e.g. orthopaedic or trauma bone models, or display-only "informs" claims. |
| (b) Intra-op display or reference | **Class C** (the surgical situation can be "critical"). If it drives or influences navigation or robot hardware, it takes **the hardware's class**. |
| (c) 3D-printed anatomical models | The physical model is **Class A** if it never touches the patient (First Schedule (iv)). It may be **Class B** if sterilised and placed in the surgical field (UNVERIFIED). A per-patient model made on a doctor's prescription may fall under the **custom-made exemption** (Eighth Schedule). The *software* that makes the model keeps its own class. |
| Who licenses | Class A/B manufacture: **State Licensing Authority** (MD-3 → MD-5). Class C/D manufacture: **CDSCO / Central Licensing Authority** (MD-7 → MD-9). Clinical investigation and test licences: always central. |
| Can doctors use it before the licence? | Yes, in three legal modes: retrospective reading studies (ethics committee only, UNVERIFIED interpretation), blinded "shadow" prospective use (ethics committee, UNVERIFIED), and **investigational use that affects care** (needs CDSCO MD-22/MD-23, a test licence, ethics approval, CTRI registration and compensation cover). Routine clinical use before the licence is **not** legal. |
| DPDP | The Rules were notified **13 Nov 2025** (G.S.R. 846(E)). Consent Manager rule in force since **13 Nov 2026**. The core duties start **13 May 2027**. |

---

## 1. Classification

### 1.1 Legal basis in MDR 2017

**First Schedule, Part I, basic principles (MDR PDF p.54):**
- (i) Classification "shall be governed by the intended purpose of the device".
- (iii) Software that drives or influences the use of a device falls automatically in that device's class.
- (v) If several rules apply, the strictest one applies (the higher class wins).

**Software is "active".** MDR r.3(d) defines an "active medical device" as one that depends on electrical or other non-body energy. Software therefore falls under the active-device rules.

**Rule (xiii), active diagnostic devices:**
- Class B by default.
- Class C when "diagnosing in a clinical situation where the patient is in immediate danger".
- Rule (xiv): other active devices are Class A.
- These rules were written for hardware. The 2026 guidance therefore adds a separate matrix for standalone software.
- Source: MDR PDF pp.59–60.

**Rule (iv), other non-invasive devices:**
- Class A "if it does not come into contact with a person or comes into contact with intact skin only".
- This is the relevant rule for a 3D-printed model kept out of the surgical field (MDR PDF p.55).

### 1.2 CDSCO software guidance

**Draft (21 Oct 2025).**
- Date confirmed by the CDSCO file name: https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadPublic_NoticesFiles/Draft%20guidance%20document%20on%20Medical%20Device%20Software%2021%2010%202025.pdf
- It treats software as an active device (summary: https://www.pureglobal.com/news/india-cdsco-medical-device-software-guidance-2026).

**Final guidance.**
- It exists: "Guidance Document on Medical Device Software", **Doc No. CDSCO/MD/GD/MDSW/01/2026**.
- Covering circular F.No. MED-16028/2/2025-eoffice, dated **21 July 2026** (https://natlawreview.com/article/code-compliance-decoding-indias-medical-device-software-guidance).
- PDF: https://cdsco.gov.in/opencms/export/sites/CDSCO_WEB/Pdf-documents/Guidance-document-on-Medical-Device-Software-under-MDR-2017.pdf (scanned; text not verified by me).
- It merges SaMD and SiMD into one term, "MDSW" (https://meddeviceguide.com/blog/india-cdsco-medical-device-software-mdsw-guidance-2026-guide).
- It states that it reflects current MDR practice and is "not a new control" (same source).

**Standalone MDSW matrix (as reported by LexOrbis and MedDeviceGuide; UNVERIFIED against the PDF):**

| Significance of information → / Situation ↓ | Treat or diagnose | Drives clinical management | Informs clinical management |
|---|---|---|---|
| Critical | **D** | **C** | B |
| Serious | C | B | A |
| Non-serious | B | A | A |

- Sources: https://www.lexorbis.com/cdsco-issues-guidance-on-medical-device-software-under-the-medical-devices-rules-2017/ and https://meddeviceguide.com/blog/india-cdsco-medical-device-software-mdsw-guidance-2026-guide
- Reported examples of "critical" conditions include malignant neoplasm detection and stroke triage (MedDeviceGuide).
- Software used by a non-clinician in a serious situation without specialist support may be treated as critical (LexOrbis).
- Software that measures a parameter (e.g. Cobb angle) cannot use the Class A registration-only route (MedDeviceGuide).

**Exclusions (LexOrbis, NatLaw):**
- Software that only stores, transfers, formats or changes how data is presented.
- PACS, HIS and LIS **unless** they add medical functions such as diagnostic image analysis.
- Software used solely for teaching or training.
- Consequence for this product: AI segmentation plus 3D models for surgery is **inside scope**. A "viewer only" claim does not escape regulation once the product segments anatomy for surgical use.

**AI-specific expectations (LexOrbis, NatLaw):**
- Disclose the composition of training, validation and test datasets, including demographics and geographic origin.
- **Justify applicability to Indian populations**, with Indian validation where needed.
- Address bias and robustness.
- Monitor after launch for drift and error rates.
- An optional Algorithm Change Protocol.
- Major vs minor change rules. Major changes (claims, input types, safety-relevant versions) need prior approval.
- For SaaS, state whether hosting is on a MeitY-empanelled cloud. This is reported as a case-by-case information request, not a hosting mandate (MedDeviceGuide).

### 1.3 Class anchors from CDSCO lists

**CDSCO radiology classification list (reported as 13 Sep 2021):**
- "Medical image storage device": A.
- "Medical image analyzer" (flags possible abnormality): **B**.
- "Radiological computer-assisted diagnostic software for lesions suspicious of cancer": **C**.
- "Radiological computer aided triage and notification software": **C**.
- No entry for planning, 3D or navigation.
- Source: https://pharmadocx.com/cdsco-classification-of-radiology-medical-devices/ (secondary; official list UNVERIFIED).

**Jan 2026 reporting:** CDSCO notified that AI software detecting or diagnosing cancer from X-ray or CT is **Class C** (https://medicaldialogues.in/news/health/ai-based-cancer-diagnostic-software-to-come-under-centres-regulatory-oversight-162267, article dated 8 Jan 2026; notification number UNVERIFIED).

**Interventional Radiology list (27 Nov 2025):** https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadPublic_NoticesFiles/Interventional%20Radiology%20Risk%20Classification%20List_27.11.2025.pdf (scanned; entries UNVERIFIED).

### 1.4 Classification of each function (reasoning, not a CDSCO ruling)

**(a) Pre-op segmentation and 3D visualisation for planning.**
- Intended use: help the surgeon plan the approach, resection or implant.
- Information significance: "drives clinical management". It is used to aid treatment decisions; the IMDRF definition matches (https://www.imdrf.org/sites/default/files/docs/imdrf/final/technical/imdrf-tech-140918-samd-framework-risk-categorization-141013.pdf).
- Critical-condition claims (tumour, cardiac, neuro) → **C**.
- Serious conditions only (e.g. fracture or orthopaedic planning) → **B**.
- If the product outputs volumes, distances or margins presented as treatment-determining, CDSCO may read it as "treat or diagnose" → **D** for critical conditions.
- **Recommendation:** plan for Class C. A narrow first claim ("bone 3D models for orthopaedic planning", which mirrors the plan's P3) could be argued as B and licensed by the state.

**(b) Intra-op display or reference.**
- The surgery is underway, so the situation is arguably "critical", or under rule (xiii)(c)(2) a patient "in immediate danger" → **C**.
- If it feeds a navigation system or robot, or registers to the patient (drives or influences hardware), principle (iii) puts it in **the hardware's class**. Navigation-system class in India is UNVERIFIED.
- Keep the intra-op mode as passive display of the pre-op plan at launch.

**(c) 3D-printed patient-specific anatomical models.**
- Physical model kept outside the surgical field → **Class A** (rule (iv)).
- Sterilised and used in the sterile field or wound for fit-checking → likely B, as surgically invasive transient use (UNVERIFIED).
- **Custom-made exemption** (Eighth Schedule S.N.1): exempts a device from Chapters IV and V (manufacture and import licensing) when it is made to a qualified practitioner's written prescription, for the sole use of one patient, and labelled "custom made device". Mass-produced devices that are merely adapted are excluded (MDR PDF p.171; definition at r.3(r)).
- The exemption covers the *model*. It does **not** cover the segmentation software that generates it.
- Non-sterile, non-measuring Class A devices need only registration under Chapter IIIB, not a licence (LexOrbis; https://zamann-pharma.com/2026/10/19/cdsco-medical-device-registration-classification-licensing-approval-and-compliance-requirements/).
- For comparison, under FDA the software that makes "diagnostic use" anatomical models carries the clearance (LLZ), per the plan's host.md.

### 1.5 Confirming the class

- Applicants should first check CDSCO's published classification lists. If the product is not listed, they can ask for a classification through the online portal (LexOrbis).
- Do this before the MD-22 filing. Use the exact intended-use text.

---

## 2. Licensing route

### 2.1 Phase-in

- All medical devices came under licensing through G.S.R. 102(E) of 11 Feb 2020.
- Class A/B licensing became mandatory from **1 Oct 2022** (CDSCO circular quoted at https://medicaldialogues.in/news/industry/pharma/class-aclass-b-non-notified-medical-devices-must-enter-a-licencing-regime-effective-on-october-10-2022-96449; CDSCO FAQ https://cdsco.gov.in/opencms/export/sites/CDSCO_WEB/Pdf-documents/mdfaq24.pdf).
- Class C/D: a 42-month transition from 1 Apr 2020, i.e. **1 Oct 2023** (https://nishithdesai.com/research-and-articles/hotline/pharma-healthcare-update/dawn-of-a-new-era-all-medical-devices-in-india-to-be-regulated-from-april-2020-4400). The exact date is computed; UNVERIFIED.

### 2.2 Forms, authority and statutory timelines (MDR text unless stated)

| Step | Form (apply → grant) | Authority | Statutory timeline | Fee (Second Schedule, ₹) |
|---|---|---|---|---|
| Test licence to manufacture an investigational device for clinical investigation or testing | MD-12 → MD-13 (r.31) | Central | not stated in the summaries I read | 500 per device |
| Clinical investigation, pilot | MD-22 → MD-23 (r.51–52) | Central | decision or rejection within **90 days** (r.52) | 1,00,000 (waived for government-run or government-funded institutions, r.51(2) proviso) |
| Clinical investigation, pivotal | MD-22 → MD-23 | Central | 90 days | 1,00,000 |
| Permission for a device **without predicate** | MD-26 → MD-27 (r.63) | Central | **120 days + up to 30** | 50,000 |
| Manufacture, Class A/B | MD-3 → MD-5 (r.20) | **State** | Class A: 45 days, no pre-audit, Notified Body audit within 120 days after grant. Class B: NB audit within 90 days of application, report within 30 days, grant within 20 days of report (about **140 days** maximum) | 5,000 per site + 500 per device |
| Manufacture, Class C/D | MD-7 → MD-9 (r.21–25) | **Central** | inspection within **60 days** (r.23), grant within **45 days** of report (r.25) | 50,000 per site + 1,000 per device |
| Import (any class) | MD-14 → MD-15 | Central | n/a for an Indian manufacturer | USD amounts |

- Fee table source: MDR PDF pp.65–67. The PDF layout garbles some rows; verify against the official Gazette.
- **"Predicate device"** under MDR means a device "first time and first of its kind, approved... by the Central Licensing Authority" with similar intended use and design (r.3(zm)).
- **Investigational device** (r.3(x)): a device with **no Indian predicate**, or a licensed device claiming a new intended use or population.
- If no CDSCO-approved 3D planning or segmentation software exists as a predicate (UNVERIFIED; a CDSCO import licence for e.g. Materialise Mimics is not confirmed), the product is investigational and the route is: MD-22 investigation → MD-26/27 → MD-7/9.
- A device claiming substantial equivalence still needs CLA approval (r.51(5)).
- **No manufacturing licence of any class is granted for an investigational device without MD-27** (r.25(4)).
- Possible shortcut, r.63(1) 4th proviso: clinical investigation results may be waived if the device is approved in the **UK, US, Australia, Canada or Japan** and **marketed there for 2 years**. An undertaking to run a post-market investigation is required. **FDA 510(k) clearance plus 2 years of US marketing could therefore replace the Indian pivotal study.**
- MD-22 requirements for Indian-developed devices: "clinical investigation is required to be carried out in India right from Pilot clinical investigation" (Seventh Schedule, MDR PDF around p.130). Foreign-developed devices still need an Indian pivotal study except Class A.

### 2.3 Portals

- **NSWS** (nsws.gov.in): test licences MD-12/13 and MD-16/17.
- **Online System for Medical Devices** (cdscomdonline.gov.in): commercial, clinical and change filings.
- Both per MedDeviceGuide (https://meddeviceguide.com/blog/india-cdsco-medical-device-software-mdsw-guidance-2026-guide). LexOrbis also places MD-12/13 on NSWS.
- **SUGAM** is CDSCO's drugs portal. Some consultancies still say device filings go through SUGAM (https://blogs.seyecs.com/cdsco-medical-device-registration-process/). That is likely outdated for devices (UNVERIFIED).

### 2.4 Notified Bodies

- Notified Bodies are registered with CDSCO under r.13.
- Class B: the NB audits the site against the Fifth Schedule **before** grant (r.20(5)–(6)).
- Class A: the NB audits within 120 days **after** grant (r.20(4)).
- Class C/D: CDSCO Medical Device Officers inspect, or an NB with at least 2 years' experience (r.13(4), r.23).
- Reported registered NBs: Intertek India, TÜV Rheinland India, TÜV SÜD South Asia, DNV, BSI India, BSCIC (secondary source, https://www.freyrsolutions.com/ja/node/1173; the live list is on cdsco.gov.in, UNVERIFIED).
- CDSCO NB registration notice: https://www.cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadPublic_NoticesFiles/notified_body_notice_70.pdf

### 2.5 After the licence

- PSUR every 6 months for the first 2 years, then yearly for 2 years.
- SUSAR and recall reporting within 15 days.
- Source: MedDeviceGuide, summarising the guidance and r.65 (UNVERIFIED against text).
- Changes are filed through the online system; major changes need prior approval (LexOrbis).

---

## 3. Clinical investigation and pre-licence doctor use

### 3.1 MDR 2017 Chapter VII (rules 49–62; Chapter VIII r.63–65 covers no-predicate permission)

- **r.49:** no clinical investigation of an investigational device in human participants without Central Licensing Authority permission.
- **r.50:** ethics committees constituted under r.122DD of the Drugs and Cosmetics Rules 1945 act under MDR. NDCT Rules 2019 have since replaced the drug-trial EC regime.
- **In practice:** EC registration is with CDSCO under NDCT Rules 2019 r.8 (Form CT-01, via the Naitik portal), plus DHR's NECRBHR registry for biomedical health research ECs.
  - NDCT Rules: https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2022/new_DC_rules/23NEW%20DRUGS%20AND%20CLINICAL%20TRIALS%20RULES,%202019.pdf
  - Registry overview: https://www.ovid.com/jnls/iphr/fulltext/10.4103/ijp.ijp_216_22~overview-of-the-national-ethics-committee-registry-for
  - Applying NDCT r.8 to device ECs is UNVERIFIED.
- **r.51:** apply on **MD-22** with Seventh Schedule information.
  - **Pilot** = "clinical investigation to be carried out for the first time in human participants" (exploratory: safety, feasibility, endpoints).
  - **Pivotal** = definitive or confirmatory study based on pilot data (Seventh Schedule paras 6–7).
- **r.51(3):** no CDSCO permission is needed for an **"academic clinical study" on a *licensed* device** if the EC approves and the data are **not** used for a marketing application. This is **not available before you hold a licence.**
- **r.51(4) and r.63 proviso:** CDSCO may abbreviate, defer or waive clinical data in the public interest.
- **r.52:** grant on **MD-23** or reject within 90 days.
- **r.53 conditions:**
  - start only after the registered EC approves the investigation plan;
  - follow CDSCO GCP and the Seventh Schedule;
  - **register on CTRI before the first participant**;
  - annual status reports;
  - SUSAE reports within 14 days;
  - medical management or compensation for injury;
  - first enrolment within 1 year.
- **r.55:** medical management for any injury. Compensation for device-attributable injury or death is paid "in the manner as specified under rule 122DAB of the Drugs and Cosmetics Rules". The Seventh Schedule informed-consent template must state the compensation and insurance terms; the application includes an "appropriate insurance certificate, if any" (MDR PDF around pp.150–160).
- **Test licence:** the device used in the study is made under an **MD-13 test licence** (r.31).
- **ICMR guidance:**
  - National Ethical Guidelines 2017 (waiver of consent for retrospective or anonymised data needs EC approval and a stated rationale): https://ethics.ncdirindia.org/asset/pdf/ICMR_National_Ethical_Guidelines.pdf
  - ICMR AI Ethics Guidelines 2023: https://www.icmr.gov.in/icmrobject/custom_data/pdf/Ethical-guidelines/Ethical_Guidelines_AI_Healthcare_2023.pdf. Reported to allow consent waiver for anonymised retrospective AI studies while keeping IEC approval mandatory (https://pmc.ncbi.nlm.nih.gov/articles/PMC13694149; UNVERIFIED against the ICMR text).

### 3.2 Pre-licence modes for doctors to use the software and give feedback

| Mode | What doctors do | Approvals | Can the data support the licence? |
|---|---|---|---|
| **1. Retrospective reader study** | Surgeons and radiologists rate segmentations and 3D models on archived, de-identified CTs of already-treated patients. Feedback is collected on a form. No patient's care is affected. | Institutional EC approval with consent waiver (ICMR 2017 and 2023). Data-sharing agreement with the hospital. **CDSCO permission arguably not needed**: a "clinical investigation" is a study "in or on human participants" (r.3(l)), and this is a data-only study. UNVERIFIED interpretation; get a written view from CDSCO. | Yes, as analytical and clinical-performance validation (Indian data), which the guidance expects (LexOrbis). |
| **2. Prospective blinded "shadow" use** | The software runs on new patients' CTs. Surgeons plan with standard tools. 3D output is shown only **after** surgery, or is reviewed by a non-treating panel. | EC approval. Patient consent or a waiver per the EC. CTRI registration advisable. CDSCO permission arguably not needed because the output does not reach the treating team before decisions are made (UNVERIFIED). | Yes, supportive. |
| **3. Investigational use in pre-op planning or intra-op** | The treating surgeon sees and may act on the output. This is the "use before or during surgery" the founder wants. | **Full Chapter VII route:** MD-12/13 test licence, MD-22/23 permission, registered EC, CTRI registration, written informed consent, compensation and insurance, SAE reporting, CDSCO GCP. | Yes. This is the pilot or pivotal evidence for MD-26/27. |
| **4. Post-licence academic studies** | Doctors study new uses of a licensed version. | EC only (r.51(3)), provided the data are not used for marketing. | No, unless filed under MD-22. |
| **5. Custom-made 3D models** | A hospital prints a model on a surgeon's prescription. | Device exempt from manufacturing licence (Eighth Schedule). The software used is still regulated if it has a medical purpose. | n/a |

- Software exposed to clinicians in any mode must say **"Investigational device – for clinical investigation only"**.
- Lock model versions per study. An unlocked, updating model breaks both GCP and the change-control rules.

---

## 4. Quality management system (Fifth Schedule)

- The **Fifth Schedule** QMS covers design and development, manufacture, labelling, testing, installation and servicing (MDR PDF p.101).
- Its clause structure follows **ISO 13485:2003**: 4 QMS, 5 management responsibility, 6 resources, 7 product realisation including 7.3 design control and software validation, 8 measurement and analysis.
- Applicants submit a QMS undertaking (r.20(3)), or a notarised QMS certificate for overseas sites (NatLaw).
- Standards hierarchy: BIS first, then ISO/IEC, then validated manufacturer standards (r.7).
- The software guidance lists **ISO 13485, ISO 14971, IEC 62304** (and IEC 82304-1 / IEC 81001-5-1 for cybersecurity; UNVERIFIED which are listed) as "may be applicable" (MedDeviceGuide).

**Audits:**

| Class | Audit |
|---|---|
| A | NB audit within 120 days after grant |
| B | NB audit before grant |
| C/D | CDSCO inspection (2+ Medical Device Officers, possibly with an expert) or an experienced NB, before grant (r.23) |
| All | Unannounced inspections possible (r.28, r.20(8)). Clinical investigation sites and the sponsor are open to inspection (r.53(vii)). |

- For a cloud product, the "manufacturing site" is the company's design and development and release site. How inspectors treat a pure-SaaS site is UNVERIFIED.

---

## 5. Data law

### 5.1 DPDP Act 2023 and DPDP Rules 2025

**Dates.**
- Act: https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf
- Rules notified **13 Nov 2025, G.S.R. 846(E)** (Gazette text mirror: https://dpdpa.com/DPDP_Rules_2025_English_only.pdf).
- Rule 1 commencement:
  - Rules 1, 2 and 17–21 on publication;
  - **Rule 4 (Consent Managers) one year later, i.e. 13 Nov 2026, now in force**;
  - **Rules 3, 5–16, 22 and 23 eighteen months later, i.e. 13 May 2027.**
- Corrigendum G.S.R. 892(E), 10 Dec 2025 (https://www.easydp.in/dpdp-rules-2025/; UNVERIFIED).
- The MeitY page that should host the official PDF returned 403 to me.

**Health data.** The DPDP Act has **no "sensitive" category**. Health data is ordinary personal data, but the founder as Data Fiduciary owes the full duties.

**Rule 3 / s.5–6: notice and consent.** Consent must be specific and informed. In a hospital deployment, the hospital is usually the fiduciary and the founder its processor under contract. If the founder collects scans directly, the founder is the fiduciary.

**Rule 6: security safeguards.** Encryption, access control, logs.

**Rule 7: breach notice.** Notify affected individuals, and notify the Board "without delay" plus a detailed report **within 72 hours**.

**Rule 8(3): log retention.** Keep personal data, traffic data and logs for **at least 1 year**.

**Rule 10 / Fourth Schedule: children.**
- Verifiable parental consent is required.
- Exemption: clinical establishments and healthcare professionals, limited to "provision of health services to the child... to the extent necessary for the protection of her health".
- This exemption likely does **not** cover a software vendor's own product purposes.

**Rule 13(4): Significant Data Fiduciaries.** Data specified by a government committee must not leave India. This is targeted localisation, and only applies if the founder is designated an SDF.

**Rule 15: cross-border.** Transfer is allowed subject to government orders on foreign states or their agencies (a negative-list approach). No countries are restricted yet (host.md; https://www.dpdpa.com/dpdparules/rule15.html).

**Rule 16 + Second Schedule: research exemption.** The Act does not apply to processing "necessary for research, archiving or statistical purposes" done per the Second Schedule standards. This is useful for Mode 1 and 2 studies from 13 May 2027.

**Consent Managers (Rule 4).**
- Registration with the Board opened via dpbi.gov.in (https://captaincompliance.com/education/india-meity-consent-manager-registration-portal/).
- Optional for a fiduciary, but can be integrated.

**Penalties.** Up to **₹250 crore** per breach for failing to take security safeguards (Act Schedule, MeitY PDF above).

### 5.2 Other overlays

**ABDM (NHA).**
- A federated model: ABHA, the HFR and HPR registries, and a consent manager (HIE-CM).
- The draft Health Data Management Policy expects storage in India: https://beta.medianama.com/wp-content/uploads/2022/06/Draft_HDM_Policy_April2022_e38c82eee5.pdf. Final policy text UNVERIFIED.
- ABDM integration is optional for a device vendor. Hospitals increasingly ask for it.

**CERT-In Directions (28 Apr 2022).**
- Report cyber incidents within 6 hours; keep logs for 180 days within Indian jurisdiction.
- https://www.cert-in.org.in/PDF/CERT-In_Directions_70B_28.04.2022.pdf (UNVERIFIED in this run).

**Hosting.**
- No general health-data localisation law was found.
- Hospitals and the CDSCO dossier expect India-region hosting. Disclose whether the cloud is MeitY-empanelled (MedDeviceGuide).
- Use Google Cloud asia-south1 (Mumbai) and asia-south2 (Delhi). GCP's MeitY empanelment status is UNVERIFIED.

**Contracts.** A data processing agreement with each hospital, EC-approved protocols, a DPIA (mandatory only for SDFs, still best practice), and de-identification at the edge (per host.md §4).

---

## 6. Indian precedents

| Company / product | What is verified | Source |
|---|---|---|
| Qure.ai (qXR chest X-ray AI) | Its own page lists "Indian CDSCO class B approval", products unnamed. qXR is described as CDSCO-approved in a PATH/Unitaid release and as CDSCO-cleared in a peer-reviewed review. Year and form UNVERIFIED. | https://www.qure.ai/regulatory-and-privacy ; https://www.qure.ai/us/news-press-coverages/Qure-ai-Teams-Up-with-PATH-India-to-Implement-AI-Enabled-Chest-X-Ray-Solution-for-TB-and-Covid-19-Screening-in-Maharashtra-through-Unitaid-Funding ; https://pmc.ncbi.nlm.nih.gov/articles/PMC13694145 |
| DeepTek (Genki, Augmento, chest X-ray AI) | Self-claims "CDSCO Approved" and a manufacturing licence. Class and year UNVERIFIED; the page did not render for me. | https://www.deeptek.ai/regulatory (via search snippet) |
| Anatomiz3D (Mumbai) | 3D service bureau for anatomical models, guides and implants; point-of-care lab with Materialise. CDSCO status UNVERIFIED. | https://www.materialise.com/en/inspiration/articles/point-of-care-lab-india |
| Indian AI 3D surgical-planning software with a CDSCO licence | **None found.** Not found for Predible, Novabeans, Think3D or Osteo3D. | — |

- No Indian-made segmentation or 3D-planning software with a CDSCO licence was found. This raises the chance that the product is "investigational" (no predicate).
- Ask CDSCO whether imported products (Materialise Mimics/Enlight, Synopsys Simpleware, Axial3D) hold MD-15 import licences; any that do could act as predicates. UNVERIFIED.

---

## 7. Staged India timeline with rough costs

**Costs are ESTIMATES unless they cite a Second Schedule fee.**

| Stage | Months (from Dec 2026) | Activities | Rough cost (₹) |
|---|---|---|---|
| S0. Company and intended use | 0–3 | Incorporate (MDR licences go to a firm). Freeze a narrow intended use, e.g. "pre-op 3D bone models, orthopaedic trauma". Ask CDSCO for classification through the portal. Hire a regulatory consultant. Start QMS (Fifth Schedule / ISO 13485, ISO 14971, IEC 62304). Lock model versions. Check licences of the segmentation models. | 5–15 lakh (consultant and QMS setup, estimate) |
| S1. Retrospective reader study (Mode 1) | 2–8 | One or two hospital partners with registered ECs. Consent waiver. De-identified CTs processed in an India region. 3–5 surgeons rate outputs. Dice and NSD against expert masks. Usability (IEC 62366). | 3–10 lakh (reader honoraria, annotation, EC fees, estimate) |
| S2. Shadow prospective study (Mode 2) | 6–12 | Blinded parallel use. Register on CTRI. Collect feedback and failure cases. Draft the clinical evaluation report. | 5–15 lakh (estimate) |
| S3. Test licence + pilot investigation (Mode 3) | 9–18 | **MD-12/13** (₹500 per device). **MD-22 pilot** (₹1,00,000; decision ≤90 days; fee waived if the sponsor is a government institution, so consider a government-hospital PI). Insurance. Surgeons actually use the output pre-op and intra-op. | Fees about 1 lakh, plus study, insurance and monitoring 10–30 lakh (estimate) |
| S4. Pivotal investigation | 15–27 | **MD-22 pivotal** (₹1,00,000). Multicentre, Indian population, endpoints agreed with CDSCO. *Alternative:* FDA 510(k) plus 2 years of US marketing lets the r.63 proviso waive Indian clinical investigation results. | 25–80 lakh (estimate) |
| S5. Marketing permission + manufacturing licence | 24–33 | **MD-26/27** if no predicate (₹50,000; 120 + 30 days). Then **MD-7/9** for Class C (₹50,000 per site + ₹1,000 per device; inspection ≤60 days, grant ≤45 days after report), or **MD-3/5** for Class B (₹5,000 + ₹500; NB audit; about 140 days). ISO 13485 certification and NB audit. | Fees about 1 lakh, plus certification and audit 3–8 lakh (estimate) |
| S6. Post-market | 30+ | PSURs (6-monthly for 2 years), drift monitoring, Algorithm Change Protocol, change approvals, licence retention fees (r.29). Add intra-op and navigation claims as **new intended uses** with new MD-22 studies. | ongoing |

**Indicative total to a Class C licence: about ₹50 lakh–1.6 crore over about 24–33 months (ESTIMATE).**

A Class B-only narrow claim with an existing predicate could skip MD-26 and possibly the pivotal study, giving about 12–18 months (ESTIMATE; depends on CDSCO accepting a predicate and the class).

**Main uncertainties to resolve with CDSCO in writing:**
1. Class for the exact intended-use text.
2. Whether a predicate exists.
3. Whether Mode 1 and 2 studies need MD-22.
4. Whether a pure-SaaS "manufacturing site" is inspected and what that inspection covers.
5. The hosting information required (MeitY empanelment).
