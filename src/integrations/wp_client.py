from __future__ import annotations
import logging
import re
import time
import unicodedata
from datetime import datetime
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from src.core.models import Property

logger = logging.getLogger(__name__)

# Taxonomy holding the formal property name, e.g. オリーブ_201. The post title is
# marketing copy, so this is the only field a portal's 物件名 can be matched to.
_BUILDNAME_TAX = "buildname"

# Trailing room number: オリーブ201 -> オリーブ, EIGHT BASEC棟2 -> EIGHT BASEC棟
_ROOM_SUFFIX = re.compile(r"\d+$")
_ROOM_IN_NAME = re.compile(r"(\d+)$")


def _room_key(text: str) -> str:
    """`0308` and `308` name the same room; compare them as one."""
    folded = unicodedata.normalize("NFKC", str(text or "")).strip().upper().replace(" ", "")
    return folded.lstrip("0") or folded


def _listed_room(prop: Property) -> str:
    """The room actually on the market for this listing, or "" if unknown.

    Only 部屋番号 counts. The number in the 物件名 is the room that was originally
    photographed and is not necessarily the one being advertised, so it is never
    read as the listed room — not even when 部屋番号 is blank. A listing with no
    部屋番号 simply cannot confirm a room, and is introduced by building alone
    (client's instruction, 2026-09-11).
    """
    return _room_key(prop.room_number) if prop.room_number else ""


def _name_stem(key: str) -> str:
    """Building identity: a formal key with any trailing room number removed.

    The client registers the room they originally photographed in the 物件名, so
    パストラーレ富士松_307 is currently let as room 107. The number in the name is
    therefore not evidence of which room is on the market and must not be matched
    against the portal's room — only the 部屋番号 field may be.
    """
    return _ROOM_SUFFIX.sub("", key)


def _room_number(floor: str | None) -> str:
    """`205（2階部分）` -> `205`. The room lives in its own field, not the name."""
    text = unicodedata.normalize("NFKC", str(floor or "")).strip()
    return re.split(r"[(（\s]", text, maxsplit=1)[0].strip()


def formal_key(name: str) -> str:
    """Comparison key for a formal property name.

    SUUMO writes 建物名+部屋番号, WordPress writes 建物名_部屋番号, and roman
    numerals differ (Ⅲ vs III) — NFKC plus separator removal folds all of it.
    """
    folded = unicodedata.normalize("NFKC", name or "")
    # ・ is kept: dropping it turned bonitorosa Ⅰ・Ⅱ into bonitorosaIII, which
    # collides with the genuinely different bonitorosa Ⅲ.
    return re.sub(r"[\s_\-—―]", "", folded).lower()


def _int(val, default: int = 0) -> int:
    try:
        return int(str(val).replace(",", "").replace("円", "").strip())
    except (ValueError, TypeError):
        return default


def _float(val, default: float = 0.0) -> float:
    try:
        return float(str(val).replace(",", "").replace("㎡", "").strip())
    except (ValueError, TypeError):
        return default


# ACF fields arrive as a list, a scalar, or null depending on how the field was
# registered, so every read goes through one of these three shape normalisers.

def _first_str(val) -> str:
    """List-or-scalar field → the first value as a trimmed string ('' if unset)."""
    if isinstance(val, list):
        val = val[0] if val else None
    return "" if val is None else str(val).strip()


def _first_int(val, default: int = 0) -> int:
    """List-or-scalar field → the first value as an int."""
    if isinstance(val, list):
        val = val[0] if val else None
    return default if val is None else _int(val, default)


def _str_list(val) -> list[str]:
    """List-or-comma-separated-string field → a list of trimmed strings."""
    if isinstance(val, list):
        return [str(v).strip() for v in val if v]
    if isinstance(val, str):
        return [v.strip() for v in val.split(",") if v.strip()]
    return []


class WordPressClient:
    """
    Fetches property data from rentmagazine.jp via WordPress REST API.
    Post type: estate  →  /wp-json/wp/v2/estate
    Taxonomies: train, area, space, condition, condition2,
                room_category, cat2, building, economical
    """

    def __init__(self, base_url: str, app_user: str, app_password: str,
                 per_page: int, field_cfg: dict, taxonomy_cfg: dict):
        self._base = base_url.rstrip("/")
        self._auth = HTTPBasicAuth(app_user, app_password) if app_user else None
        self._per_page = per_page
        self._fcfg = field_cfg      # field name mappings from settings.yaml
        self._tcfg = taxonomy_cfg   # taxonomy slug mappings

        # caches populated by load_all()
        self._term_cache: dict[str, dict[int, str]] = {}
        self._properties: list[Property] = []
        self._loaded_at: datetime | None = None

    # ── public API ──────────────────────────────────────────────────────────

    def load_all(self) -> list[Property]:
        """Fetch all estate posts and build the Property list."""
        logger.info("Loading taxonomy term caches from WordPress…")
        self._preload_terms()
        logger.info("Fetching all estate posts from WordPress…")
        raw = self._fetch_all_pages("estate")
        self._properties = [self._to_property(p) for p in raw]
        self._loaded_at = datetime.now()
        logger.info("Loaded %d properties (%d vacant)",
                    len(self._properties),
                    sum(1 for p in self._properties if p.is_vacant))
        return self._properties

    def refresh_if_stale(self, max_age_minutes: int) -> bool:
        """Reload listings when the snapshot has aged out. True if it reloaded.

        Listings were previously read once at startup and never again, so the
        service answered from whatever the site held when it last booted. A room
        let since then still read as vacant, a room freed still read as taken —
        カーナ若宮306 was offered alternatives on 2026-10-04 because the snapshot
        loaded on 09-30 had it unavailable — and a newly published listing could
        not be matched at all.
        """
        if max_age_minutes <= 0:
            return False
        if self._loaded_at is not None:
            age_minutes = (datetime.now() - self._loaded_at).total_seconds() / 60
            if age_minutes < max_age_minutes:
                return False

        previous = self._properties
        try:
            self.load_all()
            return True
        except Exception as e:
            # Keep serving the previous snapshot. Stale data is wrong, but an
            # empty list is worse: every lookup would miss, and every mail would
            # go out saying the property could not be found.
            self._properties = previous
            logger.error("Could not refresh listings (%s) — continuing with the "
                         "snapshot loaded at %s", e, self._loaded_at)
            return False

    @property
    def properties(self) -> list[Property]:
        """The most recently loaded listings."""
        return self._properties

    def get_property_by_url(self, url: str) -> Property | None:
        """Return first property whose URL matches (case-insensitive)."""
        url_norm = url.rstrip("/").lower()
        for p in self._properties:
            if p.url.rstrip("/").lower() == url_norm:
                return p
        return None

    def resolve_by_formal_name(self, name: str) -> tuple[Property | None, str]:
        """Resolve a portal-supplied formal name such as サンステージエクセル203.

        Returns (property, display_name). A room-level match wins. Failing that
        the building is matched and the room number dropped from the display
        name, per the client's instruction to introduce the building without
        referring to a room we cannot confirm.

        The room is taken from the 部屋番号 field and never from the 物件名. The
        client registers the room they originally photographed in the name, so
        パストラーレ富士松_307 is currently let as room 107 — treating the name's
        number as the listed room would offer the wrong flat. Some names carry no
        room at all, which is why the building is matched on its stem.

        A vacant listing is preferred when several rooms in a building match, so
        the mail does not offer something already taken.
        """
        key = formal_key(name)
        if not key:
            return None, ""

        stem = _name_stem(key)
        if not stem:
            return None, ""

        same_building = [p for p in self._properties
                         if _name_stem(formal_key(p.building_name)) == stem]
        if not same_building:
            return None, ""

        # Room-level match: the portal's room against 部屋番号, the only field
        # that states which room is actually on the market.
        m = _ROOM_IN_NAME.search(key)
        want_room = _room_key(m.group(1)) if m else ""
        if want_room:
            exact_room = [p for p in same_building if _listed_room(p) == want_room]
            if exact_room:
                return self._best(exact_room), name.strip()

        # Building only: say the building and drop the room we cannot confirm.
        display = _ROOM_SUFFIX.sub("", unicodedata.normalize("NFKC", name)).strip(" 　_-・")
        return self._best(same_building), display or name.strip()

    @staticmethod
    def _best(candidates: list[Property]) -> Property:
        """Prefer a vacant, priced listing; otherwise the first match."""
        return next((p for p in candidates if p.is_vacant and p.rent > 0), candidates[0])

    def get_property_by_name(self, name: str) -> Property | None:
        """Fuzzy name match — returns best match or None."""
        name_lower = name.strip().lower()
        for p in self._properties:
            if name_lower in p.name.lower() or p.name.lower() in name_lower:
                return p
        return None

    # ── fetch helpers ────────────────────────────────────────────────────────

    # Only fetch fields we actually use — avoids MemoryError on large ACF + yoast payloads
    _ESTATE_FIELDS = (
        "id,title,link,acf,buildname,"
        "train,area,space,condition1,condition2,"
        "room_category,cat2,building,economical,"
        "structure,contract_type,display_condition"
    )

    def _fetch_all_pages(self, post_type: str) -> list[dict]:
        results, page = [], 1
        while True:
            data = self._get(f"/wp-json/wp/v2/{post_type}",
                             params={"per_page": self._per_page,
                                     "page": page,
                                     "status": "publish",
                                     "_fields": self._ESTATE_FIELDS})
            if not data:
                break
            results.extend(data)
            if len(data) < self._per_page:
                break
            page += 1
        return results

    def _preload_terms(self) -> None:
        all_slugs = set()
        all_slugs.add(self._tcfg.get("train_line", "train"))
        all_slugs.add(self._tcfg.get("area", "area"))
        all_slugs.add(self._tcfg.get("building_type", "building"))
        all_slugs.add(_BUILDNAME_TAX)
        for s in self._tcfg.get("category", []):
            all_slugs.add(s)

        for slug in all_slugs:
            try:
                # Paginate to get all terms (some taxonomies have > 100 terms)
                all_terms: dict[int, str] = {}
                page = 1
                while True:
                    terms = self._get(f"/wp-json/wp/v2/{slug}",
                                      params={"per_page": 100, "page": page,
                                              "_fields": "id,name"})
                    if not terms:
                        break
                    for t in terms:
                        all_terms[t["id"]] = t["name"]
                    if len(terms) < 100:
                        break
                    page += 1
                self._term_cache[slug] = all_terms
                logger.debug("Cached %d terms for taxonomy '%s'", len(all_terms), slug)
            except Exception as e:
                logger.warning("Could not load taxonomy '%s': %s", slug, e)
                self._term_cache[slug] = {}

    def _get(self, path: str, params: dict | None = None) -> list | dict:
        url = self._base + path
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, auth=self._auth, timeout=30)
                if r.status_code == 400:
                    return []
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                if attempt == 2:
                    logger.error("WP API request failed after 3 attempts: %s %s", url, e)
                    raise
                time.sleep(5 * (attempt + 1))
        return []

    # ── normalisation ────────────────────────────────────────────────────────

    def _to_property(self, raw: dict) -> Property:
        acf = raw.get("acf", {}) or {}

        def acf_get(key: str, default=None):
            # A field can be present but null. `dict.get` only applies the
            # default when the key is absent, so map null onto it explicitly —
            # otherwise None leaks into str()/int() conversions below.
            field_name = self._fcfg.get(key, key)
            val = acf.get(field_name, default)
            return default if val is None else val

        # ── Vacancy ──────────────────────────────────────────────────────────
        # acf.display_none == "" means the property is actively listed (vacant)
        vacancy_val = str(acf_get("vacancy_status", "MISSING")).strip()
        available_val = self._fcfg.get("vacancy_available_value", "")
        is_vacant = (vacancy_val == available_val)

        # ── Commission-free ──────────────────────────────────────────────────
        # "仲介手数料０円" lives in condition2/cat2 category taxonomies (not economical)
        cf_term = self._fcfg.get("commission_free_term", "仲介手数料０円")

        # ── Taxonomy helpers ─────────────────────────────────────────────────
        def tax_names(slug: str) -> list[str]:
            ids = raw.get(slug, []) or []
            cache = self._term_cache.get(slug, {})
            return [cache[i] for i in ids if i in cache]

        train_names = tax_names(self._tcfg.get("train_line", "train"))
        area_names = tax_names(self._tcfg.get("area", "area"))
        building_names = tax_names(self._tcfg.get("building_type", "building"))

        # Multi-category taxonomies (condition1, condition2, room_category, cat2)
        cat_names: list[str] = []
        for slug in self._tcfg.get("category", []):
            cat_names.extend(tax_names(slug))

        is_commission_free = cf_term in cat_names

        # ── ACF fields ───────────────────────────────────────────────────────
        layout = _first_str(acf_get("layout", []))          # e.g. ['1LDK']
        walk_minutes = _first_int(acf_get("walk_minutes", []))  # e.g. ['10','15']
        equipment = _str_list(acf_get("equipment", []))     # e.g. ['オートロック']

        # `title` and `link` can come back null for protected or malformed posts;
        # one such post used to abort the whole startup load.
        return Property(
            wp_id=raw.get("id") or 0,
            name=(raw.get("title") or {}).get("rendered") or "",
            url=raw.get("link") or "",
            rent=_int(acf_get("rent", 0)),
            management_fee=_int(acf_get("management_fee", 0)),
            layout=layout,
            nearest_station="",    # station name comes from train taxonomy terms
            train_line=train_names[0] if train_names else "",
            city=area_names[0] if area_names else "",
            walk_minutes=walk_minutes,
            category=list(dict.fromkeys(cat_names)),   # deduplicate, preserve order
            equipment=equipment,
            is_vacant=is_vacant,
            is_commission_free=is_commission_free,
            area_sqm=_float(acf_get("area_sqm", 0.0)),
            building_type=building_names[0] if building_names else (acf.get("buildType") or ""),
            building_name=(tax_names(_BUILDNAME_TAX) or [""])[0],
            room_number=_room_number(acf.get("floor")),
            address=str(acf.get("location") or "").strip(),
            access=str(acf.get("access") or "").strip(),
        )
