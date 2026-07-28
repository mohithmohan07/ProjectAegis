# Aegis Canonical Source Phase 2.2

Phase 2.2 adds a bounded semantic adjudication layer after deterministic ACSD
validation and before concept generation.

## Contract

The immutable Mathpix MMD remains the audit source. Regex and deterministic
parsers continue to own source order, block offsets, QIDs, Figure identifiers,
image URLs, KaTeX rendering, and exact-once inventory coverage.

OpenAI is called only when those gates identify an eligible unresolved source
issue, currently:

- a numbered subsection whose parent heading is missing;
- a gap in an established numbered main-section sequence;
- a Figure enclosed by a source task boundary but owned by no compatible task.

## Evidence packet

Each issue becomes a small packet containing:

- the issue code and source offsets;
- nearby ACSD block IDs and bounded text excerpts;
- the exact list of permitted insertion anchors;
- Figure caption and image metadata where relevant;
- at most three candidate pages rendered from the original uploaded PDF/image.

The complete chapter is never sent for open-ended rewriting.

## Two-pass verification

A source transcription call and an independent verification call must agree on
visible text and page number. The recovered text must be visibly present in the
original document, retain source punctuation/numbering, satisfy a high
confidence threshold, and use an allowed insertion anchor. When the PDF has no
usable text layer, the stricter no-text confidence threshold applies.

A Figure caption is never accepted as proof of an omitted question.

## Repairs

Accepted repairs are stored as canonical overlays with permanent provenance:

- issue and repair IDs;
- original-document hash and page number;
- model and adjudication version;
- extraction and verification confidence;
- cache key and recovered source text;
- an explicit `raw_mmd_changed: false` declaration.

A missing heading creates a virtual canonical parent section and a semantic-MMD
heading overlay. A recovered Figure task creates one canonical task, preserves
source order, and takes ownership of the existing Figure. The raw MMD artifact
is never rewritten.

After every repair, Aegis reruns all Phase 2/2.1 gates. Concept generation starts
only when the complete source contract passes.

## Caching and cost

Verified and review-required verdicts are cached by raw-source hash, original
file hash, issue fingerprint, model, and adjudication version. Clean sources
make zero source-adjudication calls. A repeated run of the same unresolved source
does not repeatedly spend tokens on the same evidence packet.

## Semantic extraction

When verified overlays exist, concept extraction and deposit validation consume
a derived semantic source assembled from immutable raw MMD plus those overlays.
The upload's raw MMD and downloadable `source.raw.mmd` remain unchanged.
