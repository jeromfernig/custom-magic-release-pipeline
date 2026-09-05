# Custom Magic Release Pipeline

Proof-of-concept Python application for building releases of a custom Magic: The Gathering set from Google Sheets.

The PoC implements Google OAuth, live Sheets import, validation, canonical Pydantic models, artwork download, mtg-card-renderer integration, print PDF generation, Draftmancer export, CubeCobra CSV export, Cockatrice XML export, Streamlit UI, release manifests, and tests. Cloudflare R2 upload, incremental builds, publication automation, and full production release locking are intentionally future work.

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

If `artwork_url` is filled, it is used first. If `artist` is blank in that case, validation emits a warning. Local artwork paths are relative to the project root, so artwork created outside the pipeline should go under root `artwork/`, for example `artwork/pokemon-mtg-set/helping-hand.png`.

If `artwork_url` is blank and `art_prompt` is filled, the artwork step generates art with OpenAI's image API using `OPENAI_API_KEY`, saves it in the release-agnostic cache `artwork/<set_id>/<card-name>.png`, and writes that local relative path plus artist credit back to Google Sheets. Source artwork is stored exclusively under root `artwork/`, which is ignored by Git. The renderer reads the prepared project-relative `artwork_path` directly. Releases contain finished card renders and export files, with references to shared source artwork rather than copies of it.

Each card's artwork operation has its own retry budget (`images.generation_retry_count`, default one retry). After generation, download, or local-file errors exhaust that budget, Pillow creates a white landscape PNG with the error in black text. Explicit input moderation blocks go directly to a fallback. The run continues with other cards, and warnings appear in the UI, logs, and manifest. Missing both sources produces the message `no artwork_url or art_prompt specified in sheet`. A failure to write even the fallback itself (for example, a full disk) still fails the step.

Placeholders live only under `artwork/<set_id>/placeholders/<card-name>.png`. They are never written into Google Sheets as completed art or reused as successful generation cache entries. Rows without either artwork source also receive a placeholder. Add a prompt or manual art in the sheet and run again. If an older version wrote a `Pipeline placeholder` SVG path into the sheet, clear that `artwork_url` (or replace it with manual art) to enable generation again.

## mtg-card-renderer

Card rendering is delegated to `wingedsheep/mtg-card-renderer`; this project does not implement a renderer. Start that service separately and keep `renderer.base_url` pointed at it, defaulting to `http://localhost:8080`.

The PoC uses:

- `POST /api/render`
- `POST /api/render/upload` when downloaded local artwork exists

Render profiles are selected from YAML via each card's `render_profile`. The `fullart_colorless` profile is represented as renderer options, so a renderer template can consume it without card-name-specific logic.

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
