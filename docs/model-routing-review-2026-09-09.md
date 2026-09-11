> Current policy (Q50, 11 September 2026): every stage in new runs and new
> reviewed-file revisions uses `gpt-5.6-luna`, with the existing stage reasoning
> efforts. Version 3 supersedes the mini-only v2 policy below; recorded v1/v2
> profiles remain valid for historical replay.

# Q40 implementation and verification — 9 September 2026

**11 September update:** [Q46](aegis-restructure.md#q46--decided--gpt-54-mini-for-every-model-stage)
supersedes the model assignments below for new runs: every stage now uses
`gpt-5.4-mini`, retaining the stage reasoning efforts, complete evidence and
all review stages. The mixed routing described here remains the historical
v1 contract for already sealed runs.

New runs use the owner's stage model choices and adaptive Pre coverage. The
production API credentials already exist according to the owner. All work in
this review is local; no production generation, push, merge, deployment,
restart or running-job mutation was performed.

## Model workflow

| Stage | Provider / model | Requested reasoning |
| --- | --- | --- |
| Pre question authoring only | Gemini / `gemini-3.8-flash` | high |
| Concept writing, Concept polish/refinement, topology, semantic adjudication, coverage planning and Fixer | OpenAI / `gpt-5.6-luna` | xhigh |
| Narrow validation, transcription and outline | OpenAI / `gpt-5.4-mini` | high |
| Independent advisory reviews | OpenAI / `gpt-5.4-mini` | medium |
| Metadata | OpenAI / `gpt-5.4-mini` | low |

The complete request moves to Luna when mini cannot safely accommodate the
complete input or its visual evidence. No source truncation is introduced.
Every existing author, independent critic, Refiner and group-QA stage remains.
Model allocation follows declared stage/purpose, never a semantic keyword rule.

Google's supported OpenAI compatibility endpoint is
`https://generativelanguage.googleapis.com/v1beta/openai/`. Only the exact
`prequestions.author` stage with the Pre-learning purpose can select Gemini.
Its client receives `GEMINI_API_KEY`; OpenAI stages use `OPENAI_API_KEY`.
Credentials are selected per call and never sent to the browser or written into
artifacts. Global provider switching is disabled; the UI shows stage assignments
and configuration readiness.

The new Pre author sends a fixed JSON schema and validates it locally. Gemini
uses its supported thinking setting and output limit, without temperature,
top-p/top-k or OpenAI-only prompt-cache fields. Existing bounded retries,
cancellation, token accounting and error handling remain; refusal, blocked
content, malformed responses, missing credentials and truncation cannot silently
switch the request to another provider. Queue diagnostics name the actual
request's provider. A response is accounted for before parsing/retry decisions.

The transport profile is `owner-stage-model-routing-2026-09-09-v1`. It is frozen
per uploaded-file identity before conversion/spend, then included in fresh
source/Architect cache identities and the Phase 3 envelope seal. Context-local
binding and copied worker contexts isolate concurrent runs. PDF conversion and
the complete installed text conversion path bind the profile. Its record sits
outside the disposable canonical-shadow directory. Portable checkpoint/Drive
exports and restores carry the profile; old bundles without one preserve their
historical routing path. An explicit historical binding adds no new key fields.

The API models/settings were checked against current primary documentation:
[Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash),
[Google's OpenAI compatibility API](https://ai.google.dev/gemini-api/docs/openai),
[GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), and
[GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini).
Account-specific production availability still needs a live check.

## Coverage, topic naming and source boundaries

Fresh envelopes explicitly record `pre-coverage-adaptive-2026-09-09-v1` with
`mode: adaptive`. An API author chooses the total and tier split from each
retained prerequisite's actual mastery and prior-grade scope. No default count,
5+5 quota, balanced tiers, mandatory tier occupancy, length-based count or
padding remains for new work. The author, independent critic and release QC
use the accepted contextual plan. Counts reconcile mechanically to that plan.
Historical envelopes retain their recorded fixed quota or legacy policy.

Gemini receives prerequisite evidence and the accepted coverage plan. Current
source questions are excluded from its author payload. Prompts and the
independent review explicitly reject current-chapter teaching, paraphrased
current questions, redundant diagnostics and scope expansion. This preserves
Q39's prior-only boundary. Post questions remain source-only.

Final English topic naming remains `Detailed Analysis of '<actual work>'`, with
whole-work questions owned by their applicable characterisation/theme/language/
setting or other supported lens. Tests reject generic Analysis, generic Detailed
Analysis, chapter placeholders and the wrong work title. No new literary lens
or source content is manufactured by this transport change.

## Image and table evidence

The shared image token parser now handles a closing bracket inside a quoted alt
caption without ending the tag prematurely. Concept refinement, Master protected
answer identity and source/example cleanup use the same parser. This preserves
the complete URL, caption and image identity rather than leaking the tag tail
into prose or losing protected evidence.

A local historical English Concept/Master pair contains three matching Fly
image URL tags. Recorded concept placements include “A Loving Nest in the
Cornfield”, “Culmination: Self-Help Makes the Right Time Clear” and
“Plot/Development of Ideas”. This establishes stored ownership and transport in
those artifacts. It does not establish pixel-level relevance in a new run or
that the current public Fly endpoints deliver those pixels. The production
verification remains pending read-only access.

The exact supplied Council of Ministers/Cabinet comparison was checked through
both final workbook writers and the real KaTeX 0.18.7 engine. Its complete 7×2
cell content uses the centered `\begin{array}{|c|c|}` form with `\hline`,
`\text{}` cells and the `[Katex]...[/Katex]` wrapper. Multipart question
children remain attached. No content or civics claim was changed.

## Individual and cumulative charges in INR

The provider USD ledger remains authoritative for the estimate. Each response
records the actual provider/model, reported token evidence, effective pricing
policy and a frozen INR conversion receipt. Gemini output/thinking tokens are
reconciled against reported totals and billed once, with original fields kept
for review. Unsupported/unknown pricing and missing usage remain explicit.

Gemini 3.8 Flash standard rates through 31 December 2026 are $0.75 per million
input tokens, $0.075 cached input and $3.75 output including thinking. From
1 January 2027 these become $1.50 / $0.15 / $7.50. Explicit cache storage is a
separate time-based charge and is not invented from a generation receipt.
Complete configured overrides remain available. These rates come from
[Google's pricing page](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.8-flash).

INR display uses a dated ECB reference quote or an explicit configured quote.
The code reads the official daily XML with a short timeout and durable cache.
If refresh fails, it keeps the last verified observation date visible. The
bundled dated fallback uses the ECB's 8 September 2026 observations:
[EUR/USD 1.1614](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/eurofxref-graph-usd.en.html)
and [EUR/INR 110.1315](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/eurofxref-graph-inr.en.html).
Their ratio is an INR-per-USD reference estimate. Operators may set
`AEGIS_USD_TO_INR_RATE` and `AEGIS_USD_TO_INR_AS_OF` instead.

The run journal logs each completed request's INR estimate and the cumulative
INR total. Live usage events carry a compact latest-request receipt; final
panels expose individual requests, models, stages and lanes. Resumed/imported
runs sum historical INR receipts without repricing them. Old USD-only history
is shown as incomplete INR with the known subtotal and USD evidence available;
it is never mislabelled as rupees or silently counted as zero. Failed requests
and missing usage cannot make an incomplete bill appear final. These are
reference estimates, not a tax invoice or card-settlement amount.

## Validation

Focused checks cover routing concurrency, installed conversion, explicit
Gemini-only stage/purpose selection, strict-schema transport, retry/accounting
behavior, model-capacity fallback, adaptive and historical coverage, source and
Architect cache replay, portable checkpoint restoration, exact table rendering,
image ownership/identity and INR persistence/display. Validation results:

- Full backend run: 4,281 cases, 4,270 passed, 11 failed, no errors or skips.
  Ten failures were existing tests using backend-relative file paths while the
  run was launched from the repository root. One synthetic historical-recovery
  fixture seeded its Architect cache under the new profile; its seed now binds
  the same historical profile as the resumed job. Its no-API and invalid-final
  recovery assertions remain intact.
- Follow-up run from the backend directory: 164 passed. It includes all eleven
  earlier failures, full affected release/renderer groups, the final routing
  override checks, portable profile tests and Gemini/INR accounting checks.
  No production change was needed for the directory-related failures.
- Installed PDF/text conversion and profile-persistence checks: 16 passed.
- Full frontend suite: 152 passed across 18 files. TypeScript/Vite production
  build succeeded. Existing React Router and bundle-size warnings remain.
- Additional focused coverage, source/cache/Architect, checkpoint/Drive,
  image/table and currency suites passed; these overlap the full run and are
  not added to its test total. The entire backend suite was not repeated after
  the fixture correction; the affected cases were verified directly.

The authoritative backend reports are `q40-full.xml` and `q40-followup.xml` in
this session's verification workspace. The exact screenshot comparison was
rendered with the installed KaTeX engine and read back from both workbooks.
`git diff --check` is clean.

No live Gemini call or current Fly image/log check was possible from the local
environment. The owner has offered read-only Fly access for that verification.
No additional API key setup is assumed necessary in production.
