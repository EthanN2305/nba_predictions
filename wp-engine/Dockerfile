# Backend image — multi-stage: build wheels, then a slim runtime.
#
# Model files: the pinned demo model ships in the image (via tests/fixtures +
# bootstrap_demo.py). For the REAL trained model, volume-mount your data dir:
#   docker run -v ./data:/app/data …   (compose does this)
# — the bootstrap script never overwrites existing files.

FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
# libgomp1: OpenMP runtime for LightGBM
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /install /usr/local
COPY src ./src
COPY scripts ./scripts
COPY tests/fixtures ./tests/fixtures

ENV WP_DATA_DIR=/app/data
EXPOSE 8000

# workers MUST stay 1: GameHub state is in-memory (see README deployment notes)
CMD ["sh", "-c", "python scripts/bootstrap_demo.py && uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 1"]
