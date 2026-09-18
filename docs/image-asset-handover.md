# Handover — how Aegis stores images and puts their URLs in content

For an external workflow that uses this repository and needs to put pictures on
the Aegis server and reference them from generated content.

## The one-paragraph version

There is **no image upload endpoint**. Images enter in-process through a few
producers, and every one of them ends at `source_asset_store.pin_asset`, which
writes the JPEG under its own SHA-256 into a content-addressed directory on the
data volume. The URL is then derived from that hash, and the URL goes into
content as a **text tag**, `[img src="https://..." alt="..."]`, never as an
embedded picture.

## 1. Where the bytes live

```
$AEGIS_DATA_DIR/source-asset-store/<sha256>.jpg     # the image
$AEGIS_DATA_DIR/source-asset-store/<sha256>.json    # small provenance sidecar
```

On Fly this is `/data/source-asset-store/` (volume `aegis_data`, see `fly.toml`).

- The filename must match `^[0-9a-f]{64}\.jpg$`. That hash **is** the identity.
- Only JPEG is stored. Same bytes from two jobs mean one file.
- The sidecar records first-mint provenance (job id, minted URL, size, time).
  It is context, not identity, and the serve path does not need it.
- Writes are atomic (temp file plus `os.replace`), and `pin_asset` is idempotent.
- A data reset deliberately **preserves** this directory, because published URLs
  depend on it. It survives because the store lives outside `UPLOAD_DIR`, which is
  what the reset clears (`backend/tests/test_data_reset_durable_assets.py`).

Code: `backend/app/services/source_asset_store.py`

## 2. The URL

```
{AEGIS_PUBLIC_BASE_URL}/source-assets/{job_id}/{sha256}.jpg?sig={40 hex}
```

- Minted by `asset_url(job_id, filename)` in
  `backend/app/services/canonical_source_phase221_fallback.py`.
- `sig` is `HMAC-SHA256(secret, "{job_id}:{filename}")` truncated to 40 hex chars,
  keyed by `AEGIS_SOURCE_ASSET_SECRET`.
- **The signature is advisory.** The serving route accepts the URL with a wrong
  `sig` or with none at all. This is deliberate, so rotating a secret cannot kill
  links that are already published.
- The `{job_id}` segment is provenance. The store lookup uses only the hash, so
  any integer works there.

## 3. Serving

`GET /source-assets/{job_id}/{filename}` in `backend/app/api/source_assets.py`.

1. Look in that job's artifact directory first.
2. Fall back to the durable store.
3. **Re-hash the file before every response.** If the bytes do not hash to the
   filename, refuse with 404 rather than serve wrong bytes, because the response
   carries `Cache-Control: public, max-age=31536000, immutable`.
4. Serving a job-directory copy that is not yet in the store pins it opportunistically.

Media type is always `image/jpeg`.

## 4. Who produces assets today

| Producer | File | What it does |
| --- | --- | --- |
| PDF figure and full-table crops | `canonical_source_phase221_fallback.py` → `materialize_visual_assets` | Renders a model-declared bbox at 2x with PyMuPDF, JPEG q88, writes to the job `assets/` dir **and** pins to the store |
| PDF source regions (Q79 engine) | `pdf_source_evidence.py` → `crop_region` | Crops a retained page raster, JPEG q95, pins to the store; raises `source_asset_origin_missing` when no public origin is configured |
| Reviewer-pasted pictures | `reviewed_file_input.py` → `pin_image` | Pulls every embedded picture out of an uploaded workbook or document and pins it, cited or not |
| Stitched question image banks | `question_image_grid.py` → `_publish` | Combines a multi-image question into one figure and pins the result |

`pdf_source_evidence.region_input` is different: it makes a **private** audit
crop that is never published as a source asset.

## 5. JPEG normalisation

`reviewed_file_input.store_jpeg` is the mechanical normaliser:

- An RGB or greyscale (`L`) JPEG passes through **byte for byte**, so the same
  picture keeps one identity and is not re-encoded on every read.
- `RGBA`, `LA` and `P` are composited onto **white**, not the black a bare `RGB`
  conversion gives, which would erase a line drawing.
- Anything else (CMYK, PNG, BMP, TIFF) converts to RGB JPEG at quality 95.

## 6. The tag that goes into content

Composed only by `katex_rules.image(src, alt)`:

```
[img src="https://..." alt="..."]
```

- HTTPS is required. HTTP and relative paths are rejected.
- Alt text is required, whitespace-collapsed, HTML-escaped, and `[` and `]`
  become `&#91;` and `&#93;` so the tag stays one token.
- `src` then `alt`, double quotes, single space. That exact order is what
  `_CANONICAL_IMAGE_TAG_RE` accepts.
- A typed `Image` answer cell carries the **bare URL only**, no tag.
- This is governing-contract §35 in `docs/aegis-master-governing-contract-v2.md`.

Where it is written:

- A figure block's `asset_url` becomes a tag in the canonical source text the
  authors read (`canonical_source_phase3.py`).
- The generation prompts then tell the model to copy that exact tag into concept
  prose and questions. **Placement is the model's judgment**, never code's.
- For a reviewed file, each picture's ref maps to its pinned URL mechanically.

## 7. Two gates an external URL has to pass

**Model vision input** — `assessment_visual_evidence.py` never fetches a URL. It
parses it, accepts only the configured origin plus the exact `/source-assets/`
route with an empty query or a single 40-hex `sig`, reads the bytes back **from
the local store by hash**, and sends base64 to the provider. A URL on any other
host gets `not_an_authorized_pinned_source_asset` and the model never sees the
picture.

**Release verification** — `source_asset_publication.inspect_assets` probes each
exported URL at the configured origin over HTTPS and checks status, MIME, byte
size, hash and JPEG decode. Anything not verified produces
`source_asset_publication_unverified`, which blocks **database publication**.
The staged workbooks stay downloadable. A foreign-host URL can never reach
`verified`, so it blocks publication.

Both gates mean: an image hosted anywhere other than this deployment's own
`/source-assets/` route is effectively unusable downstream.

## 8. Doing this from your external workflow

### Option A — in process, same repo (recommended)

```python
from app.services.reviewed_file_input import pin_image
from app.services import katex_rules

pinned = pin_image(raw_bytes, job_id=1)        # normalise + hash + store + mint
tag = katex_rules.image(pinned["asset_url"], "Figure 7.7, a labelled circuit")
# -> [img src="https://projectaegis.fly.dev/source-assets/1/<sha>.jpg?sig=..." alt="..."]
```

Needs `AEGIS_DATA_DIR` pointing at the same volume and `AEGIS_PUBLIC_BASE_URL`
set. Without a public origin the bytes are still pinned but `asset_url` comes
back empty.

For bytes that are already a normalised JPEG, call
`source_asset_store.pin_asset(data, job_id=..., asset_url=...)` directly.

### Option B — write to the volume directly

Write the JPEG to `/data/source-asset-store/<sha256 of the bytes>.jpg` and build
the URL yourself. The route serves it with or without a `sig`, and with or
without the sidecar.

Two things to respect:

- The bytes must already be JPEG and must hash to the filename, or the route
  refuses them.
- Use this deployment's own origin and exact route shape, or §7's two gates will
  reject the URL.

### What not to do

- Do not use a `data:` URI. It can never become a canonical tag, since the tag
  requires `https://`.
- Do not host on Drive, an expiring link or any external CDN. Contract §35
  prohibits it and the release gate enforces it.
- Do not embed a picture into an XLSX cell as an Excel drawing and expect it to
  flow through. Content carries the text tag; only a reviewed-file **upload**
  path extracts embedded pictures.

## 9. Configuration

| Variable | Purpose |
| --- | --- |
| `AEGIS_DATA_DIR` | Root of the volume; store lives at `<root>/source-asset-store` |
| `AEGIS_PUBLIC_BASE_URL` | Public HTTPS origin used to mint and verify URLs |
| `AEGIS_SOURCE_ASSET_SECRET` | HMAC key for `sig`; falls back to `AEGIS_SESSION_SECRET`, then a local literal |

Production values are in `fly.toml` (`AEGIS_DATA_DIR = '/data'`,
`AEGIS_PUBLIC_BASE_URL = 'https://projectaegis.fly.dev'`).

## 10. Operations

- **Boot backfill** — `pin_existing_job_assets()` runs at startup and pins any
  job-directory crop that predates the store. Best effort; a failure is logged,
  never a failed boot.
- **Export** — `GET /admin/source-asset-store/export` (`X-Admin-Token` header) downloads
  the whole store as `source-asset-store.tar.gz`. Unpack into a fresh volume's
  `/data` to make every published URL resolve again.
- **Backups** — Fly volume snapshots plus this store are what keep published
  learner image links alive. See the backup section of `README.md`.

## 11. If you want a real upload API

The smallest change that fits the existing design is one authenticated route
that accepts a file, calls `pin_image`, and returns `{sha256, asset_url}` (and
optionally the composed tag). Everything downstream already works, because the
URL shape and the store are unchanged. That route does not exist today.

## 12. Where to read more

- `docs/aegis-restructure.md` — Q8 (durable asset store), Q59 (every
  reviewed-file picture is uploaded and linked), Q79 (the PDF source engine)
- `docs/pdf-source-engine-2026-09-17.md` — the current PDF crop design
- `docs/aegis-master-governing-contract-v2.md` §35 — the binding image rules
- `README.md` — the volume, backups and the store export
