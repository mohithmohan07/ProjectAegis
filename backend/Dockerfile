FROM node:20-bookworm-slim AS katex-runtime
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --omit=dev
COPY frontend/scripts/validate-katex.mjs ./scripts/validate-katex.mjs

FROM python:3.11-slim-bookworm
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
# This Dockerfile is built with the repository root as its context so both
# runtime images consume the same authoritative registry artifacts.
COPY docs/open-specific-registry-v2.md docs/open-specific-registry-v2.xlsx /app/docs/
COPY --from=katex-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=katex-runtime /frontend/node_modules/katex /app/frontend_runtime/node_modules/katex
COPY --from=katex-runtime /frontend/scripts/validate-katex.mjs /app/frontend_runtime/scripts/validate-katex.mjs
ENV AEGIS_KATEX_NODE=/usr/local/bin/node
ENV AEGIS_KATEX_SCRIPT=/app/frontend_runtime/scripts/validate-katex.mjs
ENV AEGIS_KATEX_MODULE=/app/frontend_runtime/node_modules/katex
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
