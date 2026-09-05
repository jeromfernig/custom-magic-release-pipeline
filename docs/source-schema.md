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

- `draft_mode`: boolean-like flag such as `true` or `false`. When true, card rendering uses each card's `comment_text` as rendered flavor text. The canonical `flavor_text` field remains the actual flavor text.

## Cards Worksheet

Default columns:

| column | required | notes |
| --- | --- | --- |
| id | yes | Stable card id. Must be unique. |
| name | yes | Duplicate names are warnings. |
| mana_cost | no | Scryfall-style symbols such as `{2}{G}` or compact symbols such as `2GG`, which become `{2}{G}{G}`. |
| mana_value | no | Numeric mana value. |
| supertypes | no | Comma or space separated values, for example `Legendary Snow`. |
| card_types | yes | Comma or space separated card types, for example `Artifact Creature`. |
| subtypes | no | Comma or space separated values, for example `Bird Noble`. |
| oracle_text | no | Rules text. |
| flavor_text | no | Flavor text. |
| comment_text | no | Draft/internal comment text. Used for card rendering only when `Set.draft_mode` is true. |
| art_prompt | no | Prompt used to generate artwork when `artwork_url` is blank. |
| power | no | Creature-specific. |
| toughness | no | Creature-specific. |
| loyalty | no | Planeswalker-specific. |
| rarity | yes | `common`, `uncommon`, `rare`, `mythic`, or `land`. |
| artist | no | Artist credit. If `artwork_url` is filled and `artist` is blank, validation emits a warning. |
| artwork_url | no | HTTP/HTTPS artwork URL or a local path relative to the project root. Takes precedence over `art_prompt`; missing both sources produces a warning and placeholder. Manual local art should normally live under root `artwork/`. |
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

The sheet does not contain `colors` or `color_identity`. Canonical `colors` are inferred only from `mana_cost`, in WUBRG order. `color_identity` is intentionally not part of the canonical model.

If `artwork_url` is blank and `art_prompt` is filled, the artwork step calls the OpenAI image API using `OPENAI_API_KEY`. Generated artwork is saved under the release-agnostic root cache `artwork/<set_id>/<card-name>.png`. The pipeline then updates the Google Sheet:

- `artwork_url`: local generated artwork path relative to the project root, for example `artwork/pokemon-mtg-set/helping-hand.png`.
- `artist`: configured generated artist credit, defaulting to `OpenAI gpt-image-1`.

Source artwork stays exclusively under root `artwork/`. The preparer stores its project-relative location in canonical `artwork_path`, and rendering reads that file directly. `artwork_url` remains the original source reference (or the generated local path written to Sheets). Manual files already under `artwork/` are reused without copying, including art shared between cards. Local files outside that folder are imported into `artwork/<set_id>/` with name-only filenames. Releases store references to artwork, not artwork copies.

When a prompt does not mention any subtype, the pipeline appends the card subtype as the primary Pokemon subject. When a prompt does not mention dimensions/aspect, it appends the default art box dimensions `53 mm x 39 mm`.

The default retry count is `images.generation_retry_count: 1`. Each card retries independently for generation, download, or local-file errors. Explicit input moderation blocks skip retries. After failures exhaust the retry budget, Pillow writes a white landscape PNG containing the error in black text, the step emits a warning, and processing continues with other cards. Placeholders are stored only in `artwork/<set_id>/placeholders/` and referenced by canonical `artwork_path`. They do not update the sheet's `artwork_url` or `artist`, so a later run can generate real art. Rows without either `artwork_url` or `art_prompt` get a placeholder saying `no artwork_url or art_prompt specified in sheet`.

The literal column names are centralized in `config.example.yaml` under `columns`, so the sheet can be renamed without changing exporter code.
