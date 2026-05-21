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
from src.constants import DEFAULT_IMAGE_FILE, LAYERS, PROJECT_FILE, SCALE_FILE  # noqa: E402
from src.dxf_export import export_dxf  # noqa: E402
from src.geocoder import GeocodingError, geocode_address, update_gsi_settings_from_address  # noqa: E402
from src.geometry_utils import calculate_polygon_area_m2, calculate_polyline_length_m  # noqa: E402
from src.gsi_tile import (  # noqa: E402
    fetch_gsi_tile_grid,
    get_tile_type_label,
    load_gsi_settings,
    pixel_to_latlon_in_tile_grid,
    save_gsi_settings,
)
from src.output_settings import load_output_settings  # noqa: E402
from src.project_store import (  # noqa: E402
    build_project_data as build_project_file_data,
    deserialize_lines as deserialize_project_lines,
    deserialize_points as deserialize_project_points,
    get_existing_project_created_at,
    get_project_background_warning,
    load_project as load_project_file,
    save_project as save_project_file,
)


app = Flask(
    __name__,
    template_folder=os.path.join(WEB_APP_DIR, "templates"),
    static_folder=os.path.join(WEB_APP_DIR, "static"),
)

STATE_LOCK = Lock()
MIN_GSI_ZOOM = 5
MAX_GSI_ZOOM = 18
PAN_RATIO = 0.2
TILE_SIZE = 256


def get_output_assets_dir():
    """Return the active assets directory for fetched GSI tiles."""

    output_settings = load_output_settings()
    return os.path.join(output_settings["output_dir"], "assets")


def get_output_project_path():
    """Return the active project.json path inside the configured output dir."""

    output_settings = load_output_settings()
    return os.path.join(output_settings["output_dir"], PROJECT_FILE)


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
        "meters_per_pixel": get_optional_meters_per_pixel(),
        "output_dir": output_settings.get("output_dir", ROOT_DIR),
        "image_name": os.path.basename(image_path),
        "image_url": "/map-image/current",
    }


def get_current_meters_per_pixel():
    """Return the current meters-per-pixel value from scale metadata."""

    metadata = load_scale_metadata()
    value = metadata.get("meters_per_pixel")
    if not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("scale.json の meters_per_pixel が不正です")
    return float(value)


def get_optional_meters_per_pixel():
    """Return meters-per-pixel when available for display-only UI."""

    try:
        return get_current_meters_per_pixel()
    except ValueError:
        return None


def get_layer_by_name(layer_name):
    """Return a DXF layer definition for a web layer name."""

    requested_name = str(layer_name or "").strip().upper()
    for layer in LAYERS.values():
        if layer["name"] == requested_name:
            return layer.copy()

    return LAYERS["1"].copy()


def get_layer_key_by_name(layer_name, default_key="1"):
    """Return the shared layer key for a web layer name."""

    requested_name = str(layer_name or "").strip().upper()
    for key, layer in LAYERS.items():
        if layer["name"] == requested_name:
            return key
    return default_key


def get_layer_name_by_key(layer_key, default_name="ROAD"):
    """Return the shared DXF layer name for a stored layer key."""

    layer = LAYERS.get(str(layer_key or "").strip())
    if not layer:
        return default_name
    return layer["name"]


def build_dxf_lines(entities):
    """Convert web entities into the existing dxf_export line structure."""

    if not isinstance(entities, list):
        raise ValueError("entities は配列で指定してください")

    lines = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if entity.get("type") not in {"polyline", "polygon"}:
            continue

        points = []
        for point in entity.get("points", []):
            if not isinstance(point, dict):
                continue
            x = point.get("x")
            y = point.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            points.append((float(x), float(y)))

        closed = bool(entity.get("closed", False))
        if len(points) < (3 if closed else 2):
            continue

        lines.append(
            {
                "layer": get_layer_by_name(entity.get("layer")),
                "points": points,
                "closed": closed,
            }
        )

    if not lines:
        raise ValueError("DXF保存できる線がありません")

    return lines


def build_project_lines_from_entities(entities):
    """Convert web entities into the existing project.json line structure."""

    if not isinstance(entities, list):
        return []

    lines = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if entity.get("type") not in {"polyline", "polygon"}:
            continue

        points = parse_web_points(entity.get("points", []))
        closed = bool(entity.get("closed", False))
        if len(points) < (3 if closed else 2):
            continue

        lines.append(
            {
                "layer": get_layer_by_name(entity.get("layer")),
                "points": points,
                "closed": closed,
                "length_m": float(entity.get("length", 0.0) or 0.0),
                "area_m2": float(entity.get("area", 0.0) or 0.0),
            }
        )

    return lines


def build_web_entities_from_project_lines(lines, current_layer_key="1"):
    """Convert stored project lines into the web entity structure."""

    restored_lines = deserialize_project_lines(
        lines,
        layers=LAYERS,
        default_layer_key=current_layer_key,
    )

    entities = []
    for line in restored_lines:
        layer = line.get("layer", {})
        layer_name = str(layer.get("name") or "ROAD").strip().upper() or "ROAD"
        points = [{"x": float(x), "y": float(y)} for x, y in line.get("points", [])]
        closed = bool(line.get("closed", False))
        entities.append(
            {
                "type": "polygon" if closed else "polyline",
                "layer": layer_name,
                "points": points,
                "closed": closed,
                "length": float(line.get("length_m", 0.0) or 0.0),
                "area": float(line.get("area_m2", 0.0) or 0.0),
            }
        )

    return entities


def build_web_points(points):
    """Convert stored current points to the web point structure."""

    return [{"x": float(x), "y": float(y)} for x, y in deserialize_project_points(points)]


def parse_web_points(points):
    """Convert web point dictionaries to internal (x, y) tuples."""

    parsed_points = []
    if not isinstance(points, list):
        return parsed_points

    for point in points:
        if not isinstance(point, dict):
            continue
        x = point.get("x")
        y = point.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue
        parsed_points.append((float(x), float(y)))

    return parsed_points


def parse_optional_positive_number(value):
    """Return a positive float from an optional request value."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def build_web_project_payload(data):
    """Convert project.json data into the web app state structure."""

    current_layer_key = data.get("current_layer", "1")
    current_layer_name = get_layer_name_by_key(current_layer_key)
    return {
        "entities": build_web_entities_from_project_lines(
            data.get("lines", []),
            current_layer_key=current_layer_key,
        ),
        "currentPoints": build_web_points(data.get("current_points", [])),
        "currentLayer": current_layer_name,
        "webMapState": data.get("web_map_state", {}),
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


def update_settings_timestamp(settings):
    """Update the shared GSI settings timestamp."""

    settings["updated_at"] = datetime.now().isoformat(timespec="seconds")


def adjust_map_view(action):
    """Update zoom or center coordinates and refetch the current map."""

    settings = load_gsi_settings()

    if action == "zoom_in":
        settings["zoom"] = min(int(settings["zoom"]) + 1, MAX_GSI_ZOOM)
        update_settings_timestamp(settings)
        save_gsi_settings(settings)
        return fetch_current_map(), "拡大しました"

    if action == "zoom_out":
        settings["zoom"] = max(int(settings["zoom"]) - 1, MIN_GSI_ZOOM)
        update_settings_timestamp(settings)
        save_gsi_settings(settings)
        return fetch_current_map(), "縮小しました"

    pan_offsets = {
        "pan_up": (0.0, -1.0),
        "pan_down": (0.0, 1.0),
        "pan_left": (-1.0, 0.0),
        "pan_right": (1.0, 0.0),
    }
    if action not in pan_offsets:
        raise ValueError("不明な地図操作です")

    metadata = get_current_tile_grid_metadata()
    if metadata is None:
        raise ValueError("現在の地図から移動量を計算できません")

    image_size = float(metadata["grid_size"]) * TILE_SIZE
    center_pixel = image_size / 2.0
    shift_pixels = image_size * PAN_RATIO
    offset_x, offset_y = pan_offsets[action]
    latitude, longitude = pixel_to_latlon_in_tile_grid(
        pixel_x=center_pixel + (shift_pixels * offset_x),
        pixel_y=center_pixel + (shift_pixels * offset_y),
        zoom=metadata["zoom"],
        center_tile_x=metadata["center_tile_x"],
        center_tile_y=metadata["center_tile_y"],
        grid_size=metadata["grid_size"],
        tile_size=TILE_SIZE,
    )

    settings["latitude"] = latitude
    settings["longitude"] = longitude
    update_settings_timestamp(settings)
    save_gsi_settings(settings)
    return fetch_current_map(), "地図を移動しました"


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


@app.post("/api/adjust-view")
def api_adjust_view():
    """Adjust map zoom or pan and refetch the current map."""

    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action", "")).strip()
    if not action:
        return jsonify({"ok": False, "error": "地図操作を指定してください"}), 400

    try:
        with STATE_LOCK:
            _, message = adjust_map_view(action)
    except Exception as error:
        return jsonify({"ok": False, "error": f"地図操作に失敗しました: {error}"}), 500

    return jsonify(
        {
            "ok": True,
            "message": message,
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
            update_settings_timestamp(settings)
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


@app.post("/api/export-dxf")
def api_export_dxf():
    """Export the current web drawing to DXF using the shared exporter."""

    payload = request.get_json(silent=True) or {}

    try:
        lines = build_dxf_lines(payload.get("entities", []))
        output_settings = load_output_settings()
        output_dir = output_settings["output_dir"]
        background_image_path = current_background_image()
        meters_per_pixel = get_current_meters_per_pixel()
        image_url_path = os.path.relpath(background_image_path, output_dir)

        filename = export_dxf(
            lines=lines,
            background_image_path=background_image_path,
            image_width=payload.get("image_width") or 0,
            image_height=payload.get("image_height") or 0,
            meters_per_pixel=meters_per_pixel,
            output_dir=output_dir,
            background_image_reference_path=image_url_path,
            include_scale_annotations=True,
            approximate_scale_denominator=parse_optional_positive_number(
                payload.get("approximate_scale_denominator")
            ),
        )
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400

    return jsonify({"success": True, "path": filename})


@app.post("/api/save-project")
def api_save_project():
    """Save the current web drawing as project.json in the output directory."""

    payload = request.get_json(silent=True) or {}

    try:
        output_settings = load_output_settings()
        output_dir = output_settings["output_dir"]
        os.makedirs(output_dir, exist_ok=True)
        project_path = get_output_project_path()
        current_layer_name = str(payload.get("currentLayer", "ROAD")).strip().upper() or "ROAD"
        current_layer_key = get_layer_key_by_name(current_layer_name)
        meters_per_pixel = parse_optional_positive_number(
            payload.get("app", {}).get("meters_per_pixel")
            if isinstance(payload.get("app"), dict)
            else None
        )
        if meters_per_pixel is None:
            meters_per_pixel = get_current_meters_per_pixel()

        data = build_project_file_data(
            background_image=current_background_image(),
            meters_per_pixel=meters_per_pixel,
            current_layer=current_layer_key,
            lines=build_project_lines_from_entities(payload.get("entities", [])),
            current_points=parse_web_points(payload.get("currentPoints", [])),
            layers=LAYERS,
            created_at=get_existing_project_created_at(project_path),
            storage_base_dir=output_dir,
        )
        data["web_map_state"] = payload.get("app", {}) if isinstance(payload.get("app"), dict) else {}

        success, error_message = save_project_file(project_path, data)
        if not success:
            raise ValueError(error_message or "project.json を保存できませんでした")
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    return jsonify({"ok": True, "path": project_path})


@app.get("/api/load-project")
def api_load_project():
    """Load project.json from the configured output directory for the web UI."""

    project_path = get_output_project_path()
    data, error_message = load_project_file(project_path)
    if error_message:
        return jsonify({"ok": False, "error": error_message}), 404

    warning_message = get_project_background_warning(data, current_background_image())
    project = build_web_project_payload(data)

    return jsonify(
        {
            "ok": True,
            "project": project,
            "warning": warning_message,
            "path": project_path,
        }
    )


@app.post("/api/calculate-area")
def api_calculate_area():
    """Calculate approximate polygon area using current scale settings."""

    payload = request.get_json(silent=True) or {}

    try:
        points = parse_web_points(payload.get("points", []))
        if len(points) < 3:
            raise ValueError("面積計算には3点以上必要です")
        area = calculate_polygon_area_m2(points, get_current_meters_per_pixel())
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400

    return jsonify({"success": True, "area": area})


@app.post("/api/calculate-length")
def api_calculate_length():
    """Calculate approximate polyline length using current scale settings."""

    payload = request.get_json(silent=True) or {}

    try:
        points = parse_web_points(payload.get("points", []))
        if len(points) < 2:
            raise ValueError("延長計算には2点以上必要です")
        length = calculate_polyline_length_m(points, get_current_meters_per_pixel())
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 400

    return jsonify({"success": True, "length": length})


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
