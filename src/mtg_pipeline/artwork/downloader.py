from __future__ import annotations

from pathlib import Path

import requests

from mtg_pipeline.models import CardDefinition
from mtg_pipeline.utils.filenames import with_suffix_for_content_type


class ArtworkDownloader:
    def __init__(self, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds

    def download(self, cards: list[CardDefinition], artwork_dir: Path) -> list[Path]:
        artwork_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for card in cards:
            if not card.artwork_url:
                continue
            base_path = artwork_dir / f"{card.file_stem}.jpg"
            response = requests.get(card.artwork_url, timeout=self.timeout_seconds)
            if response.status_code >= 400:
                raise RuntimeError(f"Artwork download failed for {card.collector_number} - {card.name}: HTTP {response.status_code}.")
            path = with_suffix_for_content_type(base_path, response.headers.get("content-type"))
            if path.exists():
                card.artwork_filename = path.name
                paths.append(path)
                continue
            path.write_bytes(response.content)
            card.artwork_filename = path.name
            paths.append(path)
        return paths
