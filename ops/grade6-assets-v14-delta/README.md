# Grade 6 v1.4 live-asset delta

This is an additive, content-addressed import for exactly two JPEGs required by
the final Grade 6 six-chapter v1.4 release. It does not deploy application code,
rewrite workbooks, or delete any older durable asset.

## Reviewed payload

| Asset | Final JPEG SHA-256 | Bytes | Dimensions |
|---|---|---:|---:|
| `FR-ASSET-001` corrected full spinner | `d8fc15e13e1601d15045489b92b6661ddba41265d16f725318f7ee164b31526d` | 53,567 | 273 × 283 |
| `WD-PRE-AST-LIBRARY-ACTION` | `385d4a1b3cfdb770a4a8b52fb47d063d5197c7b98d7e90b05a0f6fa19bb9a16d` | 62,654 | 960 × 540 |

The deterministic archive is
`Project_Aegis_Grade6_Fly_Asset_Delta_v1.4.tar.gz` (101,719 bytes), SHA-256
`d5d8642732641e73e8ab44cd8f58007a48f0434d3a02aae4fc90543aa140a98c`.
The two-record migration manifest SHA-256 is
`85c37c720f89a3602770c9bfc38de3d56328b9bfdb6936d89d252e1ee4129051`.
The exact source `Asset_Manifest_v1.4.json` (29 assets; 27 release-used and 2
unused; 69 public-content occurrences plus 3 internal-source-evidence
occurrences equals 72 total normalized occurrences) has SHA-256
`f5c08bfd69720021d9317c936a3fbc6e788752e6e11def18bee45d4f00ea8f8d`.
`SHA256SUMS` pins each review-relevant file outside the workflow itself.

## Retention boundary

The following hashes are absent from the v1.4 reference set but must remain in
the durable store for already-published v1.2 workbooks:

- `399ee120a3db07046905cababc944f464688d1f45ebfe54bd01d75790a62c64b`
  (`MEAS-A002`)
- `d774b64f94364b3101c25f4fec8a5c0120c6447dcebe09f175d394578967810c`
  (the superseded fixed-arrow `FR-ASSET-001` crop)

The loader only calls the existing content-addressed `pin_asset()` service for
the two reviewed additions. There is no deletion path in the bundle or workflow.

## Trigger and gates

The workflow is restricted to an exact push of branch
`ops/grade6-assets-v14-20260902`. Pushing the reviewed local commit is the
one-shot authorization and requires repository write access. No local Fly token
or GitHub CLI is needed: GitHub Actions supplies the already-configured
`FLY_API_TOKEN` secret to only the Fly steps.

The retained `workflow_dispatch` entry is a secondary option only after the
workflow file exists on the repository's default branch; GitHub does not allow
a branch-only workflow to receive `workflow_dispatch` events.

Before any durable-store write, the job:

1. requires the exact branch and permitted event;
2. checks out the exact triggering commit with credentials discarded;
3. verifies the archive, delta-manifest, and full-29 manifest hashes;
4. rejects unexpected or unsafe archive members;
5. runs the loader locally in dry-run mode and requires exactly two validated
   assets;
6. verifies the exact previously proven Fly machine and public health endpoint;
7. runs a read-only remote preflight confirming `/data` is the configured mount,
   the durable store exists and is writable, and at least 1 MiB is free; and
8. reruns the loader dry-run on the Fly machine and requires the exact result
   `{"mode": "dry-run", "pinned": 0, "validated": 2}`.

Only then does the apply step run. It must return exactly
`{"mode": "apply", "pinned": 2, "validated": 2}`. The final gate anonymously
first validates the exact source manifest SHA, the 27/2 usage split, all three
occurrence totals, and every per-asset usage/occurrence record. It then
downloads all 29 v1.4 URLs—including the 2 unused source-panel assets—and
checks no redirect, HTTP 200, `image/jpeg`, byte length, and SHA-256. Its JSON
report includes the repository, branch, triggering
commit, run ID and URL, Fly machine, archive hash, and both loader reports. The
report is uploaded as a 90-day workflow artifact; any failed URL makes the run
fail.

Do not push this branch until the exact local commit and hashes have been
reviewed. After a successful run, compare the workflow run's head SHA with the
reviewed commit SHA and retain the downloaded verification JSON with the v1.4
release evidence.
