# CT3D Research

CT-first software for turning a patient CT volume into geometry-preserving segmentation results and 3D surfaces. The current product path is one upload app: intake → volume processing → segmentation → mesh/QC → linked CT and 3D viewer.

**Research software. Not for diagnosis, treatment, or surgical planning.** A rendered structure or fracture candidate is not a validated clinical finding.

## Local setup

Use Python 3.10+ and Node.js 22+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]" -r requirements-ctseg.txt -r requirements-ctmesh.txt
cd web\ct-viewer
npm ci
npm run build
cd ..\..
ct3d
```

The app serves on `http://127.0.0.1:8080` by default. Set `CTAPP_DATA` to a private local directory for uploads/results and `CTAPP_DEVICE` to select the inference device. Model packages/weights may require separate setup; check model license and availability before processing.

## Current scope

- Upload API and worker: `src/emgmirror/ctapp/`.
- Intake and geometry-aware segmentation: `ctapp/intake.py`, `ctseg/`.
- Mesh generation, QC, provenance, exports: `ctmesh/`.
- Viewer: `web/ct-viewer/`.
- Shared DICOM and physical-space evaluation helpers: `src/emgmirror/ct/`.

Input support and model coverage are separate capabilities. A readable scan does not imply selected models cover every anatomy or detect fractures. Results must report model coverage, failures, and geometry processing explicitly. Fracture detection is a separate task and must be evaluated on labeled cases; a bone mesh alone does not detect a fracture.

Do not put patient scans, model weights, or private evaluation data in Git. Keep raw input and derived output distinct and retain scan geometry/provenance.

## Development checks

```powershell
python -m pytest tests\ctseg tests\ctmesh tests\test_ct_dicom.py tests\test_ct_validate.py -q
cd web\ct-viewer
npm run build
```

`requirements-ctseg.txt` and `requirements-ctmesh.txt` declare the inference and mesh stacks. `deploy/ctapp/` contains the hosted container configuration. See `docs/CT_TO_3D.md` and `docs/ct_platform/` for plans, datasets, requirements, and research evidence.
