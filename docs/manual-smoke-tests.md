# Manual Smoke Tests

## Google OAuth and Sheets

1. Create a Google Cloud OAuth desktop app and download it as `credentials.json`.
2. Put `credentials.json` next to `config.example.yaml` or update `source.credentials_path`.
3. Set `source.spreadsheet_id` in your YAML or enter it in the Streamlit UI.
4. Run `streamlit run ui/app.py`.
5. Click `Authenticate with Google` and complete the browser flow.
6. Run at least source, validation, and canonicalization.

## MTG.Design and R2

1. Configure R2 and set `mtg_design.render_limit: 1` for a small smoke run. Use a sheet with existing artwork to avoid image-generation charges.
2. Run `python -m streamlit run ui/app.py`. Paste the authenticated MTG.Design `laravel_session` cookie into its hidden field.
3. Select preparation, artwork R2 upload and rendering. Leave finished-card upload and exports unchecked.
4. Run once and open the release's `cards/*.jpg`. Confirm that it contains the expected art, text and frame.
5. Run again: the rendering summary should show one cache hit and zero remote renders. Change only the sheet's version and repeat to verify reuse in a new release directory.
6. Uncheck artwork R2 upload and confirm rendering and its downstream steps become disabled.

For a complete export smoke run, set the render limit to null, render all included cards, and enable finished-card R2 upload before selecting exports. Source art and finished cards use separate R2 prefixes.

## MTG Crucible (default)

1. Run `python -m mtg_pipeline.setup_crucible --download-node` once from the pipeline repository.
2. Start Streamlit and select **MTG Crucible (local)** under **Rendering engine**.
3. Leave source-art R2 upload unchecked, prepare existing artwork, and render a small selection (set `crucible.render_limit: 1` in YAML if desired).
4. Inspect the release JPEG and renderer manifest statistics; run again to verify a local cache hit. Quality, artwork, or visible card-text changes should produce a fresh render.
5. Confirm that print, notes, and archetype PDFs consume the same release JPEGs. Use a separate release/version when comparing engines.
6. Switch to **MTG.Design** and verify that source-art upload becomes required and the session field becomes editable. The MTG.Design instructions above still apply when that engine is selected.

## Draftmancer

1. Generate `releases/<set_id>/<version>/draftmancer/<set>-<version>.txt`.
2. Open Draftmancer's custom card list import UI.
3. Paste or upload the generated file.
4. Validate booster layout behavior manually before treating pack collation as final.

## CubeCobra

1. Generate `releases/<set_id>/<version>/cubecobra/<set>-<version>.csv`.
2. In CubeCobra, use the cube list export/import area and try `Replace with CSV Import`.
3. Confirm custom cards, images, and metadata manually.

CubeCobra does not expose a documented public write API for this use case. The PoC creates the most practical CSV artifact using the observed/exported CSV-style columns, but arbitrary custom-card support must be validated with your account.

## Cockatrice

1. Generate `releases/<set_id>/<version>/cockatrice/<set>-<version>.xml`.
2. In Cockatrice, open `Card Database -> Open custom sets folder`.
3. Copy the XML file there. A filename like `01.pkm.xml` is conventional.
4. Restart or reload Cockatrice's card database.
