"""GSI tile download and scale metadata helpers."""

from datetime import datetime
import json
import math
import os
import ssl
from urllib.request import urlopen

from PIL import Image

from src.config_store import get_storable_image_path, save_config
from src.constants import (
    ASSETS_DIR,
    GSI_DEFAULT_LAT,
    GSI_DEFAULT_LON,
    GSI_DEFAULT_TILE_TYPE,
    GSI_DEFAULT_ZOOM,
    GSI_SETTINGS_FILE,
    SCALE_FILE,
)

GSI_TILE_BASE_URL = "https://cyberjapandata.gsi.go.jp/xyz"
WEB_MERCATOR_MAX_LAT = 85.05112878
CA_CERTIFICATE_PATHS = (
    "/etc/ssl/cert.pem",
    "/opt/homebrew/etc/ca-certificates/cert.pem",
    "/usr/local/etc/openssl@3/cert.pem",
)
ALLOWED_GRID_SIZES = {1, 3, 5}
GSI_TILE_TYPES = {
    "std": {"label": "標準地図", "extension": "png"},
    "pale": {"label": "淡色地図", "extension": "png"},
    "seamlessphoto": {"label": "空中写真", "extension": "jpg"},
}
GSI_TILE_TYPE_LABELS = {
    tile["label"]: tile_type for tile_type, tile in GSI_TILE_TYPES.items()
}


def default_gsi_settings():
    """Return default GSI tile fetch settings."""

    return {
        "address": "",
        "latitude": GSI_DEFAULT_LAT,
        "longitude": GSI_DEFAULT_LON,
        "zoom": GSI_DEFAULT_ZOOM,
        "grid_size": 3,
        "tile_type": GSI_DEFAULT_TILE_TYPE,
    }


def normalize_tile_type(tile_type):
    """Normalize a tile type code or display label."""

    value = str(tile_type).strip()
    if value in GSI_TILE_TYPES:
        return value
    if value in GSI_TILE_TYPE_LABELS:
        return GSI_TILE_TYPE_LABELS[value]

    raise ValueError("地図種別は std / pale / seamlessphoto のいずれかです")


def get_tile_type_label(tile_type):
    """Return the display label for a tile type."""

    normalized = normalize_tile_type(tile_type)
    return GSI_TILE_TYPES[normalized]["label"]


def get_tile_extension(tile_type):
    """Return the file extension for a tile type."""

    normalized = normalize_tile_type(tile_type)
    return GSI_TILE_TYPES[normalized]["extension"]


def validate_gsi_settings(lat, lon, zoom, grid_size, tile_type=GSI_DEFAULT_TILE_TYPE):
    """Validate and normalize GSI tile fetch settings."""

    try:
        latitude = float(lat)
    except (TypeError, ValueError):
        raise ValueError("緯度は数値で入力してください")

    try:
        longitude = float(lon)
    except (TypeError, ValueError):
        raise ValueError("経度は数値で入力してください")

    try:
        zoom_int = int(str(zoom).strip())
    except (TypeError, ValueError):
        raise ValueError("ズームは整数で入力してください")

    try:
        grid_size_int = int(str(grid_size).strip())
    except (TypeError, ValueError):
        raise ValueError("グリッドサイズは 1 / 3 / 5 のいずれかです")

    if not -WEB_MERCATOR_MAX_LAT <= latitude <= WEB_MERCATOR_MAX_LAT:
        raise ValueError(
            f"緯度は -{WEB_MERCATOR_MAX_LAT:.6f} から "
            f"{WEB_MERCATOR_MAX_LAT:.6f} の範囲です"
        )

    if not -180.0 <= longitude <= 180.0:
        raise ValueError("経度は -180 から 180 の範囲です")

    if zoom_int < 0:
        raise ValueError("ズームは0以上の整数で入力してください")

    if grid_size_int not in ALLOWED_GRID_SIZES:
        raise ValueError("グリッドサイズは 1 / 3 / 5 のいずれかです")

    normalized_tile_type = normalize_tile_type(tile_type)

    return {
        "latitude": latitude,
        "longitude": longitude,
        "zoom": zoom_int,
        "grid_size": grid_size_int,
        "tile_type": normalized_tile_type,
    }


def load_gsi_settings(path=GSI_SETTINGS_FILE):
    """Load saved GSI tile fetch settings."""

    settings = default_gsi_settings()

    if not os.path.exists(path):
        return settings

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        print(f"{path} を読み込めませんでした: {error}")
        return settings

    if not isinstance(data, dict):
        return settings

    try:
        normalized = validate_gsi_settings(
            data.get("latitude", settings["latitude"]),
            data.get("longitude", settings["longitude"]),
            data.get("zoom", settings["zoom"]),
            data.get("grid_size", settings["grid_size"]),
            data.get("tile_type", settings["tile_type"]),
        )
        for key in ("address", "display_name", "updated_at"):
            value = data.get(key)
            if isinstance(value, str):
                normalized[key] = value
        return normalized
    except ValueError as error:
        print(f"{path} の設定が不正です: {error}")
        return settings


def save_gsi_settings(settings, path=GSI_SETTINGS_FILE):
    """Save GSI tile fetch settings."""

    normalized = validate_gsi_settings(
        settings.get("latitude"),
        settings.get("longitude"),
        settings.get("zoom"),
        settings.get("grid_size"),
        settings.get("tile_type", GSI_DEFAULT_TILE_TYPE),
    )
    data = dict(normalized)
    for key in ("address", "display_name"):
        value = settings.get(key)
        if isinstance(value, str):
            data[key] = value
    data["updated_at"] = settings.get("updated_at") or datetime.now().isoformat(timespec="seconds")

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    normalized.update({key: data[key] for key in ("address", "display_name") if key in data})
    normalized["updated_at"] = data["updated_at"]
    return normalized


def latlon_to_tile(lat, lon, zoom):
    """Convert latitude/longitude to Web Mercator tile x/y."""

    if zoom < 0:
        raise ValueError("zoom must be 0 or greater")

    clamped_lat = max(min(float(lat), WEB_MERCATOR_MAX_LAT), -WEB_MERCATOR_MAX_LAT)
    normalized_lon = max(min(float(lon), 180.0), -180.0)
    n = 2 ** int(zoom)
    lat_rad = math.radians(clamped_lat)

    tile_x = int((normalized_lon + 180.0) / 360.0 * n)
    tile_y = int(
        (1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi)
        / 2.0
        * n
    )

    return min(max(tile_x, 0), n - 1), min(max(tile_y, 0), n - 1)


def tile_pixel_to_latlon(tile_x, tile_y, zoom, pixel_x=0.0, pixel_y=0.0, tile_size=256):
    """Convert tile coordinates plus pixel offset to latitude/longitude."""

    n = 2 ** int(zoom)
    world_x = float(tile_x) + (float(pixel_x) / float(tile_size))
    world_y = float(tile_y) + (float(pixel_y) / float(tile_size))

    lon = (world_x / n) * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - (2.0 * world_y / n))))
    lat = math.degrees(lat_rad)
    return lat, lon


def pixel_to_latlon_in_tile_grid(
    pixel_x,
    pixel_y,
    zoom,
    center_tile_x,
    center_tile_y,
    grid_size,
    tile_size=256,
):
    """Convert pixel coordinates inside a merged tile grid image to latitude/longitude."""

    radius = int(grid_size) // 2
    world_tile_x = float(center_tile_x) - radius
    world_tile_y = float(center_tile_y) - radius
    return tile_pixel_to_latlon(
        world_tile_x,
        world_tile_y,
        zoom,
        pixel_x=float(pixel_x),
        pixel_y=float(pixel_y),
        tile_size=tile_size,
    )


def latlon_to_pixel_in_tile_grid(
    lat,
    lon,
    zoom,
    center_tile_x,
    center_tile_y,
    grid_size,
    tile_size=256,
):
    """Convert latitude/longitude to pixel coordinates inside a merged tile grid image."""

    n = 2 ** int(zoom)
    lat_rad = math.radians(float(lat))
    world_x = ((float(lon) + 180.0) / 360.0) * n
    world_y = (
        1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi
    ) / 2.0 * n

    radius = int(grid_size) // 2
    pixel_x = (world_x - (float(center_tile_x) - radius)) * float(tile_size)
    pixel_y = (world_y - (float(center_tile_y) - radius)) * float(tile_size)
    return pixel_x, pixel_y


def tile_to_url(tile_type, z, x, y):
    """Build the GSI tile URL."""

    normalized = normalize_tile_type(tile_type)
    extension = get_tile_extension(normalized)
    return f"{GSI_TILE_BASE_URL}/{normalized}/{z}/{x}/{y}.{extension}"


def download_tile(url, output_path):
    """Download a tile image to output_path."""

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with urlopen(url, timeout=20, context=create_ssl_context()) as response:
        image_data = response.read()

    with open(output_path, "wb") as file:
        file.write(image_data)

    return output_path


def create_ssl_context():
    """Create an SSL context that works with common local Python installs."""

    try:
        import certifi
    except ImportError:
        certifi = None

    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())

    for cert_path in CA_CERTIFICATE_PATHS:
        if os.path.exists(cert_path):
            return ssl.create_default_context(cafile=cert_path)

    return ssl.create_default_context()


def calculate_meters_per_pixel(lat, zoom):
    """Calculate Web Mercator ground resolution for latitude and zoom."""

    return 156543.03392 * math.cos(math.radians(float(lat))) / (2 ** int(zoom))


def build_tile_output_path(z, x, y, tile_type="std", assets_dir=ASSETS_DIR):
    """Build the local tile output path."""

    normalized = normalize_tile_type(tile_type)
    extension = get_tile_extension(normalized)
    return os.path.join(assets_dir, f"gsi_{normalized}_z{z}_x{x}_y{y}.{extension}")


def build_tile_grid_output_path(
    z,
    center_x,
    center_y,
    grid_size,
    tile_type="std",
    assets_dir=ASSETS_DIR,
):
    """Build the local merged tile grid output path."""

    normalized = normalize_tile_type(tile_type)
    extension = get_tile_extension(normalized)
    return os.path.join(
        assets_dir,
        f"gsi_{normalized}_z{z}_x{center_x}_y{center_y}_{grid_size}x{grid_size}.{extension}",
    )


def get_tile_grid(center_x, center_y, zoom, grid_size=3):
    """Return tile coordinates around the center tile."""

    if grid_size not in ALLOWED_GRID_SIZES:
        raise ValueError("grid_size must be one of 1, 3, or 5")

    max_tile = (2 ** int(zoom)) - 1
    radius = grid_size // 2
    tiles = []

    for row, tile_y in enumerate(range(center_y - radius, center_y + radius + 1)):
        for col, tile_x in enumerate(range(center_x - radius, center_x + radius + 1)):
            clamped_x = min(max(tile_x, 0), max_tile)
            clamped_y = min(max(tile_y, 0), max_tile)
            tiles.append(
                {
                    "x": clamped_x,
                    "y": clamped_y,
                    "row": row,
                    "col": col,
                }
            )

    return tiles


def merge_tiles(tile_paths, output_path, grid_size=3):
    """Merge downloaded tiles into one PNG image."""

    if len(tile_paths) != grid_size * grid_size:
        raise ValueError("tile_paths count does not match grid_size")

    first_tile = Image.open(tile_paths[0]["path"])
    tile_width, tile_height = first_tile.size
    first_tile.close()

    merged_image = Image.new(
        "RGB",
        (tile_width * grid_size, tile_height * grid_size),
    )

    for tile in tile_paths:
        tile_image = Image.open(tile["path"]).convert("RGB")
        merged_image.paste(
            tile_image,
            (tile["col"] * tile_width, tile["row"] * tile_height),
        )
        tile_image.close()

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    merged_image.save(output_path)
    merged_image.close()

    return output_path


def build_scale_data(
    image_file,
    meters_per_pixel,
    lat,
    lon,
    zoom,
    tile_x,
    tile_y,
    tile_type,
):
    """Build scale.json data for a fetched GSI tile."""

    return {
        "meters_per_pixel": meters_per_pixel,
        "image_file": get_storable_image_path(image_file),
        "image_filename": os.path.basename(image_file),
        "source": "gsi_tile",
        "latitude": lat,
        "longitude": lon,
        "zoom": zoom,
        "tile_type": normalize_tile_type(tile_type),
        "tile_x": tile_x,
        "tile_y": tile_y,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_grid_scale_data(
    image_file,
    meters_per_pixel,
    lat,
    lon,
    zoom,
    center_tile_x,
    center_tile_y,
    grid_size,
    tile_type,
):
    """Build scale.json data for a fetched GSI tile grid."""

    return {
        "meters_per_pixel": meters_per_pixel,
        "image_file": get_storable_image_path(image_file),
        "image_filename": os.path.basename(image_file),
        "source": "gsi_tile_grid",
        "latitude": lat,
        "longitude": lon,
        "zoom": zoom,
        "tile_type": normalize_tile_type(tile_type),
        "center_tile_x": center_tile_x,
        "center_tile_y": center_tile_y,
        "grid_size": grid_size,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def save_scale_data(scale_data, scale_file=SCALE_FILE):
    """Save scale metadata for the fetched tile."""

    with open(scale_file, "w", encoding="utf-8") as file:
        json.dump(scale_data, file, ensure_ascii=False, indent=2)


def fetch_gsi_tile(lat, lon, zoom, tile_type="std", assets_dir=ASSETS_DIR):
    """Fetch one GSI center tile and update config.json and scale.json."""

    zoom = int(zoom)
    tile_type = normalize_tile_type(tile_type)
    tile_x, tile_y = latlon_to_tile(lat, lon, zoom)
    url = tile_to_url(tile_type, zoom, tile_x, tile_y)
    output_path = build_tile_output_path(
        zoom,
        tile_x,
        tile_y,
        tile_type=tile_type,
        assets_dir=assets_dir,
    )
    download_tile(url, output_path)

    meters_per_pixel = calculate_meters_per_pixel(lat, zoom)
    scale_data = build_scale_data(
        image_file=output_path,
        meters_per_pixel=meters_per_pixel,
        lat=lat,
        lon=lon,
        zoom=zoom,
        tile_x=tile_x,
        tile_y=tile_y,
        tile_type=tile_type,
    )

    save_config({"background_image": get_storable_image_path(output_path)})
    save_scale_data(scale_data)

    return {
        "image_file": output_path,
        "url": url,
        "meters_per_pixel": meters_per_pixel,
        "latitude": lat,
        "longitude": lon,
        "zoom": zoom,
        "tile_type": tile_type,
        "tile_x": tile_x,
        "tile_y": tile_y,
        "scale_data": scale_data,
    }


def fetch_gsi_tile_grid(lat, lon, zoom, tile_type="std", grid_size=3, assets_dir=ASSETS_DIR):
    """Fetch a GSI tile grid, merge it, and update config.json and scale.json."""

    zoom = int(zoom)
    tile_type = normalize_tile_type(tile_type)
    center_tile_x, center_tile_y = latlon_to_tile(lat, lon, zoom)
    tiles = get_tile_grid(center_tile_x, center_tile_y, zoom, grid_size=grid_size)
    downloaded_tiles = []

    for tile in tiles:
        url = tile_to_url(tile_type, zoom, tile["x"], tile["y"])
        tile_path = build_tile_output_path(
            zoom,
            tile["x"],
            tile["y"],
            tile_type=tile_type,
            assets_dir=assets_dir,
        )
        download_tile(url, tile_path)
        downloaded_tiles.append(
            {
                "path": tile_path,
                "url": url,
                "x": tile["x"],
                "y": tile["y"],
                "row": tile["row"],
                "col": tile["col"],
            }
        )

    output_path = build_tile_grid_output_path(
        zoom,
        center_tile_x,
        center_tile_y,
        grid_size,
        tile_type=tile_type,
        assets_dir=assets_dir,
    )
    merge_tiles(downloaded_tiles, output_path, grid_size=grid_size)

    meters_per_pixel = calculate_meters_per_pixel(lat, zoom)
    scale_data = build_grid_scale_data(
        image_file=output_path,
        meters_per_pixel=meters_per_pixel,
        lat=lat,
        lon=lon,
        zoom=zoom,
        center_tile_x=center_tile_x,
        center_tile_y=center_tile_y,
        grid_size=grid_size,
        tile_type=tile_type,
    )

    save_config({"background_image": get_storable_image_path(output_path)})
    save_scale_data(scale_data)

    return {
        "image_file": output_path,
        "meters_per_pixel": meters_per_pixel,
        "latitude": lat,
        "longitude": lon,
        "zoom": zoom,
        "tile_type": tile_type,
        "center_tile_x": center_tile_x,
        "center_tile_y": center_tile_y,
        "grid_size": grid_size,
        "tiles": downloaded_tiles,
        "scale_data": scale_data,
    }
