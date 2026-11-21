# Open-source CT segmentation models for a hosted "any body part" CT-to-3D service (research as of 2026-11-21)

**How this was checked**
- Repo stats (licence, stars, last push, archived) come from the GitHub REST API via curl on 2026-11-21. The `gh` CLI is not installed. The API base is `https://api.github.com/repos/OWNER/REPO`.
- Weight licences come from each model's Hugging Face API `cardData.license`, its model card or README, and the repo LICENSE file.
- No repo is archived.
- "UNVERIFIED" means I found no primary source.
- Nothing local was read except public web and API content. No files were changed and nothing was installed.

## 1. Model-by-model findings

### TotalSegmentator (v2.18.0 on PyPI)
- **Repo:** https://github.com/wasserth/TotalSegmentator. Code is Apache-2.0, 3,172 stars, last push 2026-11-20, very active. Version from https://pypi.org/pypi/TotalSegmentator/json.
- **Built on:** nnU-Net v2. Docker image is `wasserth/totalsegmentator`.
- **Coverage (from the README):**
  - `total` has 117 CT classes: organs, all vertebrae, ribs, major muscles and vessels, brain, and some limb bones.
  - `total_mr` has 50 classes.
  - Plus about 40 subtasks.
- **Accuracy:**
  - v2 `total` test set, computed from https://raw.githubusercontent.com/wasserth/TotalSegmentator/master/resources/evaluate_results.txt: mean Dice 0.9275 over 117 classes, mean NSD (3 mm) 0.957.
  - Weak classes: kidney_cyst_right Dice 0.009, kidney_cyst_left 0.347, prostate 0.814.
  - The v1 paper reports Dice 0.943 on 104 classes: https://arxiv.org/abs/2208.05868.
- **Input:**
  - Needs original HU values (not rescaled) and standard patient orientation.
  - The model resamples internally to 1.5 mm (normal) or 3 mm (`--fast`). There is also a 6 mm cropping model.
  - Works on CT and MR. Whether it works across contrast phases is not stated in the README (UNVERIFIED).
  - Options: `--body_seg`, `--force_split`, `--roi_subset`, `--robust_crop`.
- **Runtime and memory:**
  - v1 paper, RTX 3090, 1.5 mm model: 512×512×280 took 1 min 17 s at 6.1 GB GPU; 512×512×458 took 2 min 49 s at 8.5 GB. The 3 mm model took 34 s and 53 s (https://arxiv.org/pdf/2208.05868).
  - README fastest setting: about 30 s on GPU and about 70 s on CPU for a large CT. The README says v2 runtimes are "a bit" higher; the v2 table is an image only.
  - CPU runs are advised to use `--fast` or `--roi_subset`.
- **Outputs useful for QC:**
  - `--statistics` gives volume and mean HU per class. Structures touching the image border are flagged as incomplete and get zero.
  - `--statistics_extra` adds centroid and bounding box.
  - `--report` writes a JSON run manifest.
  - `--radiomics` is available.
- **Hosting gotcha:** it sends anonymous usage statistics by default. Set `send_usage_stats=false` in `~/.totalsegmentator/config.json`.

**Subtasks with open weights (README says "openly available for any usage, Apache-2.0"):**
- total, total_mr
- lung_vessels (arteries, veins, airways, airway wall), body (body, trunk, extremities, skin), body_mr
- vertebrae_mr, vertebrae_pp, vertebrae_pp_refined, vertebrae_body
- cerebral_bleed\*, hip_implant\*, pleural_pericard_effusion\*
- head_glands_cavities, head_muscles, headneck_bones_vessels, headneck_muscles
- liver_vessels\*, liver_segments\*, liver_segments_mr\*, liver_lesions\*, liver_lesions_mr\*
- oculomotor_muscles\*, lung_nodules, kidney_cysts, breasts
- craniofacial_structures, abdominal_muscles\* (T4–L4 only), teeth (ToothFairy3), trunk_cavities, ventricle_parts

\* = the README says these were trained on small datasets and are "less robust".

**Subtasks that need a licence key** (free for non-commercial use at https://backend.totalsegmentator.com/license-academic/; commercial licence by contacting the author):
- heartchambers_highres
- appendicular_bones and appendicular_bones_mr (patella, tibia, fibula, tarsal, metatarsal, foot phalanges, ulna, radius, carpal, metacarpal, hand phalanges)
- tissue_types, tissue_types_mr, tissue_4_types
- brain_structures (CT)
- face and face_mr (for anonymisation)
- thigh_shoulder_muscles and thigh_shoulder_muscles_mr
- coronary_arteries, aortic_sinuses
- brain_aneurysm: CC BY-NC 4.0, and the README says no commercial licence is available.
- The `totalseg_get_body_stats` and aorta report tools also need a licence.
- `heartchambers` in your list is the licensed `heartchambers_highres` task. Vertebrae for CT are part of `total` and the open `vertebrae_*` tasks.

**Other notes:**
- Interactive correction: none built in. You would pair it with nnInteractive or VISTA3D.
- ONNX/WebGPU: a community ONNX port of `total_fast` exists. It runs one fold in the browser and its card says the weights are CC-BY-NC-4.0, which conflicts with upstream Apache (https://huggingface.co/Aralario/totalsegmentator-onnx-total-fast).
- An INT8 ONNX port for CPU also exists (https://huggingface.co/sigjhl/ts-portable). Not vetted.

### nnU-Net v2
- **Repo:** https://github.com/MIC-DKFZ/nnUNet. Apache-2.0, 9,060 stars, last push 2026-11-20.
- **What it is:** a training and inference framework, not a pretrained model. TotalSegmentator, CADS, MOOSE, STU-Net, nnInteractive and VoxTell are all built on it.
- **Inference features:** sliding window, mirroring test-time augmentation, 5-fold ensembling, `--save_probabilities`. Probabilities are the main input for uncertainty QC and soft label fusion.
- **Use for you:** train extra region models (for example hand or foot) on public data with ResEnc presets.
- **ONNX export:** possible but painful (https://github.com/MIC-DKFZ/nnUNet/discussions/1144).

### MONAI, Auto3DSeg, model zoo, MONAI Label
- **Repos:**
  - MONAI core: https://github.com/Project-MONAI/MONAI. Apache-2.0, 8,858 stars, last push 2026-11-18.
  - Model zoo: https://github.com/Project-MONAI/model-zoo. Apache-2.0, 349 stars, last push 2026-11-17.
  - MONAI Label: https://github.com/Project-MONAI/MONAILabel. Apache-2.0, 912 stars, last push 2026-11-19.
- **Auto3DSeg:** trains SegResNet, DiNTS and SwinUNETR with 5-fold cross-validation and ensembles them (https://arxiv.org/html/2310.04110v1). It is AutoML for your own labelled data, not a pretrained whole-body model.
- **Zoo bundle `wholeBody_ct_segmentation`:**
  - SegResNet trained on TotalSegmentator v1 data, 104 structures. The 3 mm model is the low-memory option.
  - The bundle README lists ≥48 GB GPU, and says rights stay with the dataset authors (https://github.com/Project-MONAI/model-zoo/tree/dev/models/wholeBody_ct_segmentation).
  - It is inferior in coverage to TotalSegmentator v2. Weight licence UNVERIFIED (the HF card has no licence field).
- **MONAI Label:** a server with 3D Slicer and OHIF clients. It hosts VISTA3D for automatic plus click-based segmentation (https://github.com/Project-MONAI/VISTA). I found no source for an official nnInteractive integration.

### NVIDIA VISTA3D / NV-Segment-CT and NV-Segment-CTMR
- **Repos:**
  - https://github.com/NVIDIA-Medtech/NV-Segment-CTMR: no licence detected by GitHub, 91 stars, last push 2026-10-05.
  - https://github.com/Project-MONAI/VISTA: Apache-2.0, 299 stars, last push 2026-10-06.
- **NV-Segment-CT (VISTA3D):**
  - 127 supported classes (132 in the label dictionary, 5 legacy), including 7 tumour types. Trained on 11,454 CT volumes.
  - Automatic segmentation plus zero-shot point-click interactive segmentation (https://arxiv.org/abs/2406.05285, CVPR 2025).
  - Weights licence: **NVIDIA Open Model License, "commercial friendly"** per the current HF card (https://huggingface.co/nvidia/NV-Segment-CT). An older revision of that card said "NCLS v1" (https://huggingface.co/nvidia/NV-Segment-CT/blame/cbd5451ec9302ae2b88108da04a03b8bc2f3e76b/README.md). Pin the revision you deploy.
  - Input: 1.5 mm isotropic, 128³ sliding window.
  - Runtime on a 16 GB V100 (https://huggingface.co/MONAI/vista3d/blob/main/docs/README.md):
    - 333×333×603: 1 min 07 s
    - 512³: 2 min 09 s
    - 512×512×768: 3 min 25 s
    - 1024×1024×512: 9 min 20 s
    - 1024×1024×768: killed
  - About 16 GB GPU is needed at the default `max_prompt_size` of 4. One click takes about 3.2 s on a 12 GB GPU.
  - The paper reports 1 min 43 s for VISTA3D versus 2 min 41 s for TotalSegmentator on a 12 GB GPU.
  - Headline average Dice across 127 classes: UNVERIFIED.
  - A managed NIM endpoint exists (https://build.nvidia.com/nvidia/vista-3d).
- **NV-Segment-CTMR:**
  - 345+ classes, CT and MRI (body and brain), **automatic only**.
  - **NVIDIA OneWay Non-Commercial licence, academic research only** (https://huggingface.co/nvidia/NV-Segment-CTMR).
- ONNX/WebGPU: SegResNet-based, so export is plausible but undocumented (UNVERIFIED). The interactive branch complicates export.

### MedSAM and MedSAM2
- **Repos:**
  - MedSAM (2D): https://github.com/bowang-lab/MedSAM. Apache-2.0, 4,453 stars, last push 2025-05-07.
  - MedSAM2: https://github.com/bowang-lab/MedSAM2. Apache-2.0, 741 stars, last push 2025-07-11. Stale-ish.
- **What it is:** SAM2 fine-tuned on 3D images and video. Prompts are a box or a point on one slice, then propagated through the volume. Training data includes 363,161 CT 3D image-mask pairs (https://arxiv.org/abs/2504.03600).
- **Weights licence conflict:** HF metadata says cc-by-sa-4.0, but the card text says weights are "for research and education purposes" only (https://huggingface.co/wanglab/MedSAM2). Treat as non-commercial.
- CT Dice: UNVERIFIED.
- No fixed class list; it segments what you prompt.

### SAM-Med3D (turbo)
- **Repo:** https://github.com/uni-medical/SAM-Med3D. Apache-2.0 code, 972 stars, last push 2025-09-21.
- **What it is:** 3D point-prompt SAM. Trained on SA-Med3D-140K: 22K images, 143K masks, 245 categories (https://arxiv.org/abs/2310.15161).
- **Speed:** 128³ crop, about 6 s per inference (README).
- **Dice:** the README gives conflicting numbers, 80.71 versus 62.72 at 10 points. UNVERIFIED.
- Weights licence: UNVERIFIED (https://huggingface.co/blueyo0/SAM-Med3D has no licence field).

### nnInteractive (DKFZ)
- **Repo:** https://github.com/MIC-DKFZ/nnInteractive. Code Apache-2.0, 529 stars, last push 2026-11-19.
- **Weights licence:** **CC BY-NC-SA 4.0** (https://huggingface.co/MIC-DKFZ/nnInteractive). The repo README repeats this.
- **Prompts:** points, boxes, scribbles and lasso, positive and negative, fully 3D (https://arxiv.org/abs/2503.08373).
- **Hardware:** about 10 GB VRAM recommended, under 6 GB for small objects (README). About 120–200 ms per interaction on an RTX 4090 (secondary source: https://creatis-myriad.github.io/2026/03/19/nnInteractive.html).
- **Deployment:**
  - Remote HTTP inference server with multiple concurrent sessions (05/2026).
  - Docker image (06/2026). See the README news section.
  - Clients: napari (https://github.com/MIC-DKFZ/napari-nninteractive, Apache-2.0, 126 stars), MITK, and a 3D Slicer client-server extension (https://www.theoj.org/joss-papers/joss.09003/10.21105.joss.09003.pdf).
- **Dice:** 54.3 with one click, 78.1 with a box, 67.3 with scribbles. These come from a secondary summary (https://alphaxiv.org/overview/2503.08373v1). UNVERIFIED against the paper.
- **Assessment:** best-in-class 3D interactive refinement, but the non-commercial weights block commercial hosting.

### SegVol
- **Repo:** https://github.com/BAAI-DCAI/SegVol. MIT code, 394 stars, last push 2026-01-11.
- **What it is:** NeurIPS 2024 model with point, box and text prompts. Pretrained on 90k–96k unlabeled CT plus 6k labelled CT. Evaluated externally on ULS23 lesions and AMOS22 (https://arxiv.org/abs/2311.13385).
- Category count: about 200 (UNVERIFIED).
- Weights licence: UNVERIFIED (https://huggingface.co/BAAI/SegVol has no licence field).
- Dice: shown only as images in the README.

### SuPreM
- **Repo:** https://github.com/MrGiovanni/SuPreM. GitHub reports NOASSERTION, 436 stars, last push 2026-10-26.
- **Licence conflict:** the repo LICENSE file is **CC BY-NC-ND 4.0** (https://raw.githubusercontent.com/MrGiovanni/SuPreM/main/LICENSE), but the HF card says apache-2.0 (https://huggingface.co/MrGiovanni/SuPreM). Treat as NC-ND.
- **What it is:** pretrained backbones (SwinUNETR, U-Net, SegResNet) supervised on AbdomenAtlas, 2,100 CT, 25 classes. Meant for transfer learning, not whole-body.

### STU-Net
- **Repo:** https://github.com/uni-medical/STU-Net. Apache-2.0, 374 stars, **last push 2024-09-03, stale**.
- **What it is:** nnU-Net-based models from 14M up to 1.4B parameters (STU-Net-H), trained on TotalSegmentator v1 (104 classes).
- **Accuracy:** STU-Net-H reaches 90.06% mean DSC on TotalSegmentator per the project page (https://uni-medical.github.io/gmai-web/projects/stu-net/, https://arxiv.org/abs/2304.06716).
- **Assessment:** heavy, and superseded in coverage by TotalSegmentator v2 and CADS.

### MOOSE 3.2
- **Repo:** https://github.com/ENHANCE-PET/MOOSE. Code Apache-2.0, **model weights CC BY 4.0** per the README badge (commercial use allowed with attribution). A separate commercial version is offered. 352 stars, last push 2026-10-31.
- **CT tasks:** clin_ct_body (legs, body, head, arms), clin_ct_cardiac, clin_ct_digestive, clin_ct_lungs, clin_ct_muscles, clin_ct_organs, **clin_ct_peripheral_bones** (carpals, clavicles, femur, fibula, fingers, humerus, metacarpals and more), clin_ct_ribs, clin_ct_vertebrae, clin_ct_body_composition. There are also preclinical tasks.
- **Requirements:** at least 16 GB RAM; GPU optional (README).
- Dice: UNVERIFIED (my search did not find the paper).

### CADS
- **Repo:** https://github.com/murong-xu/CADS. Code Apache-2.0, 108 stars, last push 2026-11-09.
- **Coverage:** 167 structures, head to knee. Trained on 22,022 CT volumes from about 40 sources (https://arxiv.org/abs/2507.22953).
- **Architecture:** 9 region-specific nnU-Net models (tasks 551–559), reported as ResEnc-L (secondary: https://pycad.co/blog/cads-whole-body-ct-segmentation-167-structures/).
- **Weights (README, July 2026), three variants:**
  - `reference`: custom CADS licence.
  - `research`: CC BY-NC-SA 4.0.
  - **`open`: CC BY-SA 4.0**, commercial use allowed under share-alike. May be weaker on some head-and-neck structures.
  - A separate `CADS-region` suite exists under the custom licence.
- **Accuracy:** mean Dice 90.52 versus 88.12 for TotalSegmentator on the 117 shared structures, better on 15 of 18 public test sets. This comes from a secondary source (pycad blog). UNVERIFIED against the paper.
- Has a `--cpu` mode and a staged CPU/GPU pipeline. A 3D Slicer extension exists. GPU memory and runtime: UNVERIFIED.

### 2025–2026 whole-body and universal models
- **VoxTell** (CVPR 2026, DKFZ): https://github.com/MIC-DKFZ/VoxTell.
  - Code Apache-2.0, 291 stars, last push 2026-10-10. **Weights CC BY-NC-SA 4.0** (https://huggingface.co/mrokuss/VoxTell).
  - Free-text prompt to 3D mask, across CT, PET and MR. Trained on 62K+ volumes from 158 sources (https://arxiv.org/abs/2511.11450).
  - Uses the frozen Qwen3-Embedding-4B text encoder; common prompts are precomputed. Has a remote server mode.
  - VRAM: UNVERIFIED.
  - Useful as a text-prompted fallback for structures without a fixed label.
- **CT-FM:** https://github.com/project-lighter/CT-FM. MIT code, 90 stars, last push 2026-04-22.
  - Self-supervised foundation model on 148K CT scans. Its whole-body segmentation head covers 117 labels (https://arxiv.org/abs/2501.09001).
  - HF weights are apache-2.0 (https://huggingface.co/project-lighter/whole_body_segmentation).
  - Commercially clean, but less adopted.
- **OpenVocabCT** (IEEE TMI 2026, text-driven CT): weight release UNVERIFIED (https://pubmed.ncbi.nlm.nih.gov/41411350/).
- **SAT3D** (tumour-focused, MIT code): https://arxiv.org/html/2511.09592v1.
- **Whole-body FDG PET/CT tumour model:** https://arxiv.org/abs/2605.21835. Needs PET, so it does not fit a CT-only service.

## 2. Comparison table

| Model | Structures / scope | Reported accuracy | Interactive | Code licence | Weight licence | GPU / runtime | Stars, last push | Browser / ONNX |
|---|---|---|---|---|---|---|---|---|
| TotalSegmentator v2 | 117 (`total`) plus about 40 subtasks, whole body, CT and MR | Dice 0.928, NSD 0.957 (v2 test set) | No | Apache-2.0 | `total` and most subtasks Apache; about 17 tasks licence-only (NC free); brain_aneurysm NC only | 6–8.5 GB, about 1–3 min (3090, v1); `--fast` about 30 s GPU / 70 s CPU | 3,172; 2026-11-20 | Community ONNX port of total_fast (NC weights) |
| CADS | 167, head to knee, 9 nnU-Net models | Dice 90.5 vs TotalSeg 88.1 (secondary) | No | Apache-2.0 | reference custom / research CC BY-NC-SA / **open CC BY-SA** | UNVERIFIED (9 ResEnc-L models, slower) | 108; 2026-11-09 | Possible via nnU-Net export (unproven) |
| NV-Segment-CT (VISTA3D) | 127 plus 7 tumour types | Avg Dice UNVERIFIED | Yes, points | Apache-2.0 | **NVIDIA Open Model License (commercial friendly)**; older card said NCLS | 16 GB; 1–9 min (V100); 3.2 s per click | 299 / 91; 2026-10 | UNVERIFIED |
| NV-Segment-CTMR | 345+ classes, CT and MR | UNVERIFIED | No | Apache-2.0 | NVIDIA OneWay **Non-Commercial** | as VISTA3D | 91; 2026-10-05 | UNVERIFIED |
| MOOSE 3.2 | about 10 CT task groups incl. peripheral bones, ribs, muscles | UNVERIFIED | No | Apache-2.0 | **CC BY 4.0** | ≥16 GB RAM, GPU optional | 352; 2026-10-31 | nnU-Net based |
| nnInteractive | Any structure (prompted) | 1 click 54.3 / box 78.1 Dice (secondary) | **Yes**: point, box, scribble, lasso | Apache-2.0 | **CC BY-NC-SA 4.0** | ~10 GB; 120–200 ms (4090) | 529; 2026-11-19 | Server mode; browser not practical |
| VoxTell | Free text, 200+ concepts (secondary), CT/MR/PET | UNVERIFIED | Text | Apache-2.0 | **CC BY-NC-SA 4.0** | UNVERIFIED (+4B text encoder) | 291; 2026-10-10 | No |
| MedSAM2 | Prompted, 3D and video | UNVERIFIED | Box / point | Apache-2.0 | Research and education only (conflicts with HF tag cc-by-sa) | UNVERIFIED | 741; 2025-07-11 | SAM2 ONNX ports exist (UNVERIFIED) |
| SAM-Med3D-turbo | Prompted, 245 categories | Conflicting (62.7–80.7 at 10 points) | Points | Apache-2.0 | UNVERIFIED | 128³, about 6 s | 972; 2025-09-21 | Plausible (small ViT) |
| SegVol | Prompted plus text, about 200 categories (UNVERIFIED) | Figures only | Point / box / text | MIT | UNVERIFIED | UNVERIFIED | 394; 2026-01-11 | UNVERIFIED |
| CT-FM | 117 (whole-body head) | UNVERIFIED | No | MIT | apache-2.0 | UNVERIFIED | 90; 2026-04-22 | Plausible |
| STU-Net | 104 (TotalSeg v1) | H: 90.06 mean DSC | No | Apache-2.0 | UNVERIFIED | Up to 1.4B parameters, heavy | 374; **2024-09-03** | Heavy |
| SuPreM | 25 abdominal (backbone) | n/a | No | CC BY-NC-ND (LICENSE) vs apache (HF) | as code | n/a | 436; 2026-10-26 | n/a |
| MONAI wholeBody bundle | 104 | UNVERIFIED | No | Apache-2.0 | UNVERIFIED | ≥48 GB GPU (bundle README) | 349 (zoo); 2026-11-17 | MONAI export tooling |
| nnU-Net v2 / Auto3DSeg / MONAI Label | Frameworks | n/a | MONAI Label: yes | Apache-2.0 | n/a | n/a | 9,060 / 8,858 / 912 | — |

## 3. Recommendations

The choice of models depends first on licence:
- **Licence decision first:** if the hosted service is ever commercial, or even ambiguous (free public tool with donations, a later spin-off), the NC and NC-SA weights are out: nnInteractive, VoxTell, NV-Segment-CTMR, CADS research, MedSAM2, and the licensed TotalSegmentator subtasks. Get legal review of "non-commercial" for a public web service.
- **Regulation:** marketing outputs for "post-diagnosis medical analysis" may make the service a medical device (FDA SaMD / EU MDR). None of these models is cleared. Label outputs research-only.

**(a) Primary automatic whole-body model**
- Use **TotalSegmentator v2 `total`** plus its open subtasks. Reasons: most maintained, Apache weights, built-in statistics and run manifest, CPU fallback.
- Choose open subtasks by body region found in the scan: body, lung_vessels, head_glands_cavities, headneck_\*, craniofacial_structures, trunk_cavities, vertebrae_pp_refined, liver_segments.
- Run **CADS `open` (CC BY-SA)** alongside it, or `research` if strictly non-commercial. It adds 50 extra structures, mostly head/neck and radiotherapy, and gives an independent second model for agreement QC.
- Add **VISTA3D (NV-Segment-CT)** as a third, commercially usable model.

**(b) Interactive / prompted refinement**
- Non-commercial: **nnInteractive**. It has 3D points, boxes, scribbles and lasso, an official Docker remote server, and fits about 10 GB GPUs.
- Commercial-safe: **VISTA3D point-click** (NVIDIA Open Model License). Next options are SAM-Med3D-turbo, but only after confirming its weight licence, and MedSAM2, which is research-only.

**(c) Fallback for unusual body parts (hands, feet, extremities, implants, unlabelled structures)**
- **MOOSE `clin_ct_peripheral_bones`**: CC BY 4.0, commercial-OK.
- TotalSegmentator `appendicular_bones`: needs a licence.
- TotalSegmentator `body` plus `hip_implant`.
- **VoxTell** text prompts (non-commercial).
- Last resort: the current classical HU threshold for bone and skin surface, produced only inside the TotalSegmentator `body` mask.
- If it is worth the effort, train your own nnU-Net or Auto3DSeg model for missing regions on public data.

**(d) How to ensemble and merge label maps**
1. **Canonical ontology:** map every model's labels to one table (TotalSegmentator names plus a SNOMED-CT or FMA code, side, and parent structure). Keep a per-structure source-priority list, ranked by your own validation Dice per structure, not by overall averages.
2. **Common grid:** resample all outputs to the original CT geometry with nearest-neighbour, or linear on probabilities. Use TotalSegmentator `--higher_order_resampling` for smoother masks.
3. **Fusion:**
   - Where probabilities exist (nnU-Net `--save_probabilities` gives softmax), average probabilities per canonical class.
   - Otherwise, for 3 or more models, use SimpleITK `MultiLabelSTAPLE` or majority vote. With 2 models, take the higher-priority model's label.
4. **Overlaps and topology:**
   - Resolve overlaps with exclusive argmax inside each region group.
   - Enforce hierarchy: liver segments inside the liver, vessels may pass through organs, everything inside the body mask.
   - Keep the largest connected component for single organs; keep two components for paired structures only when split by laterality.
   - Fill holes, then apply light morphological closing.
5. **Meshing:** run marching cubes per label on the fused probability or smoothed mask, then Taubin or windowed-sinc smoothing and decimation. Keep the per-structure source (which model or models produced it) as mesh metadata.

**(e) Quality-control signals**
- **Input QC:**
  - HU range check, to catch rescaled data.
  - Orientation and direction cosines (a known SimpleITK error source per the TotalSegmentator README).
  - Slice thickness: flag above 3 mm.
  - Field of view and region detection from TotalSegmentator `body` and `total`.
  - Contrast phase: `totalseg_get_phase` is legacy; `totalseg_get_body_stats` is better but licensed.
- **Model disagreement as a proxy for uncertainty:** per-structure Dice and Hausdorff distance between TotalSegmentator, CADS and VISTA3D. Flag Dice below about 0.8 for review or for interactive refinement. The 0.8 threshold is my suggestion, not taken from a source.
- **Voxel uncertainty:** softmax entropy, nnU-Net fold or mirroring test-time-augmentation variance. Show an uncertainty heat map on the mesh.
- **Anatomical sanity:**
  - Volume against reference ranges, using TotalSegmentator `--statistics` volumes.
  - Mean HU inside each mask (for example, bone well above soft tissue).
  - Connected-component counts.
  - Left/right volume ratio.
  - Cranio-caudal ordering of vertebra and rib centroids (`--statistics_extra`).
  - Treat border-truncated structures as incomplete; TotalSegmentator does this by default.
- **Accuracy against reference:** keep a held-out benchmark from public labelled CT (the TotalSegmentator dataset and the CADS dataset at https://huggingface.co/datasets/mrmrx/CADS-dataset; verify each dataset's licence). Track Dice and NSD per structure on every model or version change, and record the model version from the TotalSegmentator `--report` manifest.

**Browser / WebGPU feasibility (my assessment, not from a source):**
- Whole-body 3D sliding-window inference at 1.5 mm takes minutes on a 3090. Exports of these models are community efforts, and fp16 WebGPU exports often need graph surgery (https://onnxruntime.ai/docs/execution-providers/WebGPU-ExecutionProvider.html).
- Practical split: run inference on a server GPU; in the browser do only rendering (meshes or volume rendering) and possibly a small prompt model.
- The only shown browser port is TotalSegmentator `total_fast`, single fold, with NC weights.

**Not verified** (search needed before relying on these):
- VISTA3D average Dice.
- MOOSE Dice.
- CADS runtime and GPU memory, and the exact CADS Dice from the paper.
- Weight licences for SegVol and SAM-Med3D.
- MedSAM2 CT Dice.
- VoxTell VRAM and exact class count.
- TotalSegmentator v2 runtime table (it is an image only).
