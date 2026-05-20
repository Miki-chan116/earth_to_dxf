"""Flask prototype UI for earth_to_dxf.

This keeps the existing matplotlib app untouched and reuses the existing
Python modules for geocoding and GSI tile fetching.
"""

from __future__ import annotations

from datetime import datetime
import json
import mimetypes
import os
import socket
import sys
from threading import Lock

from flask import Flask, jsonify, render_template, request, send_file


WEB_APP_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR = os.path.abspath(os.path.join(WEB_APP_DIR, ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.chdir(ROOT_DIR)

from src.config_store import get_background_image_path, normalize_image_path  # noqa: E402
from src.constants import DEFAULT_IMAGE_FILE, SCALE_FILE  # noqa: E402
from src.geocoder import GeocodingError, geocode_address, update_gsi_settings_from_address  # noqa: E402
from src.gsi_tile import (  # noqa: E402
    fetch_gsi_tile_grid,
    get_tile_type_label,
    load_gsi_settings,
    pixel_to_latlon_in_tile_grid,
    save_gsi_settings,
)
from src.output_settings import load_output_settings  # noqa: E402


app = Flask(
    __name__,
    template_folder=os.path.join(WEB_APP_DIR, "templates"),
    static_folder=os.path.join(WEB_APP_DIR, "static"),
)

STATE_LOCK = Lock()


def get_output_assets_dir():
    """Return the active assets directory for fetched GSI tiles."""

    output_settings = load_output_settings()
    return os.path.join(output_settings["output_dir"], "assets")


def current_background_image():
    """Return the current configured background image path."""

    return normalize_image_path(get_background_image_path(default=DEFAULT_IMAGE_FILE))


def load_scale_metadata():
    """Load scale.json and return its dictionary form."""

    if not os.path.exists(SCALE_FILE):
        return {}

    try:
        with open(SCALE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def get_current_tile_grid_metadata():
    """Return usable tile-grid metadata for the current background image."""

    metadata = load_scale_metadata()
    if not metadata:
        return None

    metadata_image = metadata.get("image_file")
    if not isinstance(metadata_image, str) or not metadata_image.strip():
        return None

    if normalize_image_path(metadata_image) != current_background_image():
        return None

    source = metadata.get("source")
    if source == "gsi_tile_grid":
        required = ("zoom", "center_tile_x", "center_tile_y", "grid_size")
        if all(key in metadata for key in required):
            return metadata
        return None

    if source == "gsi_tile":
        required = ("zoom", "tile_x", "tile_y")
        if not all(key in metadata for key in required):
            return None
        converted = dict(metadata)
        converted["center_tile_x"] = converted["tile_x"]
        converted["center_tile_y"] = converted["tile_y"]
        converted["grid_size"] = 1
        converted["source"] = "gsi_tile_grid"
        return converted

    return None


def build_state_payload():
    """Build the UI state payload for the web frontend."""

    settings = load_gsi_settings()
    output_settings = load_output_settings()
    image_path = current_background_image()

    return {
        "address": settings.get("address", ""),
        "display_name": settings.get("display_name", ""),
        "latitude": settings.get("latitude"),
        "longitude": settings.get("longitude"),
        "zoom": settings.get("zoom"),
        "grid_size": settings.get("grid_size"),
        "tile_type": settings.get("tile_type"),
        "tile_type_label": get_tile_type_label(settings.get("tile_type", "pale")),
        "output_dir": output_settings.get("output_dir", ROOT_DIR),
        "image_name": os.path.basename(image_path),
        "image_url": "/map-image/current",
    }


def fetch_current_map(tile_type=None):
    """Fetch the current map image using stored GSI settings."""

    settings = load_gsi_settings()
    if tile_type is not None:
        settings["tile_type"] = tile_type
        save_gsi_settings(settings)

    result = fetch_gsi_tile_grid(
        settings["latitude"],
        settings["longitude"],
        settings["zoom"],
        tile_type=settings["tile_type"],
        grid_size=settings["grid_size"],
        assets_dir=get_output_assets_dir(),
    )
    return result


@app.get("/")
def index():
    """Render the prototype UI."""

    return render_template("index.html", initial_state=build_state_payload())


@app.get("/api/state")
def api_state():
    """Return the current app state."""

    return jsonify({"ok": True, "state": build_state_payload()})


@app.post("/api/address-search")
def api_address_search():
    """Geocode an address, fetch aerial imagery, and return the new state."""

    payload = request.get_json(silent=True) or {}
    address = str(payload.get("address", "")).strip()
    if not address:
        return jsonify({"ok": False, "error": "住所を入力してください"}), 400

    try:
        with STATE_LOCK:
            current_settings = load_gsi_settings()
            geocode_result = geocode_address(address)
            current_settings["tile_type"] = "seamlessphoto"
            update_gsi_settings_from_address(address, geocode_result, current_settings)
            fetch_current_map(tile_type="seamlessphoto")
    except GeocodingError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": f"住所検索に失敗しました: {error}"}), 500

    return jsonify(
        {
            "ok": True,
            "message": "住所検索に成功しました",
            "state": build_state_payload(),
        }
    )


@app.post("/api/map-type")
def api_map_type():
    """Switch map type and refetch the current map."""

    payload = request.get_json(silent=True) or {}
    tile_type = str(payload.get("tile_type", "")).strip()
    if not tile_type:
        return jsonify({"ok": False, "error": "地図種別を指定してください"}), 400

    try:
        with STATE_LOCK:
            fetch_current_map(tile_type=tile_type)
    except Exception as error:
        return jsonify({"ok": False, "error": f"地図取得に失敗しました: {error}"}), 500

    return jsonify(
        {
            "ok": True,
            "message": "地図を更新しました",
            "state": build_state_payload(),
        }
    )


@app.post("/api/fetch-map")
def api_fetch_map():
    """Fetch the current map without changing tile type."""

    try:
        with STATE_LOCK:
            fetch_current_map()
    except Exception as error:
        return jsonify({"ok": False, "error": f"地図取得に失敗しました: {error}"}), 500

    return jsonify(
        {
            "ok": True,
            "message": "地図を取得しました",
            "state": build_state_payload(),
        }
    )


@app.post("/api/set-center")
def api_set_center():
    """Set a new tile center from clicked image pixel coordinates."""

    payload = request.get_json(silent=True) or {}

    try:
        pixel_x = float(payload.get("pixel_x"))
        pixel_y = float(payload.get("pixel_y"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "pixel_x / pixel_y が不正です"}), 400

    metadata = get_current_tile_grid_metadata()
    if metadata is None:
        return jsonify({"ok": False, "error": "現在の地図から中心位置を計算できません"}), 400

    try:
        with STATE_LOCK:
            latitude, longitude = pixel_to_latlon_in_tile_grid(
                pixel_x=pixel_x,
                pixel_y=pixel_y,
                zoom=metadata["zoom"],
                center_tile_x=metadata["center_tile_x"],
                center_tile_y=metadata["center_tile_y"],
                grid_size=metadata["grid_size"],
                tile_size=256,
            )

            settings = load_gsi_settings()
            settings["latitude"] = latitude
            settings["longitude"] = longitude
            settings["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_gsi_settings(settings)
            fetch_current_map()
    except Exception as error:
        return jsonify({"ok": False, "error": f"中心更新に失敗しました: {error}"}), 500

    return jsonify(
        {
            "ok": True,
            "success": True,
            "message": "中心位置を更新しました",
            "lat": latitude,
            "lon": longitude,
            "image_url": "/map-image/current",
            "state": build_state_payload(),
        }
    )


@app.get("/map-image/current")
def map_image_current():
    """Serve the current background image."""

    image_path = current_background_image()
    if not os.path.exists(image_path):
        return jsonify({"ok": False, "error": "背景画像がありません"}), 404

    mime_type, _ = mimetypes.guess_type(image_path)
    return send_file(image_path, mimetype=mime_type or "application/octet-stream")


def choose_port(default_port=5001):
    """Choose an available local port."""

    for port in range(default_port, default_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    return default_port


if __name__ == "__main__":
    port = choose_port()
    print(f"Flask prototype starting: http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
