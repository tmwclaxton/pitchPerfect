"""Autoswipe filter settings: env defaults, SQLite, and local JSON (non-secrets)."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any, Dict, List, Optional

import db
from config import (
    PROFILE_IMAGE_COUNT,
    PROFILE_MIN_ATTRACTIVENESS,
    PROFILE_MIN_SLIMNESS,
)

# Local JSON under app/data/ (gitignored). Secrets stay in .env only.
SETTINGS_JSON_PATH = os.path.join(
    os.path.dirname(__file__), "data", "autoswipe_settings.json"
)
SETTINGS_NAMESPACE = "autoswipe"


@dataclass
class AutoswipeSettings:
    """Discover autoswipe filters + scoring weights."""

    preset: str = "default"
    min_attractiveness: float = 6.0
    min_slimness: float = 5.0
    min_quirkiness: float = 0.0
    min_ethnicity_fit: float = 0.0
    min_composite: float = 6.0
    weight_attractiveness: float = 0.45
    weight_slimness: float = 0.25
    weight_quirkiness: float = 0.15
    weight_ethnicity_fit: float = 0.15
    profile_image_count: int = 3
    max_swipes: int = 15
    paste_comment: bool = True
    # Soft preference for vision scoring (not a Hinge API filter).
    # Example: "East/Southeast Asian". Empty = no ethnicity scoring boost.
    ethnicity_preference: str = ""
    # Documented Hinge Filters note; setup prints this — ADB filter UI is fragile.
    hinge_filters_note: str = (
        "Set Hinge Discover Filters manually once (ethnicity/race if available "
        "in your region). Automation persists the preference for vision scoring "
        "and like/pass decisions; it does not reliably drive Hinge's Filters UI."
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Named presets (applied via setup_autoswipe.py --preset NAME).
PRESETS: Dict[str, Dict[str, Any]] = {
    "default": {
        "preset": "default",
        "min_attractiveness": 6.0,
        "min_slimness": 5.0,
        "min_quirkiness": 0.0,
        "min_ethnicity_fit": 0.0,
        "min_composite": 6.0,
        "weight_attractiveness": 0.45,
        "weight_slimness": 0.25,
        "weight_quirkiness": 0.15,
        "weight_ethnicity_fit": 0.15,
        "profile_image_count": 3,
        "max_swipes": 15,
        "paste_comment": True,
        "ethnicity_preference": "",
    },
    # Prefer East/Southeast Asian presentation; like when composite >= 6.
    # Weights lean "baddie": attractiveness + ethnicity fit dominate.
    "asian_baddies": {
        "preset": "asian_baddies",
        "min_attractiveness": 5.0,
        "min_slimness": 4.0,
        "min_quirkiness": 0.0,
        "min_ethnicity_fit": 5.0,
        "min_composite": 6.0,
        "weight_attractiveness": 0.50,
        "weight_slimness": 0.20,
        "weight_quirkiness": 0.10,
        "weight_ethnicity_fit": 0.20,
        "profile_image_count": 3,
        "max_swipes": 20,
        "paste_comment": True,
        "ethnicity_preference": "East/Southeast Asian",
    },
}


def list_presets() -> List[str]:
    return sorted(PRESETS.keys())


def env_defaults() -> Dict[str, Any]:
    """Baseline from environment / config.py."""
    return {
        "preset": os.getenv("AUTOSWIPE_PRESET", "default"),
        "min_attractiveness": float(
            os.getenv("PROFILE_MIN_ATTRACTIVENESS", str(PROFILE_MIN_ATTRACTIVENESS))
        ),
        "min_slimness": float(
            os.getenv("PROFILE_MIN_SLIMNESS", str(PROFILE_MIN_SLIMNESS))
        ),
        "min_quirkiness": float(os.getenv("PROFILE_MIN_QUIRKINESS", "0")),
        "min_ethnicity_fit": float(os.getenv("PROFILE_MIN_ETHNICITY_FIT", "0")),
        "min_composite": float(os.getenv("PROFILE_MIN_COMPOSITE", "6")),
        "weight_attractiveness": float(
            os.getenv("PROFILE_WEIGHT_ATTRACTIVENESS", "0.45")
        ),
        "weight_slimness": float(os.getenv("PROFILE_WEIGHT_SLIMNESS", "0.25")),
        "weight_quirkiness": float(os.getenv("PROFILE_WEIGHT_QUIRKINESS", "0.15")),
        "weight_ethnicity_fit": float(
            os.getenv("PROFILE_WEIGHT_ETHNICITY_FIT", "0.15")
        ),
        "profile_image_count": int(
            os.getenv("PROFILE_IMAGE_COUNT", str(PROFILE_IMAGE_COUNT))
        ),
        "max_swipes": int(os.getenv("AUTOSWIPE_MAX_SWIPES", "15")),
        "paste_comment": os.getenv("AUTOSWIPE_PASTE_COMMENT", "true").lower()
        in {"1", "true", "yes"},
        "ethnicity_preference": os.getenv("AUTOSWIPE_ETHNICITY_PREFERENCE", ""),
    }


def _coerce(settings: Dict[str, Any]) -> AutoswipeSettings:
    base = AutoswipeSettings()
    data = base.to_dict()
    data.update({k: v for k, v in settings.items() if k in data})
    # Type hygiene for JSON / SQLite string values.
    data["min_attractiveness"] = float(data["min_attractiveness"])
    data["min_slimness"] = float(data["min_slimness"])
    data["min_quirkiness"] = float(data["min_quirkiness"])
    data["min_ethnicity_fit"] = float(data["min_ethnicity_fit"])
    data["min_composite"] = float(data["min_composite"])
    data["weight_attractiveness"] = float(data["weight_attractiveness"])
    data["weight_slimness"] = float(data["weight_slimness"])
    data["weight_quirkiness"] = float(data["weight_quirkiness"])
    data["weight_ethnicity_fit"] = float(data["weight_ethnicity_fit"])
    data["profile_image_count"] = int(data["profile_image_count"])
    data["max_swipes"] = int(data["max_swipes"])
    if isinstance(data["paste_comment"], str):
        data["paste_comment"] = data["paste_comment"].lower() in {
            "1",
            "true",
            "yes",
        }
    else:
        data["paste_comment"] = bool(data["paste_comment"])
    data["ethnicity_preference"] = str(data.get("ethnicity_preference") or "")
    data["preset"] = str(data.get("preset") or "default")
    data["hinge_filters_note"] = str(
        data.get("hinge_filters_note") or AutoswipeSettings().hinge_filters_note
    )
    return AutoswipeSettings(**data)


def load_json_settings(path: Optional[str] = None) -> Dict[str, Any]:
    json_path = path or SETTINGS_JSON_PATH
    if not os.path.exists(json_path):
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("settings"), dict):
        return payload["settings"]
    return payload if isinstance(payload, dict) else {}


def save_json_settings(
    settings: AutoswipeSettings, path: Optional[str] = None
) -> str:
    json_path = path or SETTINGS_JSON_PATH
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    payload = {
        "updated_at": datetime.utcnow().isoformat(),
        "settings": settings.to_dict(),
    }
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    return json_path


def load_settings() -> AutoswipeSettings:
    """
    Merge order (later wins): env defaults -> SQLite -> local JSON.
    JSON is the practical "last write" from setup CLI.
    """
    merged: Dict[str, Any] = env_defaults()
    try:
        stored = db.get_setting_json(SETTINGS_NAMESPACE)
        if isinstance(stored, dict):
            merged.update(stored)
    except Exception:
        pass
    merged.update(load_json_settings())
    return _coerce(merged)


def save_settings(settings: AutoswipeSettings) -> AutoswipeSettings:
    """Persist to SQLite settings + local JSON (not .env — avoid secret churn)."""
    payload = settings.to_dict()
    db.set_setting_json(SETTINGS_NAMESPACE, payload)
    save_json_settings(settings)
    return settings


def apply_preset(name: str, *, overrides: Optional[Dict[str, Any]] = None) -> AutoswipeSettings:
    key = (name or "").strip().lower()
    if key not in PRESETS:
        known = ", ".join(list_presets())
        raise ValueError(f"Unknown preset '{name}'. Known: {known}")
    data = deepcopy(PRESETS[key])
    if overrides:
        data.update(overrides)
    settings = _coerce(data)
    return save_settings(settings)


def format_settings(settings: AutoswipeSettings) -> str:
    lines = [
        f"preset: {settings.preset}",
        f"min_composite: {settings.min_composite}",
        f"min_attractiveness / slimness / quirkiness / ethnicity_fit: "
        f"{settings.min_attractiveness} / {settings.min_slimness} / "
        f"{settings.min_quirkiness} / {settings.min_ethnicity_fit}",
        f"weights (A/S/Q/E): "
        f"{settings.weight_attractiveness} / {settings.weight_slimness} / "
        f"{settings.weight_quirkiness} / {settings.weight_ethnicity_fit}",
        f"ethnicity_preference: {settings.ethnicity_preference or '(none)'}",
        f"profile_image_count: {settings.profile_image_count}",
        f"max_swipes: {settings.max_swipes}",
        f"paste_comment: {settings.paste_comment}",
        f"hinge_filters: {settings.hinge_filters_note}",
    ]
    return "\n".join(lines)


def settings_field_names() -> List[str]:
    return [f.name for f in fields(AutoswipeSettings)]
