# How making the CT-to-3D platform a medical device changes the build, starting now (researched 2026-11-21)

Scope: I read `docs/ct_platform/PLAN.md`, `research/host.md` §4–5 and `research/critique.md`. No files were modified. I did not read `01_clinical_CT/`, `results/` or `data/`. Claims tagged "(secondary)" come from law-firm or consultancy pages. "UNVERIFIED" means I could not confirm the claim in this run. Unattributed engineering advice is my own recommendation, not a regulatory fact.

## 0. What changes in one paragraph

The earlier plan was "research and education only, no clinical wording" (PLAN.md line 3, §2.1). The goal is now a device that surgeons use **before and during surgery**. That is lawful and well-trodden: Axial3D INSIGHT is a cloud AI CT segmentation and 3D model device cleared for "treatment planning" under FDA product code QIH ([K250369](https://fda.innolitics.com/device/K250369)).

What changes is that **process becomes the product**. You need:
- an intended-use statement written first;
- a QMS;
- design controls, with every requirement traced to a test;
- a risk file;
- locked and versioned models;
- weights you are legally allowed to ship;
- independent clinical test data.

Doctors can use it and give feedback before clearance, but **only inside a formal clinical investigation**, and output must not drive care unless the investigation protocol allows it:
- India: ethics committee approval (MDR 2017 r.50, [MDR text](https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2022/m_device/mdr,%202017%20(1).pdf)) plus CDSCO test licence MD-12→MD-13 and clinical investigation permission MD-22→MD-23 ([secondary summary of CDSCO/MD/GD/MDSW/01/2026](https://meddeviceguide.com/blog/india-cdsco-medical-device-software-mdsw-guidance-2026-guide)).
- Uncontrolled "beta" use in theatres is not allowed.

Intra-operative use is a bigger step than planning (§4). Tracked navigation is a different, hardware-bearing device class.

## 1. Classification anchors

| Market | Planning (pre-op) | Intra-op reference display | Tracked navigation |
|---|---|---|---|
| **US** | 21 CFR 892.2050, Class II, 510(k), not GMP-exempt. QIH "Automated radiological image processing software": "Adaptive AI algorithms are outside this product code's scope" ([FDA QIH](https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD/classification.cfm?ID=QIH)); also LLZ ([FDA LLZ](https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD/classification.cfm?ID=LLZ)) | Same codes if it is display only. Documentation level likely **Enhanced** (see below) | 882.4560 stereotaxic instrument: OLO (orthopaedic) or HAW (neuro), Class II 510(k) ([FDA OLO](https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD/classification.cfm?ID=OLO)) |
| **EU** | MDR Rule 11: IIa minimum. IIb if an error could cause serious deterioration or a surgical intervention ([MDCG 2019-11](https://health.ec.europa.eu/document/download/b45335c5-1679-4c71-a91c-fc7a4d37f12b_en)). Realistically IIb | IIb (III if an error could cause death or irreversible harm) | IIb or higher, plus the active-device and hardware rules (UNVERIFIED in this run) |
| **India** | SaMD Table 2: "drive management" × "serious" = **B**; × "critical" = **C**. "Treat/diagnose" × critical = D ([secondary](https://meddeviceguide.com/blog/india-cdsco-medical-device-software-mdsw-guidance-2026-guide)) | Likely C (UNVERIFIED) | C/D, with the hardware classified under the First Schedule |

Related US and EU rules:
- **FDA documentation level.** It is Enhanced when a software failure "could present a hazardous situation with a probable risk of death or serious injury", judged *before* risk controls ([FDA software content guidance, June 2023](https://fda.gov/media/170714/download); definition via secondary summaries). Plan for Enhanced from day one, because intra-op use and surgical planning will very likely trigger it.
- **Licensing authority in India.** Class A/B licences come from the State Licensing Authority (MD-3→MD-5). Class C/D come from the Central Licensing Authority (MD-7→MD-9) (secondary, same source).
- **EU AI Act.** An AI medical device of class IIa or higher remains high-risk AI under Annex I. The obligations date moved to **2 August 2028** under the Digital Omnibus, reported as Regulation (EU) 2026/1744 ([Gibson Dunn](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/), secondary; the Official Journal was not checked).

**Recommendation:** pick one narrow first indication. An example: "automatic segmentation and 3D model generation of bone from CT, for pre-operative orthopaedic and maxillofacial planning, reviewed by a qualified clinician", which is Axial3D's pattern. Add intra-op display as indication 2, and soft-tissue or vessels as indication 3. Every extra structure or indication adds validation cost.

## 2. Standards and what each demands for a 2–5 person team

| Standard | What it demands in practice | Status |
|---|---|---|
| **ISO 13485:2016 (QMS)** | Quality manual and procedures: document control, records, design and development (7.3), purchasing and supplier control (cloud provider, annotation vendors), CAPA, complaints, internal audit, management review, training records. FDA's QMSR took effect **2 Feb 2026** and incorporates ISO 13485:2016 by reference into 21 CFR 820 ([FDA QMSR](https://www.fda.gov/medical-devices/postmarket-requirements-devices/quality-management-system-regulation-qmsr)). CDSCO lists IS/ISO 13485 (secondary). Small-team practice: an eQMS or Git-based document control with e-signatures, about 20–30 SOPs, and certification audit through a notified or certification body before the CDSCO C/D or EU filing | Mandatory in practice everywhere |
| **IEC 62304:2006+A1:2015 (software lifecycle)** | Development plan; software requirements; architecture with item and unit decomposition (and segregation if classes differ); detailed design (class C); unit verification (B/C); integration and system testing; release; maintenance; problem resolution; configuration management; and SOUP handling: identify, specify functional/performance requirements, list known anomalies (B/C). Classes: A = no injury possible; B = non-serious injury; C = death or serious injury, after *external* risk controls ([Johner](https://blog.johner-institute.com/iec-62304-medical-software/iec-62304-2nd-edition-all-areas-of-application-and-changes/), [Wikipedia](https://en.wikipedia.org/wiki/IEC_62304)). Edition 2 (two rigour levels) is still a draft; keep claims tied to the published edition | FDA-recognised; CDSCO lists it; EU state of the art (not a harmonised standard per secondary sources) |
| **ISO 14971:2019 (+A11:2021 in the EU) risk management** | Risk management plan; hazard analysis; risk estimation (severity × probability); controls verified for effectiveness; benefit-risk; production and post-production feedback loop. EN ISO 14971:2019+A11:2021 is harmonised under the MDR (secondary, [Commission page](https://health.ec.europa.eu/medical-devices-topics-interest/harmonised-standards_en)). See the hazard table below | Mandatory in practice |
| **IEC 62366-1 (usability)** | Use specification (surgeon, radiologist, OR nurse, 3D-print technician); user-interface characteristics related to safety; known use errors; hazard-related use scenarios; formative evaluations; **summative (validation) test** in a simulated OR. FDA recommends at least 15 participants per distinct user group ([FDA HF guidance 2016](https://fda.gov/media/80481/download), Appendix B; figure via secondary summaries). Surgeons and radiologists are separate groups, so plan for about 30+ sessions | FDA-recognised (UNVERIFIED number); CDSCO lists it |
| **IEC 81001-5-1:2021 (health software security)** | A secure lifecycle aligned with IEC 62443-4-1: security requirements, threat model, secure design, secure coding, SAST/DAST, vulnerability and penetration testing, a vulnerability disclosure and handling process, security updates, and an SBOM ([IEC webstore](https://webstore.iec.ch/en/publication/63293); Interpretation sheet 1, 2025-12). FDA §524B "cyber device": an SBOM, a post-market vulnerability plan and patching are statutory ([FDA cybersecurity resources](https://www.fda.gov/medical-devices/medical-device-cybersecurity/cybersecurity-resources-medical-device-manufacturers-guidance-resources-and-faqs)). The newest FDA guidance version date is UNVERIFIED (a 3 Feb 2026 revision is claimed by a vendor). CDSCO expects an SBOM (secondary) | Required for a cloud device |
| **IEC 82304-1:2016 (health software product)** | Product-level requirements: product requirements, validation, accompanying documents (instructions for use, technical description, minimum platform, browsers and GPUs), and post-market. It covers what IEC 62304 does not, for software with no dedicated hardware. CDSCO lists IS/IEC 82304-1 (secondary) | Applicable to SaMD |
| **ISO/IEC 27001 (+27017/27018) for the cloud** | Not a device standard, but the way to show the hosting ISMS. Google Cloud services in asia-south1/asia-south2 are MeitY-empanelled ([Google MeitY](https://cloud.google.com/security/compliance/meity-india)). CDSCO test-licence files ask whether SaaS is on a MeitY-empanelled cloud (secondary). Google holds 27001/17/18 certificates (UNVERIFIED in this run; check its compliance centre). Your company's own ISMS is separate, and you inherit nothing automatically: use a shared-responsibility matrix | Expected by hospitals, not by regulators |
| **ISO/IEC 23894, ISO/IEC 42001 (AI risk and management)** | Listed by CDSCO as "may be applicable" (secondary). Fold AI risk into the 14971 file | Optional |
| **DICOM conformance statement** | Required in practice for PACS integration: a PS3.2-format statement covering supported SOP classes (CT Image Storage, Segmentation Storage for DICOM-SEG, possibly Surface Segmentation), transfer syntaxes, network services (C-STORE SCP, DICOMweb STOW/WADO), character sets and security profiles ([DICOM PS3.2](https://dicom.nema.org/medical/dicom/current/output/html/part02.html)) | Write it at the S4 stage |

**IEC 62304 safety class for this product (my determination; verify with a consultant):**
- Hazardous situations: a wrong or omitted structure, a left/right flip, scale error or a truncated organ in a model used to plan or guide surgery. These can lead to wrong-site or wrong-size resection, vessel or nerve injury, or a wrong implant: **serious injury**.
- The only external risk control is "a clinician reviews the model against the source CT". Amd 1 allows credit for external controls, but for intra-op use under time pressure, crediting clinician review is weak.
- **Classify the segmentation, geometry/orientation, meshing, measurement and display items as Class C.** Segregate non-safety items so they can be Class A/B with a documented segregation rationale: user admin, billing, upload UI chrome, telemetry.
- A pre-op-only v1 with mandatory review against the CT and an explicit "verify against images" step *might* justify Class B. Do not plan on that.

**SOUP/OTS (TotalSegmentator, nnU-Net, PyTorch, CUDA/cuDNN, VTK, trimesh, manifold3d, highdicom, pydicom, three.js, NiiVue/Cornerstone3D, browsers, OS images):**
- **IEC 62304 requires**, for each item: title, manufacturer, exact version; its functional and performance requirements; the hardware and software it needs; a known-anomaly review (B/C); and verification that it meets your requirements in *your* configuration.
- **FDA OTS guidance (Aug 2023):** OTS inherits the device's documentation level (Basic or Enhanced). Enhanced adds maintenance and support detail ([FDA OTS](https://www.fda.gov/media/71794/download), [MD101](https://blog.cm-dm.com/post/2023/10/20/New-version-of-the-FDA-guidance-on-off-the-shelf-software-use-in-medical-devices)).
- **AI weights as SOUP:**
  - Treat each weights file as a SOUP item with its hash.
  - You cannot fully document training data you did not create. Compensate with independent clinical validation.
  - TotalSegmentator's README notes it is already a certified component inside FDA-cleared products ([README](https://raw.githubusercontent.com/wasserth/TotalSegmentator/master/README.md)), so this route is precedented.
- **Determinism:** pin CUDA/cuDNN, set deterministic flags, and define numerical tolerances for regression tests. GPU non-determinism must not change label maps beyond the tolerance.

**ISO 14971 hazard seeds (start the risk file with these):**

| Hazard | Cause | Example control (verify each) |
|---|---|---|
| Wrong label (structure misnamed) | Model confusion, label-table mapping bug | Locked SNOMED label table with a hash check; anatomical plausibility rules; clinician confirmation |
| **Left/right flip** | LPS→glTF axis swap bug (the critique notes GLB is written in LPS today), viewer camera mirroring, DICOM orientation misread | Asymmetric phantom tests in CI (oblique, tilted, prone, feet-first); always-on orientation cube and R/L markers; laterality in the label name ("femur_left"); never mirror the camera |
| Scale error | mm vs m (×0.001 glTF), wrong pixel spacing, resampling bug, STL unit ambiguity | Units written into every export; a ruler in the viewer; known-size phantom checks in CI; 3MF with units; mm printed on prints |
| Truncated organ | Scan field of view edge | "Cut by scan edge" flag and hatched caps (already in the plan); suppress volumes for truncated structures |
| Missing lesion or pathology | The models segment *normal* anatomy | Intended use excludes lesion detection; labelling; banner; v1 does not claim tumours |
| Stale or wrong model | Old result shown for a new scan; wrong patient or study; model version drift | Patient ID, study UID, series UID, acquisition date and model version shown on every view and export; hash of the input series stored in the result; refuse to display a mismatch |
| Wrong patient | **De-identification removes identity** | See §5: the clinical tier must keep identity end to end |
| Silent partial failure | One model failed, others shipped | Per-structure QC status; "not segmented" is shown explicitly, never just absent |
| Thick slice, child, metal, contrast, CBCT, MR | Out-of-distribution input | Hard input gates in the intended use; refuse rather than warn for the clinical tier |
| OR network loss | Cloud dependency | Local or edge mode (§4) |
| Cyber (tampered model or result) | Supply chain, storage | Signed artefacts; hash verification at load |

## 3. AI-specific requirements

- **GMLP, 10 principles** (FDA, Health Canada and MHRA, 27 Oct 2021; [Health Canada](https://www.canada.ca/en/health-canada/services/drugs-health-products/medical-devices/good-machine-learning-practice-medical-device-development.html), [FDA PDF](https://www.fda.gov/media/153486/download)):
  1. multidisciplinary expertise across the lifecycle;
  2. good software engineering and security;
  3. representative participants and datasets;
  4. training independent of test;
  5. best-available reference standards;
  6. model design tailored to data and intended use;
  7. focus on human-AI team performance;
  8. testing under clinically relevant conditions;
  9. clear essential information to users;
  10. deployed models monitored and retraining risks managed.

  Transparency principles followed ([FDA](https://www.fda.gov/medical-devices/software-medical-device-samd/transparency-machine-learning-enabled-medical-devices-guiding-principles)).
- **Locked vs adaptive.** Ship **locked** models only. QIH excludes adaptive AI (see above). Doctors' feedback must **never** retrain the deployed model automatically. Feedback goes into the complaint/CAPA and dataset pipeline, and a new model version is a design change.
- **PCCP.** The FDA final guidance "Marketing Submission Recommendations for a PCCP for AI-Enabled Device Software Functions" was issued Dec 2024 ([Federal Register](https://www.federalregister.gov/documents/2024/12/04/2024-28361/marketing-submission-recommendations-for-a-predetermined-change-control-plan-for-artificial)); a reissue in Aug 2025 is reported (secondary). A PCCP has three parts: description of modifications, the modification protocol (data, retraining, performance evaluation, update procedure), and an impact assessment. Axial3D's K250369 was essentially a PCCP addition to K232841. The AI lifecycle draft guidance ([FDA draft](https://www.fda.gov/media/184856/download)) is still draft as of Oct 2026 (secondary). India allows an optional Algorithm Change Protocol in the risk file (secondary). **Action:** design your retraining pipeline now so it can become a PCCP protocol later: fixed test sets, fixed metrics, fixed acceptance criteria, rollback.
- **Dataset documentation** (a datasheet for every dataset):
  - source site, licence or DUA, consent/ethics basis, de-ID method;
  - scanner vendor, model and kVp; slice thickness; contrast phase; age, sex and BMI if available; pathology; implants;
  - inclusion and exclusion criteria; split assignment at **patient** level; annotation protocol; annotator credentials; QA.
  - Axial3D's summary lists scanner makers and models and the slice and pixel ranges ([K250369](https://fda.innolitics.com/device/K250369)).
- **Subgroup analysis.** Pre-specify: age bands (with children excluded or analysed separately), sex, scanner vendor (Siemens, GE, Philips, Canon and Indian-installed-base vendors), slice thickness, contrast vs non-contrast, BMI, metal, pathology, and **Indian vs non-Indian**. CDSCO expects real-world and Indian-setting evidence (host.md §4.1, sourcing the CDSCO draft). FDA reviewers compare US against non-US data. ART-Plan had to show mean DSC(US) ≥ DSC(non-US) ([K234068](https://fda.innolitics.com/device/K234068)). Plan for **both Indian and US data** in the test set if a US filing is likely.
- **Ground truth:**
  - At least 2 independent annotators, of whom at least one is a board-certified radiologist (or a surgeon for planning structures).
  - An adjudicator for disagreements; a written annotation manual per structure (boundaries, partial volume, how to treat pathology).
  - Report inter-reader Dice and HD95, because they set a realistic ceiling.
  - Model-assisted pre-annotation is allowed but must be disclosed and controlled for bias.
  - TotalSegmentator's own labels were semi-automatic, model-in-the-loop ([paper](https://arxiv.org/pdf/2208.05868)), so they are weaker as a reference standard.
- **Test-set independence trap.** The TotalSegmentator dataset (CC BY 4.0) is that model's **training data**. It cannot be your independent test set, and even its held-out split comes from the same institution. You need **new multi-site data** never seen by any shipped model.
- **Standalone vs reader study:**
  - *Standalone:* algorithm vs ground truth (Dice, HD95, MSD, volume error).
  - *Clinical or reader:* clinicians rate fitness for use.
  - 510(k) examples:
    - **Axial3D K250369 (QIH, 2025):** 12 cases, 3 radiologists, RADPEER 1/2a criterion, plus a 12-case intended-use study with 9 physicians. No Dice reported in the summary ([Innolitics](https://fda.innolitics.com/device/K250369)).
    - **ART-Plan K234068 (MUJ, 2024):** a minimum of 17 patients per test. Per-organ pass if mean DSC ≥ 0.8, or ≥ inter-expert DSC + 5%, or qualitative A+B ≥ 85%. Mean HD95 ≤ 5.75 mm. Non-regression ≤ 5% DSC drop ([Innolitics](https://fda.innolitics.com/device/K234068)). The Innolitics page warns its AI table is AI-generated, so check the PDF.
  - These small numbers come from predicate-based submissions. Under FDA's current AI expectations they are not a safe target (my judgement).
  - Statistically, a patient-level pass rule with a 95% floor needs about 150–311 patients at 98–99% expected pass rates for 90% power ([Innolitics HD95 article](https://innolitics.com/articles/hd95-acceptance-criteria-sample-size/)).
  - **Plan for about 150–300 independent patients per indication across at least 3 sites, plus a reader study with at least 3 clinicians.**
- **Acceptance criteria.** Set them per structure from clinical need, not from the pilot model:
  - Dice (overlap);
  - HD95 in mm (worst-case boundary);
  - mean surface distance in mm;
  - volume error in %;
  - laterality-correct 100%;
  - scale error below a stated threshold on phantoms.

  An example proposal to justify in the risk file: bone surfaces for orthopaedic planning MSD ≤ 1.0 mm and HD95 ≤ 2–3 mm. This is my proposal, not a regulatory value. Use a patient-level pass/fail rule with a Clopper–Pearson lower bound, as Innolitics describes.

## 4. Intra-operative specifics

- **Regulatory step.** A **reference-only display**, where the model is viewed beside the field and not registered to the patient, stays image processing (QIH/LLZ), with heavier usability and risk work.
  - **Registration or tracking** (aligning the model to the patient with optical or EM trackers, overlaying on the anatomy, guiding instruments) makes it a stereotaxic or navigation device: OLO/HAW ([FDA OLO](https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD/classification.cfm?ID=OLO)).
  - That brings hardware, IEC 60601-1 electrical safety (UNVERIFIED that it is required for software-only), positional-accuracy testing to **ASTM F2554-22** ([ASTM](https://store.astm.org/f2554-22.html)), registration-error budgets, sterilisable trackers and different predicates.
  - **Keep tracked navigation out of scope until a cleared planning product exists.**
- **Offline operation.** A theatre cannot depend on the WAN. Ship an **edge or on-prem viewer**: a locked workstation or local server that caches the case (pre-computed models and source series) before surgery and works with no network. The cloud computes before surgery; the OR display only reads.
  - This turns the product into distributed software: an installer, signed updates, a platform spec under IEC 82304-1.
  - It also triggers **weight-redistribution** licence questions (§5).
- **Latency.** Rotate, zoom and clip at 60 fps or better locally; never a server round-trip per interaction. Case load completes before the patient is in the room. Show a "data loaded at HH:MM, model version X" banner.
- **Display constraints:** large surgical monitors at 2–4 m viewing distance; high-contrast dark theme; big fonts; no small hover targets; colour-blind-safe palette; persistent orientation cube and R/L markers; patient name, MRN and side banner always visible; no modal pop-ups; no auto-updates or reboots during a case (UX recommendations, validated in IEC 62366 summative testing).
- **Sterile-field interaction.** Interaction is by a non-sterile operator, or touchless: a foot pedal, a sterile-draped touchscreen or mouse, gesture, or voice with a constrained command set and on-screen confirmation. Voice and gesture misrecognition are new use-error hazards. Start with the "circulating-nurse operator + foot pedal" model, which has the lowest risk.
- **Pre-op vs intra-op versions.** Intra-op shows only verified, clinician-signed-off models from planning, read-only, with no on-the-fly AI in theatre for v1.

## 5. LICENCE CHECK for shipping weights inside a regulated commercial device

| Weights | Licence (verified this run) | Clinical-use statement | Training-data provenance | Verdict for a commercial device |
|---|---|---|---|---|
| **TotalSegmentator v2 Apache tasks** (`total`, `total_mr`, `body`, `lung_vessels`, etc.) | "Openly available for any usage (Apache-2.0)". Licensed tasks need a key, free for non-commercial use; `brain_aneurysm` is CC BY-NC 4.0 ([README](https://raw.githubusercontent.com/wasserth/TotalSegmentator/master/README.md)) | "not a medical device and is not intended for clinical usage". This is a **disclaimer**, not a licence restriction, and the README says TotalSegmentator is a certified component of FDA-cleared products | Public CT dataset of 1,228 subjects on Zenodo, **CC BY 4.0** ([Zenodo](https://zenodo.org/api/records/6802613), per critique L9). v2 weights also used non-public data (UNVERIFIED which) | **Shippable** with Apache NOTICE and attribution, and with your own validation. Ask the author for a short written confirmation of data rights (due diligence). Licensed tasks need a **paid commercial licence**: appendicular bones, tissue types, `heartchambers_highres`, `coronary_arteries`, `face`. Disable usage statistics |
| **MOOSE 3.2** `clin_ct_*` | Model CC BY 4.0, code Apache-2.0, plus "Commercial Version: Contact Zenta" ([README](https://raw.githubusercontent.com/ENHANCE-PET/MOOSE/main/README.md)) | None stated | DATA_CARD lists licences "per originating site". **AutoPET data is CC BY-NC 4.0 ("non-commercial use")**; Leipzig, Careggi and UC Davis are CC BY 4.0. Which sources trained the `clin_ct_*` models is **not stated** ([DATA_CARD](https://raw.githubusercontent.com/ENHANCE-PET/MOOSE/main/DATA_CARD.md)) | **Needs written permission or a commercial licence (Zenta) before going in a device.** The CC BY grant on the weights is legally permissive, but an NC-licensed source may be in the training data, and a paid commercial version exists. It is also your only verified source of distal-limb bones, so either license it or retrain |
| **CADS `open`** | CC BY-SA 4.0; trained on 13,269 CTs from licence-compatible sources (VerSe with written permission); `reference` uses a custom licence; `research` is CC BY-NC-SA ([LICENSE_MODEL](https://raw.githubusercontent.com/murong-xu/CADS/main/cads/LICENSE_MODEL), [README](https://raw.githubusercontent.com/murong-xu/CADS/main/README.md)) | None stated | Only partly listed | **Legally usable but a poor fit for a device.** Shipping weights to an edge or on-prem OR box is distribution, so you must pass on CC BY-SA terms. Fine-tuned weights must be released CC BY-SA, so competitors can reuse them. CC 4.0 §2(a)(5)(C) forbids technical measures that restrict recipients, which conflicts with encrypted or locked device weights. CC licences grant **no patent rights** (§2(b)(2)) ([CC BY-SA 4.0 legal code](https://creativecommons.org/licenses/by-sa/4.0/legalcode.en); clause numbers recalled, verify). **Leave it out of v1**; SaaS-only use is lower risk |
| **NV-Segment-CT (VISTA3D)** | NVIDIA Open Model License (v. 24 Oct 2025): "Models are commercially usable"; outputs are not owned by NVIDIA; NOTICE required. Rights terminate if you bypass a "guardrail" (§2.1). Use must follow Trustworthy AI terms (§2.3). You are "solely responsible for reviewing Model documentation, including any Special-Purpose Model limitations" (§6) ([licence](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/)) | Card: "This model is for research purposes and not for clinical usage" ([HF card](https://huggingface.co/nvidia/NV-Segment-CT)) | 11,454 CTs from **in-house and public** sources, with pseudo-labels from TotalSegmentator; "as much dataset with a commercial license as possible", with no per-dataset licence breakdown ([VISTA3D paper](https://arxiv.org/pdf/2406.05285v2)) | **Ambiguous, so do not ship without written permission from NVIDIA.** The licence has no explicit clinical ban, so the card line reads as a disclaimer. But §1.5/§6 tie you to documented "Special-Purpose Model limitations", and "not for clinical usage" could be argued to be one. Undisclosed in-house training data also makes FDA dataset documentation impossible. Fine for internal annotation tooling (pre-labelling your training data) |
| **TotalSegmentator dataset** | CC BY 4.0 (Zenodo) | — | — | **OK** for commercial training and fine-tuning with attribution. **Not** valid as an independent test set for TotalSegmentator-derived models |
| Excluded (non-commercial) | nnInteractive CC BY-NC-SA, NV-Segment-CTMR, MedSAM2, VoxTell, SuPreM, licensed TotalSegmentator tasks, MeshLib (critique L2, L7, L13) | — | — | Never ship |

**Bottom line:**
- **Ship in v1:** TotalSegmentator Apache tasks only, plus your own models.
- **Need written permission or a commercial licence:** MOOSE (Zenta), NV-Segment-CT (NVIDIA), and licensed TotalSegmentator tasks (Wasserthal).
- **Avoid in a locked device:** CADS `open` (share-alike).
- **Long-term moat and lowest legal risk:** retrain or fine-tune nnU-Net (Apache) on data you license (hospital DUAs that cover commercial device use) with radiologist ground truth, then use the open weights only as pre-annotation tools.

## 6. Concrete changes to PLAN.md (S0–S6), starting now

**Immediate (this week):**
1. **Rewrite PLAN.md §2 and the header.** Replace "research use, not for diagnosis or surgical planning" and the "avoid medical-device claims" decision with a **draft intended use and indications for use**. Keep a separate, clearly firewalled public research demo only if you still want one: different name, no clinical claims. FDA judges intended use from all your claims ([FDA RUO/IUO guidance](https://fda.gov/media/87374/download)).
2. **Design-control artefacts to start, as files under `docs/dhf/` (design history file), all versioned:**
   - intended use and indications; user profiles and use environments (radiology reading room, surgeon's office, OR);
   - **user needs** (surgeon interviews, recorded);
   - **design inputs / system and software requirements** with IDs; software architecture with safety classes per item;
   - **risk management plan + hazard analysis** (seed with §2);
   - **SOUP list** (name, version, licence, hash, requirements, known anomalies, monitoring);
   - **traceability matrix** (need → requirement → risk control → test → result);
   - verification and validation protocols *before* data collection; usability engineering file; cybersecurity plan and threat model; SBOM;
   - clinical evaluation / performance plan; regulatory strategy (CDSCO class + FDA Pre-Sub);
   - a dataset register and datasheets.
3. **QMS skeleton:** document control, CAPA, complaints, supplier control (Google Cloud, annotation vendors, model licensors), training records. Pick an eQMS or Git-plus-signatures process.
4. **Book an FDA Pre-Submission (Q-Sub)** and a CDSCO pre-consultation once the intended use is drafted (UNVERIFIED that CDSCO offers a formal pre-submission meeting for software).

**Milestone changes:**

| # | Was | Now |
|---|---|---|
| S0 | Decisions + repo layout | Add: intended use, safety classification, QMS SOP set, design and development plan (IEC 62304 §5.1), regulatory strategy. **New repo `ctplatform` with protected `main`, required reviews, and a CODEOWNERS file for safety-class-C paths.** Keep the device repo separate from `emgmirror` so configuration management covers only device code |
| S1 | Segmentation engine, 20–50 public CTs | Becomes a **verification** milestone with pre-written requirements and acceptance criteria. Weights allowlist: TotalSegmentator Apache only. MOOSE, CADS and VISTA3D sit behind licence gates. Every model gets a version ID and hash in a model registry. Public-data benchmark becomes **development** evidence, not validation. Start sourcing **independent multi-site clinical data** (Indian hospitals under DUA + ethics, plus US data if FDA is planned) and the annotation protocol |
| S2 | Mesh pipeline | Add laterality, scale and orientation **phantom test suite** (asymmetric, oblique, feet-first, prone) as Class C unit and integration tests, with mm-unit assertions on every export. DICOM-SEG must carry the Frame of Reference UID and the source series UID. STL/3MF carry units |
| S3 | Viewer, screenshot comparison | Becomes a **formative usability** milestone: surgeon sessions, a use-error log, and persistent patient, side and orientation banners. Add an OR display mode (dark, large, read-only) and an offline-cache design |
| S4 | Backend + GPU worker, public data | **Clinical tier keeps patient identity** (positive patient ID against the wrong-patient hazard). The browser de-ID upload path stays only for the research demo. Single-tenant, India region (asia-south1, MeitY-empanelled), audit trail, RBAC and clinician sign-off workflow. IEC 81001-5-1 threat model, SBOM (CycloneDX/SPDX), and a vulnerability process. **Write the DICOM conformance statement** |
| S5 | Browser tier (BrowSeg) | **Deprioritise for the device.** Uncontrolled client hardware and browsers are hard to validate under IEC 82304-1. Keep it for the research demo only |
| S6 | Public research demo | Split into **S6a**, the optional research demo (firewalled branding), and **S6b, Design Verification and Validation**: standalone performance study (about 150–300 independent patients per indication, at least 3 sites, pre-specified subgroups), reader study, IEC 62366 summative test (at least 15 per user group), penetration test, clinical evaluation report |
| P2 | QMS starts here | **Moved to now.** P2 becomes the clinical investigation (ethics + CDSCO MD-22/23 or test licence) where doctors use it and give structured feedback |
| P3 | Regulated product | Filing: CDSCO (B/C), FDA 510(k) QIH with a PCCP (predicates: Axial3D K232841/K250369, Mimics, ScanIP). EU later (IIb, notified body, AI Act from Aug 2028) |
| new | — | **Intra-op reference display** as a separate design change after clearance of planning. **Tracked navigation** only as a future separate product |

**Repository practices:**
- Tagged, **signed releases** (git tags + Sigstore/cosign or GPG). Release notes list resolved anomalies and known residual anomalies (IEC 62304 §5.8).
- **Reproducible builds:** pinned lockfiles (`uv`/`pip-tools` with hashes, `package-lock`), pinned base container digests, CUDA/cuDNN pinned, deterministic inference flags, a build manifest. Rebuild from a tag and compare hashes.
- **Versioned models:** weights in a registry (GCS with object versioning and retention lock). Each model ID has a hash, licence, training dataset IDs and the validation report. The release manifest binds app version to model hashes, and runtime refuses unlisted hashes.
- CI gates: unit and integration tests, phantom geometry tests, golden-case regression (label maps within tolerance), a licence allowlist check (already in critique), SAST, dependency CVE scan, SBOM generation, and trace-matrix completeness.
- Problem reports: every bug gets a safety-impact assessment field. Doctor feedback goes into complaint handling, then CAPA.
- Change control: any model, label-table or geometry change is a design change with impact assessment and re-verification.
- Per user memory: no AI attribution lines in the repo.

**What the parallel engineering sessions should do differently from today:**
1. Write the requirement and its acceptance test **before** the code, with requirement IDs in commit messages and PR titles.
2. Work only from approved, versioned requirements. Ad-hoc feature drift is not allowed in device code.
3. Do not add a dependency without a SOUP entry (version, licence, purpose, anomaly check).
4. Pull no weights at runtime. Use the allowlist only, with TotalSegmentator usage statistics off.
5. Every geometry-touching change must pass the laterality, scale and orientation phantom suite.
6. Use cloud sessions on **public data only** for development. Clinical validation data is never used for development or tuning: lock it away, under the control of a named custodian.
7. Generate test evidence as artefacts (reports with tool versions and hashes), not just green CI.
8. Avoid UI copy that claims accuracy or clinical benefit until it is validated; all labelling text goes through review.
9. Keep the research demo and the device in **separate deployables and branding**.

## 7. UNVERIFIED items to close next

- CDSCO final MDSW guidance text: the PDF is scanned, so classification and standards come from secondary summaries.
- Current FDA cybersecurity guidance revision date.
- FDA recognition numbers and editions for 62304, 62366-1, 82304-1 and 81001-5-1.
- IEC 60601-1 applicability to OR software-only displays.
- MDCG 2019-11 Rev.1 examples for surgical planning.
- Google Cloud ISO certificates.
- CC BY-SA clause interpretation for locked device weights (get legal advice).
- TotalSegmentator v2 non-public training data rights.
- MOOSE `clin_ct_*` training sources.
- Whether NVIDIA treats the "not for clinical usage" card line as a binding limitation.
