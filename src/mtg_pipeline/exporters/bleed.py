"""Print-only corner matting; source card renderings are never modified."""
from collections import Counter, deque

from PIL import Image


def matte_card_corners(source):
    """Sample the dark border and replace only connected outer-corner mattes.

    The fill stays in the corner regions and stops at the border. This avoids
    recoloring white lettering, rules boxes or artwork elsewhere on the card.
    """
    image = source.convert("RGB")
    width, height = image.size
    if width < 4 or height < 4:
        raise ValueError("Card image is too small to sample its border.")
    pixels = image.load()
    samples = []
    for x in range(width // 3, 2 * width // 3):
        samples.extend((pixels[x, 1], pixels[x, height - 2]))
    for y in range(height // 3, 2 * height // 3):
        samples.extend((pixels[1, y], pixels[width - 2, y]))
    dark = [pixel for pixel in samples if max(pixel) < 80]
    black = Counter(dark or samples).most_common(1)[0][0]
    # Crucible's radius is below 10% of either image dimension. An opaque
    # black border separates this external matte from the card's interior.
    corner_w, corner_h = max(2, round(width * .1)), max(2, round(height * .1))
    threshold = max(black) + 8
    for right, bottom in ((False, False), (True, False), (False, True), (True, True)):
        x0 = width - corner_w if right else 0
        y0 = height - corner_h if bottom else 0
        start = (width - 1 if right else 0, height - 1 if bottom else 0)
        pending = deque([start])
        seen = {start}
        while pending:
            x, y = pending.popleft()
            if max(pixels[x, y]) <= threshold:
                continue
            pixels[x, y] = black
            for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                nx, ny = point
                if x0 <= nx < x0 + corner_w and y0 <= ny < y0 + corner_h and point not in seen:
                    seen.add(point)
                    pending.append(point)
    return image, black
