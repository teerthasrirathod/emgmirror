# CT-to-3D as a medical device: US (FDA) and EU (MDR) pathways and the closest cleared products (researched 2026-11-21)

**How this was checked.** I read eleven FDA 510(k) PDFs in full (downloaded, then extracted with `pdftotext`) and the full MDCG 2019-11 Rev.1 PDF. Everything else comes from web search. Where only secondary sources were found, the item is marked as secondary, and anything not confirmed is marked UNVERIFIED. I did not read anything in 01_clinical_CT/, results/ or data/, and I modified no files.

---

## 0. Bottom line
- **A medical device is achievable, and there is a close precedent.** Two 2025 FDA clearances are almost exactly the product described:
  - **Axial3D Insight, K250369** (cleared 18 Sep 2025): product code QIH, regulation 21 CFR 892.2050. It is a cloud-based ML segmentation service that produces 3D models for treatment planning and diagnostic use. FDA cleared it **with a PCCP** (a pre-approved plan for model updates).
  - **InferOperate Suite, K250237** (cleared 15 Sep 2025): also QIH. It does ML segmentation of adult CT chest, abdomen and pelvis, and its indication covers **"preoperative surgical planning and intraoperative image display"**. It also has a cleared PCCP.
  - Sources: https://www.accessdata.fda.gov/cdrh_docs/pdf25/K250369.pdf, https://www.accessdata.fda.gov/cdrh_docs/pdf25/K250237.pdf
- **Before and during surgery:**
  - Showing the 3D model in the operating room **as a reference image with no tracking** has a QIH precedent (K250237). It stays a 510(k) under 892.2050.
  - Anything that **registers the model to the patient or tracks instruments** is a stereotaxic or navigation device under **21 CFR 882.4560** (product codes HAW for cranial, OLO for orthopaedic). That is a different product class, usually built with hardware.
  - Sources: K221087 https://www.accessdata.fda.gov/cdrh_docs/pdf22/K221087.pdf and K252054 https://www.accessdata.fda.gov/cdrh_docs/pdf25/K252054.pdf
- **EU:** under MDR Rule 11, software for surgical planning is realistically **Class IIb**. That requires a notified body.
  - The AI Act also applies on top, because a device that needs a notified body counts as high-risk AI under Article 6(1).
  - The original date for those obligations was 2 Aug 2027. Secondary sources say Regulation (EU) 2026/1744 moved it to **2 Aug 2028** (UNVERIFIED against the Official Journal).

---

## 1. FDA

### 1.1 Classification map

| Function | Regulation | Product code | Class / route | Evidence |
|---|---|---|---|---|
| Image review and processing, segmentation, 3D visualisation, planning (manual or semi-automatic) | 21 CFR 892.2050 "Medical image management and processing system" | **LLZ** | II, 510(k) | eCFR https://www.ecfr.gov/current/title-21/chapter-I/subchapter-H/part-892/subpart-B/section-892.2050 ; K212896, K173619, K223427 (letters) |
| Automated or ML image processing (e.g. AI segmentation) | 21 CFR 892.2050 | **QIH** "Automated Radiological Image Processing Software" | II, 510(k) | K250369, K250237 letters; https://fda.innolitics.com/productcode/QIH . One secondary source says QIH excludes *adaptive* (continuously learning) AI; UNVERIFIED |
| AI auto-contouring for radiotherapy | 892.2050 | **QKB** | II, 510(k) | K242745 letter https://www.accessdata.fda.gov/cdrh_docs/pdf24/K242745.pdf |
| 3D-printed patient-specific anatomical models for diagnostic use | 892.2050 | **LLZ** | II, 510(k) | Ricoh K220205 https://www.accessdata.fda.gov/cdrh_docs/pdf22/K220205.pdf |
| Stereotaxic / navigation, cranial | 21 CFR 882.4560 | **HAW** | II, 510(k) | K221087 (StealthStation), K223552 (Brainlab Trajectory Planning) https://www.accessdata.fda.gov/cdrh_docs/pdf22/K223552.pdf |
| Stereotaxic / navigation, spine and orthopaedics | 882.4560 | **OLO** | II, 510(k) | K251282 (StealthStation S8 Spine, Oct 2025) https://www.accessdata.fda.gov/cdrh_docs/pdf25/K251282.pdf |
| AR headset for planning and intra-op visualisation used with navigation | 882.4560 | **SBF** + LLZ | II, 510(k) | K252054 SpineAR SNAP letter. What SBF stands for is UNVERIFIED |
| Patient-matched planning software plus guides (CMF, ortho) | e.g. 872.4120 (DZJ), 888.3030 (PHX), PPT | DZJ / PHX / PPT | II | K223427 lists LLZ and DZJ; Blueprint PSI K232265 is PHX https://fda.innolitics.com/submissions/OR/subpart-d%E2%80%94prosthetic-devices/PHX/K232265 ; the VSP Cranial PPT code comes from an aggregator only (UNVERIFIED) |

- **What 892.2050 covers.** The regulation's identification paragraph covers reviewing and digitally processing images "for interpretation by a trained practitioner". It explicitly includes segmentation, registration and 3D visualisation. https://www.ecfr.gov/current/title-21/chapter-I/subchapter-H/part-892/subpart-B/section-892.2050

### 1.2 Showing a 3D model in the OR vs navigation
- **Display only (allowed under QIH).** InferOperate K250237 was cleared under QIH for "intraoperative image display". Its predicate is Visible Patient Suite K212896, which is LLZ and planning-only.
  - So adding intra-op viewing (no tracking, no registration to the patient, no guidance of instruments) did **not** move the device to 882.4560.
  - Source: K250237 PDF.
- **Navigation (882.4560).** Systems "intended as an aid for precisely locating anatomical structures… where reference to a rigid anatomical structure can be identified relative to images" are HAW or OLO (StealthStation K221087).
  - Brainlab Trajectory Planning is cleared under HAW even though it is *software for planning*, because it plans stereotactic instrument placement "pre-, intra- and postoperative". Source: K223552.
- **AR overlay with navigation.** SpineAR SNAP K252054 (882.4560) covers "intra-operative surgical planning and visualization… in an augmented environment… with validated navigation systems". Its labelling carries the caveat that the virtual display "should not be relied upon solely for absolute positional information". Source: K252054.
- **Practical line:**
  - Static or rotatable model on an OR monitor, used as a reference = QIH or LLZ.
  - Overlay registered to the patient, instrument tracking, or trajectory guidance = 882.4560. That usually means partnering with a navigation-hardware maker, plus accuracy testing (ASTM F2554 is the usual standard; UNVERIFIED in this run).

### 1.3 510(k) vs De Novo
- **510(k) is the route.** Several predicates exist under LLZ and QIH, including cloud and ML ones (K250369, K250237). A De Novo is only for devices with no predicate.
- **Fees:**

| Fee | FY2026 (standard / small business) | FY2027 (standard / small business) | Source |
|---|---|---|---|
| 510(k) | $26,067 / $6,517 | $28,653 / $7,163 | FY2026: https://www.federalregister.gov/documents/2025/07/30/2025-14412/medical-device-user-fee-rates-for-fiscal-year-2026 ; FY2027: https://www.federalregister.gov/documents/2026/07/30/2026-15335/medical-device-user-fee-rates-for-fiscal-year-2027 and https://www.fda.gov/industry/fda-user-fee-programs/medical-device-user-fee-amendments-mdufa-fees |
| De Novo | $173,782 / $43,446 | not retrieved (UNVERIFIED) | FY2026 Federal Register notice above |

- FY2027 is the period starting 1 Oct 2026, so it applies today.
- **Establishment registration:** about $11,423 in FY2026. This comes from a secondary source (https://www.complizen.ai/learn/fda-user-fees) and is UNVERIFIED. A secondary hint puts FY2027 at $13,785 (UNVERIFIED). Registration has no small-business discount.
- **Small-business status:** requires gross receipts of $100M or less, and the status must be obtained before submitting (secondary: https://www.complizen.ai/learn/fda-user-fees). For a firm outside the US, proof via the home tax authority's certification is UNVERIFIED.
- **Third-party review:** a 510(k) submitted through an FDA-accredited third-party reviewer pays no FDA user fee (FY2027 fees page above).
- **Review time:**
  - The MDUFA V goal is 112 days average total time to decision for FY2025–2027, and 95% of decisions within 90 FDA days. https://www.fda.gov/media/158308/download
  - Actual 2025 median: 126 calendar days overall, and 142 days for a Traditional 510(k). This is secondary (https://meddeviceguide.com/blog/fda-510k-clearances-timelines-statistics-tracking-guide).
  - Axial3D's PCCP 510(k) took about 1 month from receipt (19 Aug 2025) to clearance (18 Sep 2025). That is the "Received" date of the final version; the full review history is UNVERIFIED. Source: K250369 letter.
- **Total cost:** roughly $75k–$300k all-in for a software 510(k), with consultant preparation alone at $15k–$80k. Both figures are secondary and UNVERIFIED (https://meddeviceguide.com/blog/how-much-does-510k-cost-guide). Clinical or reader validation studies are what drive the cost.
- **Quality system:** the QMSR has been in force since 2 Feb 2026. 21 CFR 820 now incorporates ISO 13485:2016 by reference, and an ISO 13485 certificate does not exempt a company from FDA inspection. https://www.fda.gov/quality-systems-regulation

### 1.4 PCCP (pre-approved plan for AI model updates)
- **Guidance:** "Marketing Submission Recommendations for a PCCP for AI-Enabled Device Software Functions".
  - It was issued as final on 4 Dec 2024. FDA's page now shows **August 2025 (content current as of 18 Aug 2025)**, status Final, docket FDA-2022-D-2628.
  - It applies to 510(k), De Novo and PMA.
  - Sources: https://www.fda.gov/regulatory-information/search-fda-guidance-documents/marketing-submission-recommendations-predetermined-change-control-plan-artificial-intelligence ; Ropes & Gray (Dec 2024) https://www.ropesgray.com/en/insights/alerts/2024/12/fda-finalizes-guidance-on-predetermined-change-control-plans-for-ai-enabled-device
- **What a PCCP contains:** three parts: a Description of Modifications, a Modification Protocol, and an Impact Assessment. Changes outside the PCCP still need a new 510(k) under 21 CFR 807.81(a)(3). Sources: K&S https://www.kslaw.com/insights/articles/fda-publishes-final-predetermined-change-control-plan-guidance-for-ai-enabled-device-software-functions ; K250369 letter, p.2.
- **Precedent:** both K250369 and K250237 carry cleared PCCPs (letter text: "FDA's substantial equivalence determination also included the review and clearance of your Predetermined Change Control Plan").
  - Axial3D used a Traditional 510(k) whose stated purpose was to get the PCCP approved.
  - This is the template for "doctor feedback leads to model retraining".
- **Companion lifecycle guidance:** "AI-Enabled Device Software Functions: Lifecycle Management and Marketing Submission Recommendations" (https://www.fda.gov/media/184856/download). It was still a draft at last report; current status UNVERIFIED.

### 1.5 Cybersecurity (section 524B)
- **Who it covers:** any "cyber device" (software that connects to the internet). That includes a cloud-hosted product.
- **What must be submitted:**
  - a plan to monitor and address vulnerabilities after release;
  - processes that give reasonable assurance the device is secure;
  - an SBOM (software bill of materials) covering commercial, open-source and off-the-shelf components.
- **Refuse to accept:** FDA can refuse a submission that lacks this content (since 1 Oct 2023).
- **Guidance versions:** the guidance was revised in June 2025 (adding a section on cyber devices). FDA's page reportedly lists a February 2026 version aligned with the QMSR (UNVERIFIED which version is current).
- Sources: https://www.fda.gov/regulatory-information/search-fda-guidance-documents/cybersecurity-medical-devices-quality-management-system-considerations-and-content-premarket ; https://www.fda.gov/medical-devices/medical-device-cybersecurity/cybersecurity-resources-medical-device-manufacturers-guidance-resources-and-faqs
- **For a Google Cloud product, expect to submit:** a threat model, a security risk assessment, the SBOM with support status for every component, penetration testing, and a plan for vulnerabilities after release.

### 1.6 Patient-specific anatomical models (3D printing)
- **FDA guidance:** "Technical Considerations for Additive Manufactured Medical Devices" (Dec 2017) is still listed on FDA's 3D printing page. FDA reviews the *software* that makes the model for accuracy, not the printer. https://www.fda.gov/medical-devices/products-and-medical-procedures/3d-printing-medical-devices ; Pew (2022) https://www.pew.org/en/research-and-analysis/issue-briefs/2022/07/fdas-regulatory-framework-for-3d-printing-of-medical-devices-needs-more-clarity
- **FDA–RSNA collaboration:**
  - FDA and RSNA held a joint meeting on patient-specific models on 31 Aug 2017.
  - The **RSNA–ACR 3D Printing Registry** collects outcome data from hospitals that print models at the point of care.
  - RSNA's 3D printing special interest group publishes guidelines on when models are appropriate for diagnostic use.
  - Same Pew source; RSNA SIG guidelines: https://www.researchgate.net/publication/329150334
  - Any newer FDA guidance specific to anatomical models is UNVERIFIED (none found).
- **How "diagnostic use" of a printed model is cleared:**
  - The software's indication says its output file can be printed, and the physical model can be used "for diagnostic purposes" in named specialties (Mimics inPrint K173619, Axial3D K250369).
  - Printers must be validated with the software (Mimics inPrint certified specific printers: https://www.prnewswire.com/news-releases/ultimaker-gains-certification-as-part-of-materialises-510k-cleared-mimics-inprint-software-300754387.html).
  - Ricoh cleared the *models* themselves (K220205), based on output from FDA-cleared software.

---

## 2. EU MDR and the AI Act

- **Rule 11** (MDR Annex VIII; text in MDCG 2019-11 Rev.1, June 2025, https://health.ec.europa.eu/document/download/b45335c5-1679-4c71-a91c-fc7a4d37f12b_en?filename=mdcg_2019_11_en.pdf):
  - Software that provides information for diagnostic or therapeutic decisions is Class IIa.
  - It is **IIb** where a wrong decision could cause "serious deterioration of a person's state of health or surgical intervention".
  - It is III where it could cause death or irreversible deterioration.
  - MDCG says sub-rule 11a applies to essentially all medical device software. Rev.1 adds a clarification to 11a.
- **MDCG Table 1 (aligned with IMDRF):**
  - Critical condition plus "treat or diagnose" = III.
  - Critical condition plus "drives clinical management" = IIb.
  - Serious condition plus "treat or diagnose" = IIb.
- **MDCG examples:**
  - Image analysis for treatment decisions in acute stroke = Class III.
  - Image-management systems that "alter image data or complex quantitative functions to aid in diagnosis" qualify as medical device software.
  - Modules "intended to influence the surgery procedure" are medical device software (telesurgery section).
  - MDCG gives no example specific to 3D surgical planning. **Classification of this product:**
- **Planning for elective orthopaedic or CMF surgery: IIb.** The information drives a surgical intervention. This is my inference from Rule 11 and Table 1, not a cited example.
- **Planning in critical settings** (e.g. oncologic resection, vascular, neuro): could argue towards III if errors could cause irreversible harm. This is an inference, UNVERIFIED.
- **Intra-op reference display:** stays IIb under Rule 11.
- **Navigation software that drives navigation hardware:** takes the class of that hardware under MDR Annex VIII implementing rule 3.3 (https://eur-lex.europa.eu/eli/reg/2017/745/oj ; not re-fetched in this run). Navigation systems are typically IIb (UNVERIFIED).
- **Notified body route:**
  - For IIa/IIb, a notified body audits the quality system (ISO 13485) and assesses the technical documentation, usually under Annex IX. For IIb, the technical documentation is sampled per generic device group (MDR Art. 52 and Annex IX; https://eur-lex.europa.eu/eli/reg/2017/745/oj ; not re-fetched, so details UNVERIFIED).
  - A clinical evaluation under Art. 61 and post-market clinical follow-up (PMCF) are required.
  - A manufacturer outside the EU needs an EU Authorised Representative (Art. 11; UNVERIFIED in this run).
- **AI Act overlap:**
  - A device that is, or contains, an AI system and needs a notified body under the MDR is high-risk AI under Article 6(1). Class I self-certified devices fall outside this. The MDR notified body can also assess the AI Act requirements in the same procedure (Art. 43(3)).
  - Source: MDCG 2025-6 / AI Board FAQ https://health.ec.europa.eu/document/download/b78a17d7-e3cd-4943-851d-e02a2f22bbb4_en?filename=mdcg_2025-6_en.pdf
- **AI Act dates:**
  - Original: Article 6(1) obligations from **2 Aug 2027** (Art. 113(c)).
  - **Digital Omnibus on AI, Regulation (EU) 2026/1744:** published in the Official Journal on 24 Jul 2026 and in force from 27 Jul 2026, according to secondary sources. It reportedly moves Annex I high-risk obligations (including medical devices) to **2 Aug 2028**. Standalone Annex III systems move to 2 Dec 2027.
  - Sources: https://www.regulation-ai.eu/en/omnibus/ , https://www.progressio.ai/resources/digital-omnibus-eu-ai-act-high-risk-timeline/ , https://blog.atta.systems/eu-ai-act-omnibus-medical-device-software/
  - **UNVERIFIED against EUR-Lex.** One source says the 2028 date is conditional.
  - Plan for 2 Aug 2028, and build the AI Act documentation (data governance, logging, human oversight, accuracy and robustness) into the MDR technical file now.

---

## 3. Cleared comparators

All are FDA Class II, prescription use. The intended-use wording below is verbatim from the 510(k) PDFs where marked (PDF).

| Product (K-number, cleared) | Code | Intended-use wording | Body regions | AI? | Intra-op? |
|---|---|---|---|---|---|
| **Axial3D Insight K250369 (18 Sep 2025)**, predicate K232841 (itself based on K222745) | QIH | (PDF) "intended for use as a cloud-based service and image segmentation framework for the transfer of DICOM imaging information from a medical scanner to an output file… output file can be used for the fabrication of physical replicas… using additive manufacturing methods. The output file or physical replica can be used for treatment planning. The output file or the physical replica can be used for diagnostic purposes in the field of trauma, orthopedic, maxillofacial and cardiovascular applications… should be used in conjunction with other diagnostic tools and expert clinical judgment." | Ortho, trauma, CMF, cardiovascular; CT/CTA | **Yes.** "AxialML" gives the initial segmentation, which **Axial3D trained staff complete and validate**; the ML output is not used alone. **PCCP cleared.** Validation: 3 radiologists × 12 cases (RADPEER scoring), and 9 physicians × 12 cases for intended use | No |
| **InferOperate Suite K250237 (15 Sep 2025)**, Infervision; predicate Visible Patient Suite K212896 | QIH | (PDF) "tools to aid them in reading, interpreting, reporting, and treatment planning for patients, including both preoperative surgical planning and intraoperative image display… may include the generation of preliminary segmentations of anatomy using software that employs machine learning… intended to assist the clinician who is responsible for making all final patient management decisions… utilizes machine learning-based algorithms for adult patients undergoing CT chest, abdominal, or pelvic scans. For image data of other anatomical regions or modalities, patients under 21… we provide non-ML software functions." | Adult CT chest, abdomen, pelvis (ML); others non-ML | **Yes. PCCP cleared.** Deployable on-premises or in the cloud | **Yes, display only** |
| Visible Patient Suite K212896 (5 Nov 2021); first cleared K151988 (2015) | LLZ | (PDF) "tools to aid them in reading, interpreting, reporting, and treatment planning for both pediatric and adult patients… custom UI to segment anatomical structures… (bones, organs, vascular/airway structures)… detection and labeling tools of organ segments (liver, lungs and kidneys)… intended to assist the clinician who is solely responsible for making all final patient management decisions." Not for mammography | Whole body; CT, MR; adults and children | Interactive segmentation (not described as ML) | No |
| Materialise Mimics inPrint K173619 (decided 21 Mar 2018) | LLZ | (PDF) "software interface and image segmentation system for the transfer of DICOM imaging information from a medical scanner to an output file. It is also used as pre-operative software for treatment planning… output file can be used for the fabrication of physical replicas… The physical replica can be used for diagnostic purposes in the field of orthopedic, maxillofacial and cardiovascular applications… in conjunction with other diagnostic tools and expert clinical judgement." | Ortho, CMF, cardiovascular | No (at the time) | No |
| Materialise Mimics (original) K073468 (2008) | LLZ | "software interface and image segmentation system… for simulating/evaluating surgical treatment options" (via ScanIP K142779 PDF and search snippet) | General | No | No |
| Mimics Enlight K190874 (2019) | LLZ | "aid in reading and interpreting DICOM compliant images for structural heart and vascular treatment options… visualization and measurement tools to enable the user to screen and plan the procedure" (search snippet of https://www.accessdata.fda.gov/cdrh_docs/pdf19/K190874.pdf) | Structural heart, vascular | UNVERIFIED | No |
| Mimics Enlight CMF K223427 (31 May 2024); predicate K183105 | LLZ, DZJ | (PDF) "software interface and imaging segmentation system… also intended to support the diagnostic and treatment planning process of maxillofacial procedures… provides visualization, measurement and design tools… fabricated output can be used for diagnostic purposes in the field of maxillofacial applications." | CMF | UNVERIFIED | No |
| Synopsys Simpleware ScanIP K142779 (17 Apr 2015); predicate Mimics K073468 | LLZ | (PDF) "software interface and image segmentation system for the transfer of imaging information from a medical scanner such as a CT… or MRI… to an output file. It is also intended as pre-operative software for simulating/evaluating surgical treatment options. Not… for mammography." The 2021 "ScanIP Medical" extension to 3D printing is reported in news (https://3dprintingtoday.com/simpleware-scanip-medical-receives-fda-510k-clearance-for-3d-medical-printing/); its K-number is UNVERIFIED | General | No | No |
| Ricoh 3D Anatomic Models K220205 (letter signed 3 Jun 2022) | LLZ | (PDF) "physical replicas of patient anatomy to be used for diagnostic purposes in the fields of craniomaxillofacial and orthopedic applications… based on… output files from FDA cleared software… used in conjunction with other diagnostic tools and expert clinical judgement." | CMF, ortho | n/a (physical model) | No |
| Siemens AI-Rad Companion Organs RT K242745 (27 Mar 2025); earlier K193562 (2020) and K221305 (2022); K252548 (Apr 2026, secondary) | QKB | (PDF) "post-processing software intended to automatically contour DICOM CT and MR pre-defined structures using deep-learning-based algorithms… may be used as input for clinical workflows including external beam radiation therapy treatment planning… must be used in conjunction with… Treatment Planning Systems and Interactive Contouring applications, to review, edit, and accept contours… not intended to automatically detect or contour lesions." | Organs at risk, whole body | Yes | No |
| Fujifilm Synapse 3D Base Tools K221677 (10 Nov 2022); first K101662 (2010) | LLZ | "tools to aid them in reading, interpreting, and treatment planning… CT, MR, CR, US, NM, PT, XA" (https://fda.report/PMN/K221677 ; secondary) | General | Modules vary (UNVERIFIED) | No |
| Brainlab Elements 7.0 suite K243633 (13 Jun 2025; secondary) | QIH | "processing of medical images including… image co-registration, image segmentation, contouring"; Contouring 5.0 adds automatic segmentation of cranial tumours (https://xrayinterpreter.com/fda/K243633) | Cranial | Yes (tumour auto-segmentation) | Planning |
| Brainlab Elements Trajectory Planning K223552 (19 Apr 2023) | **HAW (882.4560)** | (PDF) "pre-, intra- and postoperative image-based planning and review of… neurosurgical… procedures… any medical condition in which the use of stereotactic surgery may be appropriate" | Cranial | No | **Yes (stereotactic)** |
| Medtronic StealthStation Synergy/Cranial K221087 (10 Jun 2022) | HAW | (PDF) "intended as an aid for precisely locating anatomical structures in either open or percutaneous neurosurgical procedures… reference to a rigid anatomical structure can be identified relative to images" | Cranial | No | **Yes (navigation)** |
| Medtronic StealthStation S8 Spine K251282 (Oct 2025) | OLO | navigation (search snippet) | Spine | UNVERIFIED | Yes |
| Stryker Blueprint K160555 (2016); Blueprint Mixed Reality K222510 | LLZ? (UNVERIFIED) / stereotaxic | "assist in pre-operative surgical planning for shoulder surgery"; the mixed-reality version adds "stereotaxic surgery to guide the placement of a glenoid pin" (https://www.accessdata.fda.gov/cdrh_docs/pdf16/K160555.pdf , https://www.accessdata.fda.gov/cdrh_docs/pdf22/K222510.pdf ; snippets) | Shoulder | Automated segmentation (UNVERIFIED) | Mixed-reality version: yes |
| Surgical Theater SpineAR SNAP K252054 (29 Sep 2025); predicate K243623 | 882.4560, SBF, LLZ | (PDF) "pre-operative surgical planning on-screen and in a virtual environment, and intra-operative surgical planning and visualization… augmented environment… with validated navigation systems… virtual display should not be relied upon solely for absolute positional information" | Thoracic and lumbar spine | Yes; 3D U-Net trained partly on **the TotalSegmentator dataset** | **Yes (AR plus navigation)** |
| 3D Systems VSP | various | VSP Cranial K151285 (2016, PPT, aggregator only, UNVERIFIED) https://www.aiqixie.com/fda/510k/K151285.html ; VSP Orthopedics K190044 and K250711; an adolescent expansion in Dec 2025 (https://investor.3dsystems.com/news/news-details/2025/3D-Systems-Announces-FDA-510k-Clearance-Expanding-VSP-Orthopedics-Indications-to-Include-Skeletally-Mature-Adolescents/default.aspx). VSP Orthognathic/Reconstruction K-numbers UNVERIFIED | CMF, ortho | Service with engineers in the loop | No (guides are used in surgery) |
| TotalSegmentator-based products | — | No public clearance names TotalSegmentator. Its README says it is "part of several FDA-approved products… certified as a component" (https://github.com/wasserth/TotalSegmentator); which products is UNVERIFIED | — | — | — |

### Intended-use wording patterns FDA has accepted
1. **What it is:** "intended for use as a [cloud-based service and] software interface and image segmentation system/framework for the transfer of DICOM imaging information from a medical scanner to an output file." (Mimics, ScanIP, Axial3D)
2. **What it is for:** "It is also intended as pre-operative software for simulating/evaluating surgical treatment options" / "used for treatment planning" / "tools to aid… reading, interpreting, reporting, and treatment planning… including preoperative surgical planning and intraoperative image display."
3. **Physical model:** "output file can be used for the fabrication of physical replicas… using traditional or additive manufacturing methods. The physical replica can be used for diagnostic purposes in the field of [named specialties]."
4. **Limiting the scope:** the population and anatomy covered by ML are stated explicitly ("adult patients undergoing CT chest, abdominal, or pelvic scans"; "not intended for mammography"; "not intended to automatically detect or contour lesions"). Out-of-scope inputs fall back to non-ML tools.
5. **Human oversight:** "should be used in conjunction with other diagnostic tools and expert clinical judgment"; "intended to assist the clinician who is solely responsible for making all final patient management decisions"; "must be used in conjunction with… [software] to review, edit, and accept contours"; "used by trained medical professionals."
6. **Intra-op caveat (navigation/AR):** "should not be relied upon solely for absolute positional information."

---

## 4. Predicate-based strategy

**Step 0 (prerequisites, from now to about 12 months).**
- Build the quality system to the QMSR / ISO 13485 and the software lifecycle to IEC 62304, with risk management to ISO 14971, usability to IEC 62366-1, and security to IEC 81001-5-1. (FDA recognition of these standards is UNVERIFIED in this run.)
- Lock the models and training data, with licences cleared for commercial use. Licences are covered in critique.md: Apache TotalSegmentator tasks plus MOOSE for distal limbs. `appendicular_bones` and `nnInteractive` are excluded.
- Use the FDA Q-Submission (Pre-Sub) programme to agree the predicate and testing (no fee; UNVERIFIED in this run).

**Step 1 (first 510(k), QIH, with a PCCP).** Pick the narrowest indication with the strongest predicates. Two options:
- **Option A (recommended): bone models for orthopaedics, trauma and CMF from adult CT.**
  - Primary predicate: Axial3D Insight K250369, which is cloud, ML, QIH, with a PCCP.
  - Indication draft: "cloud-based image segmentation framework for the transfer of DICOM CT imaging information to an output file (3D surface model)… the output file can be used for treatment planning in orthopedic, trauma and maxillofacial applications… in conjunction with other diagnostic tools and expert clinical judgment."
  - Leave out the cardiovascular field and "diagnostic purposes" for now.
- **Option B: organs for adult CT chest, abdomen and pelvis.**
  - Predicates: InferOperate K250237 and Visible Patient K212896.
  - This option can include "intraoperative image display" from day one, because K250237 already did.
- **Requirements either way:**
  - **Mandatory clinician review and editing before a model is final.** Both predicates keep a human in the loop: Axial3D staff finalise the segmentation, and InferOperate gives "preliminary segmentations". Fully automatic output with no review would be a technological difference FDA will question.
  - Stated exclusions: under 21 (or under 18), lesions/tumours, metal, slice thickness over a threshold, MR.
  - Performance testing: Dice and surface distance per structure on a multi-site, multi-scanner US-representative test set that is independent of training. A reader or clinician study on the scale of Axial3D's (3 radiologists, 9 physicians, 12 cases) is the minimum; FDA may expect more for a new sponsor (UNVERIFIED).
  - Cybersecurity file under 524B. eSTAR submission format (UNVERIFIED in this run).
  - **A PCCP from the start**, covering retraining on new data within the same structures and population, with a locked test set and performance acceptance criteria. This is the regulated version of "doctors give feedback and the model improves".

**Step 2 (expansion 510(k)s, each with the Step 1 device as predicate):**
- (a) Add **intra-op reference display** (InferOperate precedent), if not in Step 1.
- (b) Add the second anatomy group (organs or bone).
- (c) Add **3D-printed models for diagnostic use** (Mimics inPrint and Axial3D precedent). This requires validated printer/material combinations.
- (d) Add cardiovascular / CTA (Axial3D and Mimics Enlight precedent).
- (e) Add MR (Visible Patient and Siemens K242745 precedent).
- (f) Add paediatrics (Visible Patient covers children, without ML).
- New structures or populations outside the PCCP each need a new 510(k) (807.81(a)(3)).

**Step 3 (later, through partnerships): intra-op navigation or AR.**
- Under 21 CFR 882.4560 (HAW/OLO, possibly SBF) with a navigation-hardware partner.
- Predicates: StealthStation K221087 / K251282, Brainlab K223552, SpineAR K252054.
- This needs registration-accuracy testing and probably different human-factors work. Do not include it in Steps 1–2.

**EU, in parallel or after the US:**
- Plan for Class IIb with a notified body (expect long timelines and scarce notified-body capacity; UNVERIFIED).
- Write the clinical evaluation and PMCF around the same narrow indication.
- Build the AI Act Article 6(1) high-risk requirements into the technical file before **2 Aug 2028** (the Omnibus date; original 2 Aug 2027). The date is UNVERIFIED against EUR-Lex.

**Key UNVERIFIED items to close:**
- FY2027 De Novo and registration fees.
- The 524B guidance version (June 2025 vs February 2026).
- The EUR-Lex text of 2026/1744.
- Blueprint and VSP K-numbers and product codes.
- Which cleared products embed TotalSegmentator.
- Eligibility of an Indian firm for small-business status.
- The FDA US Agent and EU Authorised Representative requirements.

**Files read in full:** `docs/ct_platform/research/host.md` (section 4) and `critique.md`. 510(k) summaries were read from the FDA links above.