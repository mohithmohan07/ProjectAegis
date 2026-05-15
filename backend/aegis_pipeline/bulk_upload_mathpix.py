import os
import json
import time
from typing import List, Dict, Any

import requests
import pandas as pd
import openai
import re

# ====================================================
# CONFIG
# ====================================================

# ---- API KEYS ----
# Preferred: set as environment variables before running:
#   setx OPENAI_API_KEY "sk-..."
#   setx MATHPIX_APP_ID "..."
#   setx MATHPIX_API_KEY "..."
#
# Or, if you really want, you can hardcode below instead of os.getenv,
# but don't commit that file anywhere.

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or "YOUR_OPENAI_KEY_HERE"
MATHPIX_APP_ID = "upschooledtechpvtltd_a5ac24_bc4cd2"
MATHPIX_API_KEY = "911a76bbdc85df5bae0c2271ed9b20b33b8aa4cba60540c7e169de7c5712933d"

openai.api_key = OPENAI_API_KEY

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set")
if not MATHPIX_APP_ID or not MATHPIX_API_KEY:
    raise RuntimeError("MATHPIX_APP_ID / MATHPIX_API_KEY not set")

# ---- GPT MODELS ----
# Adjust to whatever deployed names you actually have.
MODEL_PARSE = "gpt-5.4-mini-2026-03-17"   # for parsing Q & A structure from OCR text
MODEL_ENRICH = "gpt-5.4-mini-2026-03-17"  # for metadata + rubrics + wrong phrases


# ---- UpSchool Question Categories (DO NOT RENAME) ----
QUESTION_CATEGORIES = [
    "Multiple Choice Question",
    "Assertion & Reasons Type",
    "Choose the ODD one Out",
    "True or False",
    "Fill in the Blanks",

    "Very Short Answer Questions",
    "Short Answer Type (2 Marks)",
    "Short Answer Type (3 Marks)",
    "Long Answer Type (4 Marks)",
    "Long Answer Type (5 Marks)",
    "Long Answer Type (6 Marks)",

    "Numerical/application based",

    "Case Based Question",
    "Passage based questions",
    "Extract based question",
    "Extract based on Map Survey",
    "Locating and Plotting on map",

    "Rearrange the following words",
    "Sentence Transformation",
    "Error correction",
    "Composition writing",
]

# ---------------------------------------------------------
# NORMALIZATION HELPERS (MUST be defined BEFORE enrichment)
# ---------------------------------------------------------

ALLOWED_COG_SKILLS = [
    "Remembering",
    "Understanding",
    "Applying",
    "Analysing",
    "Evaluating",
    "Creating",
]

ALLOWED_ANSWER_TYPES = ["Phrases", "Equation", "Image"]
ALLOWED_DIFFICULTY = ["Less", "Moderate", "High"]


def _normalize_to_allowed(value: str, allowed: List[str], default: str) -> str:
    """
    Snap GPT output to nearest allowed value.
    """
    if not value:
        return default

    v = value.strip().lower()

    # Exact match
    for a in allowed:
        if v == a.lower():
            return a

    # Startswith / contains
    for a in allowed:
        al = a.lower()
        if v.startswith(al) or al in v or v in al:
            return a

    return default


def normalize_question_category(raw: str) -> str:
    return _normalize_to_allowed(raw, QUESTION_CATEGORIES, default=QUESTION_CATEGORIES[0])


def normalize_cognitive_skill(raw: str) -> str:
    return _normalize_to_allowed(raw, ALLOWED_COG_SKILLS, default="Understanding")


def normalize_answer_type(raw: str) -> str:
    return _normalize_to_allowed(raw, ALLOWED_ANSWER_TYPES, default="Phrases")


def normalize_difficulty(raw: str) -> str:
    return _normalize_to_allowed(raw, ALLOWED_DIFFICULTY, default="Moderate")


# ====================================================
# HELPER FUNCTIONS
# ====================================================

def safe_int(x, default: int = 1) -> int:
    try:
        v = int(x)
        return v if v > 0 else default
    except Exception:
        return default
