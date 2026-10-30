from __future__ import annotations

from pathlib import Path
import os
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field, field_validator


class SourceConfig(BaseModel):
    spreadsheet_id: str = ""
    set_worksheet: str = "Set"
    cards_worksheet: str = "Cards"
    credentials_path: Path = Path("credentials.json")
    token_path: Path = Path("token.json")
    scopes: list[str] = Field(default_factory=lambda: ["https://www.googleapis.com/auth/spreadsheets"])

    @field_validator("scopes", mode="before")
    @classmethod
    def normalize_scopes(cls, value: object) -> list[str]:
        if value is None or value == "":
            return ["https://www.googleapis.com/auth/spreadsheets"]
        if isinstance(value, str):
            return [scope for scope in value.replace(",", " ").split() if scope]
        if isinstance(value, list):
            scopes = [str(scope).strip() for scope in value if str(scope).strip()]
            return scopes or ["https://www.googleapis.com/auth/spreadsheets"]
        raise TypeError("source.scopes must be a YAML list or a whitespace/comma-separated string.")


class ColumnMapping(BaseModel):
    id: str = "id"
    record_type: str = "record_type"
    link_id: str = "link_id"
    link_type: str = "link_type"
    battle_defense: str = "battle_defense"
    color_indicator: str = "color_indicator"
    name: str = "name"
    mana_cost: str = "mana_cost"
    mana_value: str = "mana_value"
    super_types: str = "supertypes"
    card_types: str = "card_types"
    subtypes: str = "subtypes"
    type_line: str = "type_line"
    oracle_text: str = "oracle_text"
    flavor_text: str = "flavor_text"
    comment_text: str = "comment_text"
    art_prompt: str = "art_prompt"
    archetype: str = "archetype"
    power: str = "power"
    toughness: str = "toughness"
    loyalty: str = "loyalty"
    rarity: str = "rarity"
    artist: str = "artist"
    artwork_url: str = "artwork_url"
    layout: str = "layout"
    render_profile: str = "render_profile"
    include: str = "include"


class MtgDesignTextSizingConfig(BaseModel):
    enabled: bool = True
    maximum_size: int = Field(default=38, ge=1)
    minimum_size: int = Field(default=20, ge=1)
    estimated_characters_per_line: int = Field(default=60, ge=1)
    size_decrease_per_line: float = Field(default=1.25, gt=0)
    warning_below_size: int = Field(default=29, ge=1)


class MtgDesignConfig(BaseModel):
    base_url: str = "https://mtg.design"
    render_limit: int | None = 10
    timeout_seconds: int = Field(default=60, gt=0)
    max_attempts: int = Field(default=4, ge=1, le=4)
    request_delay_seconds: float = Field(default=2, ge=2)
    defaults: dict[str, Any] = Field(default_factory=lambda: {
        "language": "EN", "designer": "Mockingbird", "card-border": "black",
        "watermark": 0, "stars": 0, "edit": False, "text-size": 38,
    })
    text_sizing: MtgDesignTextSizingConfig = Field(default_factory=MtgDesignTextSizingConfig)
    postprocess: "MtgDesignPostprocessConfig" = Field(default_factory=lambda: MtgDesignPostprocessConfig())


class MtgDesignCleanupMask(BaseModel):
    # Coordinates use postprocess.reference_size. right/bottom are exclusive.
    name: str
    rectangle: tuple[int, int, int, int]
    sample: tuple[int, int]


class MtgDesignPostprocessConfig(BaseModel):
    enabled: bool = True
    reference_size: tuple[int, int] = (744, 1039)
    jpeg_quality: int = Field(default=95, ge=1, le=100)
    masks: list[MtgDesignCleanupMask] = Field(default_factory=lambda: [
        MtgDesignCleanupMask(
            name="card_icon_and_not_for_sale",
            rectangle=(166, 970, 310, 991),
            sample=(320, 981),
        ),
    ])


class R2Config(BaseModel):
    bucket: str = ""
    public_url: str = ""
    artwork_prefix: str = "artwork"
    rendered_cards_prefix: str = "releases"


class CacheConfig(BaseModel):
    artwork: Path = Path(".cache/artwork")
    mtg_design: Path = Path(".cache/mtg_design")
    crucible: Path = Path(".cache/mtg_crucible")


class RenderingConfig(BaseModel):
    engine: Literal["crucible", "mtg_design"] = "crucible"


class CrucibleConfig(BaseModel):
    repository_path: Path = Path("../mtg-crucible")
    node_executable: str = "node"
    quality: Literal["low", "medium", "high"] = "high"
    timeout_seconds: int = Field(default=120, gt=0)
    render_limit: int | None = Field(default=None, ge=1)
    designer: str = "Mockingbird"


class PathsConfig(BaseModel):
    release_root: Path = Path("releases")
    artwork_root: Path = Path("artwork")


class PrintConfig(BaseModel):
    page_size: str = "A4"
    orientation: str = "landscape"
    card_width_mm: float = 63
    card_height_mm: float = 88
    bleed_enabled: bool = True
    bleed_mm: float = Field(default=2, ge=0)
    copies_by_rarity: dict[str, int] = Field(default_factory=lambda: {
        "common": 1,
        "uncommon": 1,
        "rare": 1,
        "mythic": 1,
        "land": 1,
    })


class DraftConfig(BaseModel):
    booster_layout: dict[str, Any] = Field(default_factory=lambda: {
        "layouts": {
            "Default": {
                "weight": 1,
                "slots": {"Common": 10, "Uncommon": 3, "RareMythic": 1},
            }
        }
    })


class ImagesConfig(BaseModel):
    public_image_base_url: str = ""
    generation_provider: Literal["openai", "gemini"] = "openai"
    default_art_folder: str = "/artwork/pokemon-mtg-set"
    gemini_model: str = "gemini-3.1-flash-image"
    gemini_aspect_ratio: str = "3:2"
    gemini_image_size: str = "1K"
    generation_timeout_seconds: int = Field(default=120, gt=0)
    generated_artist_credit: str = "OpenAI gpt-image-2.5-sunburst"
    generation_model: str = "gpt-image-2.5-sunburst"
    generation_size: str = "1536x1024"
    generation_quality: str = "medium"
    generation_retry_count: int = Field(default=1, ge=0)
    default_art_box_mm: str = "53 mm x 39 mm"


class RenderProfile(BaseModel):
    renderer_options: dict[str, Any] = Field(default_factory=dict)
    crucible_options: dict[str, Any] = Field(default_factory=dict)


class AppConfig(BaseModel):
    source: SourceConfig = Field(default_factory=SourceConfig)
    columns: ColumnMapping = Field(default_factory=ColumnMapping)
    mtg_design: MtgDesignConfig = Field(default_factory=MtgDesignConfig)
    rendering: RenderingConfig = Field(default_factory=RenderingConfig)
    crucible: CrucibleConfig = Field(default_factory=CrucibleConfig)
    r2: R2Config = Field(default_factory=R2Config)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    print: PrintConfig = Field(default_factory=PrintConfig)
    draft: DraftConfig = Field(default_factory=DraftConfig)
    images: ImagesConfig = Field(default_factory=ImagesConfig)
    render_profiles: dict[str, RenderProfile] = Field(default_factory=lambda: {"standard": RenderProfile()})


def load_config(path: Path) -> AppConfig:
    for name, value in dotenv_values(path.resolve().parent / ".env", encoding="utf-8-sig", interpolate=False).items():
        if value and not os.environ.get(name):
            os.environ[name] = value
    if not path.exists():
        return AppConfig()
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return AppConfig.model_validate(data)


def write_example_config(path: Path) -> None:
    data = AppConfig().model_dump(mode="json")
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)
