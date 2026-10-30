from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from mtg_pipeline.config import MtgDesignPostprocessConfig
from mtg_pipeline.utils.cache import atomic_write


def postprocess_release_jpeg(path: Path, config: MtgDesignPostprocessConfig) -> None:
    """Apply release-only cosmetic cleanup without modifying the MTG.Design cache."""
    if not config.enabled or not config.masks:
        return
    with Image.open(path) as source:
        image = source.convert("RGB")
    reference_width, reference_height = config.reference_size
    if reference_width <= 0 or reference_height <= 0:
        raise ValueError("mtg_design.postprocess.reference_size must contain positive dimensions.")

    for mask in config.masks:
        left, top, right, bottom = _scaled_rectangle(mask.rectangle, image.size, config.reference_size)
        sample_x, sample_y = _scaled_point(mask.sample, image.size, config.reference_size)
        if left >= right or top >= bottom:
            raise ValueError(f"MTG.Design cleanup mask {mask.name!r} has an empty rectangle.")
        color = image.getpixel((sample_x, sample_y))
        image.paste(color, (left, top, right, bottom))

    output = BytesIO()
    image.save(output, format="JPEG", quality=config.jpeg_quality, subsampling=0)
    atomic_write(path, output.getvalue())


def _scaled_rectangle(rectangle: tuple[int, int, int, int], size: tuple[int, int], reference: tuple[int, int]) -> tuple[int, int, int, int]:
    left, top = _scaled_point(rectangle[:2], size, reference)
    right, bottom = _scaled_point(rectangle[2:], size, reference)
    return left, top, right, bottom


def _scaled_point(point: tuple[int, int], size: tuple[int, int], reference: tuple[int, int]) -> tuple[int, int]:
    x = round(point[0] * size[0] / reference[0])
    y = round(point[1] * size[1] / reference[1])
    return max(0, min(size[0] - 1, x)), max(0, min(size[1] - 1, y))
