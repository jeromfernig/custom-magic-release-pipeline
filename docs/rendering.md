# Rendering engines

The pipeline defaults to the local MTG Crucible clone at `../mtg-crucible` (`C:\Users\User\Repos\mtg-crucible` in this workspace). Choose **Rendering engine** in Streamlit to switch between Crucible and MTG.Design. UI changes apply to that session/run; edit YAML to persist your preference:

```yaml
rendering:
  engine: crucible # or mtg_design
crucible:
  repository_path: ../mtg-crucible
  node_executable: node
  quality: high # high, medium, low
  timeout_seconds: 120
  render_limit: null
  designer: Mockingbird
cache:
  crucible: .cache/mtg_crucible
```

## Prepare Crucible

From the pipeline repository, after installing its Python dependencies:

```powershell
python -m mtg_pipeline.setup_crucible --download-node
```

If Node.js and npm are already installed, omit `--download-node`. With `--config config.local.yaml`, setup uses that file's clone and cache paths. The optional portable installer supports Windows x64, downloads Node.js 22 from nodejs.org, checks the archive against the official SHA-256 manifest, and extracts it under `.cache/node`. It does not alter system PATH. Other platforms should install Node.js with npm normally. A custom `crucible.node_executable` can point to an installed Node executable.

Setup copies source and assets into `.cache/mtg_crucible/runtime`, installs package-lock dependencies with npm lifecycle scripts disabled, generates version metadata, compiles the Node build with TypeScript, and verifies that the native canvas addon loads. The local clone is read-only throughout. The build uses the existing canvas frames, symbols, and fonts; no cloud renderer or browser service is involved. Internet is needed for initial dependency installation and for custom set-symbol URLs when configured. Rerun setup after updating the clone. The runtime fingerprint changes when source, lockfile, or assets change, causing fresh render-cache entries.

## Pipeline behavior

1. Prepare/download/generate artwork as before.
2. Render cards locally, with no source-art upload required.
3. Select print, notes, and archetypes PDFs as desired.
4. Select **Upload rendered cards to R2** when you want hosted image URLs for Draftmancer, CubeCobra, and Cockatrice. These publishing/export steps retain their R2 requirements.

The local engine sends structured card data and artwork bytes to Crucible's `renderCard` API through a Node bridge. Raster artwork is re-encoded in memory as PNG to remove metadata that the native canvas decoder may reject; source files, image dimensions, and decoded pixels remain unchanged. Neither prompts nor card IDs are rewritten. Rules text, flavor text, power/toughness, starting loyalty, type line, artist, set code, collector number, and designer are mapped. Internal comments are excluded. Crucible derives frame colors from the card data and retains its native footer/attribution.

When the set's `draft_mode` is true, the pipeline passes the exact canonical card ID to Crucible's `custom_footer_field`, which appends it after the collector number in the top-left footer line. The collector number stays unchanged. Non-draft versions omit this field. Changing draft mode invalidates cached renders as needed.

Render profiles may contain `crucible_options` for `cardTemplate`, `frameColor`, `accentColor`, `frameEffect`, `nameLineColor`, `typeLineColor`, `ptBoxColor`, `colorIndicator`, or `legendCrown`. Keep MTG.Design form parameters under `renderer_options`; these are not passed to Crucible. The example `fullart_colorless` profile selects Crucible's colorless frame.

High quality uses the library's standard 2010×2814 output; medium and low use its native lower resolutions. Outputs are JPEGs at the existing `releases/<set_id>/<version>/cards/<collector>-<name>.jpg` paths. Source art is preserved. Crucible output never passes through the MTG.Design footer-mask postprocessor. Each engine has a separate render cache; the manifest records which engine rendered the cards. No paid API requests are made by the renderer itself (artwork generation remains separate).

After canonicalization, each run removes obsolete image files directly inside the current release's `cards` folder. The full current canonical list determines which filenames stay, including transforming back faces; prepared insets do not get a separate image. This handles removed/disabled cards, renamed or renumbered cards, and removed links. Cleanup also runs when rendering is limited or skipped, retaining existing images for every current card rather than only the rendered subset. Other versions, source artwork, render caches, non-image files and subfolders are preserved. The log and manifest report the removed filenames/count. Invalid source data does not trigger cleanup.

Both renderers use the Sheet set settings `set_icon_common`, `set_icon_uncommon`, `set_icon_rare`, and `set_icon_mythic`. Crucible passes the matching URL as `setSymbol`; the land rarity uses the common icon. An unset icon uses Crucible's built-in rarity symbol. Invalid or inaccessible custom icons fail the render rather than silently substituting another symbol. Changing the configured URL invalidates the render cache; replace icons at a new URL when updating their contents. Rerun `python -m mtg_pipeline.setup_crucible` after updating the Crucible clone to copy and compile its custom-symbol support.

Crucible supports `link_id` / `link_type` relationships to separate `linked_face` Sheet rows. `prepare` renders a single card image with a spell inset; `transform` renders separate front/back JPEGs, including landscape battles with `battle_defense`. Landscape faces are rotated into portrait physical-card orientation for existing PDFs and hosting. See [linked cards](linked-cards.md).

If a local render fails, times out, returns invalid JPEG data, or cannot decode the supplied art or set symbol, no successful output/cache entry is written for that card and the rendering step reports failure. Previously completed renders remain cached. Runtime setup errors include the preparation command. Unlinked non-normal layouts remain rejected rather than silently losing faces. Planeswalker loyalty is mapped to Crucible; complex template text must follow the upstream parser's supported conventions.

## Print bleed

Streamlit's **Print options → Add 2 mm black bleed** is enabled by default. It applies to the printable PDF and also works when regenerating that PDF from existing card renderings. YAML defaults are:

```yaml
print:
  bleed_enabled: true
  bleed_mm: 2
```

The 63 × 88 mm trim size is unchanged; the printed area including bleed is 67 × 92 mm. The exporter samples the card's dark outside border, fills the connected white corner mattes inside the card rectangle, and extends the sampled color 2 mm on every side. It preserves the card's scale and rules/artwork content. Crop marks stay aligned with the original trim edges and begin beyond the bleed. Layouts where the configured bleed would overlap adjacent cards are rejected.

Corner-filled previews are generated in memory and embedded in the PDF as high-quality JPEGs. Saved card images, render caches, hosted images, artwork, note/archetype PDFs and version comparisons retain their existing dimensions and corners. Turn the checkbox off or set `print.bleed_enabled: false` to export the original images with no bleed. The UI always uses 2 mm; YAML/Python callers can choose another nonnegative `bleed_mm` value.

## MTG.Design

Select **MTG.Design** in the UI or configure `rendering.engine: mtg_design`. The existing source-art R2 upload becomes a rendering prerequisite, and the Laravel session field becomes editable. Existing MTG.Design request throttling, retries, text-size settings, render limits, and release-only cleanup are preserved. Its cache remains under `.cache/mtg_design`.

Python callers constructing a runner should pass the configuration so dependency selection matches the renderer: `runner = build_pipeline(config)`.
