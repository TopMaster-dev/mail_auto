from __future__ import annotations
import logging
import time
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from src.core.models import Property

logger = logging.getLogger(__name__)


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

    # ── public API ──────────────────────────────────────────────────────────

    def load_all(self) -> list[Property]:
        """Fetch all estate posts and build Property list. Call once at startup."""
        logger.info("Loading taxonomy term caches from WordPress…")
        self._preload_terms()
        logger.info("Fetching all estate posts from WordPress…")
        raw = self._fetch_all_pages("estate")
        self._properties = [self._to_property(p) for p in raw]
        logger.info("Loaded %d properties (%d vacant)",
                    len(self._properties),
                    sum(1 for p in self._properties if p.is_vacant))
        return self._properties

    def get_property_by_url(self, url: str) -> Property | None:
        """Return first property whose URL matches (case-insensitive)."""
        url_norm = url.rstrip("/").lower()
        for p in self._properties:
            if p.url.rstrip("/").lower() == url_norm:
                return p
        return None

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
        "id,title,link,acf,"
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
        )
