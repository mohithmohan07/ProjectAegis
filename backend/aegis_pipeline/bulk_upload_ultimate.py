"""
Bulk Upload – GPT-first Backend (Option C: PDF or TXT)

Command-line usage:
    python BulkUploadUltimate.py questions.pdf solutions.pdf
    python BulkUploadUltimate.py questions.txt solutions.txt
    python BulkUploadUltimate.py questions.pdf solutions.txt

- Accepts:
    questions file  (PDF or TXT)
    solutions file  (PDF or TXT)

- If PDF: uses Mathpix → text
- If TXT: reads text directly
- Uses gpt-5.1-2025-11-13 to:
    - Parse & pair questions and solutions
    - Extract marks
    - Classify type (MCQ / FIB / DESCRIPTIVE)
    - Enrich descriptive questions with rubrics + metadata

- Outputs:
    final_output.xlsx
      - Objective  sheet
      - Subjective sheet
      - Descriptive sheet
"""

import os
import sys
import json
import time
from typing import List, Dict, Any, Optional

import requests
import pandas as pd
import re
import io
import zipfile

from openai import OpenAI

# --------------------------------------------------------------------------
# Environment / Clients
# --------------------------------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MATHPIX_APP_ID = os.getenv("MATHPIX_APP_ID")
MATHPIX_API_KEY = os.getenv("MATHPIX_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set in environment")

client = OpenAI(api_key=OPENAI_API_KEY)

# --------------------------------------------------------------------------
# Fixed Question Categories & Constants (kept from previous pipeline)
# --------------------------------------------------------------------------

QUESTION_CATEGORIES: List[str] = [
    "Assertion & Reasons Type",
    "Multiple Choice Question",
    "Choose the ODD one Out",
    "True or False",

    "Case Based Question",
    "Numerical/application based",

    "Very Short Answer Questions",
    "Short Answer Type (2 Marks)",
    "Short Answer Type (3 Marks)",
    "Long Answer Type (4 Marks)",
    "Long Answer Type (5 Marks)",
    "Long Answer Type (6 Marks)",

    "Rearrange the following words",
    "Sentence Transformation",
    "Error correction",
    "Fill in the Blanks",
    "Passage based questions",
    "Composition writing",
    "Extract based question",

    "Locating and Plotting on map",
    "Extract based on Map Survey",
]

MODEL_PARSE = "gpt-5.4-mini-2026-03-17"
MODEL_ENRICH = "gpt-5.4-mini-2026-03-17"

# --------------------------------------------------------------------------
# Utility Helpers
# --------------------------------------------------------------------------

def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        s = str(value).strip()
        # extract first integer from string, e.g. "(5 marks)" → 5
        m = re.search(r"-?\d+", s)
        if m:
            return int(m.group(0))
        return default
    except Exception:
        return default

def infer_marks_from_question(raw_question: str, parsed_marks: Any, default: int = 5) -> int:
    """
    Try to infer marks from the raw question text, e.g.:
    - "... [5]"
    - "... (5 marks)"
    If that fails, fall back to parsed_marks; if that is <=0, fall back to default.
    """
    if raw_question:
        # Pattern 1: [5] or [10]
        m = re.search(r"\[(\d+)\]", raw_question)
        if m:
            return safe_int(m.group(1), default=default)

        # Pattern 2: (5 marks), (3 mark), etc.
        m = re.search(r"\((\d+)\s*marks?\)", raw_question, flags=re.IGNORECASE)
        if m:
            return safe_int(m.group(1), default=default)

    # Fallbacks
    m_val = safe_int(parsed_marks, default=default)
    if m_val <= 0:
        return default
    return m_val

def call_gpt_json(
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
    max_output_tokens: int = 30000,
) -> Dict[str, Any]:
    """
    Generic JSON-mode call using gpt-5.1 (or compatible).
    - Forces JSON output using response_format
    - Adds robust handling when content is empty or not JSON
    """
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_completion_tokens=max_output_tokens,
    )

    # Get content safely
    content = response.choices[0].message.content
    if content is None:
        content = ""
    raw = str(content).strip()

    if not raw:
        # Nothing came back – this usually means wrong model name / quota / internal error.
        # Print the whole response for inspection.
        try:
            print("⚠ GPT returned empty content. Full response object:")
            print(response)
        except Exception:
            pass
        raise RuntimeError(
            "GPT returned empty content. "
            "Common causes: invalid model name, missing access to the model, or quota issues."
        )

    # First, try direct JSON parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to salvage JSON substring between first '{' and last '}'
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = raw[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        # If still failing, print raw content and raise
        print("⚠ Failed to parse GPT JSON. Raw content was:")
        print(raw)
        raise RuntimeError("Failed to parse GPT JSON. See raw content above.")

def highlight_wrong_phrases(text: str, wrong_phrases: List[str]) -> str:
    """
    Safely highlight wrong phrases without triggering regex escape errors.
    Replaces literal occurrences, preserving backslashes.
    """
    if not text or not wrong_phrases:
        return text

    highlighted = text
    for phrase in wrong_phrases:
        if not phrase:
            continue

        # Escape regex pattern so backslashes become literal
        pattern = re.escape(phrase)

        # Use lambda to avoid re.sub interpreting backslashes in replacement
        highlighted = re.sub(
            pattern,
            lambda m: f"[[{m.group(0)}]]",
            highlighted
        )

    return highlighted

def strip_katex_delimiters(text: Any) -> Any:
    """
    Remove simple KaTeX inline/display delimiters from text:
    \( \), \[ \]
    (But keep LaTeX commands like \\frac, \\sin, etc.)
    """
    if not isinstance(text, str):
        return text
    # Remove the literal sequences "\(" and "\)"
    text = text.replace("\\(", "").replace("\\)", "")
    text = text.replace("\\[", "").replace("\\]", "")
    return text

# --------------------------------------------------------------------------
# Mathpix OCR Helpers (original, for file-like objects – UNUSED IN CLI)
# --------------------------------------------------------------------------

def poll_mathpix_pdf_status(pdf_id: str, headers: Dict[str, str],
                            poll_interval: int = 10,
                            max_polls: int = 30) -> Optional[Dict[str, Any]]:
    """
    Poll Mathpix for PDF conversion status.
    Returns status JSON when completed, or None if stuck/timed out.
    """
    url = f"https://api.mathpix.com/v3/pdf/{pdf_id}.json"
    last_completed = 0
    stuck_count = 0

    for attempt in range(1, max_polls + 1):
        resp = requests.get(url, headers=headers)
        data = resp.json()

        status = data.get("status")
        if status == "completed":
            return data

        current_completed = data.get("num_pages_completed", 0)
        if current_completed == last_completed:
            stuck_count += 1
            if stuck_count > 5:
                # no progress for 5 polls → treat as stuck
                return None
        else:
            stuck_count = 0
            last_completed = current_completed

        time.sleep(poll_interval)

    return None


def mathpix_pdf_to_text_filelike(file_storage) -> Optional[str]:
    """
    (Legacy) Uploads a PDF-like object to Mathpix and returns plaintext.
    Not used in CLI; kept for future integration if needed.
    """
    if not MATHPIX_APP_ID or not MATHPIX_API_KEY:
        raise RuntimeError("Mathpix credentials not set in environment")

    options = {
        "conversion_formats": {"text": True},
        "math_inline_delimiters": ["$", "$"],
        "rm_spaces": True,
    }

    r = requests.post(
        "https://api.mathpix.com/v3/pdf",
        headers={
            "app_id": MATHPIX_APP_ID,
            "app_key": MATHPIX_API_KEY,
        },
        data={"options_json": json.dumps(options)},
        files={"file": (file_storage.filename, file_storage.stream,
                        file_storage.mimetype)},
    )

    api_resp = r.json()
    pdf_id = api_resp.get("pdf_id")
    if not pdf_id:
        return None

    headers = {"app_id": MATHPIX_APP_ID, "app_key": MATHPIX_API_KEY}
    status_data = poll_mathpix_pdf_status(pdf_id, headers)
    if not status_data:
        return None

    url = f"https://api.mathpix.com/v3/pdf/{pdf_id}.text"
    response = requests.get(url, headers=headers)
    mmd_text = response.text or ""

    plain_text = mmd_text.replace("$$", "\n")
    return plain_text.strip()


# --------------------------------------------------------------------------
# Mathpix OCR for CLI (path-based)
# --------------------------------------------------------------------------

def mathpix_pdf_to_text_path(path: str) -> str:
    """
    CLI version: PDF path → OCR via Mathpix → plain text.

    We request 'tex.zip' (which your account supports), download the zip,
    extract all .tex files, and concatenate them into a single text string.
    """
    if not MATHPIX_APP_ID or not MATHPIX_API_KEY:
        raise RuntimeError("Mathpix credentials not set in environment")

    # Use tex.zip as conversion format (supported on your account)
    options = {
        "conversion_formats": {"tex.zip": True},
        "rm_spaces": True,
    }

    with open(path, "rb") as f:
        files = {
            "file": (os.path.basename(path), f, "application/pdf")
        }
        r = requests.post(
            "https://api.mathpix.com/v3/pdf",
            headers={
                "app_id": MATHPIX_APP_ID,
                "app_key": MATHPIX_API_KEY,
            },
            data={"options_json": json.dumps(options)},
            files=files,
        )

    api_resp = r.json()
    pdf_id = api_resp.get("pdf_id")
    if not pdf_id:
        raise RuntimeError(f"Mathpix did not return pdf_id. Response: {api_resp}")

    headers = {"app_id": MATHPIX_APP_ID, "app_key": MATHPIX_API_KEY}
    status_data = poll_mathpix_pdf_status(pdf_id, headers)
    if not status_data:
        raise RuntimeError("Mathpix processing stuck or timed out")

    # Fetch tex.zip and extract .tex files
    url = f"https://api.mathpix.com/v3/pdf/{pdf_id}.tex.zip"
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to download tex.zip from Mathpix: {response.status_code}, {response.text}"
        )

    # Read zip from bytes in memory
    zip_bytes = io.BytesIO(response.content)
    all_text_parts: List[str] = []

    with zipfile.ZipFile(zip_bytes, "r") as zf:
        for name in zf.namelist():
            if name.lower().endswith(".tex"):
                with zf.open(name) as tex_file:
                    try:
                        tex_content = tex_file.read().decode("utf-8", errors="ignore")
                    except Exception:
                        tex_content = tex_file.read().decode(errors="ignore")
                    all_text_parts.append(tex_content)

    if not all_text_parts:
        raise RuntimeError("No .tex files found inside tex.zip from Mathpix")

    # Join all .tex content into one string for GPT
    plain_text = "\n\n".join(all_text_parts)
    return plain_text.strip()

def extract_text_from_path(path: str) -> str:
    """
    Option C core for CLI:
    - If PDF → Mathpix → text
    - Else → assume text file, read directly
    """
    if not path:
        raise ValueError("Empty path provided")

    ext = os.path.splitext(path)[1].lower()

    if ext == ".pdf":
        print(f"📘 OCR via Mathpix: {path}")
        text = mathpix_pdf_to_text_path(path)
        if not text:
            raise RuntimeError(f"Failed to OCR PDF via Mathpix: {path}")
        return text

    print(f"📄 Reading text directly: {path}")
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


# --------------------------------------------------------------------------
# GPT Parsing: Questions + Solutions → structured JSON
# --------------------------------------------------------------------------

def gpt_parse_and_pair(question_text: str, solution_text: str) -> Dict[str, Any]:
    """
    Let GPT:
    - split questions from question_text
    - identify type, marks, options, etc.
    - map to solution_text by number
    """

    categories_str = ", ".join(f'"{c}"' for c in QUESTION_CATEGORIES)

    system_prompt = (
        "You are Joe, an AI assistant for UpSchool that parses school question "
        "papers and their solutions into a structured JSON format suitable for "
        "an Excel bulk upload pipeline."
    )

    user_prompt = f"""
You are given two raw text blobs:

1) QUESTION PAPER TEXT
----------------------
{question_text}

2) SOLUTION / ANSWER TEXT
-------------------------
{solution_text}

You must:

1. Identify each main question.
   - Use numbering like: 1., 1), Q1, (1), etc.
   - Preserve sub-question labelling such as (a), (b), (c) or (i), (ii), etc.

2. For each main question, determine:
   - qno: integer question number (1, 2, 3, ...)
   - label: string label, e.g. "Q1", "Q2", etc.
   - raw_question: the full question text as printed (including marks info or sub-parts).
   - clean_question: same question but:
       - remove leading labels like "1.", "Q1", "Question 1".
       - remove trailing marks markers like "(3 marks)", "[5]", "3M".
   - type: EXACTLY one of:
       - "MCQ"         → multiple choice with options
       - "FILL_BLANK"  → fill-in-the-blanks
       - "DESCRIPTIVE" → any short/long answer / reasoning / writing question
   - marks: integer total marks for this question (from the question paper).

3. For MCQ questions:
   - options: list of options in display order, including the option prefix, e.g. "A. Paris".
   - correct_option_letter: 'A', 'B', 'C', 'D', etc. (ONE letter)
   - correct_option_text: the full option text that is correct.
   - Use both the QUESTION and the SOLUTION sections to infer correctness.

4. For FILL_BLANK questions:
   - blanks_count: integer number of distinct blanks.

5. For DESCRIPTIVE questions:
   - Keep clean_question, marks and the raw solution mapping.

6. Solutions mapping:
   - For each question, create a `solution_text` field:
     - Contains the combined solution content from the solution text that clearly
       answers THIS question number (and its sub-parts).
     - If you cannot find a solution, use "" (empty string).

7. Question Category:
   - question_category: choose EXACTLY ONE from:
     [{categories_str}]
   - Copy the string VERBATIM from the list; do not rephrase.

8. Cognitive Skills:
   - cognitive_skills: EXACTLY one of:
       "Remembering", "Understanding", "Applying", "Analysing", "Evaluating", "Creating".

Return a SINGLE JSON object with this structure:

{{
  "questions": [
    {{
      "qno": 1,
      "label": "Q1",
      "raw_question": "...",
      "clean_question": "...",
      "type": "MCQ",
      "marks": 1,
      "question_category": "",
      "cognitive_skills": "",
      "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
      "correct_option_letter": "B",
      "correct_option_text": "B. ...",
      "blanks_count": 0,
      "solution_text": "Full solution text for Q1"
    }},
    {{
      "qno": 2,
      "label": "Q2",
      "raw_question": "...",
      "clean_question": "...",
      "type": "DESCRIPTIVE",
      "marks": 3,
      "question_category": "",
      "cognitive_skills": "",
      "options": [],
      "correct_option_letter": "",
      "correct_option_text": "",
      "blanks_count": 0,
      "solution_text": "Full solution text for Q2"
    }}
  ]
}}

Rules:
- The top-level JSON key MUST be exactly "questions".
- For questions where some fields are not relevant (e.g. options for descriptive),
  use empty list [] or empty string "" as appropriate.
"""

    data = call_gpt_json(MODEL_PARSE, system_prompt, user_prompt)
    if "questions" not in data or not isinstance(data["questions"], list):
        raise RuntimeError("GPT parse response missing 'questions' list")

    return data


# --------------------------------------------------------------------------
# Descriptive Question Enrichment (Rubrics + Metadata)
# --------------------------------------------------------------------------

def enrich_descriptive_question(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    entry: {
      'qno', 'label', 'question', 'marks', 'solution'
    }

    IMPORTANT:
    - Total marks are taken from OCR/GPT parsing (entry["marks"]) and are FIXED.
    - GPT can only distribute these marks across rubrics.
    - GPT can suggest an alternative "model_suggested_marks" for SME info only.
    """
    question_text = entry.get("question", "")
    marks_from_ocr = safe_int(entry.get("marks", 1), default=1)
    solution_text = entry.get("solution", "")

    categories_str = ", ".join(f'"{c}"' for c in QUESTION_CATEGORIES)

    system_prompt = (
        "You are Joe, an AI assistant for UpSchool that generates structured "
        "metadata and marking schemes for descriptive exam questions. "
        "Your output MUST be a single valid JSON object."
    )

    user_prompt = f"""
You are given:
- A descriptive school-level exam question.
- The model answer (solution).
- The FIXED total marks for this question: {marks_from_ocr}
  (You may NOT change this number for scoring.)

However, also include in your JSON a field called:
  "model_suggested_marks"
This is your *opinion* on what the question is actually worth based on complexity:
  - 1, 2, 3, 4, 5, 6 marks, etc.
This is ONLY advisory and must NOT affect rubrics or the 'marks' field.

---------------------------------------
QUESTION:
\"\"\"{question_text}\"\"\"


MODEL ANSWER:
\"\"\"{solution_text}\"\"\"


---------------------------------------

1. Question metadata (STRICT CHOICES):

   question_category:
     - Choose exactly ONE value from this list (copy string VERBATIM, no rephrasing):
       [{categories_str}]
     - Do NOT invent new labels.

   cognitive_skills:
     - EXACTLY ONE of:
       "Remembering", "Understanding", "Applying", "Analysing", "Evaluating", "Creating".

   question_source:
     - Always "UpSchool DB".

   question_appears_in:
     - Always "Pre/Post-Worksheet/Test".

   level_of_difficulty:
     - One of EXACTLY: "Less", "Moderate", "High".
     - Do NOT invent new wording.

2. Clean question text:
   - Remove leading numbering like "1.", "Q1", "Question 1" if present.
   - Remove trailing marks notation like "[5]", "(5 marks)" if present.
   - The "question" field should contain the clean student-facing question,
     but still include sub-parts (a), (b), etc. if relevant.

3. Answer type (for UI Answer Type dropdown):
   - One of EXACTLY: "Phrases", "Equation", "Image".

4. Generate rubrics (answer_content) based on the correct content
   implied by the MODEL ANSWER.

   You MUST follow this exact structure:

   - Divide the total marks ({marks_from_ocr}) into scoring units called "rubrics".
   - Each rubric is one simple, clear criterion in one short sentence.
   - Each rubric MUST be on its own line.
   - Each rubric line MUST follow this format exactly (including capitalization):

       Step <n>: <short, clear description>. (Marks: <m>)

     where:
       - <n> is 1, 2, 3, ... in order.
       - <m> is an integer mark for that step.

   Example for a 3-mark question:
     Step 1: States the theorem correctly in words or symbols. (Marks: 1)
     Step 2: Draws and labels the correct geometric figure required for the proof. (Marks: 1)
     Step 3: Provides a logically valid proof leading to the required result. (Marks: 1)

   Marking rules:
     - The sum of all rubric marks MUST equal exactly {marks_from_ocr}.
     - You MAY use any integer distribution: e.g., 1+1+1, 2+1, 3+2, etc.
     - Do NOT ever give only 1 rubric if {marks_from_ocr} > 1.
     - Do NOT exceed total marks.
     - Do NOT reduce total marks; you MUST use all {marks_from_ocr} marks in rubrics.

   Special case: "any two/three/four" type questions:
     - List ALL valid points as rubrics.
     - Normally give each rubric 1 mark (or a clear scheme).
     - You may include a rubric like:
       Step 1: Awards 1 mark for each correct point mentioned (any X correct points earn full marks). (Marks: {marks_from_ocr})

5. Identify clearly wrong phrases in the MODEL ANSWER:

   wrong_phrases:
     - A list of exact substrings from the MODEL ANSWER that are conceptually or factually wrong.

Return a single JSON object with EXACTLY these keys:

{{
  "question_category": "",
  "cognitive_skills": "",
  "question_source": "UpSchool DB",
  "question_appears_in": "Pre/Post-Worksheet/Test",
  "level_of_difficulty": "",
  "question": "",
  "marks": {marks_from_ocr},
  "answer_type": "",
  "answer_weightage": {marks_from_ocr},
  "answer_content": "",
  "wrong_phrases": [],
  "model_suggested_marks": {marks_from_ocr}
}}
"""

    data = call_gpt_json(MODEL_ENRICH, system_prompt, user_prompt)

    total_marks = marks_from_ocr
    suggested_marks = safe_int(
        data.get("model_suggested_marks", marks_from_ocr),
        default=marks_from_ocr,
    )

    wrong_phrases = data.get("wrong_phrases", [])
    if not isinstance(wrong_phrases, list):
        wrong_phrases = []

    highlighted_answer = highlight_wrong_phrases(solution_text, wrong_phrases)

    sme_comment = ""
    if suggested_marks != marks_from_ocr:
        sme_comment = (
            f"(SME Note: GPT suggests this question aligns more with a "
            f"{suggested_marks}-mark question, but extracted marks are "
            f"{marks_from_ocr}.)"
        )

    enriched = {
        "question_category": data.get("question_category", ""),
        "cognitive_skills": data.get("cognitive_skills", ""),
        "question_source": data.get("question_source", "UpSchool DB"),
        "question_appears_in": data.get(
            "question_appears_in", "Pre/Post-Worksheet/Test"
        ),
        "level_of_difficulty": data.get("level_of_difficulty", ""),
        "clean_question": data.get("question", question_text),
        "marks": total_marks,
        "answer_type": data.get("answer_type", "Phrases"),
        "answer_weightage": total_marks,
        "answer_content": data.get("answer_content", ""),
        "highlighted_answer": highlighted_answer,
        "sme_comment": sme_comment,
        "wrong_phrases": wrong_phrases,
        "model_suggested_marks": suggested_marks,
    }

    return enriched


# --------------------------------------------------------------------------
# Excel Builder (Objective / Subjective / Descriptive)
# --------------------------------------------------------------------------

def build_excel_from_parsed(parsed: Dict[str, Any],
                            output_path: str) -> None:
    """
    parsed["questions"] → 3 DataFrames → Excel
    """
    questions = parsed.get("questions", [])

    objective_rows: List[Dict[str, Any]] = []
    subjective_rows: List[Dict[str, Any]] = []
    descriptive_rows: List[Dict[str, Any]] = []

    for q in questions:
        qno = safe_int(q.get("qno", 0))
        label = q.get("label", f"Q{qno or ''}") or f"Q{qno or ''}"
        q_type = (q.get("type") or "").upper()
        raw_q = q.get("raw_question", "")
        parsed_marks = q.get("marks", 0)
        marks = infer_marks_from_question(raw_q, parsed_marks, default=5)
        question_category = q.get("question_category", "")
        cognitive_skills = q.get("cognitive_skills", "")
        clean_question = q.get("clean_question", q.get("raw_question", ""))
        solution_text = q.get("solution_text", "")

# ---------- MCQ → Objective Sheet ----------
if q_type == "MCQ":
    options: List[str] = q.get("options", []) or []
    correct_letter: str = (q.get("correct_option_letter") or "").upper()

    question_row = {
        "Question Label": label,
        "Question Category": question_category,
        "Cognitive Skills": cognitive_skills,
        "Question Source": "UpSchool DB",
        "Question Appears in": "Pre/Post-Worksheet/Test",
        "Level of Difficulty": "",
        "Question": clean_question,
        "Marks": marks,

        "Answer Type1": "Words",
        "Answer Type2": "Words",
        "Answer Type3": "Words",
        "Answer Type4": "Words",

        "Answer Content1": options[0] if len(options) > 0 else "",
        "Answer Content2": options[1] if len(options) > 1 else "",
        "Answer Content3": options[2] if len(options) > 2 else "",
        "Answer Content4": options[3] if len(options) > 3 else "",

        "Correct Answer1": "No",
        "Correct Answer2": "No",
        "Correct Answer3": "No",
        "Correct Answer4": "No",

        "Answer Weightage1": 0,
        "Answer Weightage2": 0,
        "Answer Weightage3": 0,
        "Answer Weightage4": 0,

        "Answer Explanation": solution_text,
    }

    # mark correct option
    if correct_letter in ["A", "B", "C", "D"]:
        idx = ord(correct_letter) - ord("A") + 1
        question_row[f"Correct Answer{idx}"] = "Yes"
        question_row[f"Answer Weightage{idx}"] = marks

    # strip KaTeX formatting from ALL MCQ fields
    question_row["Question"] = strip_katex_delimiters(question_row["Question"])
    question_row["Answer Explanation"] = strip_katex_delimiters(question_row["Answer Explanation"])

    for i in range(1, 5):
        key = f"Answer Content{i}"
        question_row[key] = strip_katex_delimiters(question_row.get(key, ""))

    objective_rows.append(question_row)

        # ---------- FILL_BLANK → Subjective Sheet ----------
        elif q_type == "FILL_BLANK":
            blanks_count = safe_int(q.get("blanks_count", 0), default=0)
            blanks_count = max(blanks_count, 1) if marks > 0 else blanks_count

            base_row = {
                "Question Label": label,
                "Question Category": "Fill in the Blanks",
                "Cognitive Skills": cognitive_skills,
                "Question Source": "UpSchool DB",
                "Question Appears in": "Pre/Post-Worksheet/Test",
                "Level of Difficulty": "",
                "Question": clean_question,
                "Marks": marks,
                "answer_explanation": solution_text,
            }

            # dynamic answer columns up to 10
            for i in range(1, 11):
                if i <= blanks_count:
                    base_row[f"Answer Type{i}"] = "Words"
                    base_row[f"Answer{i}"] = ""
                    base_row[f"Answer Display{i}"] = "Yes"
                    base_row[f"Weightage{i}"] = 1
                    base_row[f"Placeholder{i}"] = chr(96 + i)  # a, b, c...
                else:
                    base_row[f"Answer Type{i}"] = ""
                    base_row[f"Answer{i}"] = ""
                    base_row[f"Answer Display{i}"] = ""
                    base_row[f"Weightage{i}"] = ""
                    base_row[f"Placeholder{i}"] = ""
                    base_row["Question"] = strip_katex_delimiters(base_row["Question"])
                    base_row["answer_explanation"] = strip_katex_delimiters(base_row["answer_explanation"])
for i in range(1, 11):
                        ans_key = f"Answer{i}"
                        base_row[ans_key] = strip_katex_delimiters(base_row.get(ans_key, ""))


            subjective_rows.append(base_row)

        # ---------- DESCRIPTIVE → Descriptive Sheet via enrichment ----------
        else:
            entry = {
                "qno": qno,
                "label": label,
                "question": clean_question,
                "marks": marks,
                "solution": solution_text,
            }
            enriched = enrich_descriptive_question(entry)

            row = {
                "Question Label": label,
                "Question Category": enriched["question_category"],
                "Cognitive Skills": enriched["cognitive_skills"],
                "Question Source": enriched["question_source"],
                "Question Appears in": enriched["question_appears_in"],
                "Level of Difficulty": enriched["level_of_difficulty"],
                "Question": enriched["clean_question"],
                "Marks": enriched["marks"],
                "Display Answer": enriched["highlighted_answer"],
                "Answer Type": enriched["answer_type"],
                "Answer Weightage": enriched["answer_weightage"],
                "Answer Content": enriched["answer_content"],
                "Answer Explanation": (
                    enriched["highlighted_answer"]
                    + ("\n\n" + enriched["sme_comment"]
                       if enriched["sme_comment"] else "")
                ),
            }
            row["Question"] = strip_katex_delimiters(row["Question"])
            row["Display Answer"] = strip_katex_delimiters(row["Display Answer"])
            row["Answer Content"] = strip_katex_delimiters(row["Answer Content"])
            row["Answer Explanation"] = strip_katex_delimiters(row["Answer Explanation"])

            descriptive_rows.append(row)

    # Build DataFrames
    objective_df = pd.DataFrame(objective_rows)
    subjective_df = pd.DataFrame(subjective_rows)
    descriptive_df = pd.DataFrame(descriptive_rows)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        if not objective_df.empty:
            objective_df.to_excel(writer, sheet_name="Objective", index=False)
        if not subjective_df.empty:
            subjective_df.to_excel(writer, sheet_name="Subjective", index=False)
        if not descriptive_df.empty:
            descriptive_df.to_excel(writer, sheet_name="Descriptive", index=False)

    print(f"✅ Excel created: {output_path}")


# --------------------------------------------------------------------------
# CLI ENTRYPOINT
# --------------------------------------------------------------------------

def main():
    if len(sys.argv) != 3:
        print("Usage:")
        print("  python BulkUploadUltimate.py <questions.pdf/txt> <solutions.pdf/txt>")
        sys.exit(1)

    questions_path = sys.argv[1]
    solutions_path = sys.argv[2]

    if not os.path.exists(questions_path):
        print(f"❌ Questions file not found: {questions_path}")
        sys.exit(1)
    if not os.path.exists(solutions_path):
        print(f"❌ Solutions file not found: {solutions_path}")
        sys.exit(1)

    print("📥 Extracting text from question paper...")
    q_text = extract_text_from_path(questions_path)

    print("📥 Extracting text from solution paper...")
    s_text = extract_text_from_path(solutions_path)

    print("🤖 Calling GPT to parse & pair...")
    parsed = gpt_parse_and_pair(q_text, s_text)

    print("📊 Building Excel...")
    output_path = "final_output.xlsx"
    build_excel_from_parsed(parsed, output_path)

    print("🎉 DONE. Output file:", output_path)


if __name__ == "__main__":
    main()