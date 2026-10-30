# Repository Review

Reviewed 2026-09-05. Scope: all Python source, UI, configuration, packaging,
tests, and project documentation. Findings below remain open unless explicitly
listed under fixes. This is a review of behavior and contracts, not a proposal
to replace the current stack.

## Findings

### 1. P1: Sheet write-back can update the wrong card

`src/mtg_pipeline/artwork/preparer.py:25` retains only the original row number
for generated artwork updates. `src/mtg_pipeline/steps.py:74` waits until the
whole artwork batch finishes before writing anything to Sheets.
`src/mtg_pipeline/sources/google_sheets.py:191` then updates that row without
checking its card ID or current artwork fields. Sorting/inserting rows during
a long generation run can put art and credit on the wrong card; adding manual
art during the run can be overwritten.

Carry the stable card ID and original source values with each update, re-read
the sheet before writing, locate the current row by ID, and skip/report changed
artwork fields. Persist a local pending-update journal so an interrupted run
can resume. Batch header reads and writes instead of rebuilding a Sheets
service and fetching headers for each card.

### 2. P1: Invalid metadata is used in filesystem paths before rejection

`src/mtg_pipeline/steps.py:52` uses the raw version to create the release
directory and write the validation report before checking `validation_result.ok`.
For example, `../../outside` fails validation but still redirects the report
outside `releases/`. The export filenames at `steps.py:95` also include a
`set_code` that is only checked for presence, not path separators.

Validate all path components before any filesystem writes, enforce resolved
path containment, and put reports for invalid inputs in a separate, fixed
diagnostics directory. Do not sanitize invalid identifiers silently.

### 3. P1: A successful release can silently contain only ten rendered cards

`src/mtg_pipeline/config.py:59` and `config.example.yaml:35` default to a render
limit of ten. Artwork generation still processes every card, including paid
API calls. `src/mtg_pipeline/exporters/print_pdf.py:48` silently skips missing
rendered images, while the other exporters reference images for all cards.
A 261-card input can therefore produce a ten-card PDF and hundreds of missing
public image references while every selected step reports success. Rerunning
the same version can also pick up old images left in the output directory.

Make preview versus complete release an explicit run mode. A preview should
limit artwork generation too and report its subset. A complete release should
check artifact coverage and build into a clean staging directory before
publication, with a source/config snapshot and checksums in its manifest.

### 4. P1: Artwork cache names do not establish cache validity

`src/mtg_pipeline/artwork/preparer.py:66` treats an existing name-based PNG as
a successful generation regardless of its prompt, model, or origin.
`preparer.py:133` likewise ignores changes to a remote `artwork_url` when a
same-name cached file exists. `Fire / Ice` and `Fire-Ice` both become
`fire-ice`, so distinct names can also reuse or overwrite one another's art.

Keep the requested human-readable, name-only image filenames. Add a sidecar
index recording the source URL or prompt/model/settings fingerprint. Detect
filename collisions before generating or copying, and require an explicit
shared artwork reference for intentional reuse. Do not reintroduce collector
numbers or card IDs into artwork filenames.

### 5. P2: Authentication has inconsistent scope and expiry handling

`src/mtg_pipeline/sources/google_sheets.py:210` passes requested scopes when
loading saved credentials, then tests those same scopes. Locally reproduced:
Google's Credentials loader replaces the saved scopes with the supplied
ones, making this check ineffective for an older read-only token.
`token_status()` separately examines the file, so the UI and loader disagree.
The UI also describes an expired access token as needing recreation even
though `_load_credentials()` can refresh it. Missing/invalid credentials cause
that loader to launch another blocking OAuth flow inside a source read.

Use one credential manager shared by CLI and UI: inspect actual saved grants,
refresh automatically, and return an explicit authentication-required state
on failed refresh or insufficient scope. Keep interactive consent outside
sheet loading. The custom callback server also needs a timeout/cancel lifecycle.
Avoid process-wide insecure-transport settings; validate the loopback callback
and state locally while exchanging the code over HTTPS.

### 6. P2: Draftmancer layout reference

Resolved: the example no longer sets `predeterminedLayouts` to the
`RareMythic` sheet. The sole `Default` layout selects that sheet through its
slot declaration, which is the documented format. The export test verifies
that the invalid override is absent.

### 7. P2: Validation can succeed immediately before canonicalization fails

`src/mtg_pipeline/validation.py` does not validate numeric `mana_value` or
`draft_mode`. Both `mana_value="abc"` and a present-but-blank `draft_mode`
pass validation and fail when parsed by canonicalization/Pydantic. Row counts
also include excluded rows even though rarity counts and canonical cards omit
them. `src/mtg_pipeline/mana.py:6` accepts malformed compound symbols such as
`{GG}`. Its bare-cost parser groups digits (`12` becomes `{12}`), which differs
from the user's literal each-character-is-a-pip rule.

Use one normalization/typed validation path and collect errors with source
row numbers before executing output steps. Specify defaults for blank fields,
count included cards consistently, and validate individual mana symbols.

### 8. P2: Startup and configuration can silently select a different environment

`src/mtg_pipeline/config.py:117` silently returns defaults for a missing config
file, and relative paths resolve against the process working directory rather
than a shared project/config root. Launching the app from another directory
can select different token/artwork/release locations. `ui/app.py:24` reloads
only the Google module, allowing references held by already-imported modules
to diverge. The supplied traceback's source lines do not match the code on
disk, consistent with stale in-memory imports, but the exact historical cause
cannot be proven from the traceback alone.

Resolve paths once, fail clearly for a misspelled explicitly selected config,
and use a single documented launcher. Restart the process after code changes.
For longer runs, move pipeline execution to a worker with persisted job status
so browser reruns do not own work, logs, or authentication state. The unused
`artwork/downloader.py` is a second implementation with obsolete numbered
filenames; remove it once callers are confirmed absent.

## Fixes Completed In This Change

- Detect both wrapped and raw OpenAI moderation errors, including flat,
  nested, and response-only error bodies. Retain stage/category/request ID.
- Retry output blocks within the configured limit without changing the user's
  requested subject. Explicit input blocks go directly to a placeholder.
- Use PNG diagnostic placeholders; keep them outside successful image caches
  and never write them to Sheets as finished artwork. MTG.Design now receives
  publicly hosted R2 source artwork instead of local multipart uploads.
- Show runtime warnings in the UI and logs and preserve them in the manifest.
- Refresh canonical JSON at finalization so generated paths and filenames
  match the completed run.
- Replace stale release copies when manual artwork changes.
- Select PDF, Draftmancer, CubeCobra, and Cockatrice by default in new UI sessions.

OpenAI documents `moderation_stage=output` as a block on the generated image.
The category `other` does not establish that the character name caused it:
[image generation error handling](https://developers.openai.com/api/docs/guides/image-generation#content-moderation).

## Verification And Limits

The original 47 tests passed before changes, despite these gaps. Added tests
exercise real SDK exception objects through the adapter and preparer, bounded
retries, input blocks, preservation of unrelated errors, a multi-card pipeline
through real PDF/export writers, warning persistence, placeholder recovery,
changed manual art, and Streamlit UI defaults/warning presentation.
The final suite passed: 58 tests. `git diff --check` also passed.

No paid image requests or live sheet writes were made. The local UI rendered
without exceptions. There was no renderer listening on port 8080, so rendering
HTTP responses were simulated. Draftmancer, CubeCobra, and Cockatrice still
need imports against their real applications; string/XML-shape tests do not
establish interoperability.

## Design Direction

Keep Python, Pydantic, Streamlit, the stable set ID, and separate exporters.
They are appropriate for this local tool. Prioritize source snapshotting and
safe write-back, explicit complete/preview semantics, and tested authentication
over new frameworks. Then add cache provenance and a resumable worker. Pin
tested dependency versions and add a Windows CI job with offline integration
tests before building publication automation.
