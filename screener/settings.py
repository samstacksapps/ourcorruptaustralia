"""Paths and the version-controlled configuration files."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
FLAGS_DIR = Path(__file__).resolve().parent / "flags"
DATA = ROOT / "data"


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def thresholds(path: Path | None = None) -> dict:
    """Return {section: {name: value}} from thresholds.yml, dropping the source notes."""
    raw = load_yaml(path or CONFIG / "thresholds.yml")
    return {
        section: {name: entry["value"] for name, entry in entries.items()}
        for section, entries in raw.items()
    }


def flag_config(path: Path | None = None) -> dict:
    return load_yaml(path or FLAGS_DIR / "flags.yml")


def calibration(path: Path | None = None) -> dict:
    return load_yaml(path or CONFIG / "calibration.yml")
