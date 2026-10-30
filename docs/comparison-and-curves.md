# Version comparisons and mana curves

## Saved-version comparison (CLI only)

From the repository root:

```powershell
python -m mtg_pipeline.compare_versions 0.2.0 0.3.0
```

After installing/updating the project with `pip install -e .`, the equivalent console command is `mtg-pipeline-compare 0.2.0 0.3.0`.

Both versions must have saved `data/set.json` and `data/cards.json`. The default set is `pokemon-mtg-set` and the default root is `releases`. Use `--set-id other-set`, `--release-root path/to/releases`, or `--output comparison.pdf` to override these. The output defaults to `releases/<set>/comparisons/<old>-vs-<new>.pdf`.

The tool matches exact card IDs, independently of names, collector numbers, file order, or Sheet row positions. It detects changed, added, removed, and unchanged cards. Changed entries include full old and new canonical field values, plus side-by-side saved card renderings; long field diffs continue onto additional pages rather than being truncated. Added/removed entries show the available version's rendering. Unchanged cards are listed without images. Set metadata changes are summarized separately.

The default compares canonical content, including rules/flavor/comment text, names, mana, rarity, archetypes, artist, artwork URL/prompt/hash, collector numbers, and render profile. Operational fields (`source_row_number`, generated image filenames, local artwork/cache paths, public upload URLs/object keys, and render-cache keys) are excluded so build location changes alone do not mark a card changed. `--include-build-fields` includes these too. Unknown extra canonical fields are compared. Missing newer fields, such as `archetype`, use the canonical model's defaults, allowing old release JSON to be compared with new versions.

The comparison uses saved release images first and optional local immutable render-cache paths second. Missing/corrupt images get explicit placeholders. `--project-root` sets the root for cache lookups when running outside this repository. Image files and source models are never modified. The report embeds appropriately sized JPEG previews for manageable PDF size; it is not a pixel-by-pixel rendering comparison. It does not run the pipeline, regenerate art/cards, contact Google Sheets, fetch remote images, or upload anything. Duplicate IDs and mismatched set IDs are rejected. Renaming an ID appears as a removal and addition.

## Mana-curve analysis (pipeline and Streamlit)

**Analyze Mana Curves** is selected by default. It depends only on canonicalization, so it can run without artwork generation, rendering, R2 hosting, or Draftmancer export. The settings in **Draftmancer pack layout** are still used when the Draftmancer export checkbox is off. Invalid layouts and requested empty rarity sheets block running with analysis selected; set unused slots to zero or uncheck analysis to run unrelated steps.

Outputs live under the release's `analysis` folder:

- `<code>-<version>-mana-curves.pdf`: landscape A4, one chart per color in W/U/B/R/G/C order.
- `<code>-<version>-mana-curves-W.png`, etc.: the same charts, displayed as tabs in Streamlit results.
- `<code>-<version>-mana-curves.json`: the exact settings, expected sheet draws, per-card ASFAN, excluded land IDs, inferred mana-value IDs, and chart values.

Each bar represents one mana value, split into six segments: common creatures/noncreatures, uncommon creatures/noncreatures, and rare creatures/noncreatures. Mythics share the rare display category while retaining the actual probability assigned by the pack layout. Creatures are identified by the canonical Creature type; other nonland types, including uncrewed Vehicles, count as noncreatures. Zero-valued segments remain in the legend. All colors share a y-axis scale for comparison.

The y-axis measures expected cards per booster, following [Wizards' explanation of as-fan](https://magic.wizards.com/en/news/making-magic/volume-control-2014-10-06). For a simple rarity sheet, each card contributes:

`expected draws from that sheet per booster / number of cards on the sheet`

For example, if a set contains 100 commons and a booster contains 10 commons, each common contributes `10/100 = 0.1` to its mana-value/color/type bucket. This denominator is the entire sheet, not just commons of that color. A creature and a noncreature of the same rarity/mana value contribute equally if drawn from the same sheet.

For weighted pack layouts, expected slot draws are averaged by layout weight. For slots choosing between sheets, each source sheet's relative weight is applied before dividing by its card count. Supported predetermined layout sequences are averaged across their positions, including inherited or explicitly overridden layout weights. Separate Rare/Mythic sheets with weights 7:1 preserve that probability split. A combined RareMythic sheet instead uses uniform weighting across its combined contents, matching this export's sheet definition; it does not invent a 7:1 ratio.

Conventions:

- Multicolor cards contribute fully to each matching colored chart; these color totals overlap and should not be added to get whole-pack ASFAN. Cards with no colors go only into Colorless.
- Lands are excluded from spell curves but stay in their sheet denominators. This prevents the normalization from inflating the remaining spells. The JSON identifies the excluded IDs.
- Canonical `mana_value` is authoritative. If absent, it is derived from `mana_cost`, with X/Y/Z equal to zero, hybrid symbols using their higher mana value, and Phyrexian colored symbols equal to one. An absent cost gives zero. Unsupported symbols require an explicit mana value. Negative or nonfinite values fail clearly.
- This is a theoretical uniform-sheet/rarity-weighted analysis. It does not simulate color balancing, duplicate protection across overlapping sheets, finite-pool depletion, or print-run collation. Such collation rules may change actual draw probabilities. The calculation and charts state this limitation.
- Only cards that can occur in the configured booster contribute to charts. Cards in unused sheets have ASFAN zero and are recorded in JSON. Rendered image availability and print-copy settings do not affect the analysis.
