# Image-only publication for manually authored chapters

This workflow uses the existing GitHub Actions `FLY_API_TOKEN` secret in place.
It never retrieves or prints that secret. It does not deploy, start a machine,
run generation, read or write the database, restart jobs, or publish workbooks.

Authoring, crop selection, alt text and concept placement remain model-owned
decisions. The script validates only declared bytes and transport properties.

## Reviewed bundle

Use a folder under `ops/reviewed-source-assets/`. It contains a JSON manifest
and the declared JPEG files, named by their exact SHA-256. Only publishable
reviewed crops belong in this folder; never put source PDFs, credentials,
private documents or unrelated images there. Repository visibility applies.

The manifest has `schema_version: 1` and an `assets` array. Every asset carries
`filename`, `sha256`, integer `bytes`, `media_type: image/jpeg`, and a non-empty
`provenance` object recording source file/hash, page and crop bounds.

Run `python backend/scripts/publish_source_assets.py --manifest PATH --validate-only`
for local byte checks. The remote installation also decodes every image before
writing any asset. To publish without merging to the auto-deploying `main`,
commit the reviewed bundle to `ops/reviewed-source-assets/current/` on the exact
branch `codex/manual-source-asset-publisher`. Only changes in that folder on
that branch trigger publication; its manifest is `current/manifest.json`.
Other branches, PR events and code-only changes cannot trigger publication.
Once registered, the workflow also supports manual dispatch with a
repository-relative manifest path, restricted to the same branch. It accepts
no URL or shell-command input. Do not merge this PR as a publication step.

GitHub documents branch-local push workflows and the default-branch constraint
on first manual dispatch at
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows.

## Storage and verification

The uploader refuses an ambiguous running-machine topology. It pins the selected
started machine with the `/data` mount for both transfers and commands. Files are
atomically created in `/data/source-asset-store/`; identical files are reused,
existing conflicting files are never overwritten, and first-mint sidecars stay.

Manual assets use the explicit `/source-assets/0/<sha256>.jpg` namespace. Zero is
not an upload job: the existing public route serves verified bytes from the
durable store without requiring a database record. The sidecar identifies the
origin as `manual-chatgpt-authoring` and retains source provenance.

The final anonymous GET must return HTTP 200, the exact requested URL, JPEG MIME,
the declared Content-Length and identical SHA-256 bytes. Verified URLs are
returned in an Actions artifact. A failure is not a publication certificate.
Temporary transfer files alone are removed after the attempt; published assets
are retained. No paid/model calls occur in validation or publication.

Fly command references: https://fly.io/docs/flyctl/ssh-sftp-put/ and
https://fly.io/docs/flyctl/ssh-console/.
