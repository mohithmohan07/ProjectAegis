/**
 * Assessment Tagging Tool - Google Apps Script (JSON-Aware Routing Patch)
 * 
 * Adds Question Gist column to the CURRENT sheet. Select a chapter → processes only that chapter's JSON.
 * Format: "Question Label - Gist of the Question Content"
 * 
 * Setup:
 * 1. Run prepare_chapter_files.py to split the JSON by chapter
 * 2. Upload the output folder to Google Drive
 * 3. Set QUESTION_BANK_FOLDER_ID to that folder's ID
 * 4. (Optional) If folder access fails, set MANIFEST_FILE_ID to manifest.json's file ID
 * 5. Enable Drive API: Extensions → Apps Script API → add "Drive" service
 * 6. Click Run → Select a chapter → Process only that chapter's questions
 */

// ============ CONFIG ============
// All folders containing Groups sheets (extension will process sheets in all of these)
const FOLDER_IDS = [
  '188OvF9l4IWqaal3Q-IBpR7NjyMnYxGNF',
  '19dSuRrZNXFaAmmKcKcyTQSgmRuOajYwz',
  '1T01cqTWWc8TLCFJuaTDS6tEQbRolu-fo',
  '1__YWexaogJ9CVXa6jHLdo_gHikXWgC5M'
];

// JSON folder: JSON + chapter files in ONE folder (dynamic)
// https://drive.google.com/drive/folders/1Ad8h7u-Tn-Pgt_AQO9parUvJF2bAm8nE
// Contains: ULTIMATE_PUBLISHED_JSON_FILE.json, manifest.json, questions_*.json
const QUESTION_BANK_FOLDER_ID = '1Ad8h7u-Tn-Pgt_AQO9parUvJF2bAm8nE';

// Optional: If DriveApp fails (e.g. Shared Drive), set manifest.json file ID here.
// Get it from: open manifest.json in Drive → right-click → Get link → copy ID from /d/ID/view
const MANIFEST_FILE_ID = '1gmmMYOCgqRSa5GQgPAI11fau7VyJMMTq';

// OpenAI API key for Smart Workflow.
// Recommended: leave this blank and store OPENAI_API_KEY in Apps Script Properties.
const OPENAI_API_KEY = '';

// ============ HELPERS ============

const STOP_FLAG_KEY = 'assessmentTaggingStop';

function getWorkflowUserProperties() {
  return PropertiesService.getUserProperties();
}

function clearStopFlag() {
  getWorkflowUserProperties().deleteProperty(STOP_FLAG_KEY);
}

function requestTerminate() {
  getWorkflowUserProperties().setProperty(STOP_FLAG_KEY, 'true');
}

function checkStopFlag() {
  if (getWorkflowUserProperties().getProperty(STOP_FLAG_KEY) === 'true') {
    clearStopFlag();
    throw new Error('Terminated by user');
  }
}

/**
 * Fetch file content via Drive API (bypasses DriveApp when folder access fails).
 */
function getFileContentByDriveApi(fileId) {
  const url = 'https://www.googleapis.com/drive/v3/files/' + fileId + '?alt=media';
  const response = UrlFetchApp.fetch(url, {
    headers: { 'Authorization': 'Bearer ' + ScriptApp.getOAuthToken() },
    muteHttpExceptions: true
  });
  if (response.getResponseCode() !== 200) {
    throw new Error('Drive API: Could not read file ' + fileId + ' (' + response.getResponseCode() + ')');
  }
  return response.getContentText('UTF-8');
}

/**
 * Find a file by name in a folder using Drive API.
 */
function findFileInFolderByDriveApi(folderId, filename) {
  const escapedName = (filename || '').replace(/'/g, "''");
  const list = Drive.Files.list({
    q: "'" + folderId + "' in parents and name='" + escapedName + "' and trashed=false",
    fields: 'items(id,name)',
    maxResults: 1
  });
  if (!list.items || list.items.length === 0) return null;
  return list.items[0].id;
}

function sanitizeForFilename(prefix) {
  return prefix.replace(/[/\\:*?"<>|]/g, '_').replace(/\s+/g, '_').substring(0, 100);
}

/**
 * Strip HTML tags and get readable gist of question content.
 */
function questionContentGist(html, maxLength) {
  if (!html || typeof html !== 'string') return '';
  maxLength = maxLength || 120;
  
  let text = html
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<p[^>]*>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/[ \t]+/g, ' ')
    .replace(/\n\s*\n+/g, '\n')
    .trim();
  
  if (text.length > maxLength) {
    text = text.substring(0, maxLength - 3).replace(/\s+\S*$/, '') + '...';
  }
  return text;
}

/**
 * Build Assessment Gist in format: "actual_label - Gist of the Question Content"
 * e.g. "10ICHI_FrstWrfIndpndnc_PL_Q14 - Give three causes for the resentment..."
 */
function buildAssessmentGist(labels, questionContent) {
  const gist = questionContentGist(questionContent || '');
  const labelList = Array.isArray(labels) ? labels : (labels ? [labels] : []);
  if (labelList.length === 0) return gist;
  return labelList.map(function(lbl) { return (lbl || '').trim() + ' - ' + gist; }).join('\n');
}

/** @deprecated Use buildAssessmentGist. Kept for legacy single-label format. */
function buildQuestionGist(questionLabel, questionContent) {
  return buildAssessmentGist([questionLabel], questionContent);
}

/**
 * Get ALL Google Sheets in folder and all subfolders.
 */
function getAllSheetsInFolder(folderId) {
  const folder = DriveApp.getFolderById(folderId);
  const sheets = [];
  function recurse(f) {
    const files = f.getFilesByType(MimeType.GOOGLE_SHEETS);
    while (files.hasNext()) sheets.push(files.next());
    const subfolders = f.getFolders();
    while (subfolders.hasNext()) recurse(subfolders.next());
  }
  recurse(folder);
  return sheets;
}

/**
 * Get ALL Google Sheets from multiple folders (avoids duplicates).
 */
function getAllSheetsInFolders(folderIds) {
  const sheets = [];
  const seen = {};
  (folderIds || FOLDER_IDS).forEach(function(folderId) {
    try {
      const folder = DriveApp.getFolderById(folderId);
      function recurse(f) {
        const files = f.getFilesByType(MimeType.GOOGLE_SHEETS);
        while (files.hasNext()) {
          const file = files.next();
          if (!seen[file.getId()]) {
            seen[file.getId()] = true;
            sheets.push(file);
          }
        }
        const subfolders = f.getFolders();
        while (subfolders.hasNext()) recurse(subfolders.next());
      }
      recurse(folder);
    } catch (e) {
      Logger.log('Folder ' + folderId + ': ' + e.message);
    }
  });
  return sheets;
}

/**
 * Load questions map from a chapter JSON file.
 */
function loadQuestionsMapFromChapter(chapterQuestions) {
  const map = {};
  const arr = Array.isArray(chapterQuestions) ? chapterQuestions : [chapterQuestions];
  arr.forEach(function(q) {
    const label = (q.question_label || '').trim();
    if (label) {
      map[label] = q.question_content || '';
    }
  });
  return map;
}

/**
 * Find column index by header name (case-insensitive).
 */
function findColumnIndex(headers, name) {
  const lower = (name || '').toLowerCase().trim();
  for (let i = 0; i < headers.length; i++) {
    if ((headers[i] || '').toLowerCase().trim() === lower) {
      return i;
    }
  }
  return -1;
}

/**
 * Add or update Question Gist and Assessment Label in a sheet.
 * mode: 'rewrite_full' | 'rewrite_assessments' | 'add_on'
 */
function addQuestionGistToSheet(ss, questionsMap, mode) {
  mode = mode || 'add_on';
  const result = { updated: 0, skipped: 0, added: 0, errors: [] };
  const chapterLabels = Object.keys(questionsMap);

  ss.getSheets().forEach(function(sheet) {
    try {
      checkStopFlag();
      const data = sheet.getDataRange().getValues();
      if (!data || data.length < 1) return;

      const headers = data[0];
      let assessmentLabelCol = findColumnIndex(headers, 'Assessment Label');
      if (assessmentLabelCol < 0) return;

      let gistCol = findColumnIndex(headers, 'Assessment Gist');
      if (gistCol < 0) {
        const afterCol = assessmentLabelCol + 2;
        sheet.insertColumnAfter(assessmentLabelCol + 1);
        sheet.getRange(1, afterCol).setValue('Assessment Gist').setFontWeight('bold');
        gistCol = assessmentLabelCol + 1;
      }

      const existingLabels = {};
      for (let r = 1; r < data.length; r++) {
        const label = (data[r][assessmentLabelCol] || '').toString().trim();
        if (label) existingLabels[label] = r;
      }

      if (mode === 'add_on') {
        let nextRow = data.length + 1;
        for (let i = 0; i < chapterLabels.length; i++) {
          if (i % 25 === 0) checkStopFlag();
          const label = chapterLabels[i];
          if (existingLabels[label]) {
            result.skipped++;
            continue;
          }
          const content = questionsMap[label];
          const gistValue = buildQuestionGist(label, content);
          sheet.getRange(nextRow, assessmentLabelCol + 1).setValue(label);
          sheet.getRange(nextRow, gistCol + 1).setValue(gistValue);
          existingLabels[label] = nextRow;
          nextRow++;
          result.added++;
        }
      } else if (mode === 'rewrite_assessments') {
        for (let r = 1; r < data.length; r++) {
          if ((r - 1) % 50 === 0) checkStopFlag();
          const label = (data[r][assessmentLabelCol] || '').toString().trim();
          if (!label || !questionsMap[label]) continue;
          sheet.getRange(r + 1, assessmentLabelCol + 1).setValue(label);
          result.updated++;
        }
      } else {
        for (let r = 1; r < data.length; r++) {
          if ((r - 1) % 50 === 0) checkStopFlag();
          const label = (data[r][assessmentLabelCol] || '').toString().trim();
          if (!label || !questionsMap[label]) continue;
          const content = questionsMap[label];
          const gistValue = buildQuestionGist(label, content);
          sheet.getRange(r + 1, assessmentLabelCol + 1).setValue(label);
          sheet.getRange(r + 1, gistCol + 1).setValue(gistValue);
          result.updated++;
        }
        let nextRow = data.length + 1;
        for (let i = 0; i < chapterLabels.length; i++) {
          if (i % 25 === 0) checkStopFlag();
          const label = chapterLabels[i];
          if (existingLabels[label]) continue;
          const content = questionsMap[label];
          const gistValue = buildQuestionGist(label, content);
          sheet.getRange(nextRow, assessmentLabelCol + 1).setValue(label);
          sheet.getRange(nextRow, gistCol + 1).setValue(gistValue);
          existingLabels[label] = nextRow;
          nextRow++;
          result.added++;
        }
      }
    } catch (e) {
      result.errors.push(sheet.getName() + ': ' + e.message);
    }
  });

  return result;
}

// ============ CHAPTER PICKER ============

/**
 * Get chapter list from manifest.json for the picker dialog.
 * Tries: (1) MANIFEST_FILE_ID if set, (2) DriveApp, (3) Drive API fallback.
 */
function getChapterList() {
  let jsonText = null;

  // 1. Direct file ID (bypasses folder access)
  if (MANIFEST_FILE_ID) {
    try {
      const file = DriveApp.getFileById(MANIFEST_FILE_ID);
      jsonText = file.getBlob().getDataAsString('UTF-8');
    } catch (e) {
      try {
        jsonText = getFileContentByDriveApi(MANIFEST_FILE_ID);
      } catch (e2) {
        throw new Error('Could not read manifest (MANIFEST_FILE_ID). Check the file ID and sharing.');
      }
    }
  }

  // 2. Via folder - DriveApp
  if (!jsonText && QUESTION_BANK_FOLDER_ID) {
    try {
      const folder = DriveApp.getFolderById(QUESTION_BANK_FOLDER_ID);
      const manifestFile = folder.getFilesByName('manifest.json').next();
      jsonText = manifestFile.getBlob().getDataAsString('UTF-8');
    } catch (e) {
      // 3. Drive API fallback (works when DriveApp fails, e.g. Shared Drive)
      const fileId = findFileInFolderByDriveApi(QUESTION_BANK_FOLDER_ID, 'manifest.json');
      if (!fileId) {
        throw new Error('manifest.json not found. Check QUESTION_BANK_FOLDER_ID and folder access. ' + (e.message || ''));
      }
      jsonText = getFileContentByDriveApi(fileId);
    }
  }

  if (!jsonText) {
    throw new Error('Please set QUESTION_BANK_FOLDER_ID or MANIFEST_FILE_ID in Code.gs.');
  }
  return JSON.parse(jsonText);
}

/**
 * Load chapter JSON content. Tries DriveApp first, then Drive API fallback.
 */
function loadChapterFileContent(filename) {
  if (!QUESTION_BANK_FOLDER_ID) {
    throw new Error('Please set QUESTION_BANK_FOLDER_ID in Code.gs.');
  }
  let jsonText = null;
  try {
    const folder = DriveApp.getFolderById(QUESTION_BANK_FOLDER_ID);
    const files = folder.getFilesByName(filename);
    if (files.hasNext()) {
      jsonText = files.next().getBlob().getDataAsString('UTF-8');
    }
  } catch (e) {
    // Drive API fallback
    const fileId = findFileInFolderByDriveApi(QUESTION_BANK_FOLDER_ID, filename);
    if (fileId) {
      jsonText = getFileContentByDriveApi(fileId);
    }
  }
  if (!jsonText) {
    throw new Error('Chapter file not found: ' + filename);
  }
  return jsonText;
}

/**
 * Run for selected chapter. Dispatches to Rewrite Full or Tag New Assessments.
 * mode: 'rewrite_full' | 'tag_new'
 */
function runForChapter(prefix, mode) {
  Logger.log('runForChapter: ' + prefix + ', mode=' + mode);
  mode = (mode || 'rewrite_full').toLowerCase();
  if (mode === 'tag_new') {
    return runForChapterTagNew(prefix);
  }
  return runForChapterRewriteFull(prefix);
}

/**
 * Show the chapter picker dialog.
 */
function showChapterPicker() {
  if (!QUESTION_BANK_FOLDER_ID) {
    SpreadsheetApp.getUi().alert('Please set QUESTION_BANK_FOLDER_ID in Code.gs to your chapter files folder.\n\nRun prepare_chapter_files.py first, then upload the output folder to Drive.');
    return;
  }
  const html = HtmlService.createHtmlOutputFromFile('ChapterPicker')
    .setTitle('Assessment Tagging')
    .setWidth(420)
    .setHeight(720);
  SpreadsheetApp.getUi().showSidebar(html);
}

/**
 * Add custom menu. Runs when any sheet is opened (and when add-on is installed).
 */
function onOpen(e) {
  if (e && e.authMode === ScriptApp.AuthMode.NONE) return;
  SpreadsheetApp.getUi()
    .createMenu('Assessment Tagging')
    .addItem('Run (Select Chapter)', 'showChapterPicker')
    .addToUi();
}

/**
 * Runs when the add-on is first installed. Adds the menu immediately.
 */
function onInstall(e) {
  onOpen(e);
}

/**
 * Build the add-on homepage card (shown when user opens Extensions → Add-ons).
 */
function buildHomepage(e) {
  if (e && e.authMode === ScriptApp.AuthMode.NONE) {
    const card = CardService.newCardBuilder()
      .setHeader(CardService.newCardHeader().setTitle('Assessment Tagging Tool'))
      .addSection(CardService.newCardSection()
        .addWidget(CardService.newTextParagraph()
          .setText('Open this add-on from a Google Sheet and approve permissions when prompted. Then use the Assessment Tagging menu in the spreadsheet toolbar to run JSON-aware chapter tagging.')))
      .build();
    return [card];
  }

  const card = CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader().setTitle('Assessment Tagging Tool'))
    .addSection(CardService.newCardSection()
      .addWidget(CardService.newTextParagraph()
        .setText('Routes and writes assessment labels, gists, descriptions, and comments by reading full chapter JSON. Objective questions use the correct answer for placement; bundled prompts are read from the full question text.'))
      .addWidget(CardService.newTextParagraph()
        .setText('Use the Assessment Tagging menu in the spreadsheet toolbar, or tap the button below.'))
      .addWidget(CardService.newTextButton()
        .setText('Open Chapter Picker')
        .setOnClickAction(CardService.newAction().setFunctionName('showChapterPickerFromCard'))))
    .build();
  return [card];
}

/**
 * Opens the chapter picker sidebar. Called from the add-on homepage card.
 */
function showChapterPickerFromCard() {
  try {
    showChapterPicker();
  } catch (err) {
    const msg = (err && err.message) ? err.message : String(err);
    return CardService.newActionResponseBuilder()
      .setNotification(CardService.newNotification().setText(msg))
      .build();
  }
  return CardService.newActionResponseBuilder().build();
}
