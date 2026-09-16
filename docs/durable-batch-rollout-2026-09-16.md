# Selected chapter batches and durable recovery

Owner request: 16 September 2026. Implements the PR/Corrections 2.0 audit.

## Operator workflow

1. Choose the exact current catalogue chapter and stage its source PDF. Uploading does not convert or generate it.
2. Select one or more staged chapters in Chapters. Run Step 01 with Batch API now or at a scheduled time. Every source/model call follows the chosen transport.
3. Download and review the Concept files. Upload corrected files, select ready chapters and batch Step 02. Master publication retains its existing explicit review gate.
4. Use Dashboard for distinct chapter/run counts, current stages, per-run and total recorded costs, receipt delivery modes, recovery and notification status.

Advanced legacy synchronous controls are explicit and display standard API pricing. A batch failure never authorizes an automatic synchronous charge. Batch queue latency is provider-controlled; higher chapter concurrency does not guarantee lower end-to-end latency.

## Deployment and paid work

The deployment workflow takes and verifies an online SQLite backup before replacing the image. The source, checkpoint, decision store and batch journal remain on the mounted volume. An additive schema migration retains old IDs and work. Shutdown requests a cooperative pause at the next provider boundary and drains the chapter queue; Fly receives SIGTERM with a 120-second grace period. A hard stop is recovered through persisted lease expiry rather than starting competing work under an unexpired lease.

An active pre-upgrade interactive run is adopted into the queue only from its exact saved target and lifecycle. Ambiguous/missing targets or a newer source binding are recorded for attention; no title matching or replacement of newer work occurs. Review-ready, complete, explicitly failed and non-resumable work is not silently restarted. Old runs keep their frozen semantic policies and transport: a legacy direct synchronous run is not forced into Batch or switched away from its saved provider route. New batch tasks retain their recorded cohort.

Resume means the latest durable checkpoint/settled decision, not a preserved Python stack. Completed source conversions and known provider batches are reused. A pre-upgrade synchronous call whose response was not saved before a hard stop cannot be guaranteed recoverable. An uncertain provider batch creation is reconciled by wave identity; no speculative resubmission buys it twice.

A provider-completed paid line is archived before exposing its response, with a stable receipt ID and original job attribution when known. Dashboard accounting deduplicates known receipts and reports unresolved/unallocated costs explicitly; it is an estimate based on recorded usage, not the provider invoice.

## Capacity

`AEGIS_QUEUE_COHORT_CONCURRENCY` defaults to 6. `AEGIS_QUEUE_COHORT_MASTERS` defaults to 2 because each Master run retains two workbook lanes. The existing standard API gate and its separate two-run limit apply to explicitly synchronous work. UI capacity reports the selected batch ceiling and Master ceiling separately. Increase the Master ceiling only with measured memory headroom; concurrent provider waves can progress independently.

## Email configuration

The notification outbox records Concept-ready, Master-ready, publication and final failure outcomes. Its recipient is the verified authenticated first starter, independent of uploader and later reviewers/retry actors. Deployment/provider waiting sends no failure email.

Runtime secrets/settings:

- `AEGIS_SMTP_HOST` and `AEGIS_NOTIFICATION_FROM` (required)
- `AEGIS_SMTP_USER` and `AEGIS_SMTP_PASSWORD` if the sender requires login
- `AEGIS_SMTP_SECURITY=starttls` (default) or `ssl`
- `AEGIS_SMTP_PORT` (default 587 for STARTTLS or 465 for SSL)

Google Workspace/Gmail recipients need no application connection; the server needs an authorized sender. Store its credentials as hosting secrets. No credentials are committed. Until configured, events remain pending and Dashboard says the sender is not configured. Safe pre-send connection failures retry with backoff. An ambiguous SMTP acceptance is shown as delivery unknown and not automatically resent. Delivery never changes the chapter outcome. No paid chapter is run merely to test email.

## Catalogue and output

The supplied CBSE workbook defines 294 named chapters, including nine Grade 9 Social Studies chapters. Blank names are not invented. Source ordering is preserved. Older IDs, jobs and publications remain accessible in History; old runtime workbook copies cannot silently union with the current catalogue. An explicit future syllabus upload registers the active revision.

Generation-quality v5 enables Corrections 2.0 changes for new runs. Content placement, captions and misconception correctness remain model decisions; deterministic work only preserves declared identities, exact references and structural contracts. Concept and Master serializers use the same frozen keyword policy. Valid KaTeX geometry is not rejected by lexical prose guesses. Newly submitted reviewed-image answers require per-span independent source evidence; accepted historical artifacts replay under their recorded policy.
