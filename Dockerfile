# Stage 1 — build the frontend
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2 — backend runtime, serves the built frontend statically
FROM python:3.11-slim-bookworm AS runtime
WORKDIR /app

# System deps that some pinned wheels need (kept minimal)
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# Runtime policy evidence. These are the single authoritative repo-root
# artifacts; no copied mirror is checked into the backend package.
COPY docs/open-specific-registry-v2.md docs/open-specific-registry-v2.xlsx /app/docs/

COPY --from=frontend-build /frontend/dist /app/frontend_dist

# Use the same locked KaTeX engine for final workbook render validation.
# Both stages use bookworm so the copied official Node binary shares its ABI.
COPY --from=frontend-build /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-build /frontend/node_modules/katex /app/frontend_runtime/node_modules/katex
COPY --from=frontend-build /frontend/scripts/validate-katex.mjs /app/frontend_runtime/scripts/validate-katex.mjs

ENV FRONTEND_DIST_DIR=/app/frontend_dist
ENV AEGIS_KATEX_NODE=/usr/local/bin/node
ENV AEGIS_KATEX_SCRIPT=/app/frontend_runtime/scripts/validate-katex.mjs
ENV AEGIS_KATEX_MODULE=/app/frontend_runtime/node_modules/katex
ENV AEGIS_DATA_DIR=/app/data
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
