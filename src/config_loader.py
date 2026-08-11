from __future__ import annotations
import logging
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _expand(value):
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        key = value[2:-1]
        resolved = os.environ.get(key, "")
        if not resolved:
            # Silence here is how ADMIN_ALLOWED_IPS could go missing and quietly
            # disable the admin IP allowlist. Always say something.
            logger.warning("Env var %s is not set — using an empty value", key)
        return resolved
    return value


def load_config(path: str | None = None) -> dict:
    """Load settings.yaml with ${ENV_VAR} expansion. Shared by main.py and the admin app."""
    cfg_path = Path(path) if path else Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    def expand(obj):
        if isinstance(obj, dict):
            return {k: expand(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [expand(i) for i in obj]
        return _expand(obj)

    return expand(raw)
