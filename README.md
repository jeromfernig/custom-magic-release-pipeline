# Custom Magic Release Pipeline

Proof-of-concept Python application for building releases of a custom Magic: The Gathering set from Google Sheets.

The PoC implements Google OAuth, live Sheets import, validation, canonical Pydantic models, artwork preparation, local MTG Crucible rendering (default), optional MTG.Design rendering, R2 hosting, cross-release JPEG caches, PDF and external-tool exports, Streamlit UI, and release manifests. Production release locking remains future work.

## Setup on Windows

1. Install Python 3.11 or newer.
2. Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

3. Install the project:

```powershell
pip install -e .[dev]
```

4. Copy `config.example.yaml` if you want a local config:

```powershell
Copy-Item config.example.yaml config.local.yaml
```

`config.local.yaml` is ignored by Git. Do not put secrets in it anyway. Google credentials and OAuth tokens stay in `credentials.json` and `token.json`, which are also ignored.

## Google API Setup

1. In Google Cloud Console, create or select a project.
2. Enable the Google Sheets API.
3. Configure an OAuth consent screen for local testing.
4. Create OAuth client credentials of type `Desktop app`.
5. Download the client secret file and save it as `credentials.json` in this folder, or update `source.credentials_path`.
6. Put your spreadsheet ID in YAML under `source.spreadsheet_id`, or enter it in the UI.
7. Create the initial local OAuth token from PowerShell:

```powershell
python -m mtg_pipeline.auth_google --config config.example.yaml --no-browser
```

The command prints a Google authorization URL. Copy it into your browser, complete consent, and it writes `token.json`.

If sheet loading fails with Google's `invalid_scope` refresh error, recreate the local token:

```powershell
python -m mtg_pipeline.auth_google --config config.example.yaml --no-browser --reset-token
```

If you prefer the command to try opening the browser automatically, omit `--no-browser`:

```powershell
python -m mtg_pipeline.auth_google --config config.example.yaml
```

or, after editable installation:

```powershell
mtg-pipeline-auth-google --config config.example.yaml
```

This writes the same ignored `token.json` file used by the Streamlit UI.

The PoC now needs Google Sheets write scope when generated artwork is written back to `artwork_url` and `artist`. If you created `token.json` before that feature existed, rerun the auth command above to replace it.

## Source Sheet

The PoC expects two worksheets by default:

- `Set`: set metadata.
- `Cards`: one card per row.

The minimum `Set` key/value table is:

| key | value |
| --- | --- |
| set_id | pokemon-mtg-set |
| set_name | Pokemon MTG Set |
| set_code | PKM |
| version | 0.3.0 |
| draft_mode | false |

Worksheet names and card column names are configurable. See [docs/source-schema.md](docs/source-schema.md).

`set_id` is the stable identifier. You can rename `set_name` later without changing release references. Keep `set_id` lowercase with numbers and hyphens, and do not change it after the set has public artifacts.

The current `Cards` schema uses separate `supertypes`, `card_types`, and `subtypes` columns. `card_types` is required; the canonical `type_line` is derived from those columns.

Do not include collector numbers in the sheet. The pipeline assigns them during canonicalization by color/type group and card name. Colors are inferred from `mana_cost`; compact costs like `2GG` are normalized to `{2}{G}{G}`.

If `artist` is filled, an existing `artwork_url` is used first. Local artwork paths are relative to the project root, so artwork created outside the pipeline should go under root `artwork/`, for example `artwork/pokemon-mtg-set/helping-hand.png`. A blank artist requests fresh generation, even when `artwork_url` is filled: OpenAI Sunburst, then Gemini Flash Image, then same-name art in the default art folder, then a white error placeholder. See [Gemini and artwork providers](docs/artwork-providers.md) for setup, configuration, and a single-image smoke test.

If `artwork_url` is blank and `art_prompt` is filled, the artwork step generates art with the configured provider (`openai` by default), saves it in the release-agnostic cache `artwork/<set_id>/<card-name>.png`, and writes that local relative path plus artist credit back to Google Sheets. Existing files are preserved; new art gets numbered names such as `card-name-2.png`. Source artwork is stored under root `artwork/`, which is ignored by Git. The renderer reads the prepared project-relative `artwork_path` directly. Releases contain finished card renders and export files, with references to shared source artwork rather than copies of it.

Each card's artwork operation has its own retry budget (`images.generation_retry_count`, default one retry). Gemini retries transient API/transport failures with bounded backoff. Explicit input moderation blocks go directly to the next fallback. Blank-artist failures appear together on the white placeholder and never write a permanent moderation marker. The older moderation-marker behavior remains for credited rows that need generation; clear that marker to request the new provider chain. The run continues with other cards, and warnings appear in the UI, logs, and manifest. A failure to write even the fallback itself (for example, a full disk) still fails the step.

Placeholders live only under `artwork/<set_id>/placeholders/<card-name>.png`. They are never written into Google Sheets as completed art or reused as successful generation cache entries. Rows without either artwork source also receive a placeholder. Add a prompt or manual art in the sheet and run again. If an older version wrote a `Pipeline placeholder` SVG path into the sheet, clear that `artwork_url` (or replace it with manual art) to enable generation again.

## Rendering and R2

MTG Crucible is the default renderer. In the UI, select **Rendering engine → MTG Crucible (local)** or **MTG.Design**. Local rendering reads prepared artwork directly and needs no R2 source upload or MTG.Design session. Both engines write the same release JPEG filenames, so print, notes, archetype PDFs, and hosted-card exports continue to work. See [Crucible setup and renderer settings](docs/rendering.md).

Prepare the local clone once (and after updating it):

```powershell
python -m mtg_pipeline.setup_crucible --download-node
```

This reads `../mtg-crucible`, installs a checksum-verified portable Node.js if needed, and builds a private runtime under `.cache/mtg_crucible/runtime`. It does not modify the clone. Subsequent rendering uses only local assets and artwork. Set `crucible.repository_path` if your clone is elsewhere. Select rendering quality in the UI; the default is high (2010×2814 for standard cards).

The MTG.Design path remains available. Its sequence is:

1. Prepare source art under `artwork/`, including generated art and Pillow fallbacks.
2. Upload selected cards' source art to R2 and verify that its public download matches the local contents.
3. Build the MTG.Design request with `artwork_public_url`, without replacing the sheet's `artwork_url`.
4. Render a JPEG or reuse `.cache/mtg_design/renders/<request-hash>.jpg`.
5. Copy the finished JPEG into `releases/<set_id>/<version>/cards/` for PDFs and exports. Source art is never copied into a release.

Source files and generated art remain under `artwork/`. R2 build copies use `.cache/artwork/<sha256>.<actual-extension>`. Crucible render keys include visible card data, quality, original artwork hash, bridge/adapter code, and prepared runtime fingerprint. Its cache is separate from `.cache/mtg_design`; switching engines cannot reuse another engine's renders. Version-only changes reuse cached images. MTG.Design keys retain their remote request parameters. Deleting `.cache/mtg_crucible/renders` clears only local renders; deleting the runtime requires rerunning setup. None of these operations removes source artwork.

In Cloudflare, create/select an R2 bucket, enable its public r2.dev URL or custom domain, and create an object read/write API token scoped to that bucket. Put `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY` in your environment or ignored `.env`. Set `r2.bucket` and `r2.public_url` in YAML, or use `R2_BUCKET` and `R2_PUBLIC_URL`. Never use the private S3 endpoint as the public URL. Source objects use `artwork/<content-hash>.<actual-extension>` and are shared across cards and releases.

Log into MTG.Design manually. In browser DevTools, open Network and render a card. Inspect that request's cookies, copy only the `laravel_session` value, and paste it into the hidden `MTG.Design Laravel Session` field in this app. The cookie stays in memory only: never put it in YAML, `.env`, logs, or manifests. Sessions expire periodically; replace the value when authentication fails. Cached renders do not require a valid session.

Requests are sequential, at least two seconds apart, with up to four attempts for transient errors. Backoff starts at five seconds and honors Retry-After. Cache hits do not wait or contact MTG.Design. Settings are under `mtg_design`, `r2`, and `cache`; migrate old local configs to the example. The old renderer and its settings have been removed.

By default, `mtg_design.text_sizing` estimates visual rules-text lines from hard line breaks plus 60-character wrapping. It starts at `38`, subtracts `ceil(1.25 * estimated extra lines)` points, and clamps at `20`. The computed `text-size` is sent to MTG.Design and is part of the render cache key. Sizes below `29` generate a release warning; tune the settings in `config.example.yaml` to adjust the heuristic.

`Upload rendered cards to R2` publishes JPEGs under `releases/<set_id>/<version>/cards/`. Including the stable set ID prevents different sets overwriting each other. Draftmancer, CubeCobra, and Cockatrice depend on those hosted finished-card URLs; source-art URLs are never substituted. Set `mtg_design.render_limit: null` before publishing the complete set. Print PDF, finished-card publishing and exports remain opt-in.

Print PDFs order cards by their IDs: rarity prefix `C`, `U`, `R`, `M`, `T`, then color character `W`, `U`, `B`, `R`, `G`, `C`, then the numeric ID suffix. Unlisted color characters (such as `A` and `M`) follow the specified colors within their rarity.

Add the optional `archetype` column to Cards to tag cards with comma-separated values such as `Blue Tempo, Graveyard`. Values are trimmed, internal spaces and case are preserved, and blanks become an empty list in canonical `data/cards.json`. **Create Archetypes PDF** is selected by default when rendering is selected. It writes `archetypes/<set-code>-<version>-archetypes.pdf` within the release, with one landscape A4 page per distinct archetype, sorted alphabetically. Multi-tag cards appear once on each matching page; untagged cards are excluded. Each page chooses the grid that maximizes card size while fitting every available rendered card, preserving aspect ratios and the custom ID order. Cards are included once per archetype regardless of print-copy settings. Missing rendered images are noted on the relevant page; an archetype with no rendered images still gets a labeled page. If no archetypes are defined, the step produces no PDF. Older sheets without the column continue to work.

**Create Note File** is an optional pipeline step, off by default. It uses rendered card images to create `notes/<set-code>-<version>-notes.pdf` inside the release. Each portrait A4 page contains six rows of two card-and-feedback pairs, read left to right, with four handwriting lines to the right of every card. With 10 mm page margins and 3 mm between rows, each card is 43.67 mm tall: `(297 - 2×10 - 5×3) / 6`. Standard 63×88 mm cards become 31.26×43.67 mm (49.62% scale), leaving about 55.74 mm of writing width per card. It uses the same custom ID order as the print PDF and includes each rendered card once, regardless of print-copy settings. Cards without rendered images are omitted; no available images produces an error.

The UI's **Draftmancer pack layout** section lets you set cards per pack for Common, Uncommon, Rare, Mythic, Land, and RareMythic sheets. Set unused slots to zero; the total is displayed. RareMythic combines rares and mythics with equal weight per card. Enable **Allow cards to appear in multiple packs** for unlimited quantities, or **Refill sheets when exhausted** to replenish the pool. Loaded rarity counts are shown; invalid layouts and requested empty sheets prevent running with Draftmancer or mana-curve analysis selected. Uncheck those steps if you want to run other exports without a pack layout. **Advanced JSON** supports multiple weighted layouts and weighted source sheets. Changes persist during the UI session and apply to the exported `[Settings]` block and mana-curve calculations. To retain them across sessions, download the pack configuration and copy its `draft` section into your existing YAML config. Downloaded settings do not contain credentials.

**Analyze Mana Curves** is on by default. It produces six landscape charts (White, Blue, Black, Red, Green, Colorless) as `analysis/<set>-<version>-mana-curves.pdf`, individual PNGs shown in the UI results, and a JSON record of the settings and calculations. Each mana-value bar stacks common creatures/noncreatures, uncommon creatures/noncreatures, and rare creatures/noncreatures (including mythics). Weights use the current Draftmancer layout and sheet probabilities, not raw rarity counts. The step needs only canonical data, so artwork, rendering, and R2 uploads can be disabled. See [comparison and curve analysis](docs/comparison-and-curves.md) for conventions and limitations.

Crucible also supports prepared spells and transforming cards/battles using separate Sheet rows marked `record_type: linked_face`. Link them from the main row with `link_id` and `link_type` (`prepare` or `transform`). Linked faces retain their own canonical records while sharing the physical card's collector number and booster allocation. See [linked cards](docs/linked-cards.md) for examples, required fields, and how to add/remove links between versions.

Compare two saved releases independently of the pipeline:

```powershell
python -m mtg_pipeline.compare_versions 0.2.0 0.3.0
```

This pairs canonical cards by ID and creates `releases/pokemon-mtg-set/comparisons/0.2.0-vs-0.3.0.pdf`, with old/new renderings, full changed-field values, added/removed cards, and a compact unchanged list. Both releases must already exist. It does not regenerate cards, access Sheets, or download images. Use `--set-id`, `--release-root`, or `--output` for other locations; `--include-build-fields` also compares cache/upload bookkeeping.

Render profiles separate MTG.Design `renderer_options` from native Crucible `crucible_options` (for example, `frameColor: colorless`). Rarity `land` renders with the common rarity symbol; canonical rarity remains `land`. Crucible maps single-face card data, including starting loyalty, and uses its native type-based template inference. Multi-face layouts require additional canonical data and are rejected explicitly. MTG.Design retains its existing single-face non-planeswalker restriction. Manifest renderer statistics identify the selected engine, cache hits, completed renders and failures.

Before the release copy is written, `mtg_design.postprocess` applies cosmetic cleanup only to `releases/.../cards/*.jpg`; it never modifies `.cache/mtg_design`. The default mask removes the MTG.Design three-card/Not For Sale mark. The collector number, including its denominator, remains visible. The rectangle samples its RGB fill from a nearby footer pixel, so it matches the decoded JPEG's actual black. Tune `reference_size`, `rectangle`, and `sample` in `config.example.yaml`; coordinates scale proportionally when the output dimensions differ.

## Run the UI

```powershell
streamlit run ui/app.py
```

Running `python ui/app.py` directly does not execute Streamlit's normal app runner in a standard Streamlit project. This repo now redirects that command to `python -m streamlit run ui/app.py`, but `streamlit run ui/app.py` remains the clearest command.

After updating Python source, stop the running Streamlit process with Ctrl+C and start it again. Clearing Streamlit's data cache does not reliably reload imported Python modules. There is no need to delete cached artwork. References to artwork under older releases are copied into the stable `artwork/<set_id>/` folder with name-only filenames when loaded.

New sessions select the Pokemon sheet by default (unless `source.spreadsheet_id` configures another sheet) and automatically load it when a usable token is present. Ordinary UI reruns do not repeatedly load the sheet; use `Load source` to refresh or retry an error. Changing the selected source loads the new source. PDF, Draftmancer, CubeCobra, and Cockatrice exports are unchecked by default.

Temporary Google read errors (429, 500, 502, 503, and 504) retry up to twice, waiting two seconds before each retry. Permanent errors such as missing permissions or an unknown spreadsheet are reported immediately. While the pipeline runs, the status displays the current step and card, generation attempts, cache use, placeholders, rendering, and sheet write-back progress.

In the UI:

1. Confirm config path and source settings.
2. Authenticate with Google if `token.json` is missing, expired, invalid, or missing required scopes.
3. Select pipeline steps.
4. Click `Run selected steps`.
5. Inspect `releases/<set_id>/<version>`.

## Generated Release Layout

```text
releases/
  pokemon-mtg-set/
    0.3.0/
      manifest.json
      validation-report.txt
      data/
        set.json
        cards.json
      cards/
      print/
      draftmancer/
      cubecobra/
      cockatrice/
```

## Manual Export Steps

- Draftmancer: import the generated custom card list file manually.
- CubeCobra: try the generated CSV through CubeCobra's UI and validate compatibility manually.
- Cockatrice: copy the generated XML into Cockatrice's custom sets folder from `Card Database -> Open custom sets folder`.

## Tests

```powershell
pytest
```

Tests mock Google parsing, artwork downloads, and renderer HTTP calls. They do not require Google credentials, internet, or a live renderer.

## Current Limitations

- Renderer support for `fullart_colorless` depends on the external renderer template recognizing the profile options sent by this pipeline.
- Draftmancer output follows the documented CustomCards format and configurable sheet/layout sections, but pack collation still needs manual tuning.
- CubeCobra has no stable documented public write API for arbitrary custom-card publication in this PoC, so the CSV is a practical manual-import artifact.
- Cockatrice output targets card database version 4 and covers normal single-face cards. Double-faced cards, tokens, and relationships are future work.
