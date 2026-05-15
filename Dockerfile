# Stage 1 — build the frontend
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2 — backend runtime, serves the built frontend statically
FROM python:3.11-slim AS runtime
WORKDIR /app

# System deps that some pinned wheels need (kept minimal)
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# Seed the fixture workbook so first boot works without a real Bulk
# Import file. Replace /app/data/bulk_import_database.xlsx at runtime
# (e.g., via a Fly volume) to use the real database.
RUN python scripts/generate_dummy_data.py

COPY --from=frontend-build /frontend/dist /app/frontend_dist

ENV FRONTEND_DIST_DIR=/app/frontend_dist
ENV AEGIS_DATA_DIR=/app/data
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
