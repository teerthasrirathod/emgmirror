# CT to 3D: lean hosted app

Research use, not for diagnosis or surgical planning.

One container on Google Cloud Run: the web viewer, an upload API and a CPU worker
(`src/emgmirror/ctapp/server.py`). Upload a CT (DICOM folder, .zip, or .nii/.nii.gz) → TotalSegmentator
`total` (Apache-licensed weights baked in, usage statistics off) → SurfaceNets meshes → 3D viewer.

Live: https://ct-to-3d-249544398214.asia-south1.run.app (project `ct-to-3d-11222226`, region asia-south1).

## Build and deploy

Build from a staging folder that holds only code (never the repo root, which has personal scans in
`01_clinical_CT/`, `results/` and `data/`):

```bash
# stage: Dockerfile, requirements-ctseg.txt, requirements-ctmesh.txt, src/emgmirror, web/ct-viewer (no node_modules)
gcloud builds submit STAGE --project ct-to-3d-11222226 --region asia-south1 \
  --tag asia-south1-docker.pkg.dev/ct-to-3d-11222226/ct/ctapp:vN --machine-type=e2-highcpu-8 --timeout=3600s
gcloud run deploy ct-to-3d --project ct-to-3d-11222226 --region asia-south1 \
  --image asia-south1-docker.pkg.dev/ct-to-3d-11222226/ct/ctapp:vN \
  --cpu 8 --memory 32Gi --no-cpu-throttling --max-instances 1 --min-instances 1 \
  --concurrency 40 --timeout 3600 --execution-environment gen2 --allow-unauthenticated
```

The demo result shown by "See a finished example" is the public TotalSegmentator subject s0024
(CC BY 4.0), staged into `web/ct-viewer/public/demo/` at build time only.

## Known limits (2026-11-23)

- About 70 minutes per scan on CPU (segmentation ~27 min, meshing ~43 min). Next: a GPU worker and a
  meshing speed fix.
- Jobs and results live on the instance's temporary disk; a redeploy or restart deletes them. One
  instance only (`--max-instances 1`).
- `--min-instances 1` keeps the demo fast but bills continuously; set it to 0 after demos.
