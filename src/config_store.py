"""config.json helpers for background image settings."""

from datetime import datetime
import json
import os

from .constants import CONFIG_FILE, DEFAULT_IMAGE_FILE


def normalize_image_path(image_path):
    """Return an absolute normalized path for image_path."""

    if os.path.isabs(image_path):
        return os.path.normpath(image_path)

    return os.path.abspath(image_path)


def get_storable_image_path(image_path):
    """Store project-local paths as relative paths."""

    absolute_path = normalize_image_path(image_path)
    cwd = os.path.abspath(os.getcwd())

    try:
        common_path = os.path.commonpath([cwd, absolute_path])
    except ValueError:
        return absolute_path

    if common_path == cwd:
        return os.path.relpath(absolute_path, cwd)

    return absolute_path


def load_config(config_file=CONFIG_FILE):
    """Load config.json and return its raw dictionary."""

    if not os.path.exists(config_file):
        return {}

    try:
        with open(config_file, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        print(f"{config_file} を読み込めませんでした: {error}")
        return {}

    if not isinstance(data, dict):
        return {}

    return data


def save_config(config, config_file=CONFIG_FILE):
    """Save config.json, preserving the existing JSON shape."""

    data = dict(config)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")

    try:
        with open(config_file, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
    except OSError as error:
        print(f"{config_file} を保存できませんでした: {error}")
        return False

    return True


def get_background_image_path(default=DEFAULT_IMAGE_FILE, config_file=CONFIG_FILE):
    """Return the configured background image path, falling back to default."""

    default_image_path = normalize_image_path(default)
    data = load_config(config_file=config_file)
    configured_path = data.get("background_image")

    if not configured_path:
        return default_image_path

    if not isinstance(configured_path, str) or not configured_path.strip():
        print(f"{config_file} の背景画像パスが不正です。{default} を使います")
        return default_image_path

    image_path = normalize_image_path(configured_path.strip())

    if not os.path.exists(image_path):
        print(f"背景画像が見つかりません: {image_path}\n{default} を使います")
        return default_image_path

    return image_path

