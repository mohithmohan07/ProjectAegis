# Deployment Guide – Assessment Tagging Tool

## Step 1: Fix the FOLDER_ID (Important)

Your screenshot shows a typo in `FOLDER_ID`. Please correct it:

| Current (Wrong) | Correct |
|-----------------|---------|
| `10sN_1g0A-RKJ9wtmcGjPo1mEtIa_brWj` | `1OsN_lg0A-RKJ9wtmcGjPo1mEtIa_brWj` |

- `10sN` → `1OsN` (number 1, letter O, letter s, letter N)
- `1g0A` → `lg0A` (lowercase L, letter g, number 0, letter A)

**Correct line:**
```javascript
const FOLDER_ID = '1OsN_lg0A-RKJ9wtmcGjPo1mEtIa_brWj';
```

---

## Step 2: Verify JSON_FILE_ID

You have: `1d91cBD1LHDeuZYH0X2gJchXNt-jQqXCK`

- Open that file in Drive: `https://drive.google.com/file/d/1d91cBD1LHDeuZYH0X2gJchXNt-jQqXCK/view`
- Confirm it is your `ULTIMATE_PUBLISHED_JSON_FILE.json`
- Ensure the script has access (same Google account or shared with it)

---

## Step 3: Save the Project

1. **Ctrl+S** (or Cmd+S on Mac) to save
2. Click **File → Save** and give the project a name (e.g. "Assessment Tagging Tool")

---

## Step 4: First Run (Authorization)

1. In the function dropdown (top toolbar), select **`addQuestionGistToAllSheets`**
2. Click **Run** (▶)
3. When prompted **"Authorization required"**:
   - Click **Review permissions**
   - Choose your Google account
   - Click **Advanced** → **Go to [project name] (unsafe)**
   - Click **Allow**
4. The script will run. If `SpreadsheetApp.getUi()` is null (running from editor), you may see an error in the execution log. Check **View → Execution log** or **View → Logs** for the result.

---

## Step 5: Deployment Options

### Option A: Run from Script Editor (No deployment)

- Use this for testing and one-off runs
- Select `addQuestionGistToAllSheets` → Click **Run**
- Check **Executions** (left sidebar, clock icon) for status and logs

### Option B: Add Menu to a Google Sheet

1. Open any Google Sheet in your Drive
2. **Extensions → Apps Script**
3. Paste the same code (or link this project)
4. Save
5. Refresh the Sheet
6. A new menu **Assessment Tagging** appears
7. Click **Assessment Tagging → Add Question Gist to All Groups Sheets**

### Option C: Deploy as Add-on (Recommended)

Deploy as a Google Sheets Add-on so the **Assessment Tagging** menu appears in every sheet you open.

1. **Enable manifest**: Project Settings (gear icon) → check **Show "appsscript.json" manifest file in editor**
2. Ensure `appsscript.json` exists with the `addOns` block (included in this project)
3. Click **Deploy → New deployment**
4. Click the gear icon next to "Select type" → **Add-on**
5. Description: e.g. "Assessment Tagging v1"
6. Click **Deploy**
7. Authorize if prompted
8. Copy the **Installation link** from the deployment
9. Open the link in a new tab to install the add-on
10. Open any Google Sheet → the **Assessment Tagging** menu appears in the menu bar
11. Use **Extensions → Assessment Tagging Tool** for the add-on homepage, or **Assessment Tagging → Run (Select Chapter)** for the chapter picker

### Option D: Deploy as Web App (Optional)

1. Click **Deploy → New deployment**
2. Click the gear icon next to "Select type" → **Web app**
3. Description: e.g. "Assessment Tagging"
4. Execute as: **Me**
5. Who has access: **Only myself** (or your org)
6. Click **Deploy**
7. Authorize if prompted
8. Copy the Web app URL – you can open it in a browser to trigger the script

---

## Step 6: Run and Verify

1. Run the script (from editor or menu)
2. Wait for completion (may take a few minutes for many sheets)
3. Open one of your Groups Sheets (e.g. in Groups_9_CBSE)
4. Confirm the **Question Gist** column exists with format: `Question Label - Gist of the Question Content`

---

## Process All Sheets in Folder

To run the tool on **every spreadsheet** in a folder (and its subfolders) without opening each one:

1. Open the sheet that has the Assessment Tagging script (Extensions → Apps Script, or the bound sheet)
2. Click **Assessment Tagging → Run (Select Chapter)**
3. Select your chapter and workflow
4. Check **Process all sheets in folder**
5. Click **Run**

This processes all spreadsheets in `PROCESS_ALL_FOLDER_ID` (default: `1OsN_lg0A-RKJ9wtmcGjPo1mEtIa_brWj`). To change the folder, edit `PROCESS_ALL_FOLDER_ID` in `Code.gs`.

---

## Troubleshooting

| Issue | Fix |
|------|-----|
| "Please set JSON_FILE_ID" | Add your JSON file ID in the CONFIG section |
| "Exception: Cannot find folder" | Fix FOLDER_ID typo (see Step 1) |
| "Exception: Cannot find file" | Check JSON_FILE_ID; ensure file is in Drive and accessible |
| "SpreadsheetApp.getUi() is null" | Run from a Sheet (Extensions → Apps Script) so the menu/UI is available, or use Execution log |
| Script times out | For large folders, consider processing in batches (future enhancement) |
| "Server error" loading chapters | The script now uses Drive API as fallback. Enable it: **Extensions → Apps Script API** → add **Drive** service. If it still fails, set **MANIFEST_FILE_ID** in Code.gs to the manifest.json file ID (open file in Drive → Get link → copy ID from `/d/ID/view`). |
