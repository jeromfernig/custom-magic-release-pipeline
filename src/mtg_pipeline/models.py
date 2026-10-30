from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mtg_pipeline.utils.filenames import safe_card_stem, slugify

Rarity = Literal["common", "uncommon", "rare", "mythic", "land"]


class SetDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    set_id: str
    set_name: str
    set_code: str
    version: str
    release_date: date | None = None
    draft_mode: bool = False
    set_icon_mythic: str | None = None
    set_icon_rare: str | None = None
    set_icon_uncommon: str | None = None
    set_icon_common: str | None = None

    @field_validator("set_code")
    @classmethod
    def normalize_set_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("set_id")
    @classmethod
    def normalize_set_id(cls, value: str) -> str:
        return slugify(value)

    @field_validator("release_date", mode="before")
    @classmethod
    def blank_release_date_is_absent(cls, value: object) -> object:
        if value is None or str(value).strip() == "":
            return None
        return value

    @field_validator("set_icon_mythic", "set_icon_rare", "set_icon_uncommon", "set_icon_common", mode="before")
    @classmethod
    def blank_set_icon_is_absent(cls, value: object) -> object:
        if value is None or str(value).strip() == "":
            return None
        return str(value).strip()


class CardDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    record_type: Literal["card", "linked_face"] = "card"
    link_id: str | None = None
    link_type: Literal["prepare", "transform"] | None = None
    set_code: str
    collector_number: str = ""
    rarity: Rarity
    mana_cost: str | None = None
    mana_value: float | None = None
    colors: list[str] = Field(default_factory=list)
    supertypes: list[str] = Field(default_factory=list)
    card_types: list[str] = Field(default_factory=list)
    subtypes: list[str] = Field(default_factory=list)
    type_line: str = ""
    oracle_text: str | None = None
    flavor_text: str | None = None
    comment_text: str | None = None
    art_prompt: str | None = None
    archetype: list[str] = Field(default_factory=list)
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None
    battle_defense: str | None = None
    color_indicator: list[str] = Field(default_factory=list)
    artist: str | None = None
    artwork_url: str | None = None
    layout: str = "normal"
    render_profile: str = "standard"
    rendered_image_filename: str | None = None
    artwork_filename: str | None = None
    artwork_path: str | None = None
    artwork_public_url: str | None = None
    artwork_sha256: str | None = None
    artwork_object_key: str | None = None
    rendered_public_url: str | None = None
    rendered_image_path: str | None = None
    render_cache_key: str | None = None
    source_row_number: int | None = None

    @field_validator("rarity", mode="before")
    @classmethod
    def normalize_rarity(cls, value: str) -> str:
        return str(value).strip().lower()

    @field_validator("colors", "color_indicator", mode="before")
    @classmethod
    def parse_color_list(cls, value: object) -> list[str]:
        return _parse_list(value, upper=True)

    @field_validator("archetype", mode="before")
    @classmethod
    def parse_archetypes(cls, value: object) -> list[str]:
        if value is None:
            return []
        items = value if isinstance(value, list) else str(value).split(",")
        return [str(item).strip() for item in items if item is not None and str(item).strip()]

    @field_validator("supertypes", "card_types", "subtypes", mode="before")
    @classmethod
    def parse_type_list(cls, value: object) -> list[str]:
        return _parse_list(value, upper=False)

    @model_validator(mode="after")
    def derive_type_line(self) -> "CardDefinition":
        if not self.type_line:
            left = " ".join([*self.supertypes, *self.card_types]).strip()
            right = " ".join(self.subtypes).strip()
            self.type_line = f"{left} - {right}" if right else left
        return self

    @property
    def renderer_flavor_text(self) -> str:
        return self.flavor_text or ""

    @property
    def file_stem(self) -> str:
        return safe_card_stem(self.collector_number, self.name)

    @property
    def expected_rendered_filename(self) -> str:
        return self.rendered_image_filename or f"{self.file_stem}.jpg"


def _parse_list(value: object, *, upper: bool) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        items = value
    else:
        text = str(value).replace(",", " ").strip()
        items = [part for part in text.split() if part]
    parsed = [str(item).strip() for item in items if str(item).strip()]
    return [item.upper() for item in parsed] if upper else parsed


class SourceData(BaseModel):
    set_metadata: dict[str, object]
    card_rows: list[dict[str, object]]


class ReleaseDefinition(BaseModel):
    set: SetDefinition
    cards: list[CardDefinition]
    release_dir: Path
