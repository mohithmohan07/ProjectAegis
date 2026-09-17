# PDF source engine

Owner request, 17 September 2026: redesign PDF reading, sourcing and conversion
from first principles. This document records the new design and its integration
boundaries. Existing conversion output is not the design baseline or a quality
benchmark.

## Decision

Use a page/region evidence model as the source authority. Markdown and workbook
rich text are projections. Preserve the original PDF, rendered pages, native
positioned text, model transcriptions, independent audits and explicit document
relationships. Construct Aegis's canonical source directly from those recorded
relationships, without rendering Markdown and parsing it back into source
identities.

The initial adapter uses the configured multimodal API, with fresh extraction,
verification and assembly prompts. Provider selection and paid transport remain
explicit. A replaceable provider interface permits a dedicated OCR adapter to be
evaluated later without changing document identities or downstream contracts.

## Evidence and extraction

1. Hash and retain original PDF bytes. Reject an unreadable or encrypted container
   before model work with a specific physical-file diagnostic.
2. Render each page, retaining its MediaBox, CropBox, rotation and the transforms
   between native PDF coordinates and displayed-page coordinates. Keep complete
   native text spans as supporting evidence; do not assume a PDF text layer has
   correct reading order, equations or characters.
3. Transcribe all page regions through the model into ordered, typed blocks with
   exact text, mathematical expressions, table cells/spans, figures and normalized
   source boxes. Semantic roles and relationships are model decisions. Mechanical
   validation checks schema, identities, finite geometry and reference integrity.
4. Independently audit each transcription against the whole page, including areas
   outside proposed regions. Persist the candidate before requesting its audit.
   Bind the audit to the exact candidate and evidence hashes. Record uncertainty;
   a second model pass is a check, not independent ground truth.
   Attach private crops of model-declared equations, tables, figures and uncertain
   regions so small details can be inspected alongside the complete page.
5. Assemble headings, questions, subparts, answers, shared context and cross-page
   continuations using explicit block IDs. Preserve every source block, including
   unassigned material, in the evidence ledger.
6. Compile canonical sections, blocks and questions directly from that structure.
   Project the consumer's existing QINV identities and source spans mechanically.
   Preserve the engine's original block and task IDs in provenance.

## Source correctness and presentation

Reading, document assembly and presentation have separate records. Unsupported
rich-text syntax must not turn a legible source into a corrupt-PDF diagnosis.
Preserve exact source content and retain an image of the specific source region
when its equation/table/rich-text projection cannot be rendered safely.

Full-page images and original PDFs stay behind authenticated artifact access.
Only intentionally selected source crops use the content-addressed public source
asset route. A crop is a copy of original evidence, never generated imagery.

Unreadable source material must remain explicit. No fallback may invent missing
text, silently discard a question, call incomplete extraction verified, or treat
an absent provider response as an empty page.

## Recovery and integration

- Save completed page evidence, author responses, audits and document decisions
  durably in separate versioned checkpoints. Reuse verified completed work.
- Cache keys bind the PDF/page evidence, schema, prompts and frozen provider
  policy. Pin that contract before the first model call; partial-run drift stops
  explicitly instead of abandoning a paid request. A changed candidate requires
  a new audit. Reuse the exact saved private crop bytes across encoder upgrades.
- Malformed page geometry/schema and broken document references receive one
  bounded repair with the precise mechanical diagnostic and full source, then
  an independent audit. This shares the existing repair budget; repeated failure
  is saved explicitly rather than opening an unbounded retry loop.
- Propagate provider waiting and deployment suspension as scheduling deferrals.
  Preserve the selected Batch transport, request identities and paid receipts.
- Fresh conversions select the new engine explicitly. Saved historical sources
  and sealed generation envelopes retain their recorded reader and identities.
  Deployment does not retry failed jobs or repurchase their provider work.
- The canonical loader, page evidence loader, source turnover and chapter reader
  recognize the new source contract. They must not regenerate its source from
  Markdown or silently invoke a different PDF reader.
- Preserve the full source structure, relationships and per-role task references
  in the frozen generation input. Text-only decisions receive exact transcription,
  mathematical notation and table cells separately from the public display view;
  visual grounding receives the intentionally published source crops.
- Commit a complete source manifest only after referenced artifacts are durable.
  Preserve partial work when an individual page or later presentation fails.

## Current research

Research was performed against current primary documentation. These capabilities
do not establish a universal winner or an accuracy score on the user's textbooks.

| Option | Relevant capability | Decision |
| --- | --- | --- |
| OpenAI multimodal input | PDF inputs include page images and extracted text; vision documentation identifies limitations with small print, rotation and spatial localization. | Use explicit retained page evidence, complete native spans and verified regions through the configured model transport. |
| Mistral OCR 4.1 | Structured page blocks, geometry, equations, tables and extracted images through a dedicated OCR API. | Strong candidate for a later measured adapter comparison; not a mandatory new account/key dependency. |
| Google Document AI / Gemini | Managed layout/Math OCR and visual PDF understanding. | Alternative adapters, with their own credentials, processor versions and service limits. |
| Docling / Marker | Local structured parsing, layout and optional model enrichments. | Evaluate as separate workers if local OCR operation is required; model/runtime deployment is additional infrastructure. |

Primary references:

- [OpenAI file inputs](https://developers.openai.com/api/docs/guides/file-inputs)
- [OpenAI vision limitations](https://developers.openai.com/api/docs/guides/images-vision)
- [Mistral OCR 4.1](https://docs.mistral.ai/models/ocr-4-1)
- [Mistral OCR output](https://docs.mistral.ai/studio/document-processing/basic_ocr)
- [Google Enterprise Document OCR](https://docs.cloud.google.com/document-ai/docs/enterprise-document-ocr)
- [Google Layout Parser](https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk)
- [Gemini document processing](https://ai.google.dev/gemini-api/docs/document-processing)
- [Docling document model](https://docling-project.github.io/docling/concepts/docling_document/)
- [Marker](https://github.com/datalab-to/marker)

## Validation and limits

Original public NCERT Light (27 pages) and Electricity (24 pages) PDFs opened and
rendered successfully during mechanical validation. All 51 raster/native hashes
and 4,761 native spans were retained; a second pass reused all page renders.
They include vector
diagrams, equations, an 18-point CropBox offset and a circuit-symbol table spanning
pages. Successful rasterization proves that those files can be read physically;
it does not prove semantic extraction accuracy.

Use fresh synthetic PDFs for repeatable offline regression tests, and original
PDF pages for mechanical/visual checks. Do not commit copyrighted textbook bytes
or private source content. No paid generation is used as a deployment test.
Live extraction quality must be reported separately from offline contract tests;
do not invent a benchmark or guarantee perfect transcription.

Focused offline verification covers 26 engine cases, 17 physical-evidence cases,
11 conversion/adapter cases and four frozen-generation evidence cases. It includes
the actual installed upload conversion entry point, authenticated original/page
downloads, Batch suspension before and after repairs, immutable request/crop reuse,
full raw source transport, cross-page task membership, malformed display fallback,
and unchanged historical envelopes. The existing Settle golden suite also passes.
