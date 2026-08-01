import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useOptionalAuth } from "../Auth";
import { useAsync } from "../hooks";
import { useRunConsole } from "../RunConsole";
import DirectoryPicker from "../components/DirectoryPicker";
import DocumentUpload from "../components/DocumentUpload";
import SyllabusUploader from "../components/SyllabusUploader";
import ApiUsageSummary from "../components/ApiUsageSummary";
import type {
  OpenAIUsage,
  PendingSemanticDecision,
  ResumableCheckpoint,
  SemanticDecisionChoice,
  SemanticDecisionCandidate,
  SemanticDecisionEvidence,
  SemanticDecisionOption,
  SemanticDecisionSubmission,
  SemanticDecisionSubmissionResult,
  Scope,
  UploadJob,
} from "../types";

type Path = null | "post" | "pre";

export default function BuildConcepts() {
  const auth = useOptionalAuth();
  const [path, setPath] = useState<Path>(null);
  const [resumeJob, setResumeJob] = useState<UploadJob | null>(null);
  const [pendingResume, setPendingResume] =
    useState<ResumableCheckpoint | null>(null);
  const [resumeBusy, setResumeBusy] = useState(false);
  const [resumeDiscoveryLoading, setResumeDiscoveryLoading] = useState(true);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const discoveredForUserRef = useRef("");
  const vocab = useAsync(() => api.vocab(), []);
  const bookSources = vocab.data?.book_sources ?? [];
  const ownerKey = auth?.user?.sub || "local";

  useEffect(() => {
    if (auth?.loading || discoveredForUserRef.current === ownerKey) return;
    discoveredForUserRef.current = ownerKey;
    let active = true;
    setResumeDiscoveryLoading(true);
    setResumeError(null);
    Promise.all([
      api.resumableConceptCheckpoints("post"),
      api.resumableConceptCheckpoints("pre"),
    ])
      .then(([post, pre]) => {
        if (!active) return;
        const candidate = [...post.items, ...pre.items]
          .filter((job) => job.checkpoint_available)
          .sort((left, right) =>
            checkpointTime(right).localeCompare(checkpointTime(left)))
          .find((job) =>
            !safeSessionGet(checkpointPromptKey(ownerKey, job)));
        setPendingResume(candidate ?? null);
      })
      .catch((discoveryError) => {
        if (active) {
          setResumeError(
            `Could not check for saved runs: ${String(discoveryError)}`,
          );
        }
      })
      .finally(() => {
        if (active) setResumeDiscoveryLoading(false);
      });
    return () => {
      active = false;
    };
  }, [auth?.loading, ownerKey]);

  useEffect(() => {
    if (!pendingResume?.generation_running) return;
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const fresh = await api.getUploadJob("concepts", pendingResume.id);
        if (!active) return;
        const freshSummary = resumableCheckpointFromJob(fresh);
        if (fresh.generation_running) {
          setPendingResume(freshSummary);
          timer = window.setTimeout(poll, 3000);
          return;
        }
        if (fresh.checkpoint_available) {
          setPendingResume(freshSummary);
          return;
        }
        acknowledgeCheckpointPrompt(ownerKey, freshSummary);
        setPendingResume(null);
        setResumeJob(fresh);
        setPath(fresh.learning_kind === "pre" ? "pre" : "post");
      } catch (pollError) {
        if (active) {
          setResumeError(
            `Could not refresh the active run: ${String(pollError)}`,
          );
          timer = window.setTimeout(poll, 5000);
        }
      }
    };
    timer = window.setTimeout(poll, 3000);
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [ownerKey, pendingResume?.generation_running, pendingResume?.id]);

  function choosePath(nextPath: Exclude<Path, null>) {
    setResumeJob(null);
    setPath(nextPath);
  }

  function keepCheckpointForLater(job: ResumableCheckpoint) {
    acknowledgeCheckpointPrompt(ownerKey, job);
    setPendingResume(null);
  }

  async function resumeCheckpoint(job: ResumableCheckpoint) {
    setResumeBusy(true);
    setResumeError(null);
    try {
      // Discovery intentionally returns no source text or logs. Fetch the full
      // owner-scoped job only after the user chooses Resume.
      const fullJob = await api.getUploadJob("concepts", job.id);
      acknowledgeCheckpointPrompt(ownerKey, job);
      setPendingResume(null);
      setResumeJob(fullJob);
      setPath(fullJob.learning_kind === "pre" ? "pre" : "post");
    } catch (resumeFailure) {
      setResumeError(`Could not restore the saved run: ${String(resumeFailure)}`);
    } finally {
      setResumeBusy(false);
    }
  }

  async function discardCheckpoint(job: ResumableCheckpoint) {
    setResumeBusy(true);
    setResumeError(null);
    try {
      await api.clearConceptCheckpoint(job.id);
      acknowledgeCheckpointPrompt(ownerKey, job);
      setPendingResume(null);
    } catch (discardError) {
      setResumeError(`Could not discard the checkpoint: ${String(discardError)}`);
    } finally {
      setResumeBusy(false);
    }
  }

  return (
    <>
      <h1>Build Concepts</h1>
      <div className="subtitle">
        Generate concepts from documents (Post Learning) or derive prerequisite
        concepts (Pre Learning). Output is written to the Bulk Import workbook.
      </div>
      {resumeDiscoveryLoading && (
        <div className="muted" role="status" style={{ marginBottom: 12 }}>
          Checking your saved concept runs…
        </div>
      )}

      {!path && (
        <div className="grid cols-2">
          <button
            className="module-card"
            disabled={resumeDiscoveryLoading}
            onClick={() => choosePath("post")}
          >
            <div className="module-title">1 · Post Learning</div>
            <div className="module-desc">
              Upload a document → convert to MMD → parse concepts → deposit under a chapter.
            </div>
          </button>
          <button
            className="module-card"
            disabled={resumeDiscoveryLoading}
            onClick={() => choosePath("pre")}
          >
            <div className="module-title">2 · Pre Learning</div>
            <div className="module-desc">
              Upload a document, or derive pre-learning concepts from one or more
              existing Post Learning chapters.
            </div>
          </button>
        </div>
      )}

      {path && (
        <button
          className="ghost"
          onClick={() => {
            setPath(null);
            setResumeJob(null);
          }}
          style={{ marginBottom: 16 }}
        >
          ← Back to options
        </button>
      )}
      {path === "post" && (
        <PostLearningFlow
          bookSources={bookSources}
          initialJob={
            resumeJob?.learning_kind === "post" ? resumeJob : null
          }
        />
      )}
      {path === "pre" && (
        <PreLearningFlow
          bookSources={bookSources}
          initialUploadJob={
            resumeJob?.learning_kind === "pre" ? resumeJob : null
          }
        />
      )}
      {resumeError && !pendingResume && (
        <div className="error-box" style={{ marginTop: 16 }}>
          {resumeError}
        </div>
      )}
      {pendingResume && (
        <ResumeCheckpointPrompt
          job={pendingResume}
          busy={resumeBusy}
          error={resumeError}
          onResume={() => void resumeCheckpoint(pendingResume)}
          onKeep={() => keepCheckpointForLater(pendingResume)}
          onDiscard={() => void discardCheckpoint(pendingResume)}
        />
      )}
    </>
  );
}

/* ----------------------------- post learning ----------------------------- */

function PostLearningFlow({
  bookSources,
  initialJob,
}: {
  bookSources: string[];
  initialJob: UploadJob | null;
}) {
  const { run } = useRunConsole();
  const [job, setJob] = useState<UploadJob | null>(initialJob);
  const [scope, setScope] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [resultResumed, setResultResumed] = useState(false);
  const [treeReload, setTreeReload] = useState(0);
  const [pendingDecision, setPendingDecision] =
    useState<PendingSemanticDecision | null>(
      initialJob?.pending_decision ?? null,
    );

  useEffect(() => {
    if (initialJob) {
      setJob(initialJob);
      setPendingDecision(initialJob.pending_decision ?? null);
    }
  }, [initialJob]);

  const handleJob = useCallback((nextJob: UploadJob | null) => {
    setJob(nextJob);
    setPendingDecision(nextJob?.pending_decision ?? null);
    setError(null);
    setResult(null);
  }, []);

  async function generate() {
    if (!job || !scope) return;
    const resumedFromCheckpoint = Boolean(job.checkpoint_available);
    setBusy(true);
    setError(null);
    setResult(null);
    setPendingDecision(null);
    setResultResumed(resumedFromCheckpoint);
    try {
      const data = await run<Record<string, unknown>>(
        "Post Learning — generating concepts",
        api.paths.postLearningGenerate(job.id),
        { body: JSON.stringify({ target_chapter_id: scope.ids[0] }) },
        {
          cumulative: true,
          resumed: resumedFromCheckpoint,
          filename: job.filename,
          fileLabel: "Source file",
          initialUsage: job.openai_usage,
        },
      );
      let refreshedJob: UploadJob | null = null;
      try {
        refreshedJob = await api.getUploadJob("concepts", job.id);
        setJob(refreshedJob);
      } catch {
        // The result remains usable even if refreshing the completed job fails.
      }
      const decision = pendingDecisionFrom(data)
        ?? refreshedJob?.pending_decision
        ?? null;
      if (decision) {
        setPendingDecision(decision);
      } else {
        setResult(data);
      }
    } catch (e) {
      let refreshedJob: UploadJob | null = null;
      try {
        refreshedJob = await api.getUploadJob("concepts", job.id);
        setJob(refreshedJob);
      } catch {
        // Keep the generation error visible if refreshing job state also fails.
      }
      if (refreshedJob?.pending_decision) {
        setPendingDecision(refreshedJob.pending_decision);
        setError(null);
      } else {
        setError(formatGenerationError(e));
      }
    } finally {
      setBusy(false);
    }
  }

  function submitDecision(
    decisionId: string,
    submission: SemanticDecisionSubmission,
  ): Promise<SemanticDecisionSubmissionResult> {
    if (!job) {
      return Promise.reject(new Error("The saved generation job is unavailable."));
    }
    return api.submitConceptDecision(job.id, decisionId, submission);
  }

  return (
    <>
      <div className="section-title">1 · Upload document</div>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        bookSources={bookSources}
        externalJob={job}
        disabled={busy}
        onJob={handleJob}
      />
      {!result && (
        <ApiUsageSummary
          usage={job?.openai_usage}
          filename={job?.filename}
          fileLabel="Source file"
          cumulative
          resumed={Boolean(job?.checkpoint_available)}
        />
      )}

      {job && (job.status === "converted" || pendingDecision) && (
        <>
          <div className="section-title">2 · Deposit concepts under a chapter</div>
          <div className="card">
            <DirectoryPicker
              onScope={setScope}
              chapterOnly
              reloadSignal={treeReload}
              initialChapterIdentity={checkpointTargetIdentity(job)}
            />
            <SyllabusUploader disabled={busy} onLoaded={() => setTreeReload((n) => n + 1)} />
            <div className="row" style={{ marginTop: 12 }}>
              <span className="muted">{scope ? `Chapter: ${scope.label}` : "Pick a chapter"}</span>
              <div className="spacer" />
              {!pendingDecision && (
                <button disabled={!scope || busy} onClick={generate}>
                  {job.checkpoint_available
                    ? `Resume from ${Math.round(
                      (job.checkpoint_progress ?? 0) * 100,
                    )}% checkpoint`
                    : "Parse & generate concepts"}
                </button>
              )}
            </div>
          </div>
        </>
      )}

      {pendingDecision && (
        <SemanticDecisionPanel
          key={pendingDecision.decision_id}
          decision={pendingDecision}
          busy={busy}
          canResume={Boolean(scope)}
          onSubmit={submitDecision}
          onResume={generate}
        />
      )}
      {error && !pendingDecision && (
        <div className="error-box" style={{ marginTop: 16 }}>{error}</div>
      )}
      {result && (
        <ConceptResult
          result={result}
          filename={job?.filename}
          resumed={resultResumed}
        />
      )}
    </>
  );
}

/* ----------------------------- pre learning ----------------------------- */

function PreLearningFlow({
  bookSources,
  initialUploadJob,
}: {
  bookSources: string[];
  initialUploadJob: UploadJob | null;
}) {
  const [mode, setMode] = useState<"upload" | "existing">("upload");

  return (
    <>
      <div className="card row" style={{ marginBottom: 16 }}>
        <strong>Pre Learning source:</strong>
        <label className="radio">
          <input type="radio" checked={mode === "upload"} onChange={() => setMode("upload")} />
          Upload a document
        </label>
        <label className="radio">
          <input type="radio" checked={mode === "existing"} onChange={() => setMode("existing")} />
          Use existing Post Learning
        </label>
      </div>
      {mode === "upload"
        ? (
          <PreLearningUpload
            bookSources={bookSources}
            initialJob={initialUploadJob}
          />
        )
        : <PreLearningExisting bookSources={bookSources} />}
    </>
  );
}

function PreLearningUpload({
  bookSources,
  initialJob,
}: {
  bookSources: string[];
  initialJob: UploadJob | null;
}) {
  const { run } = useRunConsole();
  const [job, setJob] = useState<UploadJob | null>(initialJob);
  const [scope, setScope] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [resultResumed, setResultResumed] = useState(false);
  const [treeReload, setTreeReload] = useState(0);
  const [pendingDecision, setPendingDecision] =
    useState<PendingSemanticDecision | null>(
      initialJob?.pending_decision ?? null,
    );

  useEffect(() => {
    if (initialJob) {
      setJob(initialJob);
      setPendingDecision(initialJob.pending_decision ?? null);
    }
  }, [initialJob]);

  const handleJob = useCallback((nextJob: UploadJob | null) => {
    setJob(nextJob);
    setPendingDecision(nextJob?.pending_decision ?? null);
    setError(null);
    setResult(null);
  }, []);

  async function generate() {
    if (!job || !scope) return;
    const resumedFromCheckpoint = Boolean(job.checkpoint_available);
    setBusy(true);
    setError(null);
    setResult(null);
    setPendingDecision(null);
    setResultResumed(resumedFromCheckpoint);
    try {
      const data = await run<Record<string, unknown>>(
        "Pre Learning — generating concepts",
        api.paths.preLearningGenerate(job.id),
        { body: JSON.stringify({ target_chapter_id: scope.ids[0] }) },
        {
          cumulative: true,
          resumed: resumedFromCheckpoint,
          filename: job.filename,
          fileLabel: "Source file",
          initialUsage: job.openai_usage,
        },
      );
      let refreshedJob: UploadJob | null = null;
      try {
        refreshedJob = await api.getUploadJob("concepts", job.id);
        setJob(refreshedJob);
      } catch {
        // The result remains usable even if refreshing the completed job fails.
      }
      const decision = pendingDecisionFrom(data)
        ?? refreshedJob?.pending_decision
        ?? null;
      if (decision) {
        setPendingDecision(decision);
      } else {
        setResult(data);
      }
    } catch (e) {
      let refreshedJob: UploadJob | null = null;
      try {
        refreshedJob = await api.getUploadJob("concepts", job.id);
        setJob(refreshedJob);
      } catch {
        // Keep the generation error visible if refreshing job state also fails.
      }
      if (refreshedJob?.pending_decision) {
        setPendingDecision(refreshedJob.pending_decision);
        setError(null);
      } else {
        setError(formatGenerationError(e));
      }
    } finally {
      setBusy(false);
    }
  }

  function submitDecision(
    decisionId: string,
    submission: SemanticDecisionSubmission,
  ): Promise<SemanticDecisionSubmissionResult> {
    if (!job) {
      return Promise.reject(new Error("The saved generation job is unavailable."));
    }
    return api.submitConceptDecision(job.id, decisionId, submission);
  }

  return (
    <>
      <div className="section-title">1 · Upload document</div>
      <DocumentUpload
        module="concepts"
        conceptKind="pre"
        bookSources={bookSources}
        externalJob={job}
        disabled={busy}
        onJob={handleJob}
      />
      {!result && (
        <ApiUsageSummary
          usage={job?.openai_usage}
          filename={job?.filename}
          fileLabel="Source file"
          cumulative
          resumed={Boolean(job?.checkpoint_available)}
        />
      )}
      {job && (job.status === "converted" || pendingDecision) && (
        <>
          <div className="section-title">2 · Deposit pre-learning concepts under a chapter</div>
          <div className="card">
            <DirectoryPicker
              onScope={setScope}
              chapterOnly
              reloadSignal={treeReload}
              initialChapterIdentity={checkpointTargetIdentity(job)}
            />
            <SyllabusUploader disabled={busy} onLoaded={() => setTreeReload((n) => n + 1)} />
            <div className="row" style={{ marginTop: 12 }}>
              <span className="muted">{scope ? `Chapter: ${scope.label}` : "Pick a chapter"}</span>
              <div className="spacer" />
              {!pendingDecision && (
                <button disabled={!scope || busy} onClick={generate}>
                  {job.checkpoint_available
                    ? `Resume from ${Math.round(
                      (job.checkpoint_progress ?? 0) * 100,
                    )}% checkpoint`
                    : "Generate pre-learning concepts"}
                </button>
              )}
            </div>
          </div>
        </>
      )}
      {pendingDecision && (
        <SemanticDecisionPanel
          key={pendingDecision.decision_id}
          decision={pendingDecision}
          busy={busy}
          canResume={Boolean(scope)}
          onSubmit={submitDecision}
          onResume={generate}
        />
      )}
      {error && !pendingDecision && (
        <div className="error-box" style={{ marginTop: 16 }}>{error}</div>
      )}
      {result && (
        <ConceptResult
          result={result}
          filename={job?.filename}
          resumed={resultResumed}
        />
      )}
    </>
  );
}

function PreLearningExisting({ bookSources }: { bookSources: string[] }) {
  const { run } = useRunConsole();
  const [scope, setScope] = useState<Scope | null>(null);
  const [chapterIds, setChapterIds] = useState<number[]>([]);
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  function addChapter() {
    if (scope && scope.type === "chapter" && !chapterIds.includes(scope.ids[0])) {
      setChapterIds([...chapterIds, scope.ids[0]]);
    }
  }

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const data = await run<Record<string, unknown>>(
        "Pre Learning — deriving from existing chapters",
        api.paths.preLearningFromExisting,
        { body: JSON.stringify({ chapter_ids: chapterIds, source_book: source }) },
      );
      setResult(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="section-title">Choose Post Learning chapters (one or more)</div>
      <div className="card">
        <input placeholder="Source book (optional)" value={source}
          onChange={(e) => setSource(e.target.value)} list="book-sources" />
        <datalist id="book-sources">{bookSources.map((b) => <option key={b} value={b} />)}</datalist>
        <DirectoryPicker onScope={setScope} chapterOnly />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="ghost" disabled={!scope} onClick={addChapter}>
            + Add chapter {scope ? `(${scope.label})` : ""}
          </button>
          <div className="spacer" />
          <button disabled={busy || chapterIds.length === 0} onClick={generate}>
            Generate pre-learning concepts
          </button>
        </div>
        {chapterIds.length > 0 && (
          <div className="muted" style={{ marginTop: 8 }}>
            Selected chapter ids: {chapterIds.join(", ")}
          </div>
        )}
      </div>
      {error && <div className="error-box" style={{ marginTop: 16 }}>{error}</div>}
      {result && <ConceptResult result={result} />}
    </>
  );
}

type SemanticDecisionUiChoice = SemanticDecisionChoice;

function SemanticDecisionPanel({
  decision,
  busy,
  canResume,
  onSubmit,
  onResume,
}: {
  decision: PendingSemanticDecision;
  busy: boolean;
  canResume: boolean;
  onSubmit: (
    decisionId: string,
    submission: SemanticDecisionSubmission,
  ) => Promise<SemanticDecisionSubmissionResult>;
  onResume: () => void;
}) {
  const item = semanticDecisionItem(decision);
  const sourceReview = isSourceReviewDecision(decision);
  const workingSourcePatch = isWorkingSourcePatchDecision(decision);
  const sourceTopicReview = isSourceTopicReviewDecision(decision);
  const typeGranularity = isTypeGranularityDecision(decision);
  const topologyReview = isTopologyReviewDecision(decision);
  const groundingReview = isGroundingReviewDecision(decision);
  const candidates = decision.candidates ?? [];
  const suppliedOptions = (decision.options ?? []).filter((option) =>
    Boolean(option?.choice && option?.label));
  const fallbackOptions: SemanticDecisionOption[] = sourceReview
    ? []
    : [
      {
        choice: "expand_existing",
        label: "Expand existing concept",
        recommended: false,
      },
      {
        choice: "create_new",
        label: "Create separate concept",
        recommended: false,
      },
      {
        choice: "select_existing",
        label: "Select another existing concept",
        recommended: false,
      },
    ];
  const optionSeed = suppliedOptions.length ? suppliedOptions : fallbackOptions;
  const customInstructionOption: SemanticDecisionOption = {
    choice: "custom_instruction",
    label: "Give a custom instruction",
    recommended: false,
  };
  const availableOptions: SemanticDecisionOption[] = sourceReview
    ? optionSeed
    : optionSeed.some((option) => option.choice === "custom_instruction")
      ? optionSeed
      : [...optionSeed, customInstructionOption];
  const expandOption = availableOptions.find((option) =>
    option.choice === "expand_existing");
  const recommendedOption = availableOptions.find((option) =>
    option.recommended);
  const recommendedId = optionTargetIdentifier(
    recommendedOption ?? expandOption,
  );
  const recommendedCandidate = candidates.find((candidate) =>
    candidateIdentifier(candidate) === recommendedId);
  const selectableCandidates = candidates.filter((candidate) =>
    Boolean(candidateIdentifier(candidate)));
  const [choice, setChoice] =
    useState<SemanticDecisionUiChoice | "">("");
  const [targetId, setTargetId] = useState("");
  const [instruction, setInstruction] = useState("");
  const [saving, setSaving] = useState(false);
  const [recorded, setRecorded] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const titleId = `semantic-decision-${decision.decision_id}`;
  const questions = item.questions.length
    ? item.questions
    : decision.questions?.length
      ? decision.questions
      : decision.question
        ? [decision.question]
        : [];
  const diagnosis = firstNonEmptyString(
    decision.diagnosis,
    decision.conflict,
    decision.reason,
    decision.mismatch,
  ) || (
    workingSourcePatch
      ? "The numbered topic spine in the derived working MMD needs a verified repair before generation can continue."
      : sourceReview
      ? "The Phase 3 source graph needs human review before it can be used safely."
      : "Aegis found more than one defensible concept placement."
  );
  const decisionQuestion = firstNonEmptyString(
    decision.decision_question,
    decision.prompt,
    decision.review_question,
    sourceReview ? item.type_title : "",
  ) || (
    workingSourcePatch
      ? "Should Aegis apply the verified patch to the derived working MMD while preserving the uploaded raw MMD?"
      : sourceReview
      ? "Which verified source evidence should Aegis use to resolve this discrepancy?"
      : "How should Aegis handle this semantic mismatch?"
  );
  const sourceReplacementRequired = choice === "replace_source";
  function choose(nextChoice: SemanticDecisionUiChoice) {
    setChoice(nextChoice);
    setSubmitError(null);
    const option = availableOptions.find((candidate) =>
      candidate.choice === nextChoice);
    const suppliedTarget = optionTargetIdentifier(option);
    setTargetId(suppliedTarget);
  }

  async function saveDecision() {
    if (!choice) {
      setSubmitError("Choose what Aegis should do before saving.");
      return;
    }
    const trimmedInstruction = instruction.trim();
    if (choice === "custom_instruction" && !trimmedInstruction) {
      setSubmitError("Write the instruction Aegis should follow.");
      return;
    }
    if (decisionChoiceRequiresTarget(choice) && !targetId) {
      setSubmitError(
        sourceTopicReview
          ? "Select the source-topic recovery plan Aegis should use."
          : topologyReview
            ? "Select the source-supported concept action Aegis should use."
            : groundingReview
              ? "Select verified evidence or a topology repair."
              : sourceReview
                ? "Select the verified source evidence Aegis should use."
                : choice === "expand_existing"
                  ? "Select the existing concept Aegis should expand."
                  : "Select the existing concept Aegis should use.",
      );
      return;
    }

    const submission: SemanticDecisionSubmission = choice === "custom_instruction"
      ? {
        choice,
        instruction: trimmedInstruction,
      }
      : choice === "create_new"
        ? { choice: "create_new" }
        : choice === "select_existing" || choice === "expand_existing"
          ? {
            choice,
            target_concept_id: targetId,
          }
          : {
            choice,
            ...(targetId ? { target_id: targetId } : {}),
          };

    setSaving(true);
    setSubmitError(null);
    try {
      const response = await onSubmit(decision.decision_id, submission);
      if (response.status !== "decision_recorded") {
        throw new Error(`Unexpected decision status: ${response.status}`);
      }
      setRecorded(true);
    } catch (error) {
      setSubmitError(`Could not save this decision: ${String(error)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section
      className="card semantic-decision-card"
      role="region"
      aria-labelledby={titleId}
    >
      <div className="semantic-decision-head">
        <div>
          <span className="badge yellow">Human decision required</span>
          <h2 id={titleId}>Paused for your decision</h2>
          <p className="semantic-pause-assurance" role="status">
            No API request is running while paused.
          </p>
        </div>
        {typeof decision.checkpoint_progress === "number" && (
          <span className="badge accent">
            {Math.round(decision.checkpoint_progress * 100)}% checkpoint
          </span>
        )}
      </div>

      <p className="muted semantic-decision-intro">
        {typeGranularity
          ? "Aegis detected a deterministic Type-fragmentation risk before "
            + "the expensive host and topology stages. Review the counts, "
            + "choose whether to keep or consolidate the taxonomy, save that "
            + "decision, and then resume explicitly."
          : workingSourcePatch
            ? "Aegis found that the Phase 3 semantic graph omitted or changed a "
              + "numbered main topic. Review the exact before-and-after repair to "
              + "the derived working MMD, choose whether to apply it or provide "
              + "guidance, save that decision, and then resume explicitly. Your "
              + "uploaded raw MMD is never modified."
          : sourceTopicReview
            ? "Aegis found a numbered main topic in the source that has no "
              + "normal concept in the generated map. Review the exact source "
              + "and generated topic lists, choose one recovery direction, "
              + "save it, and then resume explicitly. One answer authorizes "
              + "at most one bounded recovery request."
          : topologyReview
            ? "GPT and its independent critic disagreed about a concept "
              + "boundary. Choose a source-supported refine, move, split, keep, "
              + "or retire action; the resumed proposal is independently "
              + "reviewed once before it can continue."
            : groundingReview
              ? "The independent grounding check could not certify this claim. "
                + "Choose verified evidence, send it to one bounded topology "
                + "repair, replace the source, or give a custom instruction."
              : sourceReview
                ? "GPT found a source-graph discrepancy it could not resolve without "
                  + "risking the source meaning. Review its diagnosis and the "
                  + "verified source evidence, choose what should happen, save that "
                  + "decision, and then resume explicitly."
                : "Generation reached a semantic choice that cannot be resolved "
                  + "safely from the source alone. Review the exact mismatch, choose "
                  + "what should happen, save your decision, and then resume explicitly."}
      </p>

      {decision.agent_review && (
        <div className="semantic-mismatch" role="status">
          <div className="row">
            <strong>Aegis autonomous review</strong>
            <div className="spacer" />
            <span className="badge yellow">
              Saved status: {agentReviewStatusLabel(decision.agent_review.status)}
            </span>
          </div>
          <p>{agentReviewStatusExplanation(decision.agent_review.status)}</p>
          {firstNonEmptyString(decision.agent_review.reason) && (
            <p>
              <strong>Saved reason:</strong>{" "}
              {firstNonEmptyString(decision.agent_review.reason)}
            </p>
          )}
        </div>
      )}

      <dl className="semantic-decision-details">
        {(item.unit_id || decision.item_id) && (
          <div>
            <dt>Item / TYPE</dt>
            <dd className="mono">
              {[item.unit_id || decision.item_id, item.type_id]
                .filter(Boolean).join(" · ")}
            </dd>
          </div>
        )}
        {(item.qids.length > 0 || (decision.qids?.length ?? 0) > 0) && (
          <div>
            <dt>Question IDs</dt>
            <dd className="mono">
              {(item.qids.length ? item.qids : decision.qids ?? []).join(", ")}
            </dd>
          </div>
        )}
        {!sourceReview && (item.type_title || decision.type) && (
          <div>
            <dt>Type</dt>
            <dd>
              {item.type_title
                ? item.type_title
                : formatSemanticValue(decision.type!)}
            </dd>
          </div>
        )}
        {(item.topic || decision.topic) && (
          <div>
            <dt>Topic</dt>
            <dd>
              {item.topic
                ? item.topic
                : formatSemanticValue(decision.topic!)}
            </dd>
          </div>
        )}
      </dl>

      {questions.length > 0 && (
        <div className="semantic-decision-section">
          <h3>
            {workingSourcePatch
              ? questions.length === 1
                ? "Affected numbered main topic"
                : "Affected numbered main topics"
              : sourceTopicReview
              ? questions.length === 1
                ? "Missing source topic"
                : "Missing source topics"
              : questions.length === 1
                ? "Question"
                : "Questions"}
          </h3>
          <ul>
            {questions.map((question, index) => (
              <li key={`${index}-${question}`}>{question}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="semantic-mismatch semantic-diagnosis">
        <strong>
          {typeGranularity
            ? "Aegis quality check"
            : workingSourcePatch
              ? "Working-source integrity diagnosis"
            : sourceTopicReview
              ? "Source-topic integrity diagnosis"
              : topologyReview
                ? "Concept-boundary diagnosis"
                : groundingReview
                  ? "Grounding-critic diagnosis"
                  : sourceReview
                    ? "GPT diagnosis"
                    : "What could not be matched"}
        </strong>
        <p>{diagnosis}</p>
      </div>

      <div className="semantic-decision-question">
        <strong>Decision needed</strong>
        <p>{decisionQuestion}</p>
      </div>

      {workingSourcePatch && decision.source_patch && (
        <section
          className="semantic-source-patch"
          aria-label="Verified working-source patch"
        >
          <div className="semantic-source-patch-head">
            <div>
              <span className={decision.source_patch.verified
                ? "badge green"
                : "badge yellow"}
              >
                {decision.source_patch.verified
                  ? "Verified derived-source patch"
                  : "Derived-source patch preview"}
              </span>
              <h3>Working MMD topic-spine repair</h3>
            </div>
            <span className="badge mono">Raw MMD unchanged</span>
          </div>

          <p className="semantic-source-patch-assurance" role="status">
            Your uploaded raw MMD remains byte-for-byte unchanged. Only the
            derived working MMD used by this generation run will be repaired
            after you save this decision and explicitly resume.
          </p>

          <div className="semantic-source-patch-diff">
            <div className="semantic-source-patch-pane">
              <strong>Before · current derived working MMD</strong>
              <pre>{decision.source_patch.before
                || "No current topic-spine text was supplied."}</pre>
            </div>
            <div className="semantic-source-patch-pane">
              <strong>After · verified derived working MMD</strong>
              <pre>{decision.source_patch.after
                || "No patched topic-spine text was supplied."}</pre>
            </div>
          </div>

          {decision.source_patch.operations.length > 0 && (
            <div className="semantic-source-patch-operations">
              <strong>Verified patch operations</strong>
              <ul>
                {decision.source_patch.operations.map((operation, index) => (
                  <li key={`${index}-${operation}`}>{operation}</li>
                ))}
              </ul>
            </div>
          )}

          <p className="muted mono semantic-source-patch-id">
            Patch target: {decision.source_patch.target_id}
            {decision.source_patch.patch_hash
              ? ` · ${decision.source_patch.patch_hash.slice(0, 12)}…`
              : ""}
          </p>
        </section>
      )}

      {candidates.length > 0 && !workingSourcePatch && (
        <div className="semantic-decision-section">
          <h3>
            {topologyReview
              ? "Source-supported concept actions"
              : sourceTopicReview
                ? "Source-topic recovery plan"
                : groundingReview
                  ? "Verified evidence and topology repairs"
                  : sourceReview
                    ? "Verified source candidates"
                    : "Candidate concepts"}
          </h3>
          <div className="semantic-candidate-list">
            {candidates.map((candidate, index) => (
              <div
                className="semantic-candidate"
                key={candidateIdentifier(candidate) || index}
              >
                <div className="row">
                  <strong>{candidateTitle(candidate)}</strong>
                  {candidateIdentifier(candidate) === recommendedId && (
                    <span className="badge green">Recommended</span>
                  )}
                  {candidateIdentifier(candidate) && (
                    <span className="badge mono">
                      {candidateIdentifier(candidate)}
                    </span>
                  )}
                </div>
                {candidateSummary(
                  candidate,
                  sourceReview
                    && !sourceTopicReview
                    && !topologyReview
                    && !groundingReview,
                ) && (
                  <p className="muted">
                    {candidateSummary(
                      candidate,
                      sourceReview
                        && !sourceTopicReview
                        && !topologyReview
                        && !groundingReview,
                    )}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {decision.evidence && decision.evidence.length > 0 && (
        <details className="semantic-evidence" open={sourceReview}>
          <summary>Source evidence ({decision.evidence.length})</summary>
          <div className="semantic-evidence-list">
            {decision.evidence.map((evidence, index) => (
              <blockquote key={index}>
                {formatEvidence(evidence)}
              </blockquote>
            ))}
          </div>
        </details>
      )}

      <ApiUsageSummary
        usage={decision.cumulative_usage}
        cumulative
      />

      {!recorded ? (
        availableOptions.length > 0 ? (
          <>
            <fieldset className="semantic-choice-list" disabled={saving || busy}>
              <legend>What should Aegis do?</legend>
              <p className="muted semantic-choice-instruction">
                No action is selected automatically. Choose one option below.
              </p>
              {availableOptions.map((option) => {
                const optionSelected = choice === option.choice;
                const suppliedTarget = optionTargetIdentifier(option);
                const needsTarget = decisionChoiceRequiresTarget(
                  option.choice,
                );
                const showTargetPicker = optionSelected
                  && needsTarget
                  && !suppliedTarget;
                const needsInstruction = option.choice === "custom_instruction";
                return (
                  <label className="semantic-choice" key={option.choice}>
                    <input
                      type="radio"
                      name={`semantic-choice-${decision.decision_id}`}
                      value={option.choice}
                      checked={optionSelected}
                      onChange={() => choose(option.choice)}
                    />
                    <span>
                      <strong>
                        {option.label}
                        {option.recommended ? " (recommended)" : ""}
                      </strong>
                      <small>
                        {decisionOptionDescription(
                          option,
                          decision,
                          recommendedCandidate,
                        )}
                      </small>
                      {showTargetPicker
                        && selectableCandidates.length > 0 && (
                        <select
                          aria-label={decisionTargetLabel(
                            option.choice,
                            decision,
                          )}
                          value={targetId}
                          onChange={(event) => {
                            setTargetId(event.target.value);
                            setSubmitError(null);
                          }}
                        >
                          <option value="">Choose an option…</option>
                          {selectableCandidates.map((candidate) => (
                            <option
                              key={candidateIdentifier(candidate)}
                              value={candidateIdentifier(candidate)}
                            >
                              {candidateTitle(candidate)}
                            </option>
                          ))}
                        </select>
                      )}
                      {showTargetPicker
                        && selectableCandidates.length === 0 && (
                        <small className="semantic-required-warning">
                          No safe candidate was supplied. Choose another action
                          or give a custom instruction; Aegis will not guess a
                          target.
                        </small>
                      )}
                      {optionSelected && needsInstruction && (
                        <textarea
                          aria-label="Custom instruction"
                          rows={4}
                          value={instruction}
                          onChange={(event) => {
                            setInstruction(event.target.value);
                            setSubmitError(null);
                          }}
                          placeholder={typeGranularity
                            ? "For example: target 12-16 reusable Types, keeping genuinely different methods separate."
                            : topologyReview || groundingReview
                              ? "Describe the exact source-supported refine, move, split, keep, retire, or evidence action to review."
                              : workingSourcePatch
                                ? "Describe how the derived working MMD topic spine should be corrected. The uploaded raw MMD will remain unchanged."
                              : sourceReview
                                ? "Describe exactly how Aegis should resolve this source discrepancy."
                                : "For example: expand the Renan concept to cover both attributes and the importance of nations."}
                        />
                      )}
                    </span>
                  </label>
                );
              })}
            </fieldset>

            {submitError && <div className="error-box">{submitError}</div>}
            <div className="row semantic-decision-actions">
              <span className="muted">
                Saving this choice does not call GPT or resume generation.
              </span>
              <div className="spacer" />
              <button
                type="button"
                disabled={saving || busy || !choice}
                onClick={() => void saveDecision()}
              >
                {saving ? "Saving decision…" : "Save decision"}
              </button>
            </div>
          </>
        ) : (
          <div className="semantic-missing-context" role="alert">
            <strong>No safe action remains for this checkpoint.</strong>
            <p>
              Aegis could not safely apply the previous guidance. Correct or
              replace the identified source material before continuing; it
              will not invent another decision or retry in a loop.
            </p>
          </div>
        )
      ) : (
        <div className="semantic-decision-recorded" role="status">
          <div>
            <strong>Decision saved.</strong>
            {sourceReplacementRequired ? (
              <p>
                Generation remains stopped. Replace or correct the source file
                using the upload controls above, convert it again, and then
                start or resume from the new verified source. Aegis will not
                alter the file automatically.
              </p>
            ) : workingSourcePatch ? (
              <p>
                The verified patch is recorded at this checkpoint. It will be
                applied only to the derived working MMD when you explicitly
                resume. Your uploaded raw MMD remains unchanged.
              </p>
            ) : (
              <p>
                It is recorded at this checkpoint. No generation request has
                started yet.
              </p>
            )}
            {!sourceReplacementRequired && !canResume && (
              <p className="muted">
                Select or restore the destination chapter above before resuming.
              </p>
            )}
          </div>
          {!sourceReplacementRequired && (
            <button
              type="button"
              disabled={busy || !canResume}
              onClick={onResume}
            >
              {busy ? "Resuming…" : "Resume generation"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

type NormalizedSemanticDecisionItem = {
  unit_id: string;
  type_id: string;
  type_title: string;
  qids: string[];
  questions: string[];
  topic: string;
};

function semanticDecisionItem(
  decision: PendingSemanticDecision,
): NormalizedSemanticDecisionItem {
  const item = decision.item;
  return {
    unit_id: item?.unit_id ?? decision.item_id ?? "",
    type_id: item?.type_id ?? "",
    type_title: item?.type_title
      ?? (typeof decision.type === "string" ? decision.type : ""),
    qids: item?.qids ?? decision.qids ?? [],
    questions: item?.questions
      ?? decision.questions
      ?? (
        decision.question && !isSourceReviewDecision(decision)
          ? [decision.question]
          : []
      ),
    topic: item?.topic
      ?? (typeof decision.topic === "string" ? decision.topic : ""),
  };
}

function isSourceReviewDecision(decision: PendingSemanticDecision): boolean {
  const kind = firstNonEmptyString(decision.kind).toLowerCase();
  return kind.includes("source_graph")
    || kind.includes("source_review")
    || isSourceTopicReviewDecision(decision)
    || decision.options?.some((option) =>
      option.choice === "accept_recommended"
      || option.choice === "select_candidate")
    || false;
}

function isWorkingSourcePatchDecision(
  decision: PendingSemanticDecision,
): boolean {
  return firstNonEmptyString(decision.item?.type_id).toLowerCase()
      === "numbered_main_topic_coverage"
    && decision.source_patch?.kind === "canonical_topic_binding"
    && decision.source_patch.target === "working_derived_source";
}

function isSourceTopicReviewDecision(
  decision: PendingSemanticDecision,
): boolean {
  return firstNonEmptyString(decision.kind).toLowerCase()
    === "source_topic_coverage_review";
}

function isTypeGranularityDecision(
  decision: PendingSemanticDecision,
): boolean {
  return firstNonEmptyString(decision.kind).toLowerCase()
    === "type_granularity_review";
}

function isTopologyReviewDecision(
  decision: PendingSemanticDecision,
): boolean {
  return firstNonEmptyString(decision.kind).toLowerCase().includes(
    "phase32_concept_blueprint",
  );
}

function isGroundingReviewDecision(
  decision: PendingSemanticDecision,
): boolean {
  return firstNonEmptyString(decision.kind).toLowerCase().includes(
    "phase31_source_grounding",
  );
}

function agentReviewStatusLabel(status: string): string {
  if (status === "escalated") return "Human judgment needed";
  if (status === "unavailable") return "No safe decision returned";
  if (status === "request_started") return "Attempt recorded";
  if (status === "resolved") return "Review completed";
  return "Human judgment needed";
}

function agentReviewStatusExplanation(status: string): string {
  if (status === "request_started") {
    return "Aegis recorded one bounded autonomous review attempt, but no safe "
      + "completed decision was saved. To avoid a duplicate paid request, it "
      + "will not repeat the review while this checkpoint is paused.";
  }
  if (status === "unavailable") {
    return "Aegis tried one bounded autonomous review, but the saved attempt "
      + "did not return a safe, usable decision. It will not run another "
      + "autonomous review while this checkpoint is paused.";
  }
  if (status === "resolved") {
    return "Aegis completed one bounded autonomous review, but this checkpoint "
      + "still requires your confirmation. It will not run another autonomous "
      + "review while this checkpoint is paused.";
  }
  return "Aegis tried one bounded autonomous review and saved that this choice "
    + "still needs your judgment. It will not run another autonomous review "
    + "while this checkpoint is paused.";
}

function optionTargetIdentifier(
  option?: SemanticDecisionOption,
): string {
  if (!option) return "";
  return firstNonEmptyString(
    option.target_id,
    option.target_concept_id,
  );
}

function decisionChoiceRequiresTarget(
  choice: SemanticDecisionUiChoice,
): boolean {
  return choice === "expand_existing"
    || choice === "select_existing"
    || choice === "accept_recommended"
    || choice === "select_candidate";
}

function decisionTargetLabel(
  choice: SemanticDecisionUiChoice,
  decision: PendingSemanticDecision,
): string {
  if (isWorkingSourcePatchDecision(decision)) {
    return "Verified working-source patch";
  }
  if (isSourceTopicReviewDecision(decision)) {
    return "Source-topic recovery plan";
  }
  if (isTopologyReviewDecision(decision)) {
    return "Source-supported concept action";
  }
  if (isGroundingReviewDecision(decision)) return "Evidence or topology repair";
  const sourceReview = isSourceReviewDecision(decision);
  if (sourceReview) return "Verified source evidence";
  return choice === "expand_existing"
    ? "Concept to expand"
    : "Existing concept";
}

function decisionOptionDescription(
  option: SemanticDecisionOption,
  decision: PendingSemanticDecision,
  recommendedCandidate?: SemanticDecisionCandidate,
): string {
  const topologyReview = isTopologyReviewDecision(decision);
  const groundingReview = isGroundingReviewDecision(decision);
  const sourceTopicReview = isSourceTopicReviewDecision(decision);
  const workingSourcePatch = isWorkingSourcePatchDecision(decision);
  const sourceReview = isSourceReviewDecision(decision);
  if (option.choice === "accept_recommended") {
    if (workingSourcePatch) {
      return "Apply the exact verified topic-spine repair shown above; the uploaded raw MMD stays unchanged.";
    }
    if (sourceTopicReview) {
      return "Preserve every numbered main topic and authorize one bounded recovery request.";
    }
    if (topologyReview) {
      return recommendedCandidate
        ? `Use ${candidateTitle(recommendedCandidate)} as the source-supported concept action.`
        : "Use GPT's proposed concept action, then require independent criticism.";
    }
    return recommendedCandidate
      ? `Use ${candidateTitle(recommendedCandidate)} as the verified source evidence.`
      : "Use the exact verified source evidence recommended by GPT.";
  }
  if (option.choice === "select_candidate") {
    if (topologyReview) {
      return "Choose refine, move, split, keep, or retire; Aegis will still require independent criticism.";
    }
    if (groundingReview) {
      return "Choose verified evidence or send the claim back for refine, move, split, or retirement.";
    }
    return "Choose a different verified page or source block; Aegis will not pick one for you.";
  }
  if (option.choice === "replace_source") {
    if (workingSourcePatch) {
      return "Reject this derived-source patch and upload a different source file; the current raw MMD is not modified.";
    }
    return "Stop at this checkpoint so you can correct or replace the source file; Aegis will not change it automatically.";
  }
  if (option.choice === "consolidate_types") {
    return "Run one bounded GPT proposal plus an independent critic; exact QID coverage, source wording, topic, activity, and scope checks still apply.";
  }
  if (option.choice === "keep_distinct_types") {
    return "Keep the current source-complete taxonomy and continue to the normal Type-host and final validation gates.";
  }
  if (option.choice === "expand_existing") {
    return recommendedCandidate
      ? `Extend ${candidateTitle(recommendedCandidate)} so it covers the missing requirement.`
      : "Choose which existing concept should be extended to cover the missing requirement.";
  }
  if (option.choice === "create_new") {
    return "Keep the unmatched requirement as a distinct, source-grounded concept.";
  }
  if (option.choice === "select_existing") {
    return "Choose a different verified concept host from the candidates.";
  }
  if (option.choice === "custom_instruction") {
    if (workingSourcePatch) {
      return "Tell Aegis how to revise the derived working MMD topic spine; the raw upload remains unchanged.";
    }
    if (topologyReview || groundingReview) {
      return "Tell Aegis the exact source-supported evidence or concept action to review once on Resume.";
    }
    if (sourceTopicReview) {
      return "Tell Aegis how to recover the missing source topics without silently merging or dropping them.";
    }
    return sourceReview
      ? "Tell Aegis exactly how to resolve this source discrepancy."
      : "Tell Aegis exactly how this item should be handled.";
  }
  return "Apply this action to the paused generation checkpoint.";
}

function pendingDecisionFrom(
  result: Record<string, unknown>,
): PendingSemanticDecision | null {
  const candidate = result.pending_decision;
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
    return null;
  }
  const decisionId = (candidate as Record<string, unknown>).decision_id;
  if (typeof decisionId !== "string" || !decisionId.trim()) return null;
  return candidate as PendingSemanticDecision;
}

function firstNonEmptyString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function candidateIdentifier(
  candidate?: SemanticDecisionCandidate,
): string {
  if (!candidate) return "";
  return firstNonEmptyString(
    candidate.target_id,
    candidate.concept_id,
    candidate.candidate_id,
    candidate.id,
    candidate.source_id,
    candidate.section_id,
  );
}

function candidateTitle(candidate: SemanticDecisionCandidate): string {
  return firstNonEmptyString(
    candidate.title,
    candidate.label,
    candidate.concept_title,
    candidate.name,
    candidateIdentifier(candidate),
  ) || "Existing concept";
}

function candidateSummary(
  candidate: SemanticDecisionCandidate,
  sourceReview = false,
): string {
  const explanation = firstNonEmptyString(
    candidate.reason,
    candidate.description,
    candidate.match_reason,
  );
  const coverage = firstNonEmptyString(candidate.coverage);
  const gap = firstNonEmptyString(candidate.gap);
  return [
    explanation,
    coverage
      ? labeledSemanticText(
        sourceReview ? "Verified source text" : "Current coverage",
        coverage,
      )
      : "",
    gap
      ? labeledSemanticText(
        sourceReview ? "Diagnostic issue" : "Missing requirement",
        gap,
      )
      : "",
  ].filter(Boolean).join(" ");
}

function labeledSemanticText(label: string, value: string): string {
  const trimmed = value.trim();
  const punctuation = /[.!?]$/.test(trimmed) ? "" : ".";
  return `${label}: ${trimmed}${punctuation}`;
}

function formatSemanticValue(value: string | Record<string, unknown>): string {
  if (typeof value === "string") return value;
  const identifier = firstNonEmptyString(
    value.id,
    value.type_id,
    value.topic_id,
  );
  const title = firstNonEmptyString(
    value.title,
    value.type_title,
    value.topic_title,
    value.name,
  );
  const method = firstNonEmptyString(
    value.method,
    value.method_summary,
    value.description,
  );
  const headline = [identifier, title].filter(Boolean).join(" — ");
  return [headline, method].filter(Boolean).join(": ")
    || JSON.stringify(value);
}

function formatEvidence(
  evidence: string | SemanticDecisionEvidence,
): string {
  if (typeof evidence === "string") return evidence;
  const label = firstNonEmptyString(
    evidence.label,
    evidence.source,
  );
  const pageValue = evidence.page === undefined || evidence.page === null
    ? ""
    : String(evidence.page).trim();
  const page = pageValue ? `page ${pageValue}` : "";
  const text = firstNonEmptyString(
    evidence.excerpt,
    evidence.text,
    evidence.quote,
  );
  const prefix = [label, page].filter(Boolean).join(" · ");
  return prefix && text
    ? `${prefix}: ${text}`
    : prefix || text || JSON.stringify(evidence);
}

function ResumeCheckpointPrompt({
  job,
  busy,
  error,
  onResume,
  onKeep,
  onDiscard,
}: {
  job: ResumableCheckpoint;
  busy: boolean;
  error: string | null;
  onResume: () => void;
  onKeep: () => void;
  onDiscard: () => void;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    headingRef.current?.focus();
  }, [job.id]);
  const running = Boolean(job.generation_running);
  return (
    <div className="resume-dialog-backdrop">
      <section
        className="card resume-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="resume-dialog-title"
        aria-describedby="resume-dialog-description"
      >
        <div>
          <span className={`badge ${running ? "yellow" : "green"}`}>
            {running ? "Run is still active" : "Saved run found"}
          </span>
          <h2 id="resume-dialog-title" ref={headingRef} tabIndex={-1}>
            {running ? "Generation is already running" : "Resume this concept run?"}
          </h2>
          <p id="resume-dialog-description" className="muted">
            {running
              ? "Aegis is polling this run. It will not start a duplicate or "
                + "make another billable generation request."
              : "Resume restores the source and saved chapter selection. It "
                + "does not call OpenAI; generation starts only after you "
                + "review the destination and click the generation button."}
          </p>
        </div>
        <dl className="resume-details">
          <div>
            <dt>File</dt>
            <dd>{job.filename}</dd>
          </div>
          <div>
            <dt>Learning flow</dt>
            <dd>{job.learning_kind === "pre" ? "Pre Learning" : "Post Learning"}</dd>
          </div>
          <div>
            <dt>Saved stage</dt>
            <dd>{formatCheckpointStage(job.checkpoint_stage)}</dd>
          </div>
          <div>
            <dt>Progress</dt>
            <dd>{Math.round((job.checkpoint_progress ?? 0) * 100)}%</dd>
          </div>
          <div>
            <dt>Saved</dt>
            <dd>{formatCheckpointTime(job.checkpoint_saved_at)}</dd>
          </div>
          <div>
            <dt>Target</dt>
            <dd>{formatCheckpointTarget(job.checkpoint_target_identity)}</dd>
          </div>
        </dl>
        {error && <div className="error-box">{error}</div>}
        <div className="row resume-actions">
          {!running && (
            <>
              <button type="button" disabled={busy} onClick={onResume}>
                Resume
              </button>
              <button
                className="ghost"
                type="button"
                disabled={busy}
                onClick={onDiscard}
              >
                {busy ? "Discarding…" : "Discard"}
              </button>
            </>
          )}
          {running && (
            <button type="button" disabled>
              Run is still active
            </button>
          )}
          <button
            className="ghost"
            type="button"
            disabled={busy}
            onClick={onKeep}
          >
            Keep for later
          </button>
        </div>
      </section>
    </div>
  );
}

function checkpointTime(job: ResumableCheckpoint): string {
  return job.checkpoint_saved_at || job.created_at || "";
}

function resumableCheckpointFromJob(job: UploadJob): ResumableCheckpoint {
  return {
    id: job.id,
    module: job.module,
    learning_kind: job.learning_kind,
    filename: job.filename,
    status: job.status,
    checkpoint_available: Boolean(job.checkpoint_available),
    checkpoint_stage: job.checkpoint_stage,
    checkpoint_saved_at: job.checkpoint_saved_at,
    checkpoint_progress: job.checkpoint_progress,
    checkpoint_target_identity: job.checkpoint_target_identity,
    generation_running: Boolean(job.generation_running),
    created_at: job.created_at,
  };
}

function checkpointTargetIdentity(
  job: UploadJob,
): Record<string, string> | undefined {
  const identity = job.checkpoint_target_identity;
  return job.checkpoint_available
    && identity
    && Object.keys(identity).length > 0
    ? identity
    : undefined;
}

function checkpointPromptKey(
  ownerKey: string,
  job: ResumableCheckpoint,
): string {
  const revision = checkpointTime(job) || job.checkpoint_stage || "checkpoint";
  return [
    "aegis-resume-prompt",
    encodeURIComponent(ownerKey),
    job.learning_kind || "concepts",
    String(job.id),
    encodeURIComponent(revision),
  ].join(":");
}

function safeSessionGet(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSessionSet(key: string): void {
  try {
    window.sessionStorage.setItem(key, "acknowledged");
  } catch {
    // The prompt still behaves once for the mounted page when storage is blocked.
  }
}

function acknowledgeCheckpointPrompt(
  ownerKey: string,
  job: ResumableCheckpoint,
) {
  safeSessionSet(checkpointPromptKey(ownerKey, job));
}

function formatCheckpointStage(stage?: string): string {
  return (stage || "saved stage").replace(/_/g, " ");
}

function formatCheckpointTime(value?: string): string {
  if (!value) return "Time unavailable";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString();
}

function formatCheckpointTarget(
  identity?: Record<string, string>,
): string {
  if (!identity) return "Select the matching chapter";
  const fields = [
    "board",
    "grade",
    "subject",
    "unit",
    "chapter_title",
    "chapter_code",
  ];
  const values = fields
    .map((field) => identity[field]?.trim())
    .filter((value): value is string => Boolean(value));
  return values.length ? values.join(" / ") : "Select the matching chapter";
}

function formatGenerationError(error: unknown): string {
  const message = String(error);
  if (
    /\b409\b/.test(message)
    || /already (?:running|in progress)/i.test(message)
    || /generation .* in progress/i.test(message)
  ) {
    return "This run is already active in another tab or laptop. Aegis did "
      + "not start a duplicate. Wait for it to finish, then refresh the job.";
  }
  return message;
}

function ConceptResult({
  result,
  filename,
  resumed = false,
}: {
  result: Record<string, unknown>;
  filename?: string;
  resumed?: boolean;
}) {
  const ids = (result.concept_ids as number[] | undefined) ?? [];
  const jobId = result.job_id as number | undefined;
  const inventoryItems = (result.inventory_items as number | undefined) ?? 0;
  const usage = result.openai_usage as OpenAIUsage | undefined;
  return (
    <div className="card success-card" style={{ marginTop: 16 }}>
      <strong>Concepts written to the Bulk Import workbook (append-only)</strong>
      <ApiUsageSummary
        usage={usage}
        filename={filename}
        fileLabel="Source file"
        cumulative={jobId != null || Boolean(filename)}
        resumed={resumed}
      />
      <pre className="mono" style={{ marginTop: 8 }}>{JSON.stringify(result, null, 2)}</pre>
      <div className="row" style={{ marginTop: 12 }}>
        {ids.length > 0 && (
          <a href={api.exportConceptsUrl(ids)}>
            <button>⬇ Download Excel (Bulk Import)</button>
          </a>
        )}
        {jobId != null && inventoryItems > 0 && (
          <a href={api.inventoryCsvUrl(jobId)}>
            <button className="ghost">⬇ Question/Task Inventory (CSV)</button>
          </a>
        )}
        <span className="muted">
          {ids.length > 0
            ? `${ids.length} concept(s) in the canonical Bulk Import format.` +
              (inventoryItems > 0
                ? ` ${inventoryItems} extracted question(s)/task(s) in the inventory CSV.`
                : "")
            : "Download the full output workbook from the Database tab."}
        </span>
      </div>
    </div>
  );
}
