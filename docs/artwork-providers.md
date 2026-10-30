# Gemini and artwork providers

Install dependencies from the repository root:

```powershell
python -m pip install -e ".[dev]"
```

Provide `GEMINI_API_KEY` in the execution environment, or in the ignored `.env` beside your YAML config. OpenAI uses `OPENAI_API_KEY`. Do not put keys in YAML, prompts, source files, or committed files. Gemini uses the billed Google AI Studio API through the official `google-genai` SDK; Sheets OAuth and R2 configuration are separate and unchanged. No new cloud infrastructure is required.

The `images` section of `config.example.yaml` includes:

```yaml
images:
  generation_provider: openai
  generation_model: gpt-image-2.5-sunburst
  generated_artist_credit: OpenAI gpt-image-2.5-sunburst
  generation_size: 1536x1024
  generation_quality: medium
  generation_retry_count: 1
  default_art_folder: /artwork/pokemon-mtg-set
  gemini_model: gemini-3.1-flash-image
  gemini_aspect_ratio: "3:2"
  gemini_image_size: "1K"
  generation_timeout_seconds: 120
```

`generation_provider` remains `openai` by default. Set it to `gemini` to generate directly with Gemini. `gemini_model` accepts `gemini-3.1-flash-image` (default), `gemini-3.1-flash-lite-image`, `gemini-3-pro-image`, or another model your account supports. Aspect ratio and image size are Gemini settings; OpenAI retains its existing size and quality settings. Gemini output is decoded, validated, and saved as PNG at the model's native dimensions, without resizing.

Run the UI with `python -m streamlit run ui/app.py`. The sidebar's **Artwork configuration** exposes provider, Gemini model, and **Default art folder**. UI edits apply to that run; edit your YAML to persist settings. `/artwork/pokemon-mtg-set` means the folder inside this repository, including on Windows. The folder must stay inside the project and outside releases.

For a card whose artist is empty or whitespace:

1. Generate with configured OpenAI Sunburst and the existing prompt composition.
2. On failure, generate with configured Gemini Flash Image using exactly the same composed prompt.
3. On failure, look for the card name in the default art folder. Matching ignores case, slugifies names, and accepts PNG, JPEG, WebP, and SVG. For example, `Helping Hand.jpg` or `helping-hand.png` matches Helping Hand. This fallback is credited `OpenAI gpt-image-1`.
4. Otherwise create the usual white landscape placeholder containing all provider-attempt errors and the default-folder error. The full error text also lives in PNG Description metadata and run warnings.

An existing artwork URL does not bypass this flow for a blank artist. Explicit Gemini provider selection starts at step 2. A missing prompt skips API calls and still tries the default folder. A successful generation or default-folder fallback writes the actual project-relative filename and artist back to Sheets. If the Sheet update fails, a later blank-artist run can generate another numbered image. Nonblank artist rows retain existing URL/manual-art handling, caches, and moderation-marker behavior. Clear an old `moderation error` marker to request the new chain. Neither provider rewrites prompts; the existing subtype, dimensions, and illustration-only additions remain unchanged.

Art files are never overwritten by generation, imports, downloads, or placeholders. Collisions use `card-name-2.png`, `card-name-3.png`, etc.; exclusive creation protects existing files across concurrent writers. Placeholders stay in `artwork/<set_id>/placeholders/`, never become completed Sheet artwork, and do not set permanent moderation markers for blank-artist rows. Existing card IDs, collector numbers, and release naming are unchanged.

Gemini uses the SDK's documented `models.generate_content` API. Prompt blocks, image safety rejections, empty/text-only responses, invalid images, and permanent API errors become recoverable artwork failures. Rate limits (429), selected server errors, and transport failures retry with exponential backoff, honoring numeric Retry-After up to 30 seconds. Each provider has the configured retry budget; safety/permanent failures do not retry in Gemini. SDK-level Gemini retries are disabled to avoid multiplying the budget. Final errors retain each failed Gemini API attempt's type and status while excluding raw responses and credential-bearing URLs.

The `mtg_pipeline` logger records JSON generation events with UTC timestamp, provider, model, outcome, actual dimensions, and returned token usage. Missing dimensions/usage are null. Raw prompts, keys, headers, and API response bodies are not logged. Costs are not estimated from hardcoded prices; use token usage and your provider billing dashboard. Successful fallbacks retain earlier errors as run warnings.

## Single-image smoke test (paid, explicit opt-in)

Save one existing Pokémon MTG art prompt as UTF-8 `prompt.txt`. This command sends that file's prompt unchanged, makes one Gemini generation request without retries, and saves a numbered PNG if the target already exists:

```powershell
python -m mtg_pipeline.smoke_image --live --provider gemini --prompt-file prompt.txt --output artwork/gemini-smoke.png
```

Use `--config config.local.yaml` for a separate config, or `--model gemini-3-pro-image` to override the model. `--provider openai` checks the existing OpenAI provider. The smoke command does not read or modify Sheets, render cards, publish to R2, or invoke fallbacks. Omit `--live` and it exits before any API call.

Automated mocked tests make no paid calls. A guard blocks unmocked SDK HTTP requests. The separate live test is skipped unless explicitly enabled:

```powershell
python -m pytest -q
# Only after GEMINI_API_KEY has been supplied; this makes a paid call:
$env:RUN_GEMINI_LIVE_TEST = "1"
python -m pytest tests/test_gemini_live.py -q
Remove-Item Env:RUN_GEMINI_LIVE_TEST
```

Model access, billing, regional availability, and quotas must be verified in your project by running the opt-in smoke test. All Gemini-generated images include Google's SynthID watermark. Model availability and supported resolutions can vary; there is no silent substitution of model IDs.

Official references: [Google image generation](https://ai.google.dev/gemini-api/docs/image-generation), [Generate Content image generation](https://ai.google.dev/gemini-api/docs/generate-content/image-generation), and [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/).
