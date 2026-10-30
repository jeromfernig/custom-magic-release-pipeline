# Source Schema

The PoC reads one Google spreadsheet and two worksheets. Worksheet names are configurable in YAML.

## Set Worksheet

Use either key/value rows:

| key | value |
| --- | --- |
| set_id | pokemon-mtg-set |
| set_name | Pokemon MTG Set |
| set_code | PKM |
| version | 0.3.0 |
| draft_mode | false |

Or a one-row table:

| set_id | set_name | set_code | version | draft_mode |
| --- | --- | --- | --- | --- |
| pokemon-mtg-set | Pokemon MTG Set | PKM | 0.3.0 | false |

Required fields:

- `set_name`
- `set_code`
- `version`
- `set_id`

`version` must look like semantic versioning, for example `0.3.0`.

`set_id` is the stable identifier for release references and public image URL paths. It should be lowercase and path-safe, using only letters, numbers, and hyphens, for example `pokemon-mtg-set`. You may change `set_name` later; avoid changing `set_id` once artifacts are shared.

Optional fields:

- `draft_mode`: boolean-like flag such as `true` or `false`. Crucible shows each card ID after its collector number when enabled.
- `set_icon_common`, `set_icon_uncommon`, `set_icon_rare`, `set_icon_mythic`: public image URLs for the matching rarity symbol in both Crucible and MTG.Design. `land` cards use `set_icon_common`. Unset icons preserve the selected renderer's default symbol.

## Cards Worksheet

Default columns:

| column | required | notes |
| --- | --- | --- |
| id | yes | Stable card id. Must be unique. |
| record_type | no | Blank/`card` for a physical card; `linked_face` for its secondary record. |
| link_id | no | ID of an included `linked_face` row. Set only on the main row, together with `link_type`. |
| link_type | no | `prepare` for an inset prepared spell, or `transform` for a separate reverse face, including battles. |
| battle_defense | for battles | Defense value, mapped to Crucible's `battleDefense`. |
| color_indicator | no | Comma/space separated W/U/B/R/G values for a face's color indicator and canonical colors, especially when it has no mana cost. |
| name | yes | Duplicate names are warnings. |
| mana_cost | no | Scryfall-style symbols such as `{2}{G}` or compact symbols such as `2GG`, which become `{2}{G}{G}`. |
| mana_value | no | Numeric mana value. |
| supertypes | no | Comma or space separated values, for example `Legendary Snow`. |
| card_types | yes | Comma or space separated card types, for example `Artifact Creature`. |
| subtypes | no | Comma or space separated values, for example `Bird Noble`. |
| oracle_text | no | Rules text. |
| flavor_text | no | Flavor text. |
| comment_text | no | Draft/internal comment text. It is not sent to either rendering engine. |
| art_prompt | no | Prompt used to generate artwork when `artist` or `artwork_url` is blank. Existing prompt composition is unchanged across providers. |
| archetype | no | Comma-separated archetype values, e.g. `A, B` or `Blue Tempo, Graveyard`. Values are trimmed; empty entries are discarded. Canonical `archetype` is a list, defaulting to `[]` when the column is absent or blank. A card appears on every matching archetype PDF page. |
| power | no | Power for creature cards and Vehicle artifacts. Vehicles require both power and toughness. |
| toughness | no | Toughness for creature cards and Vehicle artifacts. Vehicles require both power and toughness. |
| loyalty | no | Planeswalker-specific. |
| rarity | yes | `common`, `uncommon`, `rare`, `mythic`, or `land`. |
| artist | no | Blank requests OpenAI → Gemini → default art folder → white error image. Generated art receives the provider/model credit; default-folder art receives `OpenAI gpt-image-1`. Existing `moderation error` markers still skip generation; clear the marker to enable the new chain. |
| artwork_url | no | HTTP/HTTPS artwork URL or a local path relative to the project root. Used first when `artist` is filled. Blank artists request fresh generation even with an existing URL. Manual local art should normally live under root `artwork/`. |
| layout | no | Defaults to `normal`; canonical data always contains a layout value. |
| render_profile | yes | Must exist in `render_profiles` config. |
| include | no | `false`, `no`, `0`, `skip`, or `disabled` excludes a row. |

The pipeline derives canonical `type_line` from `supertypes`, `card_types`, and `subtypes`, for example `Legendary Creature - Bird Noble`. A legacy/configured `type_line` column can still be mapped for compatibility, but it is not required by the current sheet schema.

Do not include `collector_number` in the sheet. The pipeline assigns collector numbers in canonicalization:

1. Colorless nonartifact
2. White
3. Blue
4. Black
5. Red
6. Green
7. Multicolor
8. Artifact
9. Nonbasic land
10. Basic land

Within each group, cards are sorted alphabetically by name. Numbers are assigned sequentially starting at 1 and zero-padded for stable filenames, for example `001-ancient-null.png`.

The sheet does not contain `colors` or `color_identity`. Canonical `colors` come from an explicit `color_indicator`, or otherwise from `mana_cost` in WUBRG order. `color_identity` is intentionally not part of the canonical model.

Linked rows require their own ID, name, card type, and oracle text. Blank rarity, render profile, and archetypes inherit from the main row. Collector numbers are generated only for physical cards and shared with their linked faces. Prepared spells share the main illustration; transforming backs have their own artwork flow. See [linked cards](linked-cards.md) for examples, adding/removing links, outputs, and export limitations.

If `artist` is blank, the artwork step tries OpenAI, Gemini, and then same-name files in `images.default_art_folder`. Explicit `images.generation_provider: gemini` starts with Gemini. If the artist is already filled but `artwork_url` is blank, the configured provider generates art. Generated artwork is saved under `artwork/<set_id>/<card-name>.png`, adding `-2`, `-3`, etc. when needed to preserve existing files. See [artwork providers](artwork-providers.md). On success the pipeline updates the Google Sheet:

- `artwork_url`: local generated artwork path relative to the project root, for example `artwork/pokemon-mtg-set/helping-hand.png`.
- `artist`: configured OpenAI credit (default `OpenAI gpt-image-2.5-sunburst`), `Google <gemini-model>`, or `OpenAI gpt-image-1` for same-name default-folder art.

Source artwork stays exclusively under root `artwork/`. The preparer stores its project-relative location in canonical `artwork_path`, and rendering reads that file directly. `artwork_url` remains the original source reference (or the generated local path written to Sheets). Manual files already under `artwork/` are reused without copying, including art shared between cards. Local files outside that folder are imported into `artwork/<set_id>/` with name-only filenames. Releases store references to artwork, not artwork copies.

When a prompt does not mention any subtype, the pipeline appends the card subtype as the primary Pokemon subject. When a prompt does not mention dimensions/aspect, it appends the default art box dimensions `53 mm x 39 mm`.

The default retry count is `images.generation_retry_count: 1`. Each card retries independently. Gemini retries transient API errors with bounded backoff, without retrying safety or permanent errors. After the applicable providers and folder fallback fail, Pillow writes a white landscape PNG containing all errors in black text. The step emits warnings and continues with other cards. Placeholders are stored only in `artwork/<set_id>/placeholders/` and referenced by canonical `artwork_path`; they never become completed Sheet artwork. Blank-artist failures do not write permanent moderation markers. Blank-artist rows without a prompt still check the default art folder before making a placeholder; other rows without either source retain their existing shared/missing-art placeholders.

The literal column names are centralized in `config.example.yaml` under `columns`, so the sheet can be renamed without changing exporter code.
