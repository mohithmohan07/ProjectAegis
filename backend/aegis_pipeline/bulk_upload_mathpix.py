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
# All credentials are read from environment variables. Set them on the
# host/server before running, e.g.:
#   export OPENAI_API_KEY=sk-...
#   export MATHPIX_APP_ID=...
#   export MATHPIX_APP_KEY=...
# Locally with Docker Compose, put them in a .env file at the repo root
# (.env is gitignored). See .env.example.

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MATHPIX_APP_ID = os.getenv("MATHPIX_APP_ID", "")
MATHPIX_APP_KEY = os.getenv("MATHPIX_APP_KEY", "")

openai.api_key = OPENAI_API_KEY

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set")
if not MATHPIX_APP_ID or not MATHPIX_APP_KEY:
    raise RuntimeError("MATHPIX_APP_ID / MATHPIX_APP_KEY not set")

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

def extract_marks_from_question_text(text: str, fallback: int = 0) -> int:
    """
    Extract marks from the raw question text itself using simple regex patterns.
    We trust the PDF pattern more than GPT.

    Looks for things like:
    - [5]
    - (5)
    - (5 marks)
    - 5M, 5 m
    at the tail of the question text.

    If nothing is found, returns `fallback`.
    """
    if not text:
        return fallback

    # Just look at the last few lines, where marks usually sit
    lines = text.splitlines()
    tail = "\n".join(lines[-3:])  # last 3 lines

    patterns = [
        r"\[(\d+)\]",                 # [5]
        r"\((\d+)\s*marks?\)",        # (5 marks)
        r"\((\d+)\)",                 # (5)
        r"(\d+)\s*M(?:arks?)?\b",     # 5M, 5 Marks
    ]

    for pat in patterns:
        m = re.search(pat, tail, flags=re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue

    return fallback


def detect_answer_type_from_text(answer_text: str) -> str:
    """
    Decide Answer Type: Phrases | Equation | Image.
    """
    if not answer_text:
        return "Phrases"

    lower = answer_text.lower()

    image_keywords = [
        "draw a diagram", "labelled diagram", "labeled diagram",
        "sketch", "draw the figure", "diagrammatic", "flowchart"
    ]
    if any(kw in lower for kw in image_keywords):
        return "Image"

    symbolic_chars = sum(ch in "+-*/=^<>π√" for ch in answer_text)
    digits = sum(ch.isdigit() for ch in answer_text)
    letters = sum(ch.isalpha() for ch in answer_text)

    if (symbolic_chars + digits) > letters * 1.2:
        return "Equation"

    return "Phrases"


def clean_json_output(raw: str) -> str:
    """
    Remove ```json ... ``` wrappers if the model added them.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw

def escape_newlines_in_json_strings(raw: str) -> str:
    """
    Walk through a JSON-like string and replace any literal newline characters
    that appear *inside* double-quoted strings with '\\n', so json.loads can parse it.
    """
    result_chars = []
    in_string = False
    escaped = False

    for ch in raw:
        if in_string:
            if escaped:
                # previous char was backslash, so this char is escaped literally
                result_chars.append(ch)
                escaped = False
            else:
                if ch == '\\':
                    result_chars.append(ch)
                    escaped = True
                elif ch == '"':
                    # end of string
                    result_chars.append(ch)
                    in_string = False
                elif ch == '\n' or ch == '\r':
                    # 🔥 this is the key fix
                    result_chars.append('\\n')
                else:
                    result_chars.append(ch)
        else:
            # outside any string
            result_chars.append(ch)
            if ch == '"':
                in_string = True
                escaped = False

    return ''.join(result_chars)

def call_gpt_json(model: str, system_prompt: str, user_prompt: str, max_tokens: int | None = None) -> Dict[str, Any]:
    limit = max_tokens if max_tokens is not None else int(os.getenv("AEGIS_OPENAI_MAX_OUTPUT_TOKENS", "128000"))
    """
    Call GPT, get a JSON-like string, repair any literal newlines inside string
    values, then parse with json.loads.
    """
    resp = openai.ChatCompletion.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": user_prompt
                + "\n\nRespond with ONLY a single valid JSON object, no prose.",
            },
        ],
        max_completion_tokens=limit,
        temperature=0,
    )

    raw = resp.choices[0].message["content"]
    if raw is None:
        raise ValueError("Model returned no content.")

    raw = raw.strip()

    # 🔥 FIX: escape literal newlines inside JSON string values
    raw = escape_newlines_in_json_strings(raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        # Helpful debug: if it ever breaks again, you see the exact offending JSON
        raise ValueError(f"Failed to parse JSON from model:\n{e}\nRaw output:\n{raw}")

    return data

def normalize_latex_text(latex_text: str) -> str:
    """
    Normalise LaTeX from Mathpix / GPT so it fits your KaTeX/rendering:
    - Remove \section*
    - Map tabular -> array, preserving column spec
    - Remove inline math delimiters \( ... \), \[ ... \], $
    - Simplify \mathrm{} / \mathbf{}
    - Specific effects:
      (\18^{\\text{th}})\  →  18^{\\text{th}}
      \\begin{tabular}{|l|l|} → \\begin{array}{|l|l|}
    """
    if not latex_text:
        return ""

    # 1) Remove section headers
    latex_text = latex_text.replace(r" \section*", "")

    # 2) Replace special spacing placeholder (keep your old behaviour)
    latex_text = latex_text.replace("$\\qquad$", "__")

    # 3) tabular -> array, but preserve the column specification
    latex_text = re.sub(
        r"\\begin{tabular}{([^}]*)}",
        r"\\begin{array}{\1}",
        latex_text,
    )
    latex_text = latex_text.replace(r"\end{tabular}", r"\end{array}")

    # 4) Strip inline math delimiters \( ... \) and \[ ... \]
    latex_text = re.sub(r"\\\((.*?)\\\)", r"\1", latex_text, flags=re.DOTALL)
    latex_text = re.sub(r"\\\[(.*?)\\\]", r"\1", latex_text, flags=re.DOTALL)

    # 5) Remove bare $ delimiters
    latex_text = latex_text.replace("$", "")

    # 6) Simplify \mathrm{} and \mathbf{}
    latex_text = re.sub(r"\\(?:mathrm|mathbf)\{([^\}]+)\}", r"\1", latex_text)

    return latex_text

def highlight_wrong_phrases(solution_text: str, wrong_phrases: List[str]) -> str:
    """
    Wrap each wrong phrase ONCE in <span style='color:red;font-weight:bold'>...</span>.
    """
    highlighted = solution_text
    for phrase in wrong_phrases:
        phrase = phrase.strip()
        if not phrase:
            continue
        if phrase not in highlighted:
            continue
        replacement = f"<span style='color:red;font-weight:bold'>{phrase}</span>"
        highlighted = highlighted.replace(phrase, replacement, 1)
    return highlighted

def html_math_postprocess(text: str) -> str:
    """
    Post-process text to add basic HTML math rendering:
    - 19th -> 19<sup>th</sup>
    - x^2  -> x<sup>2</sup>
    - H_2O -> H<sub>2</sub>O
    (Keeps everything else as-is.)
    """
    if not text:
        return ""

    # 1) Ordinal superscripts: 1st, 2nd, 3rd, 4th, 19th, etc.
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1<sup>\2</sup>", text)

    # 2) Simple power notation: x^2, y^3, (no LaTeX, just caret)
    text = re.sub(r"([A-Za-z0-9])\^(\d+)", r"\1<sup>\2</sup>", text)

    # 3) Simple subscripts: H_2O, x_1, a_2 etc.
    text = re.sub(r"([A-Za-z])_(\d+)", r"\1<sub>\2</sub>", text)

    return text

def latex_math_to_html(text: str) -> str:
    """
    Convert only math-ish LaTeX patterns to basic HTML,
    without touching normal prose text.

    - 19th  -> 19<sup>th</sup>
    - x^2   -> x<sup>2</sup>
    - x^{2} -> x<sup>2</sup>
    - H_2O  -> H<sub>2</sub>O
    - Simple \\begin{array} ... \\end{array} -> <table>...</table>
    """
    if not text:
        return ""

    # --- 1) Simple table conversion from LaTeX array/tabular ---
    # We already convert \\begin{tabular} -> \\begin{array} in normalize_latex_text,
    # so here we only handle \\begin{array}{...} ... \\end{array}
    def _array_to_table(match):
        body = match.group(1)
        rows = body.split(r"\\")
        html_rows = []
        for row in rows:
            row = row.strip()
            if not row:
                continue
            cells = [c.strip() for c in row.split("&")]
            if not any(cells):
                continue
            tds = "".join(f"<td>{c}</td>" for c in cells)
            html_rows.append(f"<tr>{tds}</tr>")
        if not html_rows:
            return ""
        return "<table>" + "".join(html_rows) + "</table>"

    text = re.sub(
        r"\\begin{array}{[^}]*}(.*?)\\end{array}",
        _array_to_table,
        text,
        flags=re.DOTALL,
    )

    # --- 2) Ordinals like 1st, 2nd, 3rd, 4th, 19th ---
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1<sup>\2</sup>", text)

    # --- 3) Superscripts: x^{2}, x^2 ---
    text = re.sub(r"([A-Za-z0-9])\^\{(\d+)\}", r"\1<sup>\2</sup>", text)
    text = re.sub(r"([A-Za-z0-9])\^(\d+)", r"\1<sup>\2</sup>", text)

    # --- 4) Subscripts: H_2O, x_1, a_2, a_{2} ---
    text = re.sub(r"([A-Za-z])_\{(\d+)\}", r"\1<sub>\2</sub>", text)
    text = re.sub(r"([A-Za-z])_(\d+)", r"\1<sub>\2</sub>", text)

    return text

def format_answer_as_html(answer: str) -> str:
    """
    Wrap the answer in basic HTML and convert newlines to <br>
    so it renders correctly in the CMS editor.

    IMPORTANT:
    - We do NOT rephrase or edit the text logically.
    - We only:
      * normalise LaTeX wrappers,
      * convert math-ish patterns to HTML (sup/sub/table),
      * convert newlines to <br>.
    """
    if not answer:
        return ""
    # 1) Normalise LaTeX text (section headers, tabular->array, strip \\( \\), $ etc.)
    answer = normalize_latex_text(answer)

    # 2) Only change math / LaTeX bits into HTML
    answer = latex_math_to_html(answer)

    # 3) Preserve line breaks visually
    answer = answer.replace("\r\n", "\n")
    answer_html = answer.replace("\n", "<br>")

    return f"<div>{answer_html}</div>"

# ====================================================
# STAGE 0: OCR WITH MATHPIX (PDF -> MARKDOWN TEXT)
# ====================================================

# Mathpix credentials loaded earlier...
app_id = ...
app_key = ...

# Add this ↓↓↓
base_url = "https://api.mathpix.com/v3/pdf"

def mathpix_pdf_to_markdown(pdf_path: str) -> str:
    """
    Use Mathpix v3/pdf endpoint to OCR an entire PDF and get Markdown-like text (.mmd).
    This should preserve equations and often image references.

    Docs ref: https://docs.mathpix.com/ (v3/pdf)
    """
    base_url = "https://api.mathpix.com/v3/pdf"
    headers = {
        "app_id": MATHPIX_APP_ID,
        "app_key": MATHPIX_APP_KEY,
    }

    # Step 1: POST PDF to Mathpix
    with open(pdf_path, "rb") as f:
        files = {"file": f}
        data = {
            "options_json": json.dumps({
                # You can tune these, but keep them simple for now
                "rm_spaces": True,
                "rm_fonts": False,
                "numbers_default_to_math": False,
                "enable_tables_fallback": True,
                "math_inline_delimiters": ["\\(", "\\)"],
                "math_block_delimiters": ["\\[", "\\]"],
                "include_diagram_text": False,
            })
        }
        print(f"[Mathpix] Uploading PDF: {pdf_path}")
        print("[Mathpix] Using headers:", {k: repr(v) for k, v in headers.items()})
        try:
            resp = requests.post(base_url, headers=headers, files=files, data=data)
        except Exception as e:
            print("[Mathpix] Exception during requests.post:", type(e).__name__, str(e))
            raise

    # At this point we have an HTTP response from Mathpix
    print("[Mathpix] POST status code:", resp.status_code)
    if resp.text:
        print("[Mathpix] POST response (first 300 chars):", resp.text[:300])

    resp.raise_for_status()
    info = resp.json()
    pdf_id = info.get("pdf_id")
    if not pdf_id:
        raise RuntimeError(f"Mathpix did not return pdf_id: {info}")

    print(f"[Mathpix] pdf_id = {pdf_id}")

    # Step 2: Poll for .mmd result
    mmd_url = f"{base_url}/{pdf_id}.mmd"
    for attempt in range(60):  # up to ~2 minutes (60 * 2s)
        print(f"[Mathpix] Fetching .mmd (attempt {attempt+1})...")
        try:
            r = requests.get(mmd_url, headers=headers)
        except Exception as e:
            print("[Mathpix] Exception during requests.get:", type(e).__name__, str(e))
            raise

        print("[Mathpix] GET status code:", r.status_code)
        if r.status_code == 200 and r.text.strip():
            print("[Mathpix] .mmd content retrieved.")
            return r.text

        time.sleep(2)

    raise RuntimeError("Timed out waiting for Mathpix .mmd result.")

# ====================================================
# STAGE 1: PARSE QUESTIONS FROM OCR TEXT
# ====================================================

def parse_questions_with_gpt(ocr_text: str) -> List[Dict[str, Any]]:
    """
    Use GPT to turn the raw Mathpix OCR of the QUESTION PDF into a clean list
    of main questions.

    IMPORTANT:
    - Marks MUST match the source question paper.
    - GPT is NOT allowed to estimate marks based on length.
    - If a mark value is NOT clearly visible for a question,
      set marks = 0 (or 1 if you prefer) and do NOT guess.
    """
    system_prompt = (
        "You are Joe, an AI assistant for UpSchool. "
        "You will be given OCR text of a school exam QUESTION paper. "
        "You must extract ONLY the MAIN questions (Q1, Q2, ...) in order. "
        "Ignore subparts (a), (b), (i), (ii) at this stage; keep them inside the "
        "question text if they belong to the same question.\n\n"
        "For EACH question, also identify the TOTAL MARKS as an integer.\n"
        "- Look ONLY for explicit clues like [2], [3], (2), (3), '2 marks', '3M', etc. "
        "  near the question or at the end of the question text.\n"
        "- You are NOT allowed to guess or estimate marks based on length or difficulty.\n"
        "- If you cannot find a clear explicit mark for a question, set marks = 0.\n\n"
        "Output a single JSON object with this structure:\n"
        "{\n"
        "  \"questions\": [\n"
        "    {\n"
        "      \"qno\": 1,\n"
        "      \"label\": \"Q1\",\n"
        "      \"marks\": 2,\n"
        "      \"text_lines\": [\"line 1 of the question\", \"line 2\", \"...\"]\n"
        "    },\n"
        "    {\n"
        "      \"qno\": 2,\n"
        "      \"label\": \"Q2\",\n"
        "      \"marks\": 5,\n"
        "      \"text_lines\": [\"...\"]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "- Each question's text must be split into an ARRAY of lines (`text_lines`).\n"
        "- Do NOT put literal newline characters inside a single string.\n"
        "- Do NOT include any commentary outside the JSON."
    )

    user_prompt = (
        "Here is the full OCR text of the QUESTION PDF:\n\n"
        "----- OCR START -----\n"
        f"{ocr_text}\n"
        "----- OCR END -----\n\n"
        "Now extract the main questions as described. "
        "Remember:\n"
        "- Use `text_lines` as an array of strings.\n"
        "- `marks` MUST come ONLY from explicit mark indicators in the text.\n"
        "- If no mark is visible for a question, set `marks` = 0 and do not guess."
    )

    data = call_gpt_json(MODEL_PARSE, system_prompt, user_prompt)

    questions_out: List[Dict[str, Any]] = []
    for item in data.get("questions", []):
        qno = item.get("qno")
        label = item.get("label", f"Q{qno}" if qno is not None else "")

        # Join text_lines back into a single string with newlines
        lines = item.get("text_lines", [])
        if not isinstance(lines, list):
            lines = []
        text = "\n".join(str(line) for line in lines)

        # 1) Start from whatever GPT thought the marks were
        marks_gpt = safe_int(item.get("marks", 0), default=0)

        # 2) BUT override using explicit marks in the actual question text if present
        marks = extract_marks_from_question_text(text, fallback=marks_gpt)

        questions_out.append(
            {
                "qno": qno,
                "label": label,
                "marks": marks,
                "text": text.strip(),
            }
        )

    return questions_out

# ====================================================
# STAGE 2: PARSE SOLUTIONS FROM OCR TEXT
# ====================================================

def parse_solutions_with_gpt(ocr_text: str) -> List[Dict[str, Any]]:
    """
    Use GPT to turn the raw Mathpix OCR of the SOLUTION PDF into a clean list
    of solutions, aligned by question number.

    IMPORTANT: We use `text_lines` (array of strings) instead of one giant `text`
    field to avoid JSON issues with newlines/quotes.
    """
    system_prompt = (
        "You are Joe, an AI assistant for UpSchool. "
        "You will be given OCR text of a school exam SOLUTION paper. "
        "You must extract the solutions for each MAIN question (Q1, Q2, ...), "
        "matching the numbering used in the question paper.\n\n"
        "Output a single JSON object with this structure:\n"
        '{\n'
        '  "solutions": [\n'
        '    {\n'
        '      "qno": 1,\n'
        '      "text_lines": ["line 1 of the solution", "line 2", "..."]\n'
        '    },\n'
        '    {\n'
        '      "qno": 2,\n'
        '      "text_lines": ["..."]\n'
        '    }\n'
        '  ]\n'
        '}\n'
        "- Each solution's text must be split into an ARRAY of strings `text_lines`.\n"
        "- Do NOT use literal newlines inside a single string.\n"
        "- Do NOT include extra commentary outside the JSON."
    )

    user_prompt = (
        "Here is the full OCR text of the SOLUTION PDF:\n\n"
        "----- OCR START -----\n"
        f"{ocr_text}\n"
        "----- OCR END -----\n\n"
        "Now extract the solutions per main question as described. "
        "Remember: use `text_lines` as an array of strings."
    )

    data = call_gpt_json(MODEL_PARSE, system_prompt, user_prompt)

    solutions_out: List[Dict[str, Any]] = []
    for item in data.get("solutions", []):
        qno = item.get("qno")
        # Join text_lines back to a single solution string
        lines = item.get("text_lines", [])
        if not isinstance(lines, list):
            lines = []
        text = "\n".join(str(line) for line in lines)

        solutions_out.append(
            {
                "qno": qno,
                "text": text.strip(),
            }
        )

    return solutions_out

# ====================================================
# STAGE 3: MAP QUESTIONS ↔ SOLUTIONS
# ====================================================

def map_questions_to_solutions(questions: List[Dict[str, Any]],
                               solutions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sol_dict = {s["qno"]: s["text"] for s in solutions}
    mapped = []
    for q in questions:
        qno = q["qno"]
        mapped.append({
            "qno": qno,
            "label": q["label"],
            "question": q["text"],
            "marks": q["marks"],
            "solution": sol_dict.get(qno, "[SOLUTION NOT FOUND]")
        })
    return mapped


# ====================================================
# STAGE 4: ENRICH Q + A (META + RUBRICS + RED HIGHLIGHT)
# ====================================================

def enrich_descriptive_question(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    entry: { 'qno', 'label', 'question', 'marks', 'solution' }

    IMPORTANT:
    - Total marks are taken from OCR (entry["marks"]) and are FIXED.
    - GPT can only distribute these marks across rubrics.
    - GPT can suggest an alternative "model_suggested_marks" for SME info only.
    """
    question_text = entry["question"]
    marks_from_ocr = safe_int(entry["marks"], default=1)  # fixed
    solution_text = entry["solution"]

    categories_str = ", ".join(f'"{c}"' for c in QUESTION_CATEGORIES)

    system_prompt = (
        "You are Joe, an AI assistant for UpSchool that generates structured "
        "metadata and marking schemes for descriptive exam questions. "
        "Your output MUST be a single valid JSON object."
    )

    user_prompt = f"""
You are given:
- A descriptive school-level exam question.
- The OCR model answer.
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
   - Remove any leading numbering like "1.", "Q1", "Question 1" if present.
   - Remove trailing marks notation like "[5]", "(5 marks)" if present.
   - The "question" field should contain the clean student-facing question,
     but still include sub-parts (a), (b), etc. if relevant.

3. Answer type (for UI Answer Type dropdown):
   - One of EXACTLY: "Phrases", "Equation", "Image".
   - Use the MODEL ANSWER:
       - Mostly words/sentences → "Phrases".
       - Mainly symbolic maths/equations → "Equation".
       - Clearly requires a diagram or labelled figure → "Image".

4. Generate rubrics (answer_content) based on the correct content
   implied by the MODEL ANSWER.

   - Divide the total marks ({marks_from_ocr}) into scoring units called "rubrics".
   - Each rubric is one simple, clear criterion that earns some marks.

   Rubric rules:
     - Total of all rubric marks MUST equal exactly {marks_from_ocr}.
     - You MAY use any integer distribution: e.g., 1+1+1, 2+1, 3+2, etc.
     - Do NOT ever give only 1 rubric if {marks_from_ocr} > 1.
     - Do NOT exceed total marks.
     - Do NOT reduce total marks; you MUST use all {marks_from_ocr} marks in rubrics.

   Special case: "any two/three/four" type questions:
     - If the question or answer says:
        "mention any 2/3/4", "state any two/three/four",
        "list any two/three/four", "give any two/three/four",
        or ends with "(Any three)" etc:
       - List ALL valid points as rubrics.
       - Normally give each rubric 1 mark (or a clear scheme).
       - You may include a rubric text like:
         "Award 1 mark per correct point mentioned (any X correct points earn full marks)."

   CRITICAL RUBRIC FORMAT:

   - Each rubric must be on its own line.
   - Each rubric line MUST be in the format:
       <criterion text> (<marks_for_this_rubric> mark)
     or:
       <criterion text> (<marks_for_this_rubric> marks)

   Examples for a 3-mark question:
     Defines autotrophic nutrition clearly (1 mark)
     Mentions chlorophyll as the key pigment (1 mark)
     Mentions sunlight as the energy source (1 mark)

   - Do NOT add any prefixes like "Rubric A" or "[RUBRIC|...]".
   - Do NOT add "and" separators or $$and$$.
   - The sum of all bracketed marks MUST equal {marks_from_ocr}.

5. Identify clearly wrong phrases in the MODEL ANSWER:

   wrong_phrases:
     - A list of exact substrings from the MODEL ANSWER that are conceptually or factually wrong
       at standard school level.
     - Only include phrases that are unambiguously incorrect (e.g., "Mitochondria is the kitchen of the cell").
     - If the model answer is fully correct, return an empty list [].

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

    # Total marks ALWAYS from OCR
    total_marks = marks_from_ocr

    # Suggested marks (for SME info only)
    suggested_marks = safe_int(
        data.get("model_suggested_marks", marks_from_ocr),
        default=marks_from_ocr
    )

    # Wrong phrase highlighting
    wrong_phrases = data.get("wrong_phrases", [])
    if not isinstance(wrong_phrases, list):
        wrong_phrases = []

    highlighted_answer = highlight_wrong_phrases(solution_text, wrong_phrases)
    highlighted_answer_html = format_answer_as_html(highlighted_answer)

    # SME note if GPT "feels" the marks should have been different
    sme_comment = ""
    if suggested_marks != marks_from_ocr:
        sme_comment = (
            "<br><br><span style='color:blue;font-style:italic'>(SME Note: "
            f"GPT suggests this question aligns more with a {suggested_marks}-mark question, "
            f"but OCR extracted {marks_from_ocr}.)</span>"
        )

    result = {
        "question_category": normalize_question_category(data.get("question_category", "")),
        "cognitive_skills": normalize_cognitive_skill(data.get("cognitive_skills", "")),
        "question_source": "UpSchool DB",
        "question_appears_in": "Pre/Post-Worksheet/Test",
        "level_of_difficulty": normalize_difficulty(data.get("level_of_difficulty", "")),
        "question": data.get("question", question_text),

        "marks": total_marks,
        "answer_type": normalize_answer_type(
            data.get("answer_type", detect_answer_type_from_text(solution_text))
        ),
        "answer_weightage": total_marks,

        # Rubrics: plain lines like "Defines ... (1 mark)"
        "answer_content": normalize_latex_text(data.get("answer_content", "")),

        # Display Answer & Explanation (with red highlight + SME note), as HTML
        "display_answer": highlighted_answer_html,
        "answer_explanation": sme_comment + highlighted_answer_html,
    }

    return result

# ====================================================
# STAGE 5: BUILD DATAFRAME & WRITE EXCEL
# ====================================================

def build_descriptive_dataframe(mapped_qna: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []

    for entry in mapped_qna:
        print(f"Enriching Q{entry['qno']} (marks={entry['marks']})...")
        try:
            enriched = enrich_descriptive_question(entry)
        except Exception as e:
            print(f"  ERROR enriching Q{entry['qno']}: {e}")
            enriched = {
                "question_category": "Long Answer Type (5 Marks)" if entry["marks"] >= 5 else "Short Answer Type (2 Marks)",
                "cognitive_skills": "Understanding",
                "question_source": "UpSchool DB",
                "question_appears_in": "Pre/Post-Worksheet/Test",
                "level_of_difficulty": "Moderate",
                "question": entry["question"],
                "marks": entry["marks"],
                "answer_type": detect_answer_type_from_text(entry["solution"]),
                "answer_weightage": entry["marks"],
                "answer_content": "",
                "display_answer": entry["solution"],
                "answer_explanation": entry["solution"],
            }

        # Apply HTML math postprocessing to Question & Answer Content as plain text
        question_html_ready = html_math_postprocess(
            enriched.get("question", entry["question"])
        )
        answer_content_html_ready = html_math_postprocess(
            enriched.get("answer_content", "")
        )

        # ---- LOCK source marks ----
        source_marks = entry["marks"]

        # ---- Build row ----
        row = {
            "Question Label": entry["label"],  # e.g. "Q1"

            "Question Category": enriched.get("question_category", ""),
            "Cognitive Skill": enriched.get("cognitive_skills", ""),
            "Question Source": enriched.get("question_source", "UpSchool DB"),
            "Question Appears in": enriched.get("question_appears_in", "Pre/Post-Worksheet/Test"),
            "Level of Difficulty": enriched.get("level_of_difficulty", ""),

            # Convert ONLY math parts to HTML (sup/sub/table). Keep other text untouched.
            "Question": latex_math_to_html(
                normalize_latex_text(
                    enriched.get("question", entry["question"])
                )
            ),

            # ---- Marks MUST NEVER CHANGE ----
            "Marks": source_marks,

            # Display Answer is EXACT solution text + red highlights + HTML line breaks
            "Display Answer": enriched.get("display_answer", entry["solution"]),

            "Answer Type": enriched.get(
                "answer_type",
                detect_answer_type_from_text(entry["solution"])
            ),

            # Weightage can equal marks (single value rubric)
            "Answer Weightage": enriched.get("answer_weightage", source_marks),

            # ---- Rubrics: stored as-is (except LaTeX → HTML math) ----
            "Answer Content": latex_math_to_html(
                normalize_latex_text(
                    enriched.get("answer_content", "")
                )
            ),

            # SME NOTE + original solution (already built in enrichment)
            "Answer Explanation": enriched.get(
                "answer_explanation",
                enriched.get("display_answer", entry["solution"])
            )
        }
        rows.append(row)

    df = pd.DataFrame(rows)

    col_order = [
        "Question Label",
        "Question Category",
        "Cognitive Skill",
        "Question Source",
        "Question Appears in",
        "Level of Difficulty",
        "Question",
        "Marks",
        "Display Answer",
        "Answer Type",
        "Answer Weightage",
        "Answer Content",
        "Answer Explanation",
    ]
    df = df[col_order]
    return df

def write_excel_descriptive(df: pd.DataFrame, output_path: str):
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Descriptive", index=False)
    print(f"Saved Excel to: {output_path}")


# ====================================================
# MAIN ENTRYPOINT
# ====================================================

def process_bulk_upload(question_pdf_path: str, solution_pdf_path: str, output_excel_path: str):
    """
    End-to-end Bulk Upload:
      - Use Mathpix to OCR question & solution PDFs.
      - Use GPT to parse into structured main questions + solutions.
      - Enrich with metadata + rubrics + red-highlight for wrong phrases.
      - Write a single Excel file with Descriptive sheet.
    """
    print(f"Mathpix OCR: Question PDF → text")
    questions_ocr = mathpix_pdf_to_markdown(question_pdf_path)

    print(f"Mathpix OCR: Solution PDF → text")
    solutions_ocr = mathpix_pdf_to_markdown(solution_pdf_path)

    print("Parsing questions with GPT...")
    questions = parse_questions_with_gpt(questions_ocr)
    print(f"Parsed {len(questions)} main questions.")

    total_marks = sum(q["marks"] for q in questions)
    print("DEBUG: Total questions:", len(questions))
    print("DEBUG: Total marks parsed:", total_marks)
    for q in questions:
        print(f"Q{q['qno']}: marks={q['marks']} | {q['text'].splitlines()[0][:80]}")

    print("Parsing solutions with GPT...")
    solutions = parse_solutions_with_gpt(solutions_ocr)
    print(f"Parsed {len(solutions)} main solutions.")

    mapped_qna = map_questions_to_solutions(questions, solutions)
    print(f"Mapped {len(mapped_qna)} Q↔A pairs.")

    df_desc = build_descriptive_dataframe(mapped_qna)
    write_excel_descriptive(df_desc, output_excel_path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        print("Usage: python bulk_upload_mathpix_v1.py <question_pdf> <solution_pdf> <output_excel.xlsx>")
    else:
        q_pdf = sys.argv[1]
        s_pdf = sys.argv[2]
        out_xlsx = sys.argv[3]
        process_bulk_upload(q_pdf, s_pdf, out_xlsx)
