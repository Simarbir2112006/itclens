"""Reconciliation settings: packaged defaults plus validated overrides.

The tolerances and rate slabs are legal specifics. They live in
data/default_config.json, not in code, and must be verified with your CA.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from importlib import resources
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_FILE = "default_config.json"
STATE_CODES_FILE = "state_codes.json"

_OVERRIDABLE_KEYS = frozenset(
    {"tolerance_taxable", "tolerance_tax", "allowed_rate_slabs", "strip_fy_affixes", "date_formats"}
)


class ConfigError(ValueError):
    pass


def _canonical_decimal(d: Decimal) -> str:
    # Adding zero turns -0 into 0; normalize drops trailing zeros so 1.00 and 1 match.
    return format((d + 0).normalize(), "f")


@dataclass(frozen=True)
class ReconConfig:
    tolerance_taxable: Decimal
    tolerance_tax: Decimal
    allowed_rate_slabs: tuple[Decimal, ...]
    strip_fy_affixes: bool
    date_formats: tuple[str, ...]
    valid_state_codes: frozenset[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tolerance_taxable": _canonical_decimal(self.tolerance_taxable),
            "tolerance_tax": _canonical_decimal(self.tolerance_tax),
            "allowed_rate_slabs": [_canonical_decimal(s) for s in self.allowed_rate_slabs],
            "strip_fy_affixes": self.strip_fy_affixes,
            "date_formats": list(self.date_formats),
            "valid_state_codes": sorted(self.valid_state_codes),
        }

    def fingerprint(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_packaged_json(name: str) -> Any:
    text = resources.files("itclens").joinpath("data").joinpath(name).read_text(encoding="utf-8")
    return json.loads(text, parse_float=Decimal)


def _to_decimal(key: str, value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | str | Decimal):
        raise ConfigError(f"{key}: values must be numbers given as string, int or Decimal")
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation:
        raise ConfigError(f"{key}: values must be numbers") from None
    if not result.is_finite():
        raise ConfigError(f"{key}: values must be finite")
    return result


def _tolerance(raw: Mapping[str, Any], key: str) -> Decimal:
    value = _to_decimal(key, raw[key])
    if value < 0:
        raise ConfigError(f"{key}: must not be negative")
    return value


def _rate_slabs(raw: Mapping[str, Any]) -> tuple[Decimal, ...]:
    key = "allowed_rate_slabs"
    value = raw[key]
    if not isinstance(value, list | tuple) or not value:
        raise ConfigError(f"{key}: must be a non-empty list")
    slabs = [_to_decimal(key, item) for item in value]
    if any(not 0 <= slab <= 100 for slab in slabs):
        raise ConfigError(f"{key}: every slab must be between 0 and 100")
    unique: list[Decimal] = []
    for slab in sorted(slabs):
        if not unique or slab != unique[-1]:
            unique.append(slab)
    return tuple(unique)


def _date_formats(raw: Mapping[str, Any]) -> tuple[str, ...]:
    key = "date_formats"
    value = raw[key]
    if not isinstance(value, list | tuple) or not value:
        raise ConfigError(f"{key}: must be a non-empty list")
    if not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{key}: every format must be a string")
    return tuple(value)


def _state_codes() -> frozenset[str]:
    return frozenset(read_packaged_json(STATE_CODES_FILE)["codes"])


def load_config(overrides: Mapping[str, Any] | None = None) -> ReconConfig:
    """Load the packaged defaults, apply overrides and validate the result.

    Keys starting with "_" are ignored. Numeric overrides may be strings, ints or
    Decimals. Error messages name the key and the rule, never the given value.
    """
    defaults = read_packaged_json(DEFAULT_CONFIG_FILE)
    raw = {k: v for k, v in defaults.items() if not k.startswith("_")}

    if overrides:
        given = {str(k): v for k, v in overrides.items() if not str(k).startswith("_")}
        if "valid_state_codes" in given:
            raise ConfigError("valid_state_codes: cannot be overridden")
        unknown = sorted(set(given) - _OVERRIDABLE_KEYS)
        if unknown:
            raise ConfigError(f"unknown config keys: {', '.join(unknown)}")
        raw.update(given)

    missing = sorted(_OVERRIDABLE_KEYS - set(raw))
    if missing:
        raise ConfigError(f"missing config keys: {', '.join(missing)}")
    if not isinstance(raw["strip_fy_affixes"], bool):
        raise ConfigError("strip_fy_affixes: must be true or false")

    return ReconConfig(
        tolerance_taxable=_tolerance(raw, "tolerance_taxable"),
        tolerance_tax=_tolerance(raw, "tolerance_tax"),
        allowed_rate_slabs=_rate_slabs(raw),
        strip_fy_affixes=raw["strip_fy_affixes"],
        date_formats=_date_formats(raw),
        valid_state_codes=_state_codes(),
    )


def load_config_file(path: Path) -> ReconConfig:
    try:
        overrides = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except UnicodeDecodeError:
        raise ConfigError("config file is not valid UTF-8") from None
    except json.JSONDecodeError:
        raise ConfigError("config file is not valid JSON") from None
    if not isinstance(overrides, dict):
        raise ConfigError("config file must contain a JSON object")
    return load_config(overrides)
