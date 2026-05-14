# New Assessment Tagging Workflow – Design

## Overview

Rebuild the tool to:
1. Clear default Assessment Labels
2. Match each question (from chapter JSON) to a Concept using GPT
3. Set group_type from JSON difficulty_level
4. Cluster variant questions (same question, different wording) in same cell
5. Update group_description for filled rows
6. Show progress: (1/5): Concept-Assessment Matching 1/55...

---

## Sheet Columns (from sample)

| Column | Action |
|--------|--------|
| display_name | **Unaltered** |
| group_name | **Unaltered** |
| group_description | Update with clear description (similar questions, different variants) |
| group_status | Unaltered |
| group_type | Set from JSON: lessDifficult→Basic, moderatelyDifficult→Intermediate, highlyDifficult→Advanced |
| Assessment Label | Clear first, then fill with matched labels (variants in same cell, line break) |
| Concept name | Used as cluster – GPT matches question content to these |

---

## Steps

### Step 1: Accumulate
- Read sheet: unique concepts, existing group structure (display_name pattern: (prefix) BG01, IG01, AG01)
- Read chapter JSON: all questions with question_label, question_content, difficulty_level

### Step 2: Concept–Assessment Matching (GPT)
- For each question, call GPT: "Which concept does this question belong to?" given list of concepts
- Progress: (1/5): Concept-Assessment Matching 1/N, 2/N...

### Step 3: Group Type
- Map difficulty_level → group_type (from JSON, no GPT)

### Step 4: Variant Clustering (GPT)
- Within each (concept, group_type), call GPT to find variants (e.g. "Define X" vs "What is X?")
- Group variant labels; write to same cell with line breaks

### Step 5: Write + Descriptions
- Write Assessment Labels to correct rows
- Update group_description for rows with assessments

---

## GPT API

- Uses OpenAI API (gpt-5-mini-2025-08-07) via UrlFetchApp
- Set API key: **Assessment Tagging** menu → **Set OpenAI API Key**
- Or: File → Project properties → Script properties → Add `OPENAI_API_KEY`

---

## Progress Tracker

- Write progress to `PropertiesService.getScriptProperties()` (e.g. key `assessmentTaggingProgress`)
- UI polls `getProgress()` every 1s
- Format: `{ phase: 1, phaseName: "Concept-Assessment Matching", current: 5, total: 55 }`
