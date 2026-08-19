"""
Geographic proximity between the area values used in the property data.

The client's rule (spec §9-1) is that candidates are drawn from around the
inquiry property's municipality first. Scoring alone could not enforce that — a
distant listing could out-score a nearby one on rent and layout, which is how an
岡崎市 inquiry came back with 名古屋市 recommendations roughly 35 km away.

Values here are the `area` taxonomy strings exactly as WordPress returns them.
"""
from __future__ import annotations

import re

# Nagoya is split into sub-areas in the taxonomy; they are one city in practice.
NAGOYA = {
    "名古屋市 中心エリア", "名古屋市 東エリア", "名古屋市 西エリア",
    "名古屋市 南エリア", "名古屋市 北エリア",
}

# Catch-all bucket — location unknown, so it can be neither preferred nor ruled out.
UNKNOWN = "愛知 その他エリア"

# 生活圏 groupings: areas a customer would realistically consider together.
_REGIONS: dict[str, set[str]] = {
    "名古屋": NAGOYA | {"尾張旭市", "岩倉市", "日進市", "長久手市", "春日井市",
                        "清須市", "北名古屋市"},
    "西三河": {"岡崎市", "安城市", "刈谷市", "知立市", "豊田市", "西尾市",
               "碧南市", "高浜市", "みよし市", "幸田町", "豊明市"},
    "知多": {"半田市", "大府市", "東海市", "知多市", "東浦町", "武豊町"},
}

# Directly bordering municipalities.
_ADJACENT: dict[str, set[str]] = {
    "岡崎市": {"安城市", "豊田市", "西尾市", "幸田町"},
    "安城市": {"岡崎市", "刈谷市", "知立市", "西尾市", "碧南市", "高浜市", "豊田市"},
    "刈谷市": {"安城市", "知立市", "高浜市", "豊明市", "大府市", "東浦町"},
    "知立市": {"刈谷市", "安城市", "豊田市", "豊明市"},
    "豊田市": {"岡崎市", "安城市", "知立市", "みよし市", "日進市"},
    "西尾市": {"岡崎市", "安城市", "碧南市", "幸田町"},
    "碧南市": {"安城市", "西尾市", "高浜市"},
    "高浜市": {"刈谷市", "安城市", "碧南市"},
    "豊明市": {"刈谷市", "知立市", "大府市"} | NAGOYA,
    "尾張旭市": NAGOYA | {"春日井市"},
    "岩倉市": NAGOYA,
    "半田市": {"東浦町", "武豊町"},
}

SAME, ADJACENT, SAME_REGION, FAR = 0, 1, 2, 3

_CITY_RE = re.compile(r"([^\s　0-9]+?[市区町村])")


def city_from_address(address: str) -> str:
    """`愛知県安城市東新町` -> `安城市`. Used for portal mail with no WP match."""
    if not address:
        return ""
    trimmed = re.sub(r"^\s*[^\s]*?[都道府県]", "", address)
    m = _CITY_RE.search(trimmed)
    return m.group(1) if m else ""


def _region_of(area: str) -> str:
    for name, members in _REGIONS.items():
        if area in members:
            return name
    return ""


def normalise(area: str) -> str:
    """Map a bare city name onto the taxonomy value where they differ."""
    area = (area or "").strip()
    if not area:
        return ""
    if area in NAGOYA or area == UNKNOWN:
        return area
    if area.startswith("名古屋市"):
        return "名古屋市 中心エリア"     # sub-area unknown; any Nagoya value works
    return area


def distance_tier(reference: str, candidate: str) -> int:
    """How far `candidate` is from `reference` — SAME < ADJACENT < SAME_REGION < FAR."""
    a, b = normalise(reference), normalise(candidate)
    if not a or not b:
        return SAME_REGION           # unknown: neither preferred nor excluded
    if a == b:
        return SAME
    if a in NAGOYA and b in NAGOYA:
        return ADJACENT              # different districts of the same city
    if b in _ADJACENT.get(a, set()) or a in _ADJACENT.get(b, set()):
        return ADJACENT
    if UNKNOWN in (a, b):
        return SAME_REGION
    region_a, region_b = _region_of(a), _region_of(b)
    if region_a and region_a == region_b:
        return SAME_REGION
    return FAR
