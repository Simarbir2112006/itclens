import json
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from itclens.domain import config as config_module
from itclens.domain.config import (
    ConfigError,
    load_config,
    load_config_file,
    read_packaged_json,
)

D = Decimal
SENTINEL = "27AAPFU0939F1ZV-SECRET"


def test_defaults_match_json() -> None:
    raw = read_packaged_json("default_config.json")
    codes = read_packaged_json("state_codes.json")["codes"]
    cfg = load_config()

    assert isinstance(raw["tolerance_taxable"], Decimal)
    assert cfg.tolerance_taxable == raw["tolerance_taxable"] == D("1.00")
    assert cfg.tolerance_tax == raw["tolerance_tax"] == D("1.00")
    assert cfg.allowed_rate_slabs == tuple(sorted(D(str(s)) for s in raw["allowed_rate_slabs"]))
    assert cfg.allowed_rate_slabs[:3] == (D("0"), D("0.25"), D("1.5"))
    assert cfg.strip_fy_affixes is False
    assert cfg.date_formats == tuple(raw["date_formats"])
    assert cfg.valid_state_codes == frozenset(codes)
    assert {"27", "29", "97", "99"} <= cfg.valid_state_codes
    assert "25" not in cfg.valid_state_codes


def test_notes_are_present() -> None:
    raw = read_packaged_json("default_config.json")
    assert "CA" in raw["_note"]
    assert "verify" in raw["_slabs_note"].lower()
    assert "verified" in read_packaged_json("state_codes.json")["_note"]


def test_overrides_apply() -> None:
    overrides = {"tolerance_tax": "2.50", "strip_fy_affixes": True, "date_formats": ["%d%m%Y"]}
    cfg = load_config(overrides)
    assert cfg.tolerance_tax == D("2.50")
    assert cfg.tolerance_taxable == D("1.00")
    assert cfg.strip_fy_affixes is True
    assert cfg.date_formats == ("%d%m%Y",)


def test_decimal_conversion_from_str_int_and_decimal() -> None:
    cfg = load_config(
        {"tolerance_taxable": 5, "tolerance_tax": " 0.5 ", "allowed_rate_slabs": ["18", 5, D("12")]}
    )
    assert cfg.tolerance_taxable == D("5")
    assert cfg.tolerance_tax == D("0.5")
    assert cfg.allowed_rate_slabs == (D("5"), D("12"), D("18"))


def test_slabs_sorted_and_unique() -> None:
    cfg = load_config({"allowed_rate_slabs": ["18", "5", "5.0", 5, "0"]})
    assert cfg.allowed_rate_slabs == (D("0"), D("5"), D("18"))


def test_unknown_key_rejected_without_echo() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config({"tolerence_tax": SENTINEL, "extra": SENTINEL})
    message = str(excinfo.value)
    assert "tolerence_tax" in message
    assert "extra" in message
    assert SENTINEL not in message


@pytest.mark.parametrize(
    ("overrides", "key"),
    [
        ({"tolerance_tax": "-0.01"}, "tolerance_tax"),
        ({"tolerance_taxable": -1}, "tolerance_taxable"),
        ({"tolerance_tax": "NaN"}, "tolerance_tax"),
        ({"tolerance_tax": "Infinity"}, "tolerance_tax"),
        ({"tolerance_tax": 0.5}, "tolerance_tax"),
        ({"tolerance_tax": True}, "tolerance_tax"),
        ({"tolerance_tax": SENTINEL}, "tolerance_tax"),
        ({"tolerance_tax": None}, "tolerance_tax"),
        ({"allowed_rate_slabs": []}, "allowed_rate_slabs"),
        ({"allowed_rate_slabs": "5"}, "allowed_rate_slabs"),
        ({"allowed_rate_slabs": ["101"]}, "allowed_rate_slabs"),
        ({"allowed_rate_slabs": ["-1"]}, "allowed_rate_slabs"),
        ({"allowed_rate_slabs": [SENTINEL]}, "allowed_rate_slabs"),
        ({"strip_fy_affixes": "yes"}, "strip_fy_affixes"),
        ({"strip_fy_affixes": 1}, "strip_fy_affixes"),
        ({"date_formats": []}, "date_formats"),
        ({"date_formats": "%d-%m-%Y"}, "date_formats"),
        ({"date_formats": ["%d-%m-%Y", 5]}, "date_formats"),
        ({"valid_state_codes": [SENTINEL]}, "valid_state_codes"),
    ],
)
def test_invalid_values_rejected_without_echo(overrides: dict[str, Any], key: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(overrides)
    message = str(excinfo.value)
    assert key in message
    assert SENTINEL not in message
    assert excinfo.value.__cause__ is None


def test_slab_boundaries_accepted() -> None:
    assert load_config({"allowed_rate_slabs": [0, 100]}).allowed_rate_slabs == (D("0"), D("100"))
    assert load_config({"tolerance_tax": 0}).tolerance_tax == D("0")


def test_underscore_keys_ignored() -> None:
    cfg = load_config({"_note": SENTINEL, "_anything": {"nested": 1}})
    assert cfg == load_config()


def test_missing_default_key_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    real = config_module.read_packaged_json

    def fake(name: str) -> Any:
        data = real(name)
        if name == config_module.DEFAULT_CONFIG_FILE:
            del data["tolerance_tax"]
        return data

    monkeypatch.setattr(config_module, "read_packaged_json", fake)
    with pytest.raises(ConfigError, match="tolerance_tax"):
        load_config()


def test_to_dict() -> None:
    data = load_config().to_dict()
    assert data["tolerance_taxable"] == "1"
    assert data["allowed_rate_slabs"][:3] == ["0", "0.25", "1.5"]
    assert data["valid_state_codes"] == sorted(data["valid_state_codes"])
    json.dumps(data)


def test_fingerprint_stable() -> None:
    first = load_config().fingerprint()
    assert first == load_config().fingerprint()
    assert len(first) == 64
    int(first, 16)


def test_fingerprint_ignores_override_order_and_trailing_zeros() -> None:
    a = load_config({"tolerance_tax": "2", "strip_fy_affixes": True})
    b = load_config({"strip_fy_affixes": True, "tolerance_tax": D("2.00")})
    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_changes_with_tolerance() -> None:
    assert load_config().fingerprint() != load_config({"tolerance_tax": "1.01"}).fingerprint()
    assert load_config().fingerprint() != load_config({"tolerance_taxable": "0"}).fingerprint()


def test_load_config_file(tmp_path: Path) -> None:
    path = tmp_path / "overrides.json"
    path.write_text('{"tolerance_tax": 2.5, "_note": "local"}', encoding="utf-8")
    cfg = load_config_file(path)
    assert cfg.tolerance_tax == D("2.5")
    assert isinstance(cfg.tolerance_tax, Decimal)


@pytest.mark.parametrize(
    "content",
    [
        b"{not json " + SENTINEL.encode(),
        b'["tolerance_tax"]',
        b'{"tolerance_tax": NaN}',
        b"\xff\xfe" + SENTINEL.encode(),
    ],
)
def test_load_config_file_rejects_bad_content(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "overrides.json"
    path.write_bytes(content)
    with pytest.raises(ConfigError) as excinfo:
        load_config_file(path)
    assert SENTINEL not in str(excinfo.value)


def test_package_data_readable() -> None:
    data_dir = resources.files("itclens").joinpath("data")
    for name in ("default_config.json", "state_codes.json"):
        resource = data_dir.joinpath(name)
        assert resource.is_file()
        assert json.loads(resource.read_text(encoding="utf-8"))
