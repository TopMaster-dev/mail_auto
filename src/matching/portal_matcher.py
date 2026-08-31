"""
Match a portal reflection to a listing by attributes rather than by name.

A portal and the site routinely register the same building in different
scripts — SUUMO sends ボニートロッサⅠ where the site holds `bonitorosa Ⅰ・Ⅱ` —
and bridging kana and romaji is transliteration, not normalisation, so no
amount of character folding will reconcile them. These attributes travel with
every reflection and do not depend on how the name is spelled.

Field coverage across the 1,800 published listings decides what can be relied
on: 間取り 100%, 専有面積 99%, 沿線・駅 99%, 部屋番号 74%, 賃料 74%,
所在地 only 29% — so the address cannot anchor the match.

A wrong match is worse than no match: it would describe the wrong flat to a
customer. Acceptance is therefore deliberately strict, and anything short of it
returns nothing so the mail falls back to neutral wording.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from src.core.models import Property
from src.matching import area as area_mod

logger = logging.getLogger(__name__)

# SUUMO rounds rent to one decimal in 万円 (6.3万円), so ±500 is inherent.
_RENT_TOLERANCE = 3000
_AREA_TOLERANCE = 0.5

_TRAILING_ROOM = re.compile(r"(\d+)\s*(?:号室)?$")
# Applied to the original string so the displayed name keeps its own characters
# — folding it would turn ボニートロッサⅠ into ボニートロッサI in a customer's mail.
_TRAILING_ROOM_RAW = re.compile(r"[0-9０-９]+[ 　]*(?:号室)?$")


@dataclass
class Match:
    prop: Property
    display_name: str
    basis: str            # which signals agreed, for the log and 要確認履歴
    room_level: bool      # False when only the building could be confirmed


# Portals and the site write the same layout differently.
_LAYOUT_ALIASES = {"ワンルーム": "1R", "1ルーム": "1R"}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).strip()


def layout_key(text: str) -> str:
    """`ワンルーム` and `1R` are the same layout; compare them as one."""
    folded = _norm(text).upper().replace(" ", "")
    return _LAYOUT_ALIASES.get(folded, folded)


def room_key(text: str) -> str:
    """`0308` and `308` are the same room — the site zero-pads some of them."""
    folded = _norm(text).upper().replace(" ", "")
    stripped = folded.lstrip("0")
    return stripped or folded


def room_from_name(name: str) -> str:
    """`ボニートロッサⅠ205` / `…205号室` -> `205`."""
    m = _TRAILING_ROOM.search(_norm(name))
    return m.group(1) if m else ""


def strip_room(name: str) -> str:
    """Drop a trailing room number, leaving every other character untouched."""
    return _TRAILING_ROOM_RAW.sub("", name or "").strip(" 　_-・")


def rent_yen(text: str) -> int:
    """`6.3万円` -> 63000, `63,000円` -> 63000, `` -> 0."""
    s = _norm(text).replace(",", "")
    m = re.search(r"([\d.]+)\s*万", s)
    if m:
        try:
            return int(round(float(m.group(1)) * 10000))
        except ValueError:
            return 0
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else 0


def area_sqm(text: str) -> float:
    """`40.32平米` / `40.32㎡` -> 40.32."""
    m = re.search(r"([\d.]+)", _norm(text))
    try:
        return float(m.group(1)) if m else 0.0
    except ValueError:
        return 0.0


def station_names(text: str) -> list[str]:
    """`名鉄西尾線/南安城` -> ['名鉄西尾線', '南安城']."""
    return [part for part in re.split(r"[/／\s「」]", _norm(text)) if part]


def _signals(prop: Property, want: dict) -> set[str]:
    """Which attributes of this listing agree with the reflection."""
    agree: set[str] = set()

    if want["room"] and prop.room_number and want["room"] == room_key(prop.room_number):
        agree.add("部屋番号")
    if want["layout"] and prop.layout and want["layout"] == layout_key(prop.layout):
        agree.add("間取り")
    if want["area"] and prop.area_sqm and abs(prop.area_sqm - want["area"]) <= _AREA_TOLERANCE:
        agree.add("専有面積")
    if want["rent"] and prop.rent and abs(prop.rent - want["rent"]) <= _RENT_TOLERANCE:
        agree.add("賃料")
    haystack = f"{prop.access} {prop.train_line} {prop.nearest_station}"
    if any(s and s in haystack for s in want["stations"]):
        agree.add("沿線・駅")
    if (want["city"] and prop.city
            and area_mod.distance_tier(want["city"], prop.city) == area_mod.SAME):
        agree.add("エリア")
    return agree


def _accepts(agree: set[str]) -> bool:
    """Strict enough that a wrong flat is not described to a customer.

    No single field is required. Tying acceptance to 間取り in particular was a
    mistake: SUUMO writes ワンルーム where the site writes 1R, so a room agreeing
    on number, rent, size and station was still rejected.
    """
    if "部屋番号" in agree and len(agree) >= 3:
        return True
    # No confirmed room: the size/price/layout fingerprint must agree in full,
    # and the location must corroborate it.
    return ({"専有面積", "賃料", "間取り"} <= agree
            and bool(agree & {"沿線・駅", "エリア"}))


def match(reflection, properties: list[Property]) -> Match | None:
    """Best attribute match for a portal reflection, or None if unconvincing."""
    extras = getattr(reflection, "extras", {}) or {}
    name = reflection.property_name or ""
    want = {
        "room": room_key(room_from_name(name)),
        "layout": layout_key(extras.get("間取り", "")),
        "area": area_sqm(extras.get("専有面積", "")),
        "rent": rent_yen(extras.get("賃料", "")),
        "stations": station_names(extras.get("最寄り駅", "")),
        "city": area_mod.city_from_address(extras.get("所在地", "")),
    }
    if not any((want["layout"], want["area"], want["rent"])):
        return None

    scored = []
    for prop in properties:
        agree = _signals(prop, want)
        if _accepts(agree):
            # Prefer more agreement, then a confirmed room, then a vacant unit.
            scored.append((len(agree), "部屋番号" in agree, prop.is_vacant, agree, prop))
    if not scored:
        return None

    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    _, room_level, _, agree, prop = scored[0]

    display = name.strip() if room_level else strip_room(name)
    basis = "・".join(sorted(agree))
    logger.info("Portal match: 「%s」 → %s (%s) [%s]",
                name, prop.building_name or prop.name,
                "部屋一致" if room_level else "建物一致", basis)
    return Match(prop=prop, display_name=display or name.strip(),
                 basis=basis, room_level=room_level)
