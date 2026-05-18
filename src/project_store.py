"""project.json serialization and storage helpers."""

from datetime import datetime
import json
import os

from .config_store import get_storable_image_path, normalize_image_path
from .constants import LAYERS, PROJECT_FILE


def serialize_points(points):
    """Convert point tuples to JSON-friendly [x, y] lists."""

    return [[float(x), float(y)] for x, y in points]


def deserialize_points(points):
    """Convert project.json points to internal (x, y) tuples."""

    restored_points = []

    if not isinstance(points, list):
        return restored_points

    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            continue

        x, y = point
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue

        restored_points.append((float(x), float(y)))

    return restored_points


def get_layer_key_by_name(layer_name, layers=LAYERS, default_layer_key="1"):
    """Return the app layer key for a DXF layer name."""

    for key, layer in layers.items():
        if layer.get("name") == layer_name:
            return key

    return default_layer_key


def serialize_lines(lines, layers=LAYERS, current_layer_key="1"):
    """Convert confirmed lines to project.json data."""

    serialized_lines = []

    for line in lines:
        layer = line.get("layer", {})
        layer_name = layer.get("name")
        layer_key = get_layer_key_by_name(
            layer_name,
            layers=layers,
            default_layer_key=current_layer_key,
        )

        serialized_lines.append(
            {
                "layer_key": layer_key,
                "layer": layer,
                "points": serialize_points(line.get("points", [])),
                "closed": bool(line.get("closed", False)),
            }
        )

    return serialized_lines


def deserialize_lines(lines, layers=LAYERS, default_layer_key="1"):
    """Convert project.json line data to internal app line data."""

    restored_lines = []

    if not isinstance(lines, list):
        return restored_lines

    for line in lines:
        if not isinstance(line, dict):
            continue

        points = deserialize_points(line.get("points", []))
        if len(points) < 2:
            continue

        layer_key = line.get("layer_key")
        layer = None

        if isinstance(layer_key, str) and layer_key in layers:
            layer = layers[layer_key].copy()
        elif isinstance(line.get("layer"), dict):
            layer = line["layer"].copy()

        if layer is None:
            layer = layers.get(default_layer_key)
            if layer is None and layers:
                layer = next(iter(layers.values()))

        if layer is None:
            continue

        restored_lines.append(
            {
                "layer": layer.copy(),
                "points": points,
                "closed": bool(line.get("closed", False)),
            }
        )

    return restored_lines


def project_exists(path=PROJECT_FILE):
    """Return whether project.json exists."""

    return os.path.exists(path)


def load_project(path=PROJECT_FILE):
    """Load project.json and return (data, error_message)."""

    if not project_exists(path):
        return None, f"{path} がありません"

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        return None, f"{path} を読み込めませんでした: {error}"

    if not isinstance(data, dict):
        return None, f"{path} の形式が不正です"

    return data, None


def save_project(path, data):
    """Save project.json and return (success, error_message)."""

    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
    except OSError as error:
        return False, f"{path} を保存できませんでした: {error}"

    return True, None


def get_existing_project_created_at(path=PROJECT_FILE):
    """Return created_at from an existing project file."""

    data, error = load_project(path)
    if error:
        return None

    created_at = data.get("created_at")
    if isinstance(created_at, str) and created_at:
        return created_at

    return None


def build_project_data(
    background_image,
    meters_per_pixel,
    current_layer,
    lines,
    current_points,
    layers=LAYERS,
    created_at=None,
):
    """Build the project.json dictionary without changing its shape."""

    now = datetime.now().isoformat(timespec="seconds")

    return {
        "background_image": get_storable_image_path(background_image),
        "meters_per_pixel": meters_per_pixel,
        "current_layer": current_layer,
        "lines": serialize_lines(lines, layers=layers, current_layer_key=current_layer),
        "current_points": serialize_points(current_points),
        "layers": layers,
        "created_at": created_at or now,
        "updated_at": now,
    }


def get_project_background_warning(data, current_background_image):
    """Return a warning when project and current background images differ."""

    project_background = data.get("background_image")
    if not isinstance(project_background, str) or not project_background.strip():
        return None

    project_path = normalize_image_path(project_background)
    current_path = normalize_image_path(current_background_image)

    if project_path == current_path:
        return None

    return (
        "警告: project.json の背景画像が現在の背景画像と違います: "
        f"{os.path.basename(project_path)} -> {os.path.basename(current_path)}"
    )
