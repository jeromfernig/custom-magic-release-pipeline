from __future__ import annotations

import re

COLOR_ORDER = ["W", "U", "B", "R", "G"]
BRACED_MANA_RE = re.compile(r"^(\{[0-9WUBRGCXYZwubrgcxyz/Phy]+\})+$")
BARE_MANA_RE = re.compile(r"^[0-9WUBRGCXYZwubrgcxyz]+$")


def normalize_mana_cost(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "{" in text or "}" in text:
        if not BRACED_MANA_RE.match(text):
            raise ValueError(f"mana_cost '{text}' does not look like Scryfall symbology such as {{2}}{{G}}.")
        return re.sub(r"\{([^}]+)\}", lambda match: "{" + match.group(1).upper() + "}", text)
    if not BARE_MANA_RE.match(text):
        raise ValueError(f"mana_cost '{text}' must contain bare mana symbols like 2GG or braced symbols like {{2}}{{G}}{{G}}.")

    parts: list[str] = []
    index = 0
    while index < len(text):
        if text[index].isdigit():
            start = index
            while index < len(text) and text[index].isdigit():
                index += 1
            parts.append(text[start:index])
            continue
        parts.append(text[index].upper())
        index += 1
    return "".join(f"{{{part}}}" for part in parts)


def infer_colors_from_mana_cost(mana_cost: str | None) -> list[str]:
    normalized = normalize_mana_cost(mana_cost)
    if not normalized:
        return []
    present = set(re.findall(r"\{([^}]+)\}", normalized))
    return [color for color in COLOR_ORDER if any(color in symbol for symbol in present)]
