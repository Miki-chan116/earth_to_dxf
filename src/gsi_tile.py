"""GSI tile download and scale metadata helpers."""

from datetime import datetime
import json
import math
import os
import ssl
from urllib.request import urlopen

from src.config_store import get_storable_image_path, save_config
from src.constants import ASSETS_DIR, SCALE_FILE

GSI_TILE_BASE_URL = "https://cyberjapandata.gsi.go.jp/xyz"
WEB_MERCATOR_MAX_LAT = 85.05112878
CA_CERTIFICATE_PATHS = (
    "/etc/ssl/cert.pem",
    "/opt/homebrew/etc/ca-certificates/cert.pem",
    "/usr/local/etc/openssl@3/cert.pem",
)


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


def tile_to_url(tile_type, z, x, y):
    """Build the GSI tile URL."""

    return f"{GSI_TILE_BASE_URL}/{tile_type}/{z}/{x}/{y}.png"


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


def build_tile_output_path(z, x, y, assets_dir=ASSETS_DIR):
    """Build the local tile output path."""

    return os.path.join(assets_dir, f"gsi_tile_z{z}_x{x}_y{y}.png")


def build_scale_data(
    image_file,
    meters_per_pixel,
    lat,
    lon,
    zoom,
    tile_x,
    tile_y,
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
        "tile_x": tile_x,
        "tile_y": tile_y,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def save_scale_data(scale_data, scale_file=SCALE_FILE):
    """Save scale metadata for the fetched tile."""

    with open(scale_file, "w", encoding="utf-8") as file:
        json.dump(scale_data, file, ensure_ascii=False, indent=2)


def fetch_gsi_tile(lat, lon, zoom, tile_type="std"):
    """Fetch one GSI center tile and update config.json and scale.json."""

    zoom = int(zoom)
    tile_x, tile_y = latlon_to_tile(lat, lon, zoom)
    url = tile_to_url(tile_type, zoom, tile_x, tile_y)
    output_path = build_tile_output_path(zoom, tile_x, tile_y)
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
        "tile_x": tile_x,
        "tile_y": tile_y,
        "scale_data": scale_data,
    }
