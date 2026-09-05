# Manual Smoke Tests

## Google OAuth and Sheets

1. Create a Google Cloud OAuth desktop app and download it as `credentials.json`.
2. Put `credentials.json` next to `config.example.yaml` or update `source.credentials_path`.
3. Set `source.spreadsheet_id` in your YAML or enter it in the Streamlit UI.
4. Run `streamlit run ui/app.py`.
5. Click `Authenticate with Google` and complete the browser flow.
6. Run at least source, validation, and canonicalization.

## mtg-card-renderer

1. Start `wingedsheep/mtg-card-renderer` separately.
2. Confirm the renderer is listening at `http://localhost:8080`.
3. Select artwork and rendering steps in the UI.
4. A successful PoC render writes PNG files under `releases/<set_id>/<version>/cards`.

The PoC sends Scryfall-like card JSON to `/api/render`. When a local artwork file exists it uses `/api/render/upload` with multipart fields `card` and `image`.

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
