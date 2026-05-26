from __future__ import annotations
import logging

import pandas as pd

from src.core.models import Property

logger = logging.getLogger(__name__)

# Adjacent city pairs for scoring priority-4 (near city bonus)
_ADJACENT_CITIES: list[tuple[str, str]] = [
    ("知立市", "刈谷市"), ("知立市", "安城市"), ("知立市", "豊田市"),
    ("知立市", "碧南市"), ("刈谷市", "高浜市"), ("刈谷市", "安城市"),
]

# Property-type category keywords
_SINGLE_KEYWORDS = {"単身", "1K", "1R", "1DK", "ワンルーム"}
_FAMILY_KEYWORDS = {"ファミリー", "2LDK", "3LDK", "4LDK", "戸建て"}
_DESIGNER_KEYWORDS = {"デザイナーズ", "リノベーション", "ガレージ", "秘密基地"}


def _property_type(p: Property) -> str:
    cats = " ".join(p.category) + " " + p.layout
    if any(k in cats for k in _DESIGNER_KEYWORDS):
        return "designer"
    if any(k in cats for k in _FAMILY_KEYWORDS):
        return "family"
    return "single"


def _adjacent(city_a: str, city_b: str) -> bool:
    pair = (city_a, city_b)
    return pair in _ADJACENT_CITIES or (pair[1], pair[0]) in _ADJACENT_CITIES


class PropertyScorer:
    """
    Scores vacant alternative properties against an inquiry property
    using an 8-tier priority system with property-type weighting.
    """

    def __init__(self, properties: list[Property]):
        self._all = properties
        self._df = self._build_df(properties)

    def _build_df(self, properties: list[Property]) -> pd.DataFrame:
        rows = [{
            "idx": i,
            "wp_id": p.wp_id,
            "name": p.name,
            "url": p.url,
            "rent": p.rent,
            "management_fee": p.management_fee,
            "layout": p.layout,
            "nearest_station": p.nearest_station,
            "train_line": p.train_line,
            "city": p.city,
            "walk_minutes": p.walk_minutes,
            "category_str": " ".join(p.category),
            "equipment_str": " ".join(p.equipment),
            "is_vacant": p.is_vacant,
            "is_commission_free": p.is_commission_free,
            "area_sqm": p.area_sqm,
            "building_type": p.building_type,
            "ptype": _property_type(p),
        } for i, p in enumerate(properties)]
        return pd.DataFrame(rows)

    def find_alternatives(self, inquiry_property: Property,
                          top_n: int = 3) -> list[Property]:
        """Return top_n vacant properties sorted by score, excluding the inquiry property."""
        df = self._df[
            (self._df["is_vacant"] == True) &
            (self._df["wp_id"] != inquiry_property.wp_id) &
            (self._df["rent"] > 0)
        ].copy()

        if df.empty:
            logger.warning("No vacant properties available for scoring")
            return []

        ptype = _property_type(inquiry_property)
        df["score"] = 0

        # ── Priority 1: same nearest station ───────────────────────────────
        same_station = df["nearest_station"] == inquiry_property.nearest_station
        df.loc[same_station, "score"] += 100

        # ── Priority 2: same train line ────────────────────────────────────
        same_line = df["train_line"] == inquiry_property.train_line
        df.loc[same_line & ~same_station, "score"] += 60

        # ── Priority 3: same city ──────────────────────────────────────────
        same_city = df["city"] == inquiry_property.city
        df.loc[same_city, "score"] += 40

        # ── Priority 4: adjacent city ──────────────────────────────────────
        df.loc[
            df["city"].apply(lambda c: _adjacent(c, inquiry_property.city)) & ~same_city,
            "score"
        ] += 20

        # ── Priority 5: rent range (±10,000 yen) ──────────────────────────
        rent_diff = (df["rent"] - inquiry_property.rent).abs()
        df.loc[rent_diff <= 10000, "score"] += 30
        df.loc[(rent_diff > 10000) & (rent_diff <= 20000), "score"] += 15

        # ── Priority 6: same layout ────────────────────────────────────────
        df.loc[df["layout"] == inquiry_property.layout, "score"] += 20

        # ── Priority 7: category match ─────────────────────────────────────
        for cat in inquiry_property.category:
            df.loc[df["category_str"].str.contains(cat, regex=False, na=False), "score"] += 15

        # ── Priority 8: equipment match ────────────────────────────────────
        for eq in inquiry_property.equipment:
            df.loc[df["equipment_str"].str.contains(eq, regex=False, na=False), "score"] += 5

        # ── Property-type weighting ────────────────────────────────────────
        if ptype == "single":
            # single-unit: boost station/line/walk, reduce area weight
            df.loc[same_station, "score"] += 30
            df.loc[same_line, "score"] += 20
            df.loc[df["walk_minutes"] <= 10, "score"] += 10
        elif ptype == "family":
            # family: boost same city, parking (駐車場 in equipment)
            df.loc[same_city, "score"] += 20
            df.loc[df["equipment_str"].str.contains("駐車", na=False), "score"] += 15
            df.loc[df["area_sqm"] >= inquiry_property.area_sqm * 0.9, "score"] += 10
        elif ptype == "designer":
            # designer: boost category match strongly
            for cat in inquiry_property.category:
                df.loc[df["category_str"].str.contains(cat, regex=False, na=False),
                       "score"] += 20

        top = df.nlargest(min(top_n, len(df)), "score")
        logger.info("Scored %d candidates → returning top %d", len(df), len(top))
        return [self._all[int(row["idx"])] for _, row in top.iterrows()]

    def reload(self, properties: list[Property]) -> None:
        """Call when property data is refreshed from WordPress."""
        self._all = properties
        self._df = self._build_df(properties)
