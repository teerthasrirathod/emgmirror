# Hosting a public CT-to-3D service: architecture, privacy, regulation, licences (researched 2026-11-21)

**Method note:** `gh` is not installed on this machine, and the unauthenticated GitHub API quota is used up (core 0/60). So **repo stars, last-push dates and archived state are UNVERIFIED** for every repo below. Licences are taken from project pages or search results where a source is cited; any licence without a source is marked UNVERIFIED. No project files were read and nothing was installed or changed.

---

## 0. Bottom line
1. **Phase 1 should not take identifiable CT scans onto a server.** Make segmentation run in the browser the default, so scans stay on the device. That is feasible today: BrowSeg runs TotalSegmentator's 117-structure CT model in Chrome or Edge using WebGPU or WASM. Offer server upload only as an opt-in path. That path de-identifies in the browser before upload, deletes data automatically within 24 hours, and is labelled research use only.
2. **Use only weights you are allowed to use.** TotalSegmentator code is Apache-2.0. Several subtasks have non-commercial weights. VISTA3D / NV-Segment-CT weights are NVIDIA non-commercial. Use the Apache-licensed TotalSegmentator tasks only, or get a commercial licence.
3. **The stated purpose makes it a medical device.** "Post-diagnosis medical analysis" is a medical purpose. Segmentation and 3D visualisation software is FDA Class II (21 CFR 892.2050, product code LLZ, 510(k)). It is at least Class IIa under EU MDR Rule 11, and Class A–D under CDSCO's 2026 software guidance. Until there is clearance, the public product must be positioned and built as research or education only, with no clinical claims.
4. **Phase 2 should bring compute to the data.** Install the stack inside partner hospitals, or in an India-region cloud under a contract, with de-identification done at the edge. Do not collect scans centrally from the public.

---

## 1. Backend architecture for GPU inference

### 1.1 Upload
- **tus (resumable uploads):** an open HTTP protocol, so an interrupted upload of many gigabytes resumes instead of restarting ([tus.io FAQ](https://www.tus.io/faq)).
  - `tusd` is the reference server and can write straight to S3 or GCS. The tus project recommends tusd with the Uppy client ([tus.io FAQ](https://www.tus.io/faq); [Uppy tus docs](https://uppy.io/docs/tus/)).
  - S3 multipart limits apply: parts of 5 MB to 5 GB ([tus-ruby-server](https://github.com/janko/tus-ruby-server)).
  - There is no DICOM awareness. You must handle study and series grouping yourself.
- **Recommended pattern:**
  1. In the browser, unzip the folder, parse it with dcmjs or itk-wasm, and pick one CT series.
  2. De-identify in the browser.
  3. Optionally convert to a single NIfTI or NRRD file. This is roughly 10x fewer objects and removes most header PHI.
  4. Upload that one file with tus.
- **DICOMweb vs plain upload:** use DICOMweb (STOW-RS, QIDO-RS, WADO-RS) only when partner PACS or hospitals push to you (phase 2). For anonymous public users it adds a PACS you then have to secure, retain and license (see Orthanc licensing in §5). Plain tus upload of a de-identified volume is simpler and leaves fewer places for PHI to sit.

### 1.2 PACS and DICOM servers (phase 2)

| Project | Licence | Notes | Source |
|---|---|---|---|
| Orthanc | Core GPLv3+. Some plugins (PostgreSQL, Web viewer) are AGPLv3+ | Calling Orthanc over REST or DICOM from your own code is treated as permissive. A derived viewer in a proprietary cloud portal needs a dual licence from Osimis | [Orthanc licensing FAQ](https://orthanc.uclouvain.be/book/faq/licensing.html) |
| dcm4chee-arc-light | UNVERIFIED (believed MPL/GPL/LGPL tri-licence) | Used as the internal PACS in Kaapana | [Kaapana docs](https://kaapana.readthedocs.io/en/master/intro_kaapana.html) |

### 1.3 Platforms you could reuse

| Platform | What it gives you | Licence | Source |
|---|---|---|---|
| **MONAI Deploy** (App SDK, Informatics Gateway, Workflow Manager) | Packages a model as a MONAI Application Package (MAP). The gateway receives DICOM C-STORE, DICOMweb STOW-RS and HL7, and routes results back to PACS | Informatics Gateway Apache-2.0. App SDK and Workflow Manager UNVERIFIED | [gateway repo](https://github.com/project-monai/monai-deploy-informatics-gateway), [MONAI Deploy](https://monai.io/deploy.html), [AWS blog](https://aws.amazon.com/blogs/machine-learning/build-a-medical-imaging-ai-inference-pipeline-with-monai-deploy-on-aws) |
| **Kaapana** | Kubernetes platform with dcm4chee, OHIF and Airflow. Runs nnU-Net and TotalSegmentator on whole datasets. Deployed at all 36 German university hospitals (RACOON) | AGPL-3.0-only per the Helmholtz listing; repo file UNVERIFIED | [arXiv 2512.09644](https://arxiv.org/abs/2512.09644), [helmholtz.software](https://helmholtz.software/software/kaapana) |
| **Grand Challenge** algorithm hosting | You upload a Docker container. A "Try Out Algorithm" button lets users run it on their own data. Container has no network access. One NVIDIA T4 with 4 vCPU and 16 GiB per job (some challenges use an A10G 24 GB) | Service, not self-hosted | [container docs](https://grand-challenge.org/documentation/creating-an-algorithm-container), [runtime env](https://grand-challenge.org/documentation/runtime-environment) |
| XNAT, Girder | Research imaging archive and data management | UNVERIFIED (not researched in depth) | — |

- **Grand Challenge is the cheapest "phase 0" hosted demo.** It needs zero infrastructure from you. The data-hosting region and terms are UNVERIFIED.
- **Kaapana is the strongest phase 2 on-premises option for hospital partners.** Because it is AGPL, any changes you make to it must be published if you offer it over a network.

### 1.4 Job queue
- FastAPI enqueues the job, a GPU worker pulls it, and status goes back over Server-Sent Events or polling.
- **Arq** (asyncio and Redis) or **RQ** is enough for phase 1.
- **Celery** suits phase 2 if you need retries, canvas workflows and rate limits.
- **Temporal** is worth it only for long, multi-step and auditable pipelines (phase 2 or 3).
- Licences (UNVERIFIED in this run): Celery BSD-3, RQ BSD-2, Arq MIT, Temporal MIT.
- Ready-made alternatives: Airflow (inside Kaapana) or the MONAI Deploy Workflow Manager.

### 1.5 GPU worker sizing
- **TotalSegmentator v1, measured on an RTX 3090:**

| Scan size | Model | Peak VRAM | Runtime |
|---|---|---|---|
| 512×512×280 | 1.5 mm | ~6.1 GB | 1 min 17 s |
| 512×512×458 | 1.5 mm | ~8.5 GB | 2 min 49 s |
| 512×512×280 | 3 mm "fast" | 5.2 GB | 34 s |
| 512×512×458 | 3 mm "fast" | 7.4 GB | 53 s |

  Source: [arXiv 2208.05868](https://arxiv.org/pdf/2208.05868).
- The Slicer extension asks for at least 7 GB of VRAM. A community report says full resolution reaches about 12 GB ([Slicer forum](https://discourse.slicer.org/t/totalsegmentation-hardware-requirements/44870)). v2 runtimes are somewhat higher; there is no official v2 VRAM table (UNVERIFIED).
- **Plan for:** a 24 GB L4 or A10G per worker, one scan at a time, 2–5 minutes of GPU time per full-body scan including the extra subtasks.
- **Meshing** (marching cubes, smoothing, decimation, GLB/Draco export) runs on CPU, usually in seconds to a minute. Run it in the same worker or a CPU worker.

### 1.6 GPUs that scale to zero
- **Modal:** L4 about $0.000222/s (~$0.80/h), A10 about $0.000306/s (~$1.10/h), billed per second. CPU and RAM are billed on top ([Beam: Modal pricing](https://www.beam.cloud/blog/modal-pricing-explained); confirm on modal.com/pricing).
- **RunPod Serverless:** 24 GB flex worker about $0.69/h ([gpuperhour](https://gpuperhour.com/blog/serverless-gpu-pricing)). These figures are less certain.
- **Cost per scan:** about 3 min on an L4 is roughly $0.04 of GPU time. The extra cost is cold start, because a container image with weights is several GB.
- **Above roughly 70% utilisation, a reserved GPU pod becomes cheaper** ([Beam](https://www.beam.cloud/blog/modal-pricing-explained)).
- **HIPAA BAAs:**
  - Modal signs a BAA only on its Enterprise plan, and the BAA excludes Volumes ([Modal HIPAA](https://modal.com/blog/hipaa)).
  - RunPod offers HIPAA/GDPR options. Its BAA appears to depend on usage of at least about $3k/month ([RunPod help](https://contact.runpod.io/hc/en-us/sections/45450902246931-Compliance-Documentation)).
  - AWS SageMaker is HIPAA-eligible under the AWS BAA ([AWS list](https://aws.amazon.com/compliance/services-in-scope/HIPAA_BAA/)).

### 1.7 Storage, retention, deletion
- Use object storage with server-side encryption. Have three prefixes, each with lifecycle rules:
  - `uploads/`: delete 24 hours after the job ends.
  - `results/`: delete after 7 days, or as soon as the user downloads and clicks "delete now".
  - `logs/`: no PHI, kept 30 days.
- **Never keep raw DICOM.** Keep only the de-identified volume while the job runs.
- Use signed URLs with short expiry and job IDs that cannot be guessed. Phase 1 needs no accounts.
- Deletion must also cover queue payloads, worker scratch disks and CDN caches. Write a deletion receipt.
- **Avoid Modal Volumes for PHI**, because they are outside its BAA.

---

## 2. Privacy

### 2.1 DICOM de-identification
- **Standard:** DICOM PS3.15 Annex E, the Basic Application Level Confidentiality Profile, plus options.
  - The **Clean Pixel Data option** requires burned-in identifying text to be removed from the stored pixel values. An overlay or shutter is not enough.
  - After cleaning, set Burned In Annotation (0028,0301) to "NO".
  - The **Clean Graphics option** covers overlays and annotations.
  - Sources: [PS3.15 E.3 (2026c)](https://dicom.nema.org/medical/dicom/current/output/chtml/part15/sect_E.3.html), [E.3.3](https://dicom.nema.org/medical/dicom/current/output/chtml/part15/sect_E.3.3.html).
- **Tools:**
  - **dicognito:** Python library and CLI on top of pydicom. Replaces UIDs and keeps the study/series grouping ([PyPI](https://pypi.org/project/dicognito)). Reportedly last pushed February 2024 (UNVERIFIED).
  - **RSNA Anonymizer:** v18, installed with `pip install rsna-anonymizer`, runs with a GUI or headless ([PyPI](https://pypi.org/project/rsna-anonymizer/), [GitHub](https://github.com/RSNA/Anonymizer)).
  - **RSNA CTP:** can also remove burned-in pixel PHI ([rsna.org/ctp](https://rsna.org/ctp.aspx)).
  - **pydicom/deid:** recipe-based tool with pixel-cleaning filters. Details UNVERIFIED in this run.
  - **Synthetic PHI test data** for validating your de-identifier: [arXiv 2508.01889](https://arxiv.org/pdf/2508.01889).
- **CT-specific risks:**
  - Scout or localizer images and dose report screenshots (secondary capture) often have burned-in names. Drop every series except axial CT image series.
  - Private tags: remove them.
  - Dates: shift or remove them.
  - **Face:** a head CT can be rendered into a recognisable face. Deface, or crop the head, before any server-side storage. The model outputs themselves include a face mesh for head scans.

### 2.2 Laws

**US (HIPAA)**
- HIPAA applies to covered entities and their business associates. If US hospitals or clinicians send PHI to you, you are a business associate and need a BAA, with every subprocessor also under a BAA.
- **A BAA alone does not make you compliant** ([AWS blog](https://aws.amazon.com/blogs/industries/hipaa-compliance-for-generative-ai-solutions-on-aws)).
- A direct-to-consumer upload site may fall outside HIPAA and under the FTC Health Breach Notification Rule instead (UNVERIFIED in this run).

**EU (GDPR)**
- Health data is special-category data under Article 9.
- Pseudonymised data is still personal data (Recital 26, Art. 4(5)) ([APDCAT opinion](https://apdcat.gencat.cat/web/.content/Resolucio/Resolucions_Cercador/Dictamens/2019/Documents/en_cns_2019_015.pdf)).
- Legal basis: explicit consent under Art. 9(2)(a), or research under Art. 9(2)(j) plus Art. 89 safeguards. Member states differ; Ireland, for example, defaults to consent ([EJP RD / EDPB](https://www.ejprarediseases.org/wp-content/uploads/2021/10/edpb_application-of-the-GDPR-on-health-research_2021.pdf)). The EDPB 2026 research guidelines are still a consultation draft ([EDPB](https://www.edpb.europa.eu/system/files/2026-04/edpb_guidelines_202601_scientificresearch_en.pdf)).
- **Consequences for the service:**
  - A DPIA is effectively mandatory.
  - EU users need EU hosting, or a transfer mechanism to India or the US.
  - A browser-only mode avoids most GDPR processing on your side. You never receive the data.

**India (DPDP Act 2023 and DPDP Rules 2025)**
- The Rules were notified around 13 November 2025 (G.S.R. 846(E)). Consent Manager provisions start at about 12 months. The substantive duties start about **13–14 May 2027** ([protectcomply](https://protectcomply.com/blog/dpdp-rules-2025-timeline/), [dcomply](https://dpdpa.dcomply.in/rules/), [AZB](https://www.azbpartners.com/bank/indias-digital-personal-data-protection-act-phased-rollout-and-key-compliance-milestones/)). Check the Gazette.
- The Act has **no separate "sensitive" category**, so health data is ordinary personal data ([DSCI](https://www.dsci.in/files/content/documents/2025/cross-border-flow-of-data.pdf)).
- **Cross-border transfers** (s.16): a negative-list model. No countries are restricted yet. Stricter sectoral laws still apply ([Taxmann](https://www.taxmann.com/post/blog/cross-border-data-transfers-under-the-dpdp-act/)).
- **Significant Data Fiduciary:** a large health-data processor could be designated one, which brings localisation-type duties ([SFLC](https://sflc.in/?p=11194)). Details UNVERIFIED.
- **Practical duties:** notice and consent screen, purpose limitation, erasure on request, breach notification to the Board and to users, and verifiable parental consent for children's data (s.9; UNVERIFIED in this run).
- Penalties go up to INR 250 crore (UNVERIFIED in this run).

**Data residency recommendation**
- Indian users: host in an India region (e.g. AWS Mumbai or Hyderabad).
- EU partners: host in the EU.
- US partners: host in the US under a BAA.
- Do not mix regions. Use one stack per region.

---

## 3. In-browser inference (scans never leave the device)
- **BrowSeg:** TotalSegmentator's nnU-Net CT models compiled to C++/WebAssembly and WebGPU. Works in Chrome or Edge 113+, with a CPU (WASM) fallback ([GitHub](https://github.com/meimeimei1223/BrowSeg), [org repo](https://github.com/BrowSeg/BrowSeg)).
  - **Accuracy:** label maps were voxel-identical to TotalSegmentator 2.18 (CPU) in 75 of 80 case×task pairs on WebGPU and 55 of 60 on CPU (3D-IRCADb-01). The remaining pairs differed by a few voxels.
  - **Memory:** needs about 3–4 GB of free RAM. The heap peaked at 3.5 GB, close to the 4 GB 32-bit WASM ceiling. The GPU path needs at least 2 GB of VRAM.
  - **Browsers:** Firefox's WebGPU path was about 10x slower, with intermittent buffer failures. On Linux, Chrome flags are needed.
  - **Licence:** code Apache-2.0. It ships only the TotalSegmentator tasks released under Apache-2.0 (not checked against weights/MANIFEST.json, UNVERIFIED).
  - Tested on an RTX 4070 and a 4 GB T1200 laptop GPU.
- **brainchop:** MeshNet in TensorFlow.js, brain MRI only (T1, 256³), MIT licence ([JOSS](https://joss.theoj.org/papers/10.21105/joss.05098), [arXiv 2310.16162](https://arxiv.org/pdf/2310.16162)). Useful as a pattern, not for CT body work. WebGPU support is UNVERIFIED.
- **ONNX Runtime Web:** has a `webgpu` execution provider. Earlier builds had spotty support ([Medium example](https://medium.com/@geronimo7/in-browser-image-segmentation-with-segment-anything-model-2-c72680170d92)). An official ONNX export of TotalSegmentator was not found.
- **Supporting libraries:** itk-wasm for DICOM reading and resampling, and niivue / VTK.js for viewing. Their licences are UNVERIFIED in this run (believed Apache-2.0 for itk-wasm and BSD-2 for niivue).
- **Feasibility verdict:**
  - Whole-body CT on consumer hardware works for the 117-class "total" task on a 2020-or-newer discrete or Apple GPU with 8–16 GB of system RAM, in Chrome or Edge.
  - Expect minutes rather than seconds.
  - Phones and low-RAM laptops will fail; route them to a server-side opt-in or a demo gallery.
  - The 4 GB WASM limit means very long scans (e.g. more than 1,000 slices at 0.5 mm) must be resampled or cropped first.
  - High-resolution extra subtasks are the ones with non-commercial weights, so leave them out anyway.

---

## 4. Regulation

### 4.1 What makes it a device
- **US (FDA)**
  - Software that analyses or processes medical images is a device. Image-processing software falls outside the non-device clinical decision support carve-out (FD&C 520(o)(1)(E); UNVERIFIED in this run).
  - Segmentation, registration and 3D visualisation fit **21 CFR 892.2050, product code LLZ, Class II, 510(k), not GMP-exempt** ([FDA LLZ](https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD/classification.cfm?ID=LLZ), [Innolitics](https://510k.innolitics.com/submissions/RA/subpart-b%E2%80%94diagnostic-devices/892.2050)). QIH (automated radiological image processing) may fit an AI version better.
  - Predicates exist, so a 510(k) rather than De Novo is the likely route.
- **EU (MDR Rule 11)**
  - Software that gives information used for diagnostic or therapeutic decisions is at least Class IIa.
  - It is Class IIb if a wrong decision could cause serious deterioration of health or a surgical intervention, and Class III if it could cause death or irreversible harm ([MDCG 2019-11](https://health.ec.europa.eu/document/download/b45335c5-1679-4c71-a91c-fc7a4d37f12b_en), [Johner](https://blog.johner-institute.com/regulatory-affairs/mdr-rule-11/)).
  - 3D models for surgical planning are realistically **IIb**. Class I is practically not available.
- **India (CDSCO)**
  - Medical Devices Rules 2017, plus a draft software guidance (21 October 2025) and a final "Guidance Document on Medical Device Software" (CDSCO/MD/GD/MDSW/01/2026, reported as 21 July 2026).
  - Standalone software is Class A–D, based on how serious the condition is and how strongly the output drives care.
  - AI software must be monitored for drift and bias and needs real-world evidence from Indian settings.
  - Cloud software counts as a device when it supports clinical functions.
  - Sources: [CDSCO draft](https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadPublic_NoticesFiles/Draft%20guidance%20document%20on%20Medical%20Device%20Software%2021%2010%202025.pdf), [CDSCO final](https://www.cdsco.gov.in/opencms/export/sites/CDSCO_WEB/Pdf-documents/Guidance-document-on-Medical-Device-Software-under-MDR-2017.pdf), [Cyril Amarchand](https://corporate.cyrilamarchandblogs.com/2026/01/medical-device-as-software-has-cdsco-guidance-changed-the-rules/).
  - Likely Class B or C (UNVERIFIED). Under MDR 2017, Class A/B licences come from state authorities and Class C/D from the central authority (UNVERIFIED in this run).
  - Clinical investigations of investigational devices need ethics committee approval (MDR 2017 r.50) ([MDR 2017 text](https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2022/m_device/mdr,%202017%20(1).pdf)).

### 4.2 What "research use only / not for clinical use" positioning allows
- **Allowed:** research, education and method development. Retrospective research under ethics approval, where outputs do not affect patient care. Showing results to clinicians as research output.
- **Forbidden:**
  - Marketing for diagnosis, surgical planning or "post-diagnosis medical analysis".
  - Claims such as "clinical-grade" or "accurate for surgery".
  - Integrations that feed results into care decisions.
  - Selling it to clinics for patient care.
- **Intended use is judged from all your claims, not only the disclaimer.** FDA says RUO labelling must match the manufacturer's real intended use ([FDA RUO/IUO guidance](https://fda.gov/media/87374/download), [Jama Software](https://www.jamasoftware.com/?p=10988)). The 809.10(c) RUO exemption is formally an IVD rule. For imaging software, the operative concept is intended use plus "not for clinical use" labelling.
- **Comparable cases:**
  - TotalSegmentator's own site calls it a research prototype, not for diagnosis ([totalsegmentator.com](https://totalsegmentator.com/)).
  - NVIDIA VISTA3D is research-only ([NVIDIA model card](https://build.nvidia.com/nvidia/vista-3d/modelcard)).
  - **3D Slicer is not approved for clinical use.** Its BSD-style licence permits commercial use, and products built on it (e.g. Polarean XENOVIEW VDP) were cleared separately ([Slicer about](https://slicer.readthedocs.io/en/latest/user_guide/about.html), [Wikipedia](https://en.wikipedia.org/wiki/3D_Slicer)).
- **Cleared comparators:**

| Product | Clearance | Source |
|---|---|---|
| Materialise Mimics inPrint / Mimics Innovation Suite | First 510(k) for anatomical-model 3D printing software. K-number UNVERIFIED | [Knobbe](https://www.knobbe.com/blog/materialise-receives-first-ever-510k-clearance-anatomical-model-3d-printing-software/) |
| Synopsys Simpleware ScanIP | 510(k) in 2015 | [MEDE](https://mede-innovation.ac.uk/news-article/simpleware-scanip-software-receives-fda-510k-market-clearance/) |
| Simpleware ScanIP Medical | 510(k) in 2021 for point-of-care printing | [News-Medical](https://www.news-medical.net/news/20210614/Simpleware-ScanIP-Medical-Receives-FDA-510(k)-Clearance-for-3D-Medical-Printing.aspx) |
| Axial3D INSIGHT | Cloud AI segmentation, 510(k) July 2023. Orthopaedic, trauma, maxillofacial and (inconsistently reported) cardiovascular. A later record K250369 exists | [BusinessWire](https://www.businesswire.com/news/home/20230731276469/en), [Innolitics K250369](https://510k.innolitics.com/device/K250369) |

  **Axial3D is the closest analogue to what is proposed.**

### 4.3 Staged path
1. **Research demo (now to 6 months):** public, free, research and education only.
   - No clinical claims anywhere: UI, domain name, marketing or README.
   - Browser-first, with server uploads de-identified and deleted within 24 hours.
   - Apache-licensed weights only.
   - Click-through terms with the research-only acknowledgement.
   - Benchmark against public labelled datasets and publish Dice and surface-distance metrics.
2. **Clinical research tool (6–18 months):** named hospital partners under a data processing agreement and ethics committee approval.
   - Retrospective studies, then prospective non-interventional ones.
   - Run on-premises or in a single-tenant regional cloud.
   - Set up a quality management system, software lifecycle, risk management and cybersecurity documentation from the start: ISO 13485, IEC 62304, ISO 14971, IEC 81001-5-1 (standard names UNVERIFIED in this run).
   - Lock model versions, and build a clinical evaluation dataset that includes Indian data, as CDSCO expects.
3. **Regulated product (18 months or more):** pick one narrow indication first, e.g. "segmentation and 3D model generation of bone for orthopaedic and maxillofacial planning", the same way Axial3D started.
   - Commercial weight licences in place.
   - **Regulatory filings:**
     - **India:** CDSCO licence.
     - **US:** FDA 510(k) under LLZ/QIH with Mimics, ScanIP or Axial3D as predicate. A Predetermined Change Control Plan (PCCP) for model updates is UNVERIFIED.
     - **EU:** notified body for IIa/IIb.

---

## 5. Licence implications of hosting
- **Weights vs code:**
  - **TotalSegmentator:** code Apache-2.0. Some tasks (appendicular bones, tissue types, high-res heart chambers, face and others) need a licence key, free only for non-commercial use. Brain aneurysm weights are CC BY-NC 4.0 with no commercial licence available ([GitHub README](https://github.com/wasserth/TotalSegmentator), [totalsegmentator.com](https://totalsegmentator.com/), [libraries.io](https://libraries.io/pypi/TotalSegmentator)).
  - **VISTA3D / NV-Segment-CT:** code Apache-2.0, but the weights are under NVIDIA non-commercial terms. Named variously as OneWay Noncommercial, NCLS v1 or the NVIDIA evaluation licence ([HF NV-Segment-CT](https://huggingface.co/nvidia/NV-Segment-CT/blob/main/README.md), [AMD container](https://hub.docker.com/r/amdenterpriseai/aim-instinct-model-monai-vista3d)). Use it for internal research benchmarking only.
  - A free public demo run by a person who plans to commercialise later is a grey area under "non-commercial". Keep non-commercial weights off the public service unless the author agrees in writing.
  - nnU-Net framework: Apache-2.0 (UNVERIFIED in this run).
- **Copyleft over a network:**
  - **AGPL** (Kaapana; Orthanc PostgreSQL and Web-viewer plugins): if you modify the software and let users interact with it over a network, you must offer them the modified source.
  - **GPL** (Orthanc core): no source duty when you only run it as a service. Talking to it over REST is treated as permissive ([Orthanc FAQ](https://orthanc.uclouvain.be/book/faq/licensing.html)). A derived Stone or Orthanc viewer inside a proprietary portal needs an Osimis dual licence.
  - **LGPL:** fine for dynamic linking. A server-side service triggers nothing.
- **Permissive front-end stack:** OHIF and Cornerstone3D are MIT ([OHIF docs](https://docs.ohif.org/)). 3D Slicer is BSD-style ([slicer.org commercial use](https://slicer.org/commercial-use.html)).
- **Datasets:** model licences can inherit dataset terms. The TotalSegmentator dataset is reportedly CC BY-NC-SA in one secondary source ([noze.it](https://www.noze.it/en/insights/totalsegmentator-whole-body-ct/)) (UNVERIFIED). Check before using it for a demo gallery or fine-tuning.

---

## 6. Recommended architecture

### Phase 1: safe public research demo
```
Browser (Chrome/Edge)
 ├─ Load DICOM folder locally (itk-wasm/dcmjs) → pick CT series, drop scouts/SC/dose reports
 ├─ MODE A (default, no upload): BrowSeg-style WASM/WebGPU TotalSegmentator (Apache tasks)
 │     → marching cubes + smoothing + decimation in a Web Worker → GLB/STL download
 ├─ MODE B (opt-in, "send de-identified volume"): in-browser de-id (Annex E basic + drop private
 │     tags + date removal + deface/crop head) → NIfTI → tus upload (Uppy) with consent checkbox
 └─ Viewer: niivue / VTK.js volume + mesh overlay, label toggles, measurements, "RESEARCH USE ONLY" banner

Server (single India region; EU region later)
 FastAPI (stateless, behind CDN/WAF, rate-limited, no accounts, captcha on upload)
  → tusd → S3-compatible bucket (SSE, lifecycle: uploads 24 h, results 7 d)
  → Redis + Arq queue (one job per volume, size/slice caps)
  → GPU worker on serverless L4/A10G 24 GB (Modal or RunPod), image with baked-in weights, no outbound network
       TotalSegmentator (Apache tasks) [+ --fast fallback] → label map → CPU meshing (VTK flying-edges,
       Taubin smoothing, quadric decimation) → GLB (Draco) + NIfTI seg + JSON volumes
  → result via signed URL; "delete now" button; deletion receipts; logs carry job-id only
Demo gallery: precomputed results from public datasets with compatible licences
```
- Cost: about $0.04 of GPU time per scan, $0 when idle, plus cold start.
- Optional alternative: also publish the container on Grand Challenge, which gives a zero-ops hosted "Try Out" with a T4 and no network access.

### Phase 2: clinical research partners
```
Hospital (edge, inside firewall)            Your regional cloud (single-tenant per partner or region)
 PACS → MONAI Deploy Informatics Gateway     Orthanc or dcm4chee (unmodified, REST only) as research archive
   or Kaapana node (on-prem GPU)             → Celery or Temporal pipeline: QC → TotalSegmentator + task models
 → RSNA Anonymizer/CTP de-id incl. Clean       → meshing → DICOM SEG/RTSTRUCT + GLB/STL back to partner
   Pixel Data, keyed pseudonym map stays     OHIF (MIT) viewer with SEG overlay + 3D mesh panel
   at hospital                               SSO (OIDC), RBAC, audit log, per-study retention by contract,
 → DICOMweb STOW-RS over mTLS                  KMS encryption, India/EU/US region per partner
```
- **Preferred deployment:** inference on-premises inside a Kaapana or MONAI Deploy node. Only de-identified results or metrics leave the hospital.
- **Paperwork:** data processing agreement (DPDP / GDPR Art. 28; a BAA for US partners), ethics committee approval, DPIA, locked model versions.
- **Start the regulated-product groundwork now** (QMS, risk file, software lifecycle docs), so phase-2 evidence can be reused for CDSCO, 510(k) and MDR filings.

---

## 7. UNVERIFIED items to close next
1. Repo stars, last push and archived state for every repo named: GitHub API was not available.
2. Exact TotalSegmentator per-task weight licences: read the README licence table and BrowSeg `weights/MANIFEST.json`.
3. Licences for MONAI Deploy App SDK and Workflow Manager, niivue, itk-wasm, VolView, dcm4chee, XNAT and Girder.
4. Final CDSCO software guidance date and its class examples for imaging software.
5. Exact DPDP dates and penalties from the Gazette.
6. Hosting region of Grand Challenge.
7. Licence of the TotalSegmentator dataset.
8. FDA K-numbers for Mimics and ScanIP.
9. Whether a PCCP pathway applies.