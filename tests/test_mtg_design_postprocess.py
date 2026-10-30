from io import BytesIO

from PIL import Image, ImageDraw

from mtg_pipeline.config import MtgDesignCleanupMask, MtgDesignPostprocessConfig
from mtg_pipeline.renderers.mtg_design_postprocess import postprocess_release_jpeg


def test_cleanup_masks_release_copy_with_sampled_background(tmp_path):
    path = tmp_path / "release-card.jpg"
    image = Image.new("RGB", (744, 1039), (7, 8, 9))
    ImageDraw.Draw(image).rectangle((166, 970, 309, 990), fill="white")
    image.save(path, format="JPEG", quality=100, subsampling=0)
    config = MtgDesignPostprocessConfig(
        jpeg_quality=100,
        masks=[MtgDesignCleanupMask(name="card_icon_and_not_for_sale", rectangle=(166, 970, 310, 991), sample=(320, 981))],
    )

    postprocess_release_jpeg(path, config)

    with Image.open(path) as processed:
        assert processed.getpixel((200, 980)) == processed.getpixel((320, 981))
