/**
 * Smart Workflow: Concept matching, group_type from difficulty, variant clustering, progress tracking.
 * Requires OPENAI_API_KEY in Script Properties (Project Settings or setApiKey function).
 */

const PROGRESS_KEY = 'assessmentTaggingProgress';
const OPENAI_MODEL = 'gpt-5.4-mini-2026-03-17';
const PHASES = [
  'Loading data',
  'Topic routing',
  'Variant clustering',
  'Group descriptions',
  'Writing',
  'QA review'
];

/**
 * qProgress: { qTotal?, qCurrent? } optional — chapter question count for sidebar; qCurrent is the match index when set.
 */
function setProgress(phase, current, total, detail, qProgress) {
  qProgress = qProgress || {};
  let prevQTotal = 0;
  let prevQCurrent = 0;
  try {
    const raw = getWorkflowProgressProperties().getProperty(PROGRESS_KEY);
    if (raw) {
      const prev = JSON.parse(raw);
      prevQTotal = prev.qTotal || 0;
      prevQCurrent = prev.qCurrent || 0;
    }
  } catch (e) {}

  const qTotal = qProgress.qTotal != null ? qProgress.qTotal : prevQTotal;
  let qCurrent = qProgress.qCurrent != null ? qProgress.qCurrent : prevQCurrent;
  if (qProgress.qCurrent == null && phase < 1) {
    qCurrent = 0;
  }

  const o = {
    phase: phase,
    phaseName: PHASES[phase] || 'Processing',
    phaseTotal: PHASES.length,
    current: current || 0,
    total: total || 0,
    detail: detail || '',
    ts: new Date().getTime(),
    qTotal: qTotal,
    qCurrent: qCurrent
  };
  getWorkflowProgressProperties().setProperty(PROGRESS_KEY, JSON.stringify(o));
}

/** Sidebar UI: canonical step labels (same order as progress phases). */
function getWorkflowPhaseLabels() {
  return PHASES.slice();
}

function getProgress() {
  const s = getWorkflowProgressProperties().getProperty(PROGRESS_KEY);
  if (!s) return { phase: -1, phaseName: 'Idle', phaseTotal: PHASES.length, current: 0, total: 0, detail: '', qTotal: 0, qCurrent: 0 };
  try {
    return JSON.parse(s);
  } catch (e) {
    return { phase: -1, phaseName: 'Idle', phaseTotal: PHASES.length, current: 0, total: 0, detail: '', qTotal: 0, qCurrent: 0 };
  }
}

function clearProgress() {
  getWorkflowProgressProperties().deleteProperty(PROGRESS_KEY);
}

function getOpenAIKey() {
  const codeKey = (typeof OPENAI_API_KEY !== 'undefined' && OPENAI_API_KEY) ? String(OPENAI_API_KEY).trim() : '';
  const propKey = (PropertiesService.getScriptProperties().getProperty('OPENAI_API_KEY') || '').trim();
  return propKey || codeKey || '';
}

function setApiKey(key) {
  if (key && key.trim()) {
    PropertiesService.getScriptProperties().setProperty('OPENAI_API_KEY', key.trim());
    return true;
  }
  return false;
}

function difficultyToGroupType(difficulty) {
  const d = (difficulty || '').toString().trim().toLowerCase();
  if (!d) return 'Basic';
  if (/(^|\b)(basic|low|lessdifficult|less difficult)(\b|$)/i.test(d)) return 'Basic';
  if (/(^|\b)(intermediate|moderate|moderatelydifficult|moderately difficult|medium)(\b|$)/i.test(d)) return 'Intermediate';
  if (/(^|\b)(advanced|high|highlydifficult|highly difficult)(\b|$)/i.test(d)) return 'Advanced';
  return 'Basic';
}

/** Highlight for rows where a question was placed on a topic's culmination concept. */
var CULMINATION_ROW_BG = '#FFF2CC';
/** Light red: post-write QA — possible mismatch among label, gist, concept, topic. */
var POTENTIAL_ERROR_BG = '#FCE4D6';


function getWorkflowProgressProperties() {
  return (typeof getWorkflowUserProperties === 'function')
    ? getWorkflowUserProperties()
    : PropertiesService.getUserProperties();
}

function safeJsonParse(text, fallback) {
  try {
    return JSON.parse(text);
  } catch (e) {
    return fallback;
  }
}


function decodeBasicHtmlEntities(text) {
  return String(text || '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/g, "'")
    .replace(/&#x2F;/g, '/');
}

function htmlToReadableTextFull(html) {
  if (!html && html !== 0) return '';
  var text = String(html)
    .replace(/<img[^>]*alt=\"([^\"]*)\"[^>]*>/gi, ' [Image: $1] ')
    .replace(/<img[^>]*>/gi, ' ')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<p[^>]*>/gi, '')
    .replace(/<\/div>/gi, '\n')
    .replace(/<div[^>]*>/gi, '')
    .replace(/<li[^>]*>/gi, '\n- ')
    .replace(/<\/li>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\$\$\s*([a-z0-9]+)\s*\$\$/gi, ' [AnswerKey:$1] ');
  text = decodeBasicHtmlEntities(text)
    .replace(/[ \t]+/g, ' ')
    .replace(/\s*\n\s*/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  return text;
}

function getQuestionTypeLower(questionOrContent) {
  if (!questionOrContent || typeof questionOrContent === 'string') return '';
  return String(questionOrContent.question_type || '').trim().toLowerCase();
}

function extractCorrectAnswerObjects(questionRecord) {
  var answers = (questionRecord && Array.isArray(questionRecord.answers_of_question)) ? questionRecord.answers_of_question : [];
  return answers.filter(function(ans) {
    var wt = parseFloat(String(ans.answer_weightage || '0').trim() || '0');
    var display = String(ans.answer_display || '').trim().toLowerCase();
    return wt > 0 || display === 'yes' || display === 'true';
  });
}

function normalizeAnswerTextForRouting(answer) {
  if (!answer) return '';
  var raw = answer.answer_content != null ? answer.answer_content : '';
  return htmlToReadableTextFull(raw);
}

function buildRoutingInput(questionOrContent, questionLabel) {
  if (questionOrContent == null) {
    return {
      text: '',
      isObjective: false,
      isBundled: false,
      fullText: '',
      stem: '',
      correctAnswers: []
    };
  }
  if (typeof questionOrContent === 'string') {
    var plain = htmlToReadableTextFull(questionOrContent);
    return {
      text: plain,
      isObjective: false,
      isBundled: /\(\s*(?:i{1,4}|v|vi{0,3}|\d+)\s*\)|\[AnswerKey:/i.test(plain),
      fullText: plain,
      stem: plain,
      correctAnswers: []
    };
  }

  var q = questionOrContent;
  var qType = getQuestionTypeLower(q);
  var fullText = htmlToReadableTextFull(q.question_content || '');
  var answers = Array.isArray(q.answers_of_question) ? q.answers_of_question : [];
  var correctAnswers = extractCorrectAnswerObjects(q);
  var answerLines = [];
  for (var i = 0; i < answers.length; i++) {
    var a = answers[i] || {};
    var key = String(a.answer_option || String.fromCharCode(97 + i)).trim();
    var body = normalizeAnswerTextForRouting(a);
    if (!body) continue;
    var isCorrect = correctAnswers.indexOf(a) >= 0;
    answerLines.push(key + ': ' + body + (isCorrect ? ' [CORRECT]' : ''));
  }

  var stem = fullText;
  var marker = stem.search(/\b(?:a\)|b\)|c\)|d\)|\(i\)|\(ii\)|\(iii\)|\(iv\)|\(v\)|\[AnswerKey:)/i);
  if (marker > 80) stem = stem.substring(0, marker).trim();

  var bundled = /\(\s*(?:i{1,4}|v|vi{0,3}|\d+)\s*\)|\[AnswerKey:/i.test(fullText) || correctAnswers.length > 1;
  var lines = [];
  lines.push('Question label: ' + (questionLabel || q.question_label || ''));
  lines.push('Question type: ' + (q.question_type || 'Unknown'));
  if (qType === 'objective') {
    lines.push('Placement rule: use the stem and the correct option. Ignore distractor options that do not answer the stem.');
  } else {
    lines.push('Placement rule: use the full question text. Preserve all subparts and answer anchors when present.');
  }
  if (stem) lines.push('Stem:\n' + stem);
  if (fullText && fullText !== stem) lines.push('Full question:\n' + fullText);
  if (answerLines.length) lines.push('Options / answer keys:\n' + answerLines.join('\n'));
  if (correctAnswers.length) {
    lines.push('Correct answer anchor:\n' + correctAnswers.map(function(a) {
      var key = String(a.answer_option || '').trim();
      var body = normalizeAnswerTextForRouting(a);
      return (key ? key + ': ' : '') + body;
    }).join('\n'));
  }

  return {
    text: lines.join('\n\n').substring(0, 7000),
    isObjective: qType === 'objective',
    isBundled: bundled,
    fullText: fullText,
    stem: stem,
    correctAnswers: correctAnswers.map(function(a) { return normalizeAnswerTextForRouting(a); }).filter(Boolean)
  };
}

function routingTextForPrompt(questionOrContent, questionLabel, maxLength) {
  var info = buildRoutingInput(questionOrContent, questionLabel);
  var txt = String(info.text || '').trim();
  if (maxLength && txt.length > maxLength) {
    txt = txt.substring(0, maxLength - 3).trim() + '...';
  }
  return txt;
}

function openAIChatText(prompt, maxTokens) {
  const apiKey = getOpenAIKey();
  if (!apiKey) throw new Error('Set OPENAI_API_KEY in Script Properties or Code.gs.');
  const response = UrlFetchApp.fetch('https://api.openai.com/v1/chat/completions', {
    method: 'post',
    contentType: 'application/json',
    headers: { 'Authorization': 'Bearer ' + apiKey },
    payload: JSON.stringify({
      model: OPENAI_MODEL,
      messages: [{ role: 'user', content: prompt }],
      max_completion_tokens: maxTokens || 120
    }),
    muteHttpExceptions: true
  });
  const code = response.getResponseCode();
  const body = safeJsonParse(response.getContentText(), {});
  if (code !== 200) {
    const msg = body && body.error && body.error.message ? body.error.message : ('HTTP ' + code);
    throw new Error('OpenAI API: ' + msg);
  }
  return (body.choices && body.choices[0] && body.choices[0].message && body.choices[0].message.content || '').trim();
}

function normalizeTopicDisplayForComment(topicIdOrRoutingKey) {
  return humanizeTopicIdForPrompt(String(topicIdOrRoutingKey || '').replace(/^.*::/, ''));
}

function summarizeTopicForPrompt(topic) {
  const conceptNames = (topic.concepts || []).map(shortConceptDisplayName).filter(Boolean);
  const shown = conceptNames.slice(0, 12);
  let suffix = '';
  if (conceptNames.length > shown.length) {
    suffix = '; +' + (conceptNames.length - shown.length) + ' more concepts';
  }
  return shown.join('; ') + suffix;
}

function buildConflictComment(details) {
  if (!details || !details.kind) return '';
  const out = [];
  if (details.kind === 'cross_topic') {
    out.push('Cross-topic conflict');
    if (details.questionLabel) out.push('Question: ' + details.questionLabel);
    if (details.matchedTopics && details.matchedTopics.length) out.push('Matched topics: ' + details.matchedTopics.join('; '));
    if (details.selectedTopic) out.push('Chosen topic: ' + details.selectedTopic);
    out.push('Rule applied: later topic in chapter');
    if (details.selectedConcept) out.push('Written to: ' + details.selectedConcept);
  } else if (details.kind === 'within_topic_overlap') {
    out.push('Within-topic ambiguity');
    if (details.questionLabel) out.push('Question: ' + details.questionLabel);
    if (details.topic) out.push('Topic: ' + details.topic);
    if (details.matchedConcepts && details.matchedConcepts.length) out.push('Matched concepts: ' + details.matchedConcepts.join('; '));
    if (details.selectedConcept) out.push('Written to: ' + details.selectedConcept);
  } else if (details.kind === 'fallback_global') {
    out.push('Fallback placement');
    if (details.questionLabel) out.push('Question: ' + details.questionLabel);
    out.push('Reason: topic match was unclear');
    if (details.selectedConcept) out.push('Written to: ' + details.selectedConcept);
  } else if (details.kind === 'capacity_append') {
    out.push('Append review');
    out.push('Reason: no empty row available in this concept and difficulty bucket');
    out.push('Action: appended to an existing row for manual review');
  } else if (details.kind === 'qa_flag') {
    out.push('Review flag');
    if (details.questionLabel) out.push('Question: ' + details.questionLabel);
    if (details.expectedTopic) out.push('Current topic: ' + details.expectedTopic);
    if (details.currentConcept) out.push('Current concept: ' + details.currentConcept);
    if (details.reason) out.push('Reason: ' + details.reason);
  }
  return out.join('\n');
}

function mergeReadableComments(parts) {
  const seen = {};
  const blocks = [];
  (parts || []).forEach(function(part) {
    const cleanBlock = String(part || '')
      .replace(/\r/g, '')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
    if (!cleanBlock) return;
    const key = cleanBlock.toLowerCase();
    if (!seen[key]) {
      seen[key] = true;
      blocks.push(cleanBlock);
    }
  });
  return blocks.join('\n\n');
}

function appendTextBlock(existing, addition) {
  const a = String(existing || '').trim();
  const b = String(addition || '').trim();
  if (!a) return b;
  if (!b) return a;
  return a + '\n' + b;
}

function firstQuestionLabelFromText(text) {
  return String(text || '').split(/[\n,]+/).map(function(x) { return x.trim(); }).filter(Boolean)[0] || '';
}

function chooseBestConceptIndexWithinTopic(questionOrContent, concepts, questionLabel) {
  if (!concepts || concepts.length === 0) return -1;
  if (concepts.length === 1) return 0;
  var contentText = routingTextForPrompt(questionOrContent, questionLabel, 4200);
  var conceptsList = concepts.map(function(c, i) {
    return (i + 1) + '. ' + shortConceptDisplayName(c);
  }).join('\n');
  var prompt = 'Question routing packet:\n' + contentText + '\n\nSelect the ONE best-fit concept from the list below.\nRules:\n- Choose the concept that is most directly being assessed.\n- For Objective / MCQ questions, base the choice on the stem and the correct option, not on distractor options.\n- Do not prefer a later concept unless the question genuinely targets it.\n- Reply with ONLY one number.\n\nConcepts:\n' + conceptsList;
  try {
    var text = openAIChatText(prompt, 25);
    var num = parseInt(String(text).replace(/\D/g, ''), 10);
    if (num >= 1 && num <= concepts.length) return num - 1;
  } catch (e) {}
  return 0;
}

function buildTopicPlacementPack(conceptToRows) {
  const uniqueConcepts = Object.keys(conceptToRows || {});
  if (!uniqueConcepts.length) {
    return { uniqueConcepts: [], culminationByBand: {}, bandOrderIndex: {}, topicPack: { topicsOrdered: [], topicOrderIndex: {} } };
  }
  return {
    uniqueConcepts: uniqueConcepts,
    culminationByBand: buildCulminationByTopic(uniqueConcepts, conceptToRows),
    bandOrderIndex: buildTopicOrderIndex(uniqueConcepts, conceptToRows),
    topicPack: extractTopicsAndConcepts(uniqueConcepts, conceptToRows)
  };
}

function buildRoutingClusterKey(concept, groupType) {
  return String(concept || '') + '|||' + String(groupType || '');
}

function clusterQuestionItemsByRouting(items) {
  const buckets = {};
  (items || []).forEach(function(item) {
    const key = buildRoutingClusterKey(item.concept, item.groupType);
    if (!buckets[key]) buckets[key] = [];
    buckets[key].push(item);
  });
  return buckets;
}

function compileClusterPayloads(rawSlots) {
  resetGlobalGistUniquifier();
  const payload = {};
  for (const key in rawSlots) {
    const items = rawSlots[key] || [];
    let clusters = [];
    if (items.length <= 1) {
      clusters = items.map(function(i) { return [i.label]; });
    } else {
      try {
        clusters = clusterVariants(items);
      } catch (e) {
        clusters = items.map(function(i) { return [i.label]; });
      }
    }
    payload[key] = (clusters || []).map(function(clusterLabels) {
      const clusterItems = clusterLabels.map(function(lbl) {
        for (var i = 0; i < items.length; i++) {
          if (items[i].label === lbl) return items[i];
        }
        return null;
      }).filter(Boolean);
      const gistBlock = uniquifyGistBlockGlobal(clusterItems.map(function(it) {
        return (it.label || '').trim() + ' - ' + questionContentGist(it.content || '', 150);
      }).join('\n'));
      const mergedComments = mergeReadableComments(clusterItems.map(function(it) { return it.comment || ''; }));
      const yellow = clusterItems.some(function(it) { return it.highlightYellow === true; });
      return {
        labelsText: clusterItems.map(function(it) { return (it.label || '').trim(); }).join('\n'),
        gistBlock: gistBlock,
        items: clusterItems,
        comments: mergedComments,
        highlightYellow: yellow
      };
    });
  }
  return payload;
}

function matchClusterToExistingRow(clusterGistBlock, rows) {
  const apiKey = getOpenAIKey();
  if (!apiKey || !rows || rows.length === 0) return -1;
  const block = String(clusterGistBlock || '').substring(0, 2200);
  const list = rows.map(function(r, i) {
    const desc = String(r.group_description || '').trim();
    const gist = String(r.assessmentGist || '').trim();
    return (i + 1) + '. Group description: ' + (desc || 'NA') + '\nExisting assessment gists:\n' + gist.substring(0, 1200);
  }).join('\n\n');
  const prompt = 'New clustered question block:\n' + block + '\n\nSelect the existing row that matches the SAME questioning element. Choose 0 if none match closely enough. Reply with ONLY one number.\n\nRows:\n' + list;
  try {
    const text = openAIChatText(prompt, 20);
    const num = parseInt(String(text).replace(/\D/g, ''), 10);
    if (num >= 1 && num <= rows.length) return num - 1;
  } catch (e) {}
  return -1;
}


/**
 * Parse bracket slug: segment before _PL_ is coarse group id; everything after _PL_ is the **topic id**
 * (underscore form), e.g. Nervous_Tissue_and_Neuronal_Structure for “Nervous tissue and neuronal structure”.
 * Example: (09CBBI_TISSUES_PL_Nervous_Tissue_and_Neuronal_Structure) → topicGroupId 09CBBI_TISSUES,
 * topicSlug = Nervous_Tissue_and_Neuronal_Structure
 */
function parseTopicGroupFromConcept(concept) {
  const m = String(concept || '').match(/\(([^)]+)\)\s*$/);
  if (!m) return { topicGroupId: '', topicSlug: '', hasPl: false };
  const inner = m[1];
  const plIdx = inner.indexOf('_PL_');
  if (plIdx < 0) return { topicGroupId: inner, topicSlug: '', hasPl: false };
  return {
    topicGroupId: inner.substring(0, plIdx),
    topicSlug: inner.substring(plIdx + 4),
    hasPl: true
  };
}

/**
 * Routing band for culmination: coarse group id + full topic id (entire segment after _PL_).
 * Example: 09CBBI_TISSUES::Nervous_Tissue_and_Neuronal_Structure
 */
function routingTopicKeyFromConcept(concept) {
  const s = String(concept || '').trim();
  const p = parseTopicGroupFromConcept(concept);
  if (!p.topicGroupId) return s || '';
  if (!p.hasPl || !p.topicSlug || !String(p.topicSlug).trim()) return p.topicGroupId;
  const topicId = String(p.topicSlug).trim();
  return p.topicGroupId + '::' + topicId;
}

function humanRoutingTopicLabel(routingKey) {
  const s = String(routingKey || '');
  const i = s.indexOf('::');
  if (i < 0) return s;
  return s.substring(0, i) + ' — ' + s.substring(i + 2);
}

function shortConceptDisplayName(concept) {
  const s = (concept || '').trim();
  const paren = s.indexOf('(');
  if (paren > 0) return s.substring(0, paren).trim().substring(0, 100) || s.substring(0, 100);
  return s.substring(0, 100);
}

/**
 * Prevent writing group_type / group_description into the Concept column when headers resolve
 * to the same index (would replace concept text with "Basic" etc.).
 */
function assertUniqueColumnIndices(ctx, cols) {
  const pairs = [
    ['Assessment Label', cols.assessmentCol],
    ['Assessment Gist', cols.assessmentGistCol],
    ['Concept name', cols.conceptCol],
    ['group_type', cols.groupTypeCol],
    ['group_description', cols.groupDescCol],
    ['Comments', cols.commentsCol]
  ];
  for (var i = 0; i < pairs.length; i++) {
    if (pairs[i][1] < 0) continue;
    for (var j = i + 1; j < pairs.length; j++) {
      if (pairs[j][1] < 0) continue;
      if (pairs[i][1] === pairs[j][1]) {
        throw new Error(ctx + ': Column header conflict — "' + pairs[i][0] + '" and "' + pairs[j][0] + '" map to the same column (column ' + (pairs[i][1] + 1) + '). Use a unique header per column.');
      }
    }
  }
}

/**
 * Full used range (last row × last column). Safer than getDataRange() when trailing columns are sparse.
 */
function getSheetValuesFull(sheet) {
  const lr = sheet.getLastRow();
  const lc = sheet.getLastColumn();
  if (lr < 1 || lc < 1) return [[]];
  return sheet.getRange(1, 1, lr, lc).getValues();
}

/**
 * Per-row concept for GPT/topic logic: prefer "Concept name" when non-empty; else "group_name"
 * (some exports leave Concept name blank but fill group_name).
 */
function resolveRowConcept(rowArr, conceptCol, groupNameCol) {
  const fromConcept = (rowArr[conceptCol] || '').toString().trim();
  if (fromConcept) return fromConcept;
  if (groupNameCol >= 0) return (rowArr[groupNameCol] || '').toString().trim();
  return '';
}

/** Per sheet row (1-based), raw Concept name cell text — backup before column inserts / clear. */
function snapshotConceptNameCells(data, conceptCol) {
  const snap = {};
  if (!data || conceptCol < 0) return snap;
  for (let r = 1; r < data.length; r++) {
    const v = data[r][conceptCol];
    snap[r + 1] = v != null && String(v).trim() !== '' ? String(v) : '';
  }
  return snap;
}

function restoreConceptNameCellsIfStripped(sheet, data, conceptCol, snapshot) {
  if (!snapshot || conceptCol < 0 || !data) return;
  for (let r = 2; r < data.length + 1; r++) {
    const saved = snapshot[r] || '';
    if (!saved.trim()) continue;
    const cur = (data[r - 1][conceptCol] || '').toString().trim();
    if (cur) continue;
    sheet.getRange(r, conceptCol + 1).setValue(saved);
  }
}

/**
 * conceptToRows: concept string → [{ row, ... }]
 * Last concept per routing band (coarse id + slug prefix) by sheet row is that band's culmination.
 */
function buildCulminationByTopic(uniqueConcepts, conceptToRows) {
  const byBand = {};
  uniqueConcepts.forEach(function(c) {
    const rk = routingTopicKeyFromConcept(c);
    if (!rk) return;
    if (!byBand[rk]) byBand[rk] = [];
    const rows = conceptToRows[c] || [];
    const minRow = rows.length ? Math.min.apply(null, rows.map(function(r) { return r.row; })) : 999999;
    byBand[rk].push({ concept: c, minRow: minRow });
  });
  const culminationByTopic = {};
  for (const rk in byBand) {
    byBand[rk].sort(function(a, b) { return a.minRow - b.minRow; });
    culminationByTopic[rk] = byBand[rk][byBand[rk].length - 1].concept;
  }
  return culminationByTopic;
}

function buildTopicOrderIndex(uniqueConcepts, conceptToRows) {
  const bandMinRow = {};
  uniqueConcepts.forEach(function(c) {
    const rk = routingTopicKeyFromConcept(c);
    if (!rk) return;
    const rows = conceptToRows[c] || [];
    const minR = rows.length ? Math.min.apply(null, rows.map(function(x) { return x.row; })) : 999999;
    if (bandMinRow[rk] == null || minR < bandMinRow[rk]) bandMinRow[rk] = minR;
  });
  const sorted = Object.keys(bandMinRow).sort(function(a, b) { return bandMinRow[a] - bandMinRow[b]; });
  const order = {};
  sorted.forEach(function(t, i) { order[t] = i; });
  return order;
}

/**
 * Step 1: Topics from sheet = distinct topic ids (full segment after _PL_) with their concepts and per-topic culmination row.
 */
function extractTopicsAndConcepts(uniqueConcepts, conceptToRows) {
  const byTopicId = {};
  uniqueConcepts.forEach(function(c) {
    const p = parseTopicGroupFromConcept(c);
    let tid;
    if (p.hasPl && p.topicSlug && String(p.topicSlug).trim()) {
      tid = String(p.topicSlug).trim();
    } else if (p.topicGroupId) {
      tid = '__group__' + p.topicGroupId;
    } else {
      tid = '__row__' + String((conceptToRows[c] && conceptToRows[c][0] && conceptToRows[c][0].row) || 0);
    }
    if (!byTopicId[tid]) {
      byTopicId[tid] = { topicId: tid, concepts: [], minRow: 999999, routingKey: routingTopicKeyFromConcept(c) };
    }
    const rows = conceptToRows[c] || [];
    const mr = rows.length ? Math.min.apply(null, rows.map(function(r) { return r.row; })) : 999999;
    if (mr < byTopicId[tid].minRow) byTopicId[tid].minRow = mr;
    if (byTopicId[tid].concepts.indexOf(c) < 0) byTopicId[tid].concepts.push(c);
  });
  const topicsOrdered = Object.keys(byTopicId).map(function(k) { return byTopicId[k]; });
  topicsOrdered.sort(function(a, b) { return a.minRow - b.minRow; });
  topicsOrdered.forEach(function(t) {
    t.concepts.sort(function(ca, cb) {
      const ra = conceptToRows[ca] && conceptToRows[ca].length ? Math.min.apply(null, conceptToRows[ca].map(function(x) { return x.row; })) : 999999;
      const rb = conceptToRows[cb] && conceptToRows[cb].length ? Math.min.apply(null, conceptToRows[cb].map(function(x) { return x.row; })) : 999999;
      return ra - rb;
    });
    t.culminationConcept = t.concepts[t.concepts.length - 1];
  });
  const topicOrderIndex = {};
  topicsOrdered.forEach(function(t, i) { topicOrderIndex[t.topicId] = i; });
  return { topicsOrdered: topicsOrdered, topicOrderIndex: topicOrderIndex };
}

function humanizeTopicIdForPrompt(topicId) {
  const s = String(topicId || '');
  if (s.indexOf('__') === 0) return s;
  return s.replace(/_/g, ' ');
}

/**
 * Step 2a: Match question to topic(s) only (1-based indices into topicsOrdered).
 */
function matchQuestionToTopicIndices(questionOrContent, topicsOrdered, questionLabel) {
  if (!topicsOrdered || topicsOrdered.length === 0) return [];
  if (topicsOrdered.length === 1) return [0];

  var contentText = routingTextForPrompt(questionOrContent, questionLabel, 5000);
  var list = topicsOrdered.map(function(t, i) {
    return (i + 1) + '. ' + humanizeTopicIdForPrompt(t.topicId) + ' — concepts: ' + summarizeTopicForPrompt(t);
  }).join('\n');
  var prompt = 'Question routing packet:\n' + contentText + '\n\nIdentify all TOPICS that reasonably match this question.\nRules:\n- For Objective / MCQ questions, use the stem and the correct answer anchor. Ignore distractor options.\n- Return one number if exactly one topic clearly fits.\n- Return multiple numbers only for genuine cross-topic overlap after reading the full question.\n- Return 0 if the match is unclear.\n- Do not force a match just because one option word overlaps.\n\nTopics:\n' + list + '\n\nReply with ONLY numbers and commas.';
  try {
    var text = openAIChatText(prompt, 60);
    var nums = String(text).match(/\d+/g);
    if (!nums || !nums.length) return [];
    var seen = {};
    var out = [];
    nums.forEach(function(raw) {
      var num = parseInt(raw, 10);
      if (num >= 1 && num <= topicsOrdered.length) {
        var idx = num - 1;
        if (!seen[idx]) {
          seen[idx] = true;
          out.push(idx);
        }
      }
    });
    return out;
  } catch (e) {
    return [];
  }
}

/**
 * Topic-first pipeline: assign topic(s) → pick later topic if ambiguous → match concepts within that topic only.
 * highlightYellow: true only when multiple topics matched (place on later topic’s culmination when multi-concept there).
 */
function placeQuestionWithTopicFirstPipeline(label, questionOrContent, topicPack, uniqueConcepts, culminationByBand, bandOrderIndex) {
  var topicsOrdered = topicPack.topicsOrdered || [];
  var tidOrder = topicPack.topicOrderIndex || {};

  var topicIdxsRaw = matchQuestionToTopicIndices(questionOrContent, topicsOrdered, label);
  var topicIdxs = [];
  var seenT = {};
  (topicIdxsRaw || []).forEach(function(i) {
    if (i >= 0 && i < topicsOrdered.length && !seenT[i]) {
      seenT[i] = true;
      topicIdxs.push(i);
    }
  });

  if (!topicIdxs.length) {
    var fallback = fallbackPlacementFromGlobalConcepts(label, questionOrContent, uniqueConcepts, culminationByBand, bandOrderIndex);
    return {
      concept: fallback.concept,
      comment: buildConflictComment({
        kind: 'fallback_global',
        questionLabel: label,
        selectedConcept: shortConceptDisplayName(fallback.concept)
      }),
      highlightYellow: fallback.highlightYellow === true
    };
  }

  var matchedTopics = topicIdxs.map(function(i) { return topicsOrdered[i]; });
  var crossTopic = matchedTopics.length > 1;
  var laterTid = pickLaterTopicAmongMatched(matchedTopics.map(function(t) { return t.topicId; }), tidOrder);

  var chosenTopic = matchedTopics[0];
  for (var j = 0; j < matchedTopics.length; j++) {
    if (matchedTopics[j].topicId === laterTid) {
      chosenTopic = matchedTopics[j];
      break;
    }
  }

  var conceptIdxs = matchQuestionToConceptIndices(questionOrContent, chosenTopic.concepts || [], label);
  var matchedConcepts = (conceptIdxs || []).map(function(i) { return chosenTopic.concepts[i]; }).filter(Boolean);

  if (crossTopic) {
    var targetConcept = chosenTopic.culminationConcept || chosenTopic.concepts[chosenTopic.concepts.length - 1] || matchedConcepts[matchedConcepts.length - 1] || '';
    return {
      concept: targetConcept,
      comment: buildConflictComment({
        kind: 'cross_topic',
        questionLabel: label,
        matchedTopics: matchedTopics.map(function(t) { return humanizeTopicIdForPrompt(t.topicId); }),
        selectedTopic: humanizeTopicIdForPrompt(chosenTopic.topicId),
        selectedConcept: shortConceptDisplayName(targetConcept)
      }),
      highlightYellow: true
    };
  }

  if (!matchedConcepts.length) {
    var bestFallbackIdx = chooseBestConceptIndexWithinTopic(questionOrContent, chosenTopic.concepts || [], label);
    var bestFallback = (chosenTopic.concepts || [])[bestFallbackIdx >= 0 ? bestFallbackIdx : 0] || '';
    return {
      concept: bestFallback,
      comment: buildConflictComment({
        kind: 'fallback_global',
        questionLabel: label,
        selectedConcept: shortConceptDisplayName(bestFallback)
      }),
      highlightYellow: false
    };
  }

  if (matchedConcepts.length <= 1) {
    return { concept: matchedConcepts[0] || '', comment: '', highlightYellow: false };
  }

  var bestIdx = chooseBestConceptIndexWithinTopic(questionOrContent, matchedConcepts, label);
  var bestConcept = matchedConcepts[(bestIdx >= 0 ? bestIdx : 0)] || matchedConcepts[0];
  return {
    concept: bestConcept,
    comment: buildConflictComment({
      kind: 'within_topic_overlap',
      questionLabel: label,
      topic: humanizeTopicIdForPrompt(chosenTopic.topicId),
      matchedConcepts: matchedConcepts.map(shortConceptDisplayName),
      selectedConcept: shortConceptDisplayName(bestConcept)
    }),
    highlightYellow: false
  };
}

function fallbackPlacementFromGlobalConcepts(label, questionOrContent, uniqueConcepts, culminationByBand, bandOrderIndex) {
  var indices = matchQuestionToConceptIndices(questionOrContent, uniqueConcepts, label);
  var placement = resolvePlacementWithCulmination(label, indices, uniqueConcepts, culminationByBand, bandOrderIndex);
  return {
    concept: placement.concept,
    comment: placement.comment || '',
    highlightYellow: placement.isCulmination === true
  };
}

/**
 * Step 5: Rewrite group_description from the final Assessment Gist block (non-redundant, comprehensive).
 */
function rewriteGroupDescriptionFromGists(assessmentGistBlock, conceptContext) {
  if (!assessmentGistBlock || !String(assessmentGistBlock).trim()) {
    return generateGroupDescription([]);
  }
  const gist = String(assessmentGistBlock).trim().substring(0, 4500);
  const ctx = String(conceptContext || '').substring(0, 500);
  const prompt = 'Assessment gist lines for one cluster (each line = label - gist):\n\n' + gist + '\n\nConcept context: ' + ctx + '\n\nWrite ONE comprehensive, non-redundant group description. Capture the shared questioning element and theme. Do not repeat the same wording from the gists. Keep it crisp, reviewer-friendly, and specific. Reply with ONLY one line.';
  try {
    let desc = openAIChatText(prompt, 120);
    desc = desc.replace(/^["']|["']$/g, '').trim();
    if (desc) desc = desc.charAt(0).toUpperCase() + desc.slice(1);
    return desc || generateGroupDescription([]);
  } catch (e) {
    return generateGroupDescription([]);
  }
}

/**
 * Returns true if label / gist / concept / topic look inconsistent (light red highlight).
 */
function auditWrittenRowMismatch(questionLabelsText, assessmentGistText, conceptNameCell, topicNameHuman) {
  const prompt = 'Review one spreadsheet row for internal alignment.\n\nQuestion label(s):\n' + String(questionLabelsText || '').substring(0, 2000) + '\n\nAssessment gist:\n' + String(assessmentGistText || '').substring(0, 2500) + '\n\nConcept name:\n' + String(conceptNameCell || '').substring(0, 1000) + '\n\nExpected topic:\n' + String(topicNameHuman || '').substring(0, 400) + '\n\nReturn ONLY compact JSON in this exact shape:\n{"flag":false,"reason":""}\n\nRules:\n- Set flag true only for a clear contradiction, not for mild uncertainty.\n- Valid reasons: topic-language mismatch, concept-language mismatch, multi-topic mixed question, cluster too broad, unrelated gist.\n- If the row is broadly acceptable, return flag false.';
  try {
    const text = openAIChatText(prompt, 60);
    const match = String(text).match(/\{[\s\S]*\}/);
    const obj = match ? safeJsonParse(match[0], null) : null;
    if (obj && obj.flag === true) {
      return {
        flag: true,
        reason: String(obj.reason || '').trim() || 'topic-language mismatch'
      };
    }
  } catch (e) {}
  return { flag: false, reason: '' };
}

/**
 * Natural compare for topic group ids (e.g. ..._T2 vs ..._T10: 10 wins).
 * Digit runs compare numerically; other runs compare as strings.
 */
function naturalCompareTopicIds(a, b) {
  a = String(a || '');
  b = String(b || '');
  if (a === b) return 0;
  const re = /(\d+|\D+)/g;
  const pa = a.match(re) || [];
  const pb = b.match(re) || [];
  const n = Math.max(pa.length, pb.length);
  for (let i = 0; i < n; i++) {
    if (i >= pa.length) return -1;
    if (i >= pb.length) return 1;
    const da = /^\d+$/.test(pa[i]);
    const db = /^\d+$/.test(pb[i]);
    if (da && db) {
      const diff = parseInt(pa[i], 10) - parseInt(pb[i], 10);
      if (diff !== 0) return diff;
    } else {
      if (pa[i] < pb[i]) return -1;
      if (pa[i] > pb[i]) return 1;
    }
  }
  return 0;
}

/**
 * Among matched topics in a cross-topic conflict, pick the later topic in **chapter/sheet order**
 * (topicOrderIndex: higher = further down the sheet / later in the chapter), then tie-break by id string.
 * This avoids picking an unrelated “alphabetically last” topicGroupId when only e.g. topic 2 and 3 matched.
 */
function pickLaterTopicAmongMatched(uniqTopics, topicOrderIndex) {
  if (!uniqTopics || uniqTopics.length === 0) return '';
  if (uniqTopics.length === 1) return uniqTopics[0];
  const sorted = uniqTopics.slice().sort(function(a, b) {
    const oa = topicOrderIndex[a] != null ? topicOrderIndex[a] : -1;
    const ob = topicOrderIndex[b] != null ? topicOrderIndex[b] : -1;
    if (oa !== ob) return oa - ob;
    return naturalCompareTopicIds(a, b);
  });
  return sorted[sorted.length - 1];
}

/**
 * Flatten Tag New conceptToRows (keyed by concept|||groupType) to concept → rows for culmination.
 */
function flattenConceptToRowsByConcept(conceptToRowsByKey) {
  const out = {};
  Object.keys(conceptToRowsByKey).forEach(function(k) {
    const c = k.split('|||')[0];
    const val = conceptToRowsByKey[k];
    const rows = (val.empty && val.filled) ? (val.empty || []).concat(val.filled || []) : (Array.isArray(val) ? val : []);
    if (!out[c]) out[c] = [];
    rows.forEach(function(r) { out[c].push(r); });
  });
  return out;
}

var globalGistBodySeen = {};

function resetGlobalGistUniquifier() {
  globalGistBodySeen = {};
}

/** Ensure gist text after " - " is unique across the whole run (reviewers scan without CMS ambiguity). */
function uniquifyGistLineGlobal(line) {
  const idx = line.indexOf(' - ');
  if (idx < 0) return line;
  const lbl = line.substring(0, idx).trim();
  let body = line.substring(idx + 3).trim();
  let key = body;
  let n = 0;
  while (globalGistBodySeen[key]) {
    n++;
    key = body + ' · ' + lbl.substring(0, 40) + (n > 1 ? ' ' + n : '');
  }
  globalGistBodySeen[key] = true;
  return lbl + ' - ' + key;
}

function uniquifyGistBlockGlobal(block) {
  if (!block) return '';
  return block.split(/\n/).filter(function(l) { return l.trim(); }).map(function(l) {
    return uniquifyGistLineGlobal(l);
  }).join('\n');
}

/**
 * Ensure a "Comments" column exists. Prefer placing it immediately after "Assessment Gist"
 * so reviewers see it next to updated gists. Falls back to sheet end if Gist is missing.
 */
function ensureCommentsColumn(sheet, assessmentGistCol) {
  const data = getSheetValuesFull(sheet);
  const headers = data[0];
  let idx = findColumnIndex(headers, 'Comments');
  if (idx >= 0) return idx;

  const gistCol = (assessmentGistCol != null && assessmentGistCol >= 0)
    ? assessmentGistCol
    : findColumnIndex(headers, 'Assessment Gist');

  const ss = sheet.getParent();

  if (gistCol >= 0) {
    sheet.insertColumnAfter(gistCol + 1);
    sheet.getRange(1, gistCol + 2).setValue('Comments').setFontWeight('bold');
    try {
      ss.toast('Added "Comments" after "Assessment Gist".', 'Assessment Tagging', 8);
    } catch (e) {}
    SpreadsheetApp.flush();
    return gistCol + 1;
  }

  const lastCol1b = Math.max(1, sheet.getLastColumn());
  sheet.insertColumnAfter(lastCol1b);
  sheet.getRange(1, lastCol1b + 1).setValue('Comments').setFontWeight('bold');
  try {
    ss.toast('Added "Comments" at the end of the sheet.', 'Assessment Tagging', 8);
  } catch (e) {}
  SpreadsheetApp.flush();
  return lastCol1b;
}

/**
 * Multi-concept match: returns 0-based indices. Falls back to single matchQuestionToConcept on parse failure.
 */
function matchQuestionToConceptIndices(questionOrContent, concepts, questionLabel) {
  if (!concepts || concepts.length === 0) return [];
  var contentText = routingTextForPrompt(questionOrContent, questionLabel, 5000);
  var conceptsList = concepts.map(function(c, i) {
    return (i + 1) + '. ' + shortConceptDisplayName(c);
  }).join('\n');
  var prompt = 'Question routing packet:\n' + contentText + '\n\nIdentify all concepts that reasonably match this question.\nRules:\n- For Objective / MCQ questions, use the stem and the correct option. Ignore distractor options.\n- Return one number if only one concept clearly fits.\n- Return multiple numbers only when the question genuinely overlaps multiple concepts after reading the full question.\n- Do not guess. If uncertain, reply 0.\n\nConcepts:\n' + conceptsList + '\n\nReply with ONLY numbers and commas.';
  try {
    var text = openAIChatText(prompt, 55);
    var nums = String(text).match(/\d+/g);
    if (!nums || !nums.length) return [];
    var seen = {};
    var indices = [];
    nums.forEach(function(raw) {
      var num = parseInt(raw, 10);
      if (num >= 1 && num <= concepts.length) {
        var idx = num - 1;
        if (!seen[idx]) {
          seen[idx] = true;
          indices.push(idx);
        }
      }
    });
    return indices;
  } catch (e) {
    return [];
  }
}

/**
 * Resolve GPT indices to target concept, culmination placement, and reviewer comment.
 */
function resolvePlacementWithCulmination(label, indices, uniqueConcepts, culminationByTopic, topicOrderIndex) {
  const concepts = (indices || []).map(function(i) { return uniqueConcepts[i]; }).filter(Boolean);
  if (concepts.length === 0) {
    return { concept: uniqueConcepts[0] || '', comment: '', isCulmination: false };
  }
  if (concepts.length === 1) {
    return { concept: concepts[0], comment: '', isCulmination: false };
  }

  const routingKeys = concepts.map(function(c) { return routingTopicKeyFromConcept(c); });
  const uniqBands = [];
  routingKeys.forEach(function(rk) {
    if (rk && uniqBands.indexOf(rk) < 0) uniqBands.push(rk);
  });

  if (uniqBands.length === 1) {
    const bestConcept = concepts[concepts.length - 1] || concepts[0];
    return {
      concept: bestConcept,
      comment: buildConflictComment({
        kind: 'within_topic_overlap',
        questionLabel: label,
        topic: normalizeTopicDisplayForComment(uniqBands[0]),
        matchedConcepts: concepts.map(shortConceptDisplayName),
        selectedConcept: shortConceptDisplayName(bestConcept)
      }),
      isCulmination: false
    };
  }

  const laterBand = pickLaterTopicAmongMatched(uniqBands, topicOrderIndex);
  const culm = culminationByTopic[laterBand] || concepts[concepts.length - 1];
  return {
    concept: culm,
    comment: buildConflictComment({
      kind: 'cross_topic',
      questionLabel: label,
      matchedTopics: uniqBands.map(normalizeTopicDisplayForComment),
      selectedTopic: normalizeTopicDisplayForComment(laterBand),
      selectedConcept: shortConceptDisplayName(culm)
    }),
    isCulmination: true
  };
}

/**
 * Call OpenAI API for concept matching. Returns concept index (0-based) or -1.
 */
/**
 * Use GPT to cluster question labels that are the same question with different wording.
 * Returns array of arrays, e.g. [[label1, label2], [label3]]
 */
function clusterVariants(items) {
  if (!items || items.length === 0) return [];
  if (items.length === 1) return [[items[0].label]];
  const labels = items.map(function(i) { return i.label; });
  const conceptContext = shortConceptDisplayName((items[0] && items[0].concept) || '');
  const difficultyContext = (items[0] && items[0].groupType) || '';
  const list = items.map(function(it, i) {
    return (i + 1) + '. ' + it.label + ' | gist: ' + questionContentGist(it.content || '', 160);
  }).join('\n');
  const prompt = 'Cluster only the questions that ask the SAME questioning element with different wording.\nExamples of SAME: "Define mitochondria" and "What is mitochondria?"\nExamples of DIFFERENT: "Functions of mitochondria" and "Structure of mitochondria"\n\nContext:\nConcept: ' + conceptContext + '\nDifficulty bucket: ' + difficultyContext + '\n\nReturn ONLY a JSON array of arrays of item numbers.\nRules:\n- Every item must appear exactly once.\n- Do not merge just because the broad topic is similar.\n- If one item is broader, multi-part, or mixed, keep it separate.\n\nItems:\n' + list;
  try {
    const text = openAIChatText(prompt, 700);
    const match = String(text).match(/\[[\s\S]*\]/);
    if (!match) throw new Error('No JSON array returned');
    const arr = JSON.parse(match[0]);
    const seen = {};
    const result = [];
    (Array.isArray(arr) ? arr : []).forEach(function(group) {
      const rawGroup = Array.isArray(group) ? group : [group];
      const labelsInGroup = [];
      rawGroup.forEach(function(idx) {
        const n = parseInt(idx, 10);
        if (n >= 1 && n <= labels.length) {
          const lbl = labels[n - 1];
          if (!seen[lbl]) {
            seen[lbl] = true;
            labelsInGroup.push(lbl);
          }
        }
      });
      if (labelsInGroup.length) result.push(labelsInGroup);
    });
    labels.forEach(function(lbl) {
      if (!seen[lbl]) result.push([lbl]);
    });
    return result.length ? result : items.map(function(i) { return [i.label]; });
  } catch (e) {
    return items.map(function(i) { return [i.label]; });
  }
}

/**
 * Match question to a group description. Returns index (0-based) or -1 if no match.
 */
function matchQuestionToGroupDescription(questionContent, descriptions) {
  if (!descriptions || descriptions.length === 0) return -1;
  const contentText = questionContentGist(questionContent, 300);
  const list = descriptions.map(function(d, i) { return (i + 1) + '. ' + (d.desc || d.group_description || ''); }).join('\n');
  const prompt = 'Given this question:\n\n"' + contentText + '"\n\nWhich of these group descriptions matches the SAME questioning element? Reply with ONLY one number, or 0 if none match.\n\nDescriptions:\n' + list;
  try {
    const text = openAIChatText(prompt, 10);
    const num = parseInt(String(text).replace(/\D/g, ''), 10);
    if (num >= 1 && num <= descriptions.length) return num - 1;
  } catch (e) {}
  return -1;
}

/**
 * Generate a group description in format: "[Questioning element] on [Topic]"
 * e.g. "Direct Questioning on the Factors affecting Historical Significance of Begum Hazrat Mahal"
 */
function generateGroupDescription(questionContents) {
  const apiKey = getOpenAIKey();
  if (!apiKey) return 'Similar questions with different variants.';

  const contents = (questionContents || []).filter(Boolean).map(function(c) { return questionContentGist(c, 200); });
  if (contents.length === 0) return 'Similar questions with different variants.';

  const text = contents.slice(0, 5).join('\n---\n');
  const prompt = 'These questions are in the same group. Create a group description in this EXACT format:\n\n"[Questioning element] on [Topic/Theme]"\n\nRules:\n- Questioning element: Identify HOW the questions ask (e.g. Direct Questioning, Definition-based, Cause and Effect, Comparison, Analysis, Recall, Application, Evaluation). Use the most accurate one.\n- Topic/Theme: The specific subject matter (e.g. "Factors affecting Historical Significance of Begum Hazrat Mahal", "Structure of Mitochondria").\n- Do NOT use phrases like "The questions cover" or "These questions are about". Start directly with the questioning element.\n- Reply with ONLY the description, nothing else.\n\nExample output: Direct Questioning on the Factors affecting Historical Significance of Begum Hazrat Mahal\n\nQuestions:\n' + text;

  try {
    const response = UrlFetchApp.fetch('https://api.openai.com/v1/chat/completions', {
      method: 'post',
      contentType: 'application/json',
      headers: { 'Authorization': 'Bearer ' + apiKey },
      payload: JSON.stringify({
        model: 'gpt-5.4-mini-2026-03-17',
        messages: [{ role: 'user', content: prompt }],
        max_completion_tokens: 100
      }),
      muteHttpExceptions: true
    });
    const code = response.getResponseCode();
    const body = JSON.parse(response.getContentText());
    if (code !== 200) return 'Similar questions with different variants.';
    let desc = (body.choices && body.choices[0] && body.choices[0].message && body.choices[0].message.content || '').trim();
    desc = desc.replace(/^["']|["']$/g, '');
    desc = desc.replace(/^(the questions?|these questions?)\s+(cover|are about|ask about)\s*/gi, '').trim();
    if (desc) desc = desc.charAt(0).toUpperCase() + desc.slice(1);
    return desc || 'Similar questions with different variants.';
  } catch (e) {
    return 'Similar questions with different variants.';
  }
}

function matchQuestionToConcept(questionContent, concepts) {
  const apiKey = getOpenAIKey();
  if (!apiKey) throw new Error('Set OPENAI_API_KEY in Code.gs (config) or Script Properties');

  const contentText = questionContentGist(questionContent, 300);
  const conceptsList = concepts.map(function(c, i) { return (i + 1) + '. ' + c; }).join('\n');

  const prompt = 'Given this question:\n\n"' + contentText + '"\n\nWhich concept does it belong to? Reply with ONLY the number (1 to ' + concepts.length + ').\n\nConcepts:\n' + conceptsList;

  const response = UrlFetchApp.fetch('https://api.openai.com/v1/chat/completions', {
    method: 'post',
    contentType: 'application/json',
    headers: { 'Authorization': 'Bearer ' + apiKey },
    payload: JSON.stringify({
      model: 'gpt-5.4-mini-2026-03-17',
      messages: [{ role: 'user', content: prompt }],
      max_completion_tokens: 10
    }),
    muteHttpExceptions: true
  });

  const code = response.getResponseCode();
  const body = JSON.parse(response.getContentText());

  if (code !== 200) {
    throw new Error('OpenAI API: ' + (body.error && body.error.message ? body.error.message : code));
  }

  const text = (body.choices && body.choices[0] && body.choices[0].message && body.choices[0].message.content || '').trim();
  const num = parseInt(text.replace(/\D/g, ''), 10);
  if (num >= 1 && num <= concepts.length) return num - 1;
  return 0;
}

/**
 * Run the full rewrite workflow for selected chapter.
 * Clears all Assessment Labels and Assessment Gist, then refills using GPT concept matching.
 */
function runForChapterRewriteFull(prefix) {
  Logger.log('runForChapterRewriteFull: ' + prefix);
  clearStopFlag();
  clearProgress();

  if (!QUESTION_BANK_FOLDER_ID) {
    throw new Error('Please set QUESTION_BANK_FOLDER_ID in Code.gs.');
  }
  if (!getOpenAIKey()) {
    throw new Error('Set OPENAI_API_KEY in Script Properties or Code.gs.');
  }

  setProgress(0, 0, 6, 'Loading manifest and chapter JSON...', { qTotal: 0, qCurrent: 0 });

  const manifest = getChapterList();
  const entry = manifest.find(function(m) { return m.prefix === prefix; });
  const filename = entry ? entry.filename : ('questions_' + sanitizeForFilename(prefix) + '.json');
  const jsonText = loadChapterFileContent(filename);
  const chapterQuestions = JSON.parse(jsonText);
  const questions = Array.isArray(chapterQuestions) ? chapterQuestions : [chapterQuestions];
  const chapterQCount = questions.length;

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getActiveSheet();
  let data = getSheetValuesFull(sheet);
  if (!data || data.length < 2) throw new Error('Sheet needs headers and at least one data row.');

  let headers = data[0];
  let displayNameCol = findColumnIndex(headers, 'display_name');
  let groupDescCol = findColumnIndex(headers, 'group_description');
  let groupTypeCol = findColumnIndex(headers, 'group_type');
  let assessmentCol = findColumnIndex(headers, 'Assessment Label');
  let assessmentGistCol = findColumnIndex(headers, 'Assessment Gist');
  let conceptCol = findColumnIndex(headers, 'Concept name');
  let groupNameCol = findColumnIndex(headers, 'group_name');

  if (assessmentCol < 0) throw new Error('Column "Assessment Label" not found.');
  if (conceptCol < 0 && groupNameCol < 0) throw new Error('Column "Concept name" or "group_name" not found.');

  const conceptNameSnapshot = snapshotConceptNameCells(data, conceptCol);

  if (assessmentGistCol < 0) {
    sheet.insertColumnAfter(assessmentCol + 1);
    sheet.getRange(1, assessmentCol + 2).setValue('Assessment Gist').setFontWeight('bold');
    assessmentGistCol = assessmentCol + 1;
    SpreadsheetApp.flush();
  }

  ensureCommentsColumn(sheet, assessmentGistCol);
  data = getSheetValuesFull(sheet);
  headers = data[0];
  assessmentCol = findColumnIndex(headers, 'Assessment Label');
  assessmentGistCol = findColumnIndex(headers, 'Assessment Gist');
  conceptCol = findColumnIndex(headers, 'Concept name');
  displayNameCol = findColumnIndex(headers, 'display_name');
  groupDescCol = findColumnIndex(headers, 'group_description');
  groupTypeCol = findColumnIndex(headers, 'group_type');
  groupNameCol = findColumnIndex(headers, 'group_name');
  const commentsCol = findColumnIndex(headers, 'Comments');

  assertUniqueColumnIndices('Rewrite Full', {
    assessmentCol: assessmentCol,
    assessmentGistCol: assessmentGistCol,
    conceptCol: conceptCol,
    groupTypeCol: groupTypeCol,
    groupDescCol: groupDescCol,
    commentsCol: commentsCol
  });

  const lastRow = sheet.getLastRow();
  const numCols = sheet.getLastColumn();
  if (lastRow >= 2) {
    sheet.getRange(2, assessmentCol + 1, lastRow, assessmentCol + 1).clearContent();
    sheet.getRange(2, assessmentGistCol + 1, lastRow, assessmentGistCol + 1).clearContent();
    if (commentsCol >= 0) sheet.getRange(2, commentsCol + 1, lastRow, commentsCol + 1).clearContent();
    sheet.getRange(2, 1, lastRow, numCols).setBackground(null);
  }

  data = getSheetValuesFull(sheet);
  restoreConceptNameCellsIfStripped(sheet, data, conceptCol, conceptNameSnapshot);
  data = getSheetValuesFull(sheet);

  const conceptToRows = {};
  for (let r = 1; r < data.length; r++) {
    const concept = resolveRowConcept(data[r], conceptCol, groupNameCol);
    if (!concept) continue;
    let groupType = groupTypeCol >= 0 ? (data[r][groupTypeCol] || '').toString().trim() : '';
    const displayName = displayNameCol >= 0 ? (data[r][displayNameCol] || '').toString() : '';
    if (!groupType && displayName) {
      if (/\)\s*BG\d/i.test(displayName)) groupType = 'Basic';
      else if (/\)\s*IG\d/i.test(displayName)) groupType = 'Intermediate';
      else if (/\)\s*AG\d/i.test(displayName)) groupType = 'Advanced';
    }
    if (!conceptToRows[concept]) conceptToRows[concept] = [];
    conceptToRows[concept].push({ row: r + 1, groupType: difficultyToGroupType(groupType), displayName: displayName });
  }

  const placementPack = buildTopicPlacementPack(conceptToRows);
  const uniqueConcepts = placementPack.uniqueConcepts;
  if (!uniqueConcepts.length) {
    throw new Error('No concepts found on data rows. Fill "Concept name" or "group_name".');
  }

  setProgress(1, 0, chapterQCount, 'Routing questions to topics and concepts...', { qTotal: chapterQCount, qCurrent: 0 });

  const matchedItems = [];
  for (let i = 0; i < questions.length; i++) {
    checkStopFlag();
    const q = questions[i];
    setProgress(1, i + 1, chapterQCount, '(' + (i + 1) + '/' + chapterQCount + ') ' + (q.question_label || '').substring(0, 40), { qTotal: chapterQCount, qCurrent: i + 1 });
    const placement = placeQuestionWithTopicFirstPipeline(
      (q.question_label || '').trim(),
      q,
      placementPack.topicPack,
      placementPack.uniqueConcepts,
      placementPack.culminationByBand,
      placementPack.bandOrderIndex
    );
    matchedItems.push({
      label: (q.question_label || '').trim(),
      content: q.question_content || '',
      concept: placement.concept,
      groupType: difficultyToGroupType(q.difficulty_level),
      comment: placement.comment || '',
      highlightYellow: placement.highlightYellow === true
    });
    Utilities.sleep(120);
  }

  setProgress(2, 0, 1, 'Clustering variants within each concept and difficulty bucket...', { qTotal: chapterQCount, qCurrent: chapterQCount });
  const rawSlots = clusterQuestionItemsByRouting(matchedItems);
  const clusterPayloads = compileClusterPayloads(rawSlots);

  setProgress(3, 0, Object.keys(clusterPayloads).length || 1, 'Building group descriptions...', { qTotal: chapterQCount, qCurrent: chapterQCount });
  for (const key in clusterPayloads) {
    const conceptHint = key.split('|||')[0];
    (clusterPayloads[key] || []).forEach(function(cluster) {
      cluster.groupDescription = rewriteGroupDescriptionFromGists(cluster.gistBlock, shortConceptDisplayName(conceptHint));
    });
  }

  const emptyRowsByKey = {};
  Object.keys(rawSlots).forEach(function(key) {
    emptyRowsByKey[key] = [];
  });
  for (const concept in conceptToRows) {
    const rows = conceptToRows[concept] || [];
    rows.forEach(function(rowObj) {
      const key = buildRoutingClusterKey(concept, difficultyToGroupType(rowObj.groupType));
      if (!emptyRowsByKey[key]) emptyRowsByKey[key] = [];
      emptyRowsByKey[key].push(rowObj.row);
    });
  }

  setProgress(4, 0, Object.keys(clusterPayloads).length || 1, 'Writing grouped rows...', { qTotal: chapterQCount, qCurrent: chapterQCount });
  const writtenRows = [];
  for (const key in clusterPayloads) {
    checkStopFlag();
    const clusters = clusterPayloads[key] || [];
    const conceptNameForRow = key.split('|||')[0];
    const groupType = key.split('|||')[1];
    const allRows = (conceptToRows[conceptNameForRow] || []).filter(function(r) {
      return difficultyToGroupType(r.groupType) === groupType;
    }).map(function(r) { return r.row; }).sort(function(a, b) { return a - b; });

    for (let i = 0; i < clusters.length && i < allRows.length; i++) {
      const row = allRows[i];
      const cluster = clusters[i];
      sheet.getRange(row, assessmentCol + 1).setValue(cluster.labelsText);
      sheet.getRange(row, assessmentGistCol + 1).setValue(cluster.gistBlock);
      if (groupTypeCol >= 0) sheet.getRange(row, groupTypeCol + 1).setValue(groupType);
      if (groupDescCol >= 0) sheet.getRange(row, groupDescCol + 1).setValue(cluster.groupDescription || 'Similar questions with different variants.');
      if (commentsCol >= 0) sheet.getRange(row, commentsCol + 1).setValue(cluster.comments || '');
      if (cluster.highlightYellow) {
        sheet.getRange(row, 1, row, numCols).setBackground(CULMINATION_ROW_BG);
      }
      writtenRows.push({
        row: row,
        labels: cluster.labelsText,
        gist: cluster.gistBlock,
        concept: conceptNameForRow,
        yellow: cluster.highlightYellow === true,
        comments: cluster.comments || ''
      });
    }
  }

  setProgress(5, 0, writtenRows.length || 1, 'Running QA review on written rows...', { qTotal: chapterQCount, qCurrent: chapterQCount });
  for (let wi = 0; wi < writtenRows.length; wi++) {
    checkStopFlag();
    const w = writtenRows[wi];
    const p = parseTopicGroupFromConcept(w.concept);
    const topicHuman = humanizeTopicIdForPrompt((p.hasPl && p.topicSlug) ? p.topicSlug : (p.topicGroupId || ''));
    const qaReview = auditWrittenRowMismatch(w.labels, w.gist, w.concept, topicHuman);
    if (qaReview && qaReview.flag) {
      sheet.getRange(w.row, 1, w.row, numCols).setBackground(POTENTIAL_ERROR_BG);
      if (commentsCol >= 0) {
        const merged = mergeReadableComments([w.comments, buildConflictComment({
          kind: 'qa_flag',
          questionLabel: firstQuestionLabelFromText(w.labels),
          expectedTopic: topicHuman,
          currentConcept: shortConceptDisplayName(w.concept),
          reason: qaReview.reason
        })]);
        sheet.getRange(w.row, commentsCol + 1).setValue(merged);
      }
    }
    Utilities.sleep(80);
  }

  clearProgress();
  return 'Rewrite Full complete.\nChapter: ' + prefix + '\nQuestions routed: ' + chapterQCount + '\nRows written: ' + writtenRows.length;
}

/**
 * Tag New Assessments:
 * Step 01: Compare chapter vs sheet, eliminate already tagged.
 * Step 02: Match untagged to group_description of existing rows; append if match (same difficulty only).
 * Step 03: For remainder, apply Rewrite Full logic but only to empty rows.
 * Step 04: Repeatable for future additions.
 * Note: Basic->Basic, Intermediate->Intermediate, Advanced->Advanced. No mixing.
 */
function runForChapterTagNew(prefix) {
  clearStopFlag();
  clearProgress();

  if (!QUESTION_BANK_FOLDER_ID) {
    throw new Error('Please set QUESTION_BANK_FOLDER_ID in Code.gs.');
  }
  if (!getOpenAIKey()) {
    throw new Error('Set OPENAI_API_KEY in Script Properties or Code.gs.');
  }

  setProgress(0, 0, 6, 'Creating backup...', { qTotal: 0, qCurrent: 0 });

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getActiveSheet();
  let data = getSheetValuesFull(sheet);
  if (!data || data.length < 2) throw new Error('Sheet needs headers and at least one data row.');

  const backupName = 'Backup ' + Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HHmmss');
  const backupSheet = ss.duplicateActiveSheet();
  backupSheet.setName(backupName.substring(0, 100));
  ss.setActiveSheet(sheet);

  const manifest = getChapterList();
  const entry = manifest.find(function(m) { return m.prefix === prefix; });
  const filename = entry ? entry.filename : ('questions_' + sanitizeForFilename(prefix) + '.json');
  const jsonText = loadChapterFileContent(filename);
  const chapterQuestions = JSON.parse(jsonText);
  const questions = Array.isArray(chapterQuestions) ? chapterQuestions : [chapterQuestions];
  const chapterQCount = questions.length;

  let headers = data[0];
  let displayNameCol = findColumnIndex(headers, 'display_name');
  let groupDescCol = findColumnIndex(headers, 'group_description');
  let groupTypeCol = findColumnIndex(headers, 'group_type');
  let assessmentCol = findColumnIndex(headers, 'Assessment Label');
  let assessmentGistCol = findColumnIndex(headers, 'Assessment Gist');
  let conceptCol = findColumnIndex(headers, 'Concept name');
  let groupNameCol = findColumnIndex(headers, 'group_name');

  if (assessmentCol < 0) throw new Error('Column "Assessment Label" not found.');
  if (conceptCol < 0 && groupNameCol < 0) throw new Error('Column "Concept name" or "group_name" not found.');

  if (assessmentGistCol < 0) {
    sheet.insertColumnAfter(assessmentCol + 1);
    sheet.getRange(1, assessmentCol + 2).setValue('Assessment Gist').setFontWeight('bold');
    assessmentGistCol = assessmentCol + 1;
    SpreadsheetApp.flush();
  }

  ensureCommentsColumn(sheet, assessmentGistCol);
  data = getSheetValuesFull(sheet);
  headers = data[0];
  assessmentCol = findColumnIndex(headers, 'Assessment Label');
  assessmentGistCol = findColumnIndex(headers, 'Assessment Gist');
  conceptCol = findColumnIndex(headers, 'Concept name');
  groupDescCol = findColumnIndex(headers, 'group_description');
  groupTypeCol = findColumnIndex(headers, 'group_type');
  displayNameCol = findColumnIndex(headers, 'display_name');
  groupNameCol = findColumnIndex(headers, 'group_name');
  const commentsCol = findColumnIndex(headers, 'Comments');

  assertUniqueColumnIndices('Tag New', {
    assessmentCol: assessmentCol,
    assessmentGistCol: assessmentGistCol,
    conceptCol: conceptCol,
    groupTypeCol: groupTypeCol,
    groupDescCol: groupDescCol,
    commentsCol: commentsCol
  });

  const existingLabels = {};
  for (let r = 1; r < data.length; r++) {
    const cell = (data[r][assessmentCol] || '').toString().trim();
    if (!cell) continue;
    cell.split(/[\n,]+/).forEach(function(l) {
      const t = l.trim();
      if (t) existingLabels[t] = true;
    });
  }

  const newQuestions = questions.filter(function(q) {
    const lbl = (q.question_label || '').trim();
    return lbl && !existingLabels[lbl];
  });

  if (!newQuestions.length) {
    clearProgress();
    return 'Tag New Assessments: no new labels.\nChapter: ' + prefix + '\nBackup: ' + backupName;
  }

  const conceptGroupRows = {};
  const conceptToRowsFlat = {};
  for (let r = 1; r < data.length; r++) {
    const concept = resolveRowConcept(data[r], conceptCol, groupNameCol);
    if (!concept) continue;
    let groupType = groupTypeCol >= 0 ? (data[r][groupTypeCol] || '').toString().trim() : '';
    const displayName = displayNameCol >= 0 ? (data[r][displayNameCol] || '').toString() : '';
    if (!groupType && displayName) {
      if (/\)\s*BG\d/i.test(displayName)) groupType = 'Basic';
      else if (/\)\s*IG\d/i.test(displayName)) groupType = 'Intermediate';
      else if (/\)\s*AG\d/i.test(displayName)) groupType = 'Advanced';
    }
    groupType = difficultyToGroupType(groupType);
    const key = buildRoutingClusterKey(concept, groupType);
    if (!conceptGroupRows[key]) conceptGroupRows[key] = { empty: [], filled: [] };
    const rowEntry = {
      row: r + 1,
      concept: concept,
      groupType: groupType,
      assessmentLabel: (data[r][assessmentCol] || '').toString().trim(),
      assessmentGist: (data[r][assessmentGistCol] || '').toString().trim(),
      group_description: groupDescCol >= 0 ? (data[r][groupDescCol] || '').toString().trim() : '',
      comments: commentsCol >= 0 ? (data[r][commentsCol] || '').toString().trim() : ''
    };
    if (!conceptToRowsFlat[concept]) conceptToRowsFlat[concept] = [];
    conceptToRowsFlat[concept].push({ row: r + 1, groupType: groupType, displayName: displayName });
    if (rowEntry.assessmentLabel) conceptGroupRows[key].filled.push(rowEntry);
    else conceptGroupRows[key].empty.push(rowEntry);
  }

  const placementPack = buildTopicPlacementPack(conceptToRowsFlat);
  if (!placementPack.uniqueConcepts.length) {
    throw new Error('No concepts found on data rows. Fill "Concept name" or "group_name".');
  }

  setProgress(1, 0, newQuestions.length, 'Routing new questions to topics and concepts...', { qTotal: chapterQCount, qCurrent: 0 });
  const routedItems = [];
  for (let i = 0; i < newQuestions.length; i++) {
    checkStopFlag();
    const q = newQuestions[i];
    setProgress(1, i + 1, newQuestions.length, '(' + (i + 1) + '/' + newQuestions.length + ') ' + (q.question_label || '').substring(0, 40), { qTotal: chapterQCount, qCurrent: i + 1 });
    const placement = placeQuestionWithTopicFirstPipeline(
      (q.question_label || '').trim(),
      q,
      placementPack.topicPack,
      placementPack.uniqueConcepts,
      placementPack.culminationByBand,
      placementPack.bandOrderIndex
    );
    routedItems.push({
      label: (q.question_label || '').trim(),
      content: q.question_content || '',
      concept: placement.concept,
      groupType: difficultyToGroupType(q.difficulty_level),
      comment: placement.comment || '',
      highlightYellow: placement.highlightYellow === true
    });
    Utilities.sleep(120);
  }

  setProgress(2, 0, 1, 'Clustering new questions inside their routed concept and difficulty buckets...', { qTotal: chapterQCount, qCurrent: newQuestions.length });
  const rawSlots = clusterQuestionItemsByRouting(routedItems);
  const clusterPayloads = compileClusterPayloads(rawSlots);

  setProgress(3, 0, Object.keys(clusterPayloads).length || 1, 'Checking append targets and building group descriptions...', { qTotal: chapterQCount, qCurrent: newQuestions.length });
  const operations = [];
  for (const key in clusterPayloads) {
    const concept = key.split('|||')[0];
    const groupType = key.split('|||')[1];
    const bucket = conceptGroupRows[key] || { empty: [], filled: [] };
    const emptyRows = (bucket.empty || []).slice().sort(function(a, b) { return a.row - b.row; });
    const filledRows = (bucket.filled || []).slice().sort(function(a, b) { return a.row - b.row; });

    (clusterPayloads[key] || []).forEach(function(cluster) {
      cluster.groupDescription = rewriteGroupDescriptionFromGists(cluster.gistBlock, shortConceptDisplayName(concept));
      const matchIdx = filledRows.length ? matchClusterToExistingRow(cluster.gistBlock, filledRows) : -1;
      if (matchIdx >= 0) {
        operations.push({
          mode: 'append_existing',
          row: filledRows[matchIdx].row,
          concept: concept,
          groupType: groupType,
          labelsText: cluster.labelsText,
          gistBlock: cluster.gistBlock,
          groupDescription: cluster.groupDescription,
          comments: cluster.comments || '',
          highlightYellow: cluster.highlightYellow === true
        });
      } else if (emptyRows.length) {
        const targetEmpty = emptyRows.shift();
        operations.push({
          mode: 'write_empty',
          row: targetEmpty.row,
          concept: concept,
          groupType: groupType,
          labelsText: cluster.labelsText,
          gistBlock: cluster.gistBlock,
          groupDescription: cluster.groupDescription,
          comments: cluster.comments || '',
          highlightYellow: cluster.highlightYellow === true
        });
      } else if (filledRows.length) {
        const fallbackRow = filledRows[filledRows.length - 1];
        operations.push({
          mode: 'append_existing',
          row: fallbackRow.row,
          concept: concept,
          groupType: groupType,
          labelsText: cluster.labelsText,
          gistBlock: cluster.gistBlock,
          groupDescription: cluster.groupDescription,
          comments: mergeReadableComments([cluster.comments || '', buildConflictComment({ kind: 'capacity_append' })]),
          highlightYellow: cluster.highlightYellow === true
        });
      }
    });
  }

  setProgress(4, 0, operations.length || 1, 'Writing appended and new rows...', { qTotal: chapterQCount, qCurrent: newQuestions.length });
  const numCols = sheet.getLastColumn();
  const touchedRows = [];
  for (let i = 0; i < operations.length; i++) {
    checkStopFlag();
    const op = operations[i];
    setProgress(4, i + 1, operations.length, '(' + (i + 1) + '/' + operations.length + ') row ' + op.row, { qTotal: chapterQCount, qCurrent: newQuestions.length });

    const existingLabelsText = (sheet.getRange(op.row, assessmentCol + 1).getValue() || '').toString().trim();
    const existingGistText = (sheet.getRange(op.row, assessmentGistCol + 1).getValue() || '').toString().trim();
    const existingCommentsText = commentsCol >= 0 ? (sheet.getRange(op.row, commentsCol + 1).getValue() || '').toString().trim() : '';
    const existingDesc = groupDescCol >= 0 ? (sheet.getRange(op.row, groupDescCol + 1).getValue() || '').toString().trim() : '';

    if (op.mode === 'append_existing') {
      sheet.getRange(op.row, assessmentCol + 1).setValue(appendTextBlock(existingLabelsText, op.labelsText));
      sheet.getRange(op.row, assessmentGistCol + 1).setValue(appendTextBlock(existingGistText, op.gistBlock));
      if (groupDescCol >= 0 && (!existingDesc || existingDesc === 'NA')) {
        sheet.getRange(op.row, groupDescCol + 1).setValue(op.groupDescription || 'Similar questions with different variants.');
      }
      if (commentsCol >= 0) {
        sheet.getRange(op.row, commentsCol + 1).setValue(mergeReadableComments([existingCommentsText, op.comments]));
      }
    } else {
      sheet.getRange(op.row, assessmentCol + 1).setValue(op.labelsText);
      sheet.getRange(op.row, assessmentGistCol + 1).setValue(op.gistBlock);
      if (groupTypeCol >= 0) sheet.getRange(op.row, groupTypeCol + 1).setValue(op.groupType);
      if (groupDescCol >= 0) {
        sheet.getRange(op.row, groupDescCol + 1).setValue(op.groupDescription || 'Similar questions with different variants.');
      }
      if (commentsCol >= 0) {
        sheet.getRange(op.row, commentsCol + 1).setValue(op.comments || '');
      }
    }

    if (op.highlightYellow) {
      sheet.getRange(op.row, 1, op.row, numCols).setBackground(CULMINATION_ROW_BG);
    }

    touchedRows.push({
      row: op.row,
      concept: op.concept,
      labels: (sheet.getRange(op.row, assessmentCol + 1).getValue() || '').toString(),
      gist: (sheet.getRange(op.row, assessmentGistCol + 1).getValue() || '').toString(),
      comments: commentsCol >= 0 ? (sheet.getRange(op.row, commentsCol + 1).getValue() || '').toString() : ''
    });
  }

  setProgress(5, 0, touchedRows.length || 1, 'Running QA review on touched rows...', { qTotal: chapterQCount, qCurrent: newQuestions.length });
  for (let wi = 0; wi < touchedRows.length; wi++) {
    checkStopFlag();
    const w = touchedRows[wi];
    const p = parseTopicGroupFromConcept(w.concept);
    const topicHuman = humanizeTopicIdForPrompt((p.hasPl && p.topicSlug) ? p.topicSlug : (p.topicGroupId || ''));
    const qaReview = auditWrittenRowMismatch(w.labels, w.gist, w.concept, topicHuman);
    if (qaReview && qaReview.flag) {
      sheet.getRange(w.row, 1, w.row, numCols).setBackground(POTENTIAL_ERROR_BG);
      if (commentsCol >= 0) {
        sheet.getRange(w.row, commentsCol + 1).setValue(mergeReadableComments([w.comments, buildConflictComment({
          kind: 'qa_flag',
          questionLabel: firstQuestionLabelFromText(w.labels),
          expectedTopic: topicHuman,
          currentConcept: shortConceptDisplayName(w.concept),
          reason: qaReview.reason
        })]));
      }
    }
    Utilities.sleep(80);
  }

  clearProgress();
  return 'Tag New Assessments complete.\nChapter: ' + prefix + '\nNew labels processed: ' + newQuestions.length + '\nRows touched: ' + touchedRows.length + '\nBackup: ' + backupName;
}
