# GPU decision for the CT worker (S5 hosting readiness)

Status: decision of 2026-11-22, to be re-checked when the owner opens the Google Cloud project.
Prices are list prices in US dollars as published by Google or by price trackers on the dates below; the owner's billing account may show INR. Conversion used here: **₹88 per US$** (assumption; re-check).

## 1. Decision

**Primary: Cloud Run *jobs* with one NVIDIA L4 GPU in asia-south1 (Mumbai).**
One CT = one job execution (`python -m emgmirror.ctservice.worker run --job-id ID`), started by a Pub/Sub → Eventarc → Workflows dispatcher. Scales to zero, billed per second, data stays in India, nothing to patch.

**Blocking caveat:** Google lists asia-south1 for Cloud Run GPUs **by invitation only**. The owner must ask for access (account team or the request form linked from the Cloud Run GPU page) in the same message as the other grants (see [DEPLOY_RUNBOOK.md](DEPLOY_RUNBOOK.md) §A).

**Fallback 1 (if the invitation is not granted within about two weeks): Compute Engine G2 (L4) VMs in asia-south1**, Spot first and on-demand as backup, in a regional managed instance group that scales to zero on queue depth. It is generally available in Mumbai and keeps data in India. It needs a GPU quota request and a small follow-up module (a pull-based worker VM), about 1–2 days of work. It is not coded in this milestone.

**Fallback 2 (if no GPU quota is granted in India at all):** run the same container **on CPU** as a Cloud Run job in asia-south1 (8 vCPU, 32 GiB, 24 h job limit). It is compliant but slow: tens of minutes per CT, to be measured. Regions outside India (e.g. asia-southeast1 Singapore, where Cloud Run L4 is generally offered) may be used **only for public-data benchmarks**, never for uploads.

**Plain answer to "is there any GPU in India on Google Cloud?":** yes.
- Compute Engine offers L4 (G2) in asia-south1-a/b/c and T4 (N1+T4) in asia-south1-a/b (documentation snapshots, see sources).
- Cloud Run L4 jobs list asia-south1, invitation only.

## 2. Options compared

Workload: TotalSegmentator `total` plus region subtasks, then SurfaceNets meshing, about **2–10 min per CT on an L4** (PLAN/critique estimate, to be measured), one CT per GPU, bursty, mostly idle.

| Option | In India? | Scale to zero | Cold start (estimate) | Cost per CT (typical 7 min incl. start) | Quota / access | Ops burden | Verdict |
|---|---|---|---|---|---|---|---|
| **Cloud Run jobs + L4** | asia-south1 listed, **invitation only** | Yes, per execution | 1–3 min (pull of a ~10–15 GB image with baked weights, model load); Google advertises ~5 s GPU instance start, image pull dominates | **≈ $0.17 (₹15)**; range $0.07–0.31 (₹6–27) | 3 L4 auto-granted per region on first deploy (zonal redundancy off); more by request | Lowest: no VMs, no drivers, no OS patching | **Chosen** |
| Cloud Run GPU *service* (push to `/pubsub`) | asia-south1 listed for **RTX PRO 6000 only**, invitation; L4 services in asia-south1 not confirmed | Yes | Same as above | L4 rate if it arrives; RTX PRO 6000 costs more | As above | Low | Supported by `worker_mode = "service"` if L4 services open in Mumbai |
| Compute Engine G2 Spot, MIG 0→N | **Yes, GA** (asia-south1-a/b/c) | Yes, via autoscaler on queue depth | 3–6 min (boot, driver, image pull) + scale-in delay | Spot g2-standard-8 ≈ $0.45/h → **≈ $0.15** per CT with boot and idle tail; less when jobs are batched; preemption can kill a run | GPU quota is usually 0 on new projects: request `NVIDIA_L4_GPUS` / `PREEMPTIBLE_NVIDIA_L4_GPUS` in asia-south1 and `GPUS_ALL_REGIONS` | High: images, drivers, OS patching, preemption handling | **Fallback 1** |
| Compute Engine G2 on-demand | Yes | Yes (same MIG) | Same | g2-standard-8 ≈ $0.89/h → ≈ $0.30 | Same quota | High | Backup inside fallback 1 |
| Compute Engine N1 + T4 | Yes (asia-south1-a/b) | Yes | Same | Cheaper per hour, slower (16 GB, older GPU) | T4 quota | High | Only if L4 quota is refused |
| GKE Autopilot + L4 | Yes (follows Compute Engine zones) | Pods yes; nodes take minutes to appear | 4–8 min (node provisioning + pull) | Autopilot GPU premium over the VM price; cluster fee $0.10/h, offset by the one-cluster free tier | GPU quota as Compute Engine | Highest (cluster, node pools, upgrades) | Rejected: too much platform for one queue |

Why Cloud Run jobs win, even though Spot VMs can be cheaper per GPU-hour:
1. **Residency:** Cloud Run states that customer data of a resource is stored in its selected region. The job runs in asia-south1, the buckets, database, Pub/Sub messages (message storage policy) and logs are pinned there too.
2. **Scale-to-zero is exact:** billing stops when the execution ends. No idle tail, no autoscaler cooldown, no preemption.
3. **Smaller attack surface for a medical device:** no VM, no SSH, no OS or driver patching (IEC 81001-5-1 maintenance burden), and the network rules are declared in Terraform (no internet egress).
4. **Cost difference is small at this volume:** at hundreds of CTs before the credits expire, the gap between ≈ ₹15 and ≈ ₹13 per CT is a few thousand rupees. It does not outweigh the operational risk.
5. **Contract fit:** the runtime contract already defines the `worker run --job-id` entry point for Cloud Run Jobs.

## 3. Cost per CT on Cloud Run jobs

Rates (Cloud Run pricing page, instance-based billing; asia-south1 is a **Tier 1** Cloud Run region per the Cloud Run locations page):

| Item | Rate | Per second for the default worker (8 vCPU, 32 GiB, 1 L4) |
|---|---|---|
| NVIDIA L4, no zonal redundancy | $0.0001867 / s | $0.0001867 |
| vCPU | $0.000018 / vCPU-s (Tier 1) | $0.000144 |
| Memory | $0.000002 / GiB-s (Tier 1) | $0.000064 |
| **Total** | | **$0.000395 / s ≈ $1.42 / h** |

The GPU line is from the official pricing table (it is the same for jobs and services). The CPU and memory lines are the Tier 1 list rates, which this session could not re-read directly (the pricing page renders client-side). Re-check both in the console before relying on them.

| Case | Billed time | US$ | ₹ |
|---|---|---|---|
| Small scan, warm-ish start | 3 min | 0.07 | 6 |
| Typical (6 min run + 1 min start) | 7 min | 0.17 | 15 |
| Large scan, slow start | 13 min | 0.31 | 27 |

Cheaper variant: 4 vCPU / 16 GiB (the L4 minimum) costs about $1.05/h, but meshing is CPU-bound, so measure both on the S4 load test before switching (`worker` module `cpu` / `memory`).

GPU work is not covered by the Cloud Run free tier, and committed-use discounts do not apply to the GPU part.

## 4. Budget against the ₹30,000 credits (expire 13 Jan 2027)

From 22 Nov 2026 to 13 Jan 2027 is about **52 days (1.7 months)**.

Fixed monthly cost of **staging** (scale-to-zero API and worker; estimates from list prices):

| Item | US$/month | Note |
|---|---|---|
| Two global forwarding rules (HTTPS + HTTP redirect) | ~18 | first 5 rules billed as a block |
| Cloud Armor Standard: 1 policy + ~13 rules | ~18 | + $0.75 per million requests |
| Cloud SQL `db-f1-micro`, 10 GB SSD, backups | ~12–15 | shared core; fine for staging only |
| KMS (7 keys), Secret Manager, private DNS zone | ~2 | |
| Artifact Registry (worker image ~10–15 GB, kept versions dedupe layers) | ~2–10 | cleanup policy keeps 20 versions + releases |
| Logging (180-day security bucket), Monitoring, CDN, Workflows, Pub/Sub | ~1–5 | small volumes |
| **Staging total** | **≈ $55–70 (₹4,800–6,200)** | |

Prod with `db-custom-1-3840` REGIONAL (HA) adds **≈ $140–170/month (₹12,000–15,000)**.

**Recommendation: run only staging on the credits.** Create prod only when the S6 demo is ready, or create it with `db_availability_type = "ZONAL"` and keep it at zero traffic.

| Use of credits until 13 Jan 2027 | ₹ |
|---|---|
| Staging fixed cost, 1.7 months | ~8,000–10,500 |
| Contingency (egress, logs, mistakes) | ~3,000 |
| **Left for GPU jobs** (S1/S2 benchmarks on public data, S4 load test of 10 parallel jobs, demo) | **~16,500–19,000** |
| At ₹15 per typical CT | **≈ 1,100–1,250 CTs** |
| At ₹27 per large CT | ≈ 600–700 CTs |

This is consistent with the plan's earlier "600–1,700 scans" estimate.

The budget in `infra/modules/budget` **excludes credits** (`EXCLUDE_ALL_CREDITS`), so it measures credit burn. It alerts at 25/50/75/90 % of ₹30,000 actual spend and at 100 % of forecast. A second monthly budget, which includes credits, alerts on real money after expiry. A GitHub Actions reminder opens an issue 31, 14, 7 and 1 day(s) before 13 Jan 2027.

**Check the credit type first.** A 90-day credit window ending 13 Jan 2027 looks like the Google Cloud **Free Trial**. Free Trial accounts cannot use GPUs and have tight quotas. The owner must **activate a full (paid) billing account** before any GPU work. According to Google's Free Trial terms, the remaining credit carries over until it expires; confirm this on the billing page.

## 5. Quota and access the owner must request

1. **Cloud Run GPU in asia-south1:** invitation or allowlisting (Google account team or the form linked from the Cloud Run GPU documentation). After access, the first deployment grants 3 L4 GPUs without zonal redundancy. Request 4–6 for the 10-parallel load test (jobs queue beyond quota).
2. **Fallback quota, requested in parallel so it is ready:** Compute Engine `NVIDIA_L4_GPUS` = 2 and `PREEMPTIBLE_NVIDIA_L4_GPUS` = 4 in asia-south1, and `GPUS_ALL_REGIONS` = 4.

## 6. What the Terraform implements

- `infra/modules/worker`, `mode = "job"` (default):
  - `google_cloud_run_v2_job`: 1 × `nvidia-l4`, 8 vCPU / 32 GiB, `gpu_zonal_redundancy_disabled = true`, 30 min timeout, 1 retry, Direct VPC egress `ALL_TRAFFIC` into a VPC with no internet route;
  - a Workflows dispatcher, triggered by Eventarc on the jobs topic, that validates `job_id` and starts the job with `--job-id`. A bad message or a failed run is published to the dead-letter topic.
- `mode = "service"`: a Cloud Run GPU service with concurrency 1 and Pub/Sub **push** to `POST /pubsub`, with a dead-letter policy (5 attempts).
- `worker_launch_stage` lets the owner set `BETA` if Google still marks GPU in the region as preview.

## 7. To measure in S4 and then update this file

- Real per-CT runtime on an L4 for the device build (`total` only) and the research build (subtasks).
- Cold start: image pull and model load, with weights baked into the image.
- 8 vs 4 vCPU for meshing.
- Peak GPU memory (the L4 has 24 GB).

## Sources (accessed 2026-11-22)

Google documentation (several language/version snapshots of the same pages disagreed; the live page wins):
- Cloud Run GPU for services (regions per GPU type, invitation-only note for asia-south1): https://docs.cloud.google.com/run/docs/configuring/services/gpu
- Cloud Run GPU for jobs (asia-south1 listed; zonal redundancy must be off; 3 L4 auto-granted; min 4 CPU / 16 GiB): https://docs.cloud.google.com/run/docs/configuring/jobs/gpu
- Cloud Run GPU for worker pools (asia-south1 listed for L4, invitation): https://docs.cloud.google.com/run/docs/configuring/workerpools/gpu
- Cloud Run locations (asia-south1 = Tier 1 pricing; GPU invitation note; data stored in the selected region): https://docs.cloud.google.com/run/docs/locations
- Cloud Run pricing (L4 $0.0001867/s non-zonal, $0.0002909/s zonal; no free tier for GPU): https://cloud.google.com/run/pricing
- Compute Engine GPU regions and zones (asia-south1-a/b: G2, N1+T4; asia-south1-c: G2): https://docs.cloud.google.com/compute/docs/gpus/gpu-regions-zones
- g2-standard-8 in asia-south1, Spot ≈ $0.45/h, on-demand ≈ $0.89/h (price trackers, secondary): https://sparecores.com/server/gcp/g2-standard-8 , https://gcloud-compute.com/g2-standard-8.html
- Cloud Run GPU GA (June 2025) and jobs GPU history (secondary): https://www.infoq.com/news/2025/06/google-cloud-run-nvidia-gpu/
- Cloud Run GPU vs GKE (invitation-only note for Mumbai in 2026, secondary): https://www.qovery.com/blog/cloud-run-gpu-vs-gke-node-pool-self-host-open-weight-model

Not verified in this session:
- Tier 1 CPU and memory rates, Cloud SQL, Armor and forwarding-rule prices: the pricing pages render client-side and the docs host was blocked from this environment. They are taken from the published list rates as known; re-check them in the console pricing calculator.
- Whether Cloud Run GPU jobs are GA or preview in asia-south1 specifically.
