"""Vendored Aegis pipeline scripts.

These modules originate from the user's prior work and are kept as-is so the
existing CLI behavior continues to work. The FastAPI stage runner in
`app.services.pipeline` wraps them with a uniform interface and adds a
dry-run path that produces realistic dummy artifacts when API keys
(MATHPIX_APP_ID/KEY, OPENAI_API_KEY) are not available.
"""
