# Assessment Tagging – Google Apps Script

Adds **Question Gist** column to all Groups Sheets. Format: `Question Label - Gist of the Question Content`

**Dynamic, one folder:** JSON + chapter files live in [this Drive folder](https://drive.google.com/drive/folders/1Ad8h7u-Tn-Pgt_AQO9parUvJF2bAm8nE).

## Setup

### Step 1: Prepare chapter files (Python – run when JSON changes)

```bash
cd assessment-tagging-tool
pip install gdown google-api-python-client google-auth
python prepare_chapter_files.py --drive-folder 1Ad8h7u-Tn-Pgt_AQO9parUvJF2bAm8nE
```

This downloads the JSON from that folder, creates chapter files, and **uploads them to the same folder**. No separate folder needed.

For upload, add `credentials.json` (service account) and share the folder with the service account email.

### Step 2: Apps Script

1. Go to [script.google.com](https://script.google.com) → New project
2. Add **Code.gs** and **ChapterPicker.html**
3. `QUESTION_BANK_FOLDER_ID` is already set to the JSON folder
4. Save and authorize

## Usage

1. Open any Google Sheet
2. **Extensions → Apps Script** (or use the script project)
3. Refresh the Sheet – menu **Assessment Tagging** appears
4. Click **Assessment Tagging → Run (Select Chapter)**
5. Sidebar opens with list of chapter codes
6. Search or scroll, click a chapter to select
7. Click **Run for Selected Chapter**
8. Only that chapter's questions are processed

## What it does

1. Reads `manifest.json` from your chapter files folder (small)
2. Shows chapter list in sidebar
3. On Run: loads only the selected chapter's JSON (small file)
4. Updates all Groups Sheets in the folder with Question Gist for matching Assessment Labels
