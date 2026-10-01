# Pinned Tesseract 5 OCR image (plan §11 F3, §4.7): base image by digest, model by SHA-256.
# Runs with --network none; it only ever reads mounted page renders and writes TSV.
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

ARG TESSDATA_BEST_URL=https://github.com/tesseract-ocr/tessdata_best/raw/4.1.0/eng.traineddata
ARG TESSDATA_BEST_ENG_SHA256=8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr ca-certificates curl \
 && mkdir -p /opt/tessdata_best \
 && curl -fsSL -o /opt/tessdata_best/eng.traineddata "$TESSDATA_BEST_URL" \
 && echo "$TESSDATA_BEST_ENG_SHA256  /opt/tessdata_best/eng.traineddata" | sha256sum -c - \
 && apt-get purge -y curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

ENV OMP_THREAD_LIMIT=1 \
    TESSDATA_PREFIX=/opt/tessdata_best \
    TESSDATA_BEST_ENG_SHA256=${TESSDATA_BEST_ENG_SHA256}

WORKDIR /work
