# Aegis Canonical Source Phase 2.2.1

Phase 2.2.1 contains two source-quality lanes for Build Concepts.

## 1. Bounded source adjudication

Phase 2.2 still detects a small set of source gaps after Mathpix conversion, but
2.2.1 replaces model-selected PDF page numbers with opaque evidence IDs such as
`EVIDENCE-PAGE-A`. Extraction uses strict JSON Schema enums for evidence IDs and
ACSD insertion anchors. The independent verifier receives one frozen page and
verifies only the exact transcription. Protocol violations receive one bounded
retry. Only fully verified decisions are cached.

The immutable Mathpix MMD remains unchanged. Accepted repairs are source-visible
ACSD overlays with PDF page, model, confidence, source hash, cache and verifier
provenance.

## 2. GPT PDF-to-ACSD fallback

SUPERSEDED IN CODE: Mathpix was removed entirely (`app/services/mmd.py`
raises for every PDF). The GPT PDF-to-ACSD reader is now the ONLY PDF
converter — the Build Concepts convert step for a PDF *is* this reader
(`canonical_source_phase221_contract` short-circuits the generic convert),
and the MMD it emits is a deterministic rendering of the reader's verified
output, serving as the identity/audit key downstream. There is no second
converter to compare against and therefore no quality gate choosing
between converters. Isolated heading/task or Figure gaps still route
through the cheaper bounded-adjudication lane at generate time.

The fallback pipeline is:

1. Render original PDF pages with PyMuPDF.
2. Extract strict page/block JSON in small ordered batches.
3. Independently verify each batch against the same page images.
4. Reject low-confidence, incomplete, reordered or invented content.
5. Crop verified source Figures into content-addressed Aegis assets.
6. Render deterministic parser-safe MMD from the verified page ACSD.
7. Compile and validate the result through the normal Phase 2 and 2.1 gates.
8. Publish the replacement bundle atomically only after every gate passes.

GPT does not write free-form MMD, choose QIDs, reorder pages, invent image URLs,
or bypass canonical validation. Publisher- and language-specific task cues are
preserved in page ACSD while the derived MMD uses parser-stable structural cues.
Explicit cross-page Figure references and resolved shared context survive the
page-local relationship pass.

## Preserved artifacts

A verified fallback stores:

- `source.gpt-page-acsd.json`, the page/block extraction and verification ledger;
- `source.raw.mmd`, deterministic MMD rendered from verified page ACSD;
- normal canonical JSON, derived Aegis MMD and source validation report;
- immutable signed source-image crops under the upload's canonical-source assets.

## Configuration

- `AEGIS_SOURCE_ADJUDICATION_ENABLED` defaults to `1`.
- `AEGIS_GPT_PDF_ACSD_FALLBACK_ENABLED` defaults to `1`.
- `AEGIS_GPT_PDF_ACSD_FALLBACK_FORCE` defaults to `0` and is for controlled tests.
- `AEGIS_GPT_PDF_ACSD_BATCH_PAGES` defaults to `3`, capped at `4`.
- `AEGIS_GPT_PDF_ACSD_MAX_PAGES` defaults to `120`.
- `AEGIS_GPT_PDF_ACSD_MIN_CONFIDENCE` defaults to `0.96`.
- `AEGIS_GPT_PDF_ACSD_MIN_TEXT_COVERAGE` defaults to `0.45` and is used only
  when the original PDF exposes a substantial text layer.
- `AEGIS_PUBLIC_BASE_URL` must be the public HTTPS Aegis origin in hosted use.
- `AEGIS_SOURCE_ASSET_SECRET` may be set as a dedicated stable signing secret;
  otherwise Aegis uses its existing session/admin secret chain.

## Fail-closed contract

A fallback extraction is never deposited merely because both model calls
completed. It must also pass deterministic task identity, task order, heading
hierarchy, Figure ownership, image rendering, KaTeX/rich-text and source
inventory gates. Failed fallback output remains staged and never replaces the
previous source bundle. When Mathpix has already been classified as broadly
unusable and the fallback also fails, the existing source report is marked
`review_required` with a blocking issue, so generation cannot quietly continue
on the rejected Mathpix source. Billable fallback usage is retained on failure.

The verified page ledger is durable. If a future ACSD compiler version rebuilds
the core canonical files, Aegis rehydrates task locations, visual ownership and
shared context from `source.gpt-page-acsd.json` without another model call.
