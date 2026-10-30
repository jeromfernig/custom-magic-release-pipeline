# Linked cards

Each face occupies its own Sheet row and flat canonical JSON record. Existing rows default to `record_type: card`. Add these optional columns (their names can be mapped under `columns` in YAML):

| Field | Main row | Linked row |
| --- | --- | --- |
| `id` | Stable physical-card ID, e.g. `UG04` | Unique stable ID, e.g. `UG04_linked` |
| `record_type` | Blank or `card` | `linked_face` |
| `link_id` | Exact linked-row ID | Blank |
| `link_type` | `prepare` or `transform` | Blank |
| `battle_defense` | Required for a Battle | Required if this face is itself a Battle |
| `color_indicator` | Optional W/U/B/R/G list | Optional W/U/B/R/G list; particularly useful without a mana cost |

Only the main row points to the linked row. Links must reference an included row marked `linked_face`. Duplicate IDs, missing targets, self-links, chains/cycles, multiple parents, and orphan linked rows are rejected. IDs are case-sensitive in the pipeline; use consistent casing in Sheets too.

Linked rows require their own ID, name, card type and nonblank oracle text. Creature linked faces also require power/toughness; planeswalker faces require loyalty. Blank rarity, render profile and archetypes inherit from the main row. Set code comes from the set. Collector numbers are generated for physical cards only and shared by both records. Mana cost, mana value, names, types, rules, stats, artwork and artist do not inherit. Leave a cost blank when that face has no printed mana cost. Explicit color indicators set that face's canonical colors; otherwise colors are derived from its own cost.

## Tangela example

On **ChatGPT's copy of Cards**, `UG04` (Tenacious Tangle) keeps:

```text
Reach
This creature enters prepared.
```

Its `link_id` is `UG04_linked`, and `link_type` is `prepare`. The new row immediately below it contains:

```yaml
id: UG04_linked
record_type: linked_face
name: Crushing Vines
mana_cost: '{2}{G}'
mana_value: 3
card_types: Instant
color_indicator: G
oracle_text: |
  Choose one —
  • Destroy target creature with flying.
  • Destroy target artifact.
```

The spell's cost and rules were taken from the existing main-row text. Its rarity and render profile can stay blank. The prepared inset uses Tangela's illustration, so it neither generates separate artwork nor receives a separate card image/print copy. Its canonical rendering reference points to the combined image after rendering.

## Battles and separate back faces

Set the front's `card_types` to `Battle`, optionally `subtypes: Siege`, fill `battle_defense`, and use `link_type: transform`. The linked row describes the reverse face with its own type, rules, stats, and artwork fields. Its artwork follows the existing provider/manual-art flow. As elsewhere, provide an artist credit when reusing an existing `artwork_url`; a blank artist requests generation/fallback.

Crucible receives `linkedCard`, `linkType`, `battleDefense` and `colorIndicator`. It renders the pair together. Both JPEGs must be valid before caching the pair. The main output keeps its existing filename; the back uses `<collector>-<linked-name>-back.jpg`. Landscape images are rotated to fit a portrait physical card. Cache identity includes the linked face's rendered fields and the back artwork digest. Editing, adding or removing a link invalidates the main render as needed; an unchanged pair reuses its cache. Render limits count physical cards, not faces.

## Add or remove a link in another version

Add the new row, set its `record_type`, then fill `link_id` and `link_type` on its main row. Row order is irrelevant. To remove the relationship, clear both link fields **and delete or disable (`include: disabled`) the linked row**. You can also turn that row into an independent `card` with its own rarity if that is the intended design. Do not leave an included orphan `linked_face`.

Each build resolves links from its current source alone. Previous version JSON and art files are not modified. Canonicalization prunes removed faces' image files from the current release's `cards` folder, and those faces are excluded from PDFs, hosting and exports. Shared render caches and source artwork remain available. Saved-version comparison reports the main row as changed and the secondary ID as added/removed. Adding a secondary row does not consume an extra collector number or shift physical-card numbers.

## Counts, PDFs and exports

- Draftmancer sheets, booster probabilities, mana curves and manifest card/rarity counts include physical cards only. The manifest also reports total `record_count`.
- Print, note and archetype PDFs include a prepared card once. Transforming fronts/backs appear adjacent, with the back using the front's print-copy count. Print sheets are cut-and-pair sheets; this does not implement automatic duplex registration.
- R2 uploads one combined prepared image or both transform images. Prepared records share the main public URL.
- Draftmancer exports one custom-card entry per physical card, using its documented `back` object for transforming cards. Prepared rules are appended to the front's text and shown in its combined image; no independent spell is placed in booster sheets. See [Draftmancer's custom-card format](https://www.draftmancer.com/cubeformat.html).
- CubeCobra exports one row per physical card and fills `image Back URL` for transforming cards. Prepared details remain visible on the combined image.
- Cockatrice emits linked front/back entries for transform cards, with `side: back` and a `related attach="transform"` relationship. Prepared rules are included in the front text. See [Cockatrice's v4 format](https://github.com/Cockatrice/Cockatrice/wiki/Custom-Cards-%26-Sets).
- MTG.Design linked rendering is explicitly rejected; select Crucible for these cards. Other Crucible link types are not yet exposed by this pipeline.

## Differences worksheet

The live `Differences!A1` spill formula matches the union of record IDs and column headers from `Cards` and `ChatGPT's copy of Cards`. It compares looked-up values rather than physical coordinates. Source rows and columns can be inserted, moved or removed without shifting comparisons. Newly added IDs/headers appear automatically. Ambiguous duplicate IDs are labeled `Duplicate ID` instead of comparing an arbitrary row. The former legend is retained as a note on A1, leaving the spill area free for additional headers.

The formula generator lives in `mtg_pipeline.sources.sheet_differences.difference_formula`. It reads all columns in the first 1,011 source rows (the verified grid size when installed). If the set grows beyond that bound, regenerate it with a larger `max_rows` that fits both source grids and ensure the Differences grid has room for the union. ID/header spelling must remain consistent. Source column reordering does not change matching. Blank values compare as blank; displayed numeric/string equivalents compare equally.
