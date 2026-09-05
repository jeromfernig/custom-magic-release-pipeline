from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
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
    power: str = "power"
    toughness: str = "toughness"
    loyalty: str = "loyalty"
    rarity: str = "rarity"
    artist: str = "artist"
    artwork_url: str = "artwork_url"
    layout: str = "layout"
    render_profile: str = "render_profile"
    include: str = "include"


class RendererConfig(BaseModel):
    base_url: str = "http://localhost:8080"
    format: str = "PNG"
    scale: int = 3
    render_limit: int | None = 10


class PathsConfig(BaseModel):
    release_root: Path = Path("releases")
    artwork_root: Path = Path("artwork")


class PrintConfig(BaseModel):
    page_size: str = "A4"
    orientation: str = "landscape"
    card_width_mm: float = 63
    card_height_mm: float = 88
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
    generated_artist_credit: str = "OpenAI gpt-image-1"
    generation_model: str = "gpt-image-1"
    generation_size: str = "1536x1024"
    generation_quality: str = "medium"
    generation_retry_count: int = Field(default=1, ge=0)
    default_art_box_mm: str = "53 mm x 39 mm"


class RenderProfile(BaseModel):
    renderer_options: dict[str, Any] = Field(default_factory=dict)


class AppConfig(BaseModel):
    source: SourceConfig = Field(default_factory=SourceConfig)
    columns: ColumnMapping = Field(default_factory=ColumnMapping)
    renderer: RendererConfig = Field(default_factory=RendererConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    print: PrintConfig = Field(default_factory=PrintConfig)
    draft: DraftConfig = Field(default_factory=DraftConfig)
    images: ImagesConfig = Field(default_factory=ImagesConfig)
    render_profiles: dict[str, RenderProfile] = Field(default_factory=lambda: {"standard": RenderProfile()})


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return AppConfig.model_validate(data)


def write_example_config(path: Path) -> None:
    data = AppConfig().model_dump(mode="json")
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)
