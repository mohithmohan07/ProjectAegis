FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
# This Dockerfile is built with the repository root as its context so both
# runtime images consume the same authoritative registry artifacts.
COPY docs/open-specific-registry-v2.md docs/open-specific-registry-v2.xlsx /app/docs/
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
