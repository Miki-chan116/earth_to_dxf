"""Helpers for output_settings.json."""

import json
import os

from .constants import DEFAULT_OUTPUT_DIR, OUTPUT_SETTINGS_FILE


def normalize_output_dir(output_dir):
    """Return a normalized absolute output directory path."""

    if not isinstance(output_dir, str) or not output_dir.strip():
        return os.path.abspath(os.getcwd())

    return os.path.abspath(os.path.expanduser(output_dir.strip()))


def default_output_settings():
    """Return the default output settings dictionary."""

    return {"output_dir": normalize_output_dir(DEFAULT_OUTPUT_DIR)}


def load_output_settings(path=OUTPUT_SETTINGS_FILE):
    """Load output settings, falling back to cwd when missing or invalid."""

    if not os.path.exists(path):
        return {"output_dir": os.path.abspath(os.getcwd())}

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        print(f"{path} を読み込めませんでした: {error}")
        return {"output_dir": os.path.abspath(os.getcwd())}

    if not isinstance(data, dict):
        return {"output_dir": os.path.abspath(os.getcwd())}

    return {
        "output_dir": normalize_output_dir(
            data.get("output_dir", os.path.abspath(os.getcwd()))
        )
    }


def save_output_settings(settings, path=OUTPUT_SETTINGS_FILE):
    """Save output settings and return the normalized dictionary."""

    normalized = {"output_dir": normalize_output_dir(settings.get("output_dir"))}

    with open(path, "w", encoding="utf-8") as file:
        json.dump(normalized, file, ensure_ascii=False, indent=2)

    return normalized
