# Upload a CT, get a 3D model: one container (viewer + API + CPU worker).
# Research use, not for diagnosis or surgical planning.

# ---- web viewer
FROM node:22-slim AS web
WORKDIR /web
COPY web/ct-viewer/package.json web/ct-viewer/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ct-viewer/ ./
RUN npx vite build

# ---- app
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    TOTALSEG_HOME_DIR=/opt/totalseg \
    CTAPP_WEB=/app/web \
    CTAPP_DATA=/tmp/ctapp \
    OMP_NUM_THREADS=8 \
    CTMESH_WORKERS=6
# build-essential: the meshoptimizer Python bindings compile from source
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxrender1 libgomp1 build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# torch and torchvision must come from the same (CPU) build, or torchvision ops fail at import
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY requirements-ctseg.txt requirements-ctmesh.txt ./
RUN grep -v -E "^\s*(moosez|torch)" requirements-ctseg.txt > req-seg.txt \
    && pip install --no-cache-dir -r req-seg.txt -r requirements-ctmesh.txt \
       "fastapi>=0.110" "uvicorn[standard]>=0.29" pyyaml scikit-image fast-simplification networkx
# bake the Apache-licensed TotalSegmentator `total` weights (1.5 mm and 3 mm) into the image; no usage statistics
RUN mkdir -p /opt/totalseg && echo '{"send_usage_stats": false, "prediction_counter": 0}' > /opt/totalseg/config.json \
    && totalseg_download_weights -t total \
    && totalseg_download_weights -t total_fast
COPY src/emgmirror ./src/emgmirror
COPY --from=web /web/dist ./web
EXPOSE 8080
CMD ["python", "-m", "emgmirror.ctapp.server"]
