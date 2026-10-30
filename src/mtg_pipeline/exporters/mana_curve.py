from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import math
import re
from pathlib import Path

from mtg_pipeline.exporters.draftmancer import RARITY_SHEETS, pack_layout_errors, sheet_counts
from mtg_pipeline.mana import normalize_mana_cost
from mtg_pipeline.models import CardDefinition, SetDefinition
from mtg_pipeline.utils.cache import write_json
from mtg_pipeline.linked_cards import physical_cards


COLORS = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "C": "Colorless"}
SEGMENTS = [(rarity, kind) for rarity in ("common", "uncommon", "rare") for kind in ("creatures", "noncreatures")]


def layout_probabilities(settings: dict) -> dict[str, float]:
    layouts = settings["layouts"]
    sequence = settings.get("predeterminedLayouts")
    if sequence is None:
        total = sum(layout["weight"] for layout in layouts.values())
        return {name: layout["weight"] / total for name, layout in layouts.items()}
    if not isinstance(sequence, list) or not sequence:
        raise ValueError("predeterminedLayouts must be a nonempty sequence for mana-curve analysis.")
    probabilities = defaultdict(float)
    for position in sequence:
        if isinstance(position, str):
            weights = {position: 1}
        elif isinstance(position, dict):
            weights = position
        elif isinstance(position, list) and position:
            weights = defaultdict(float)
            for choice in position:
                if isinstance(choice, str) and choice in layouts:
                    weights[choice] += layouts[choice]["weight"]
                elif isinstance(choice, dict) and isinstance(choice.get("name"), str):
                    weight = choice.get("weight")
                    if type(weight) is not int or weight <= 0:
                        raise ValueError("Predetermined layout weights must be positive integers.")
                    weights[choice["name"]] += weight
                else:
                    raise ValueError("Invalid predetermined layout choice.")
        else:
            raise ValueError("Invalid predetermined layout sequence.")
        if any(name not in layouts or isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0
               for name, weight in weights.items()):
            raise ValueError("Predetermined layout names must exist and weights must be positive.")
        total = sum(weights.values())
        for name, weight in weights.items():
            probabilities[name] += weight / total / len(sequence)
    return dict(probabilities)


def card_asfan(cards: list[CardDefinition], settings: dict) -> tuple[dict[str, float], dict[str, float]]:
    cards = physical_cards(cards)
    errors = pack_layout_errors(settings, sheet_counts(cards))
    if errors:
        raise ValueError("Cannot calculate ASFAN: " + " ".join(errors))
    if len({card.id for card in cards}) != len(cards):
        raise ValueError("Cannot calculate ASFAN with duplicate card IDs.")
    draws = defaultdict(float)
    for name, probability in layout_probabilities(settings).items():
        slots = settings["layouts"][name]["slots"]
        if isinstance(slots, dict):
            slots = [{"name": sheet, "count": count} for sheet, count in slots.items()]
        for slot in slots:
            sources = slot.get("sheets", [{"name": slot["name"], "weight": 1}])
            total = sum(source["weight"] for source in sources)
            for source in sources:
                draws[source["name"]] += probability * slot["count"] * source["weight"] / total
    members = {sheet: [card for card in cards if card.rarity == rarity] for rarity, sheet in RARITY_SHEETS.items()}
    members["RareMythic"] = [card for card in cards if card.rarity in {"rare", "mythic"}]
    weights = {card.id: 0.0 for card in cards}
    for sheet, count in draws.items():
        for card in members[sheet]:
            weights[card.id] += count / len(members[sheet])
    return weights, dict(draws)


def mana_value(card: CardDefinition) -> float:
    if card.mana_value is not None:
        value = card.mana_value
    else:
        value = 0.0
        cost = normalize_mana_cost(card.mana_cost)
        for symbol in re.findall(r"\{([^}]+)\}", cost or ""):
            parts = symbol.split("/")
            numbers = []
            for part in parts:
                if part.isdigit():
                    numbers.append(int(part))
                elif part in {"X", "Y", "Z", "P"}:
                    numbers.append(0)
                elif part in set("WUBRGCS"):
                    numbers.append(1)
                else:
                    raise ValueError(f"Set mana_value explicitly for {card.id}: unsupported mana symbol {symbol!r}.")
            value += max(numbers)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid mana value for {card.id}: expected a finite, nonnegative number.")
    return float(value)


def analyze_mana_curve(cards: list[CardDefinition], settings: dict) -> dict:
    cards = physical_cards(cards)
    weights, draws = card_asfan(cards, settings)
    curves = {color: defaultdict(lambda: defaultdict(float)) for color in COLORS}
    lands = []
    not_in_boosters = []
    derived = []
    for card in cards:
        types = {value.casefold() for value in card.card_types}
        if "land" in types or card.rarity == "land":
            lands.append(card.id)
            continue
        if weights[card.id] == 0:
            not_in_boosters.append(card.id)
            continue
        value = mana_value(card)
        if card.mana_value is None:
            derived.append(card.id)
        rarity = "rare" if card.rarity == "mythic" else card.rarity
        kind = "creatures" if "creature" in types else "noncreatures"
        for color in card.colors or ["C"]:
            curves[color][value][f"{rarity}_{kind}"] += weights[card.id]
    return {
        "settings": deepcopy(settings), "expected_sheet_draws": draws, "expected_pack_size": sum(draws.values()),
        "card_asfan": weights, "excluded_lands": lands, "cards_not_in_boosters": not_in_boosters,
        "derived_mana_values": derived,
        "curves": {color: {str(value): dict(segments) for value, segments in sorted(values.items())} for color, values in curves.items()},
        "method": "Uniform per-card sheet weighting before color balancing, duplicate protection, or print-run collation. Multicolor cards count fully in each matching color. Lands remain in sheet denominators but are excluded from spell curves.",
    }


def export_mana_curve(set_def: SetDefinition, cards: list[CardDefinition], settings: dict, output: Path) -> list[Path]:
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.figure import Figure

    analysis = analyze_mana_curve(cards, settings)
    curves = analysis["curves"]
    observed = {float(value) for values in curves.values() for value in values}
    maximum = max(observed, default=0)
    bins = sorted(observed | (set(range(math.ceil(maximum) + 1)) if maximum <= 30 else {0}))
    width = .8 * min((b - a for a, b in zip(bins, bins[1:])), default=1)
    ymax = max((sum(segments.values()) for values in curves.values() for segments in values.values()), default=0) * 1.15 or 1
    palette = {"common": "#4477AA", "uncommon": "#EEAA33", "rare": "#AA3377"}
    output.parent.mkdir(parents=True, exist_ok=True)
    charts = []
    with PdfPages(output, metadata={"Title": f"{set_def.set_name} {set_def.version} - ASFAN mana curves"}) as pdf:
        for color, label in COLORS.items():
            figure = Figure(figsize=(11.6929, 8.2677))
            ax = figure.subplots()
            figure.subplots_adjust(left=.09, right=.97, bottom=.19, top=.79)
            bottom = [0.0] * len(bins)
            for rarity, kind in SEGMENTS:
                heights = [curves[color].get(str(float(value)), {}).get(f"{rarity}_{kind}", 0) for value in bins]
                ax.bar(bins, heights, width=width, bottom=bottom, label=f"{rarity.title()} {'noncreatures' if kind == 'noncreatures' else 'creatures'}",
                       color=palette[rarity], alpha=.5 if kind == "noncreatures" else 1,
                       hatch="//" if kind == "noncreatures" else None, edgecolor="white", linewidth=.4)
                bottom = [before + amount for before, amount in zip(bottom, heights)]
            total = sum(bottom)
            ax.set_title(f"{set_def.set_name} {set_def.version} — {label}\nSpell ASFAN: {total:.3f} per booster", fontsize=16, pad=15)
            ax.set_xlabel("Mana value")
            ax.set_ylabel("Expected cards per booster (ASFAN)")
            ax.set_xticks(bins, [f"{value:g}" for value in bins])
            ax.set_ylim(0, ymax)
            ax.set_axisbelow(True)
            ax.grid(axis="y", alpha=.2)
            ax.legend(loc="lower center", bbox_to_anchor=(.5, 1.16), ncol=3, frameon=False, fontsize=9)
            if not any(bottom):
                ax.text(.5, .5, "No spells of this color in the configured booster pool.", transform=ax.transAxes, ha="center")
            figure.text(.09, .09, "Mythics are included with rares. Multicolor cards appear in each matching color; color totals overlap.", fontsize=8)
            figure.text(.09, .065, "Lands excluded from curves, retained in sheet denominators. Uniform sheet weighting before collation adjustments.", fontsize=8)
            formula = "; ".join(f"{sheet}: {amount:.3g}" for sheet, amount in analysis["expected_sheet_draws"].items())
            figure.text(.09, .04, "Expected slots per booster — " + formula, fontsize=8)
            pdf.savefig(figure)
            chart = output.with_name(f"{output.stem}-{color}.png")
            figure.savefig(chart, dpi=140)
            charts.append(chart)
            figure.clear()
    data_path = output.with_suffix(".json")
    write_json(data_path, analysis)
    return [output, data_path, *charts]
