"""Shared constants for earth_to_dxf."""

DEBUG = False

DEFAULT_IMAGE_FILE = "map.png"
OUTPUT_PREFIX = "output"
CONFIG_FILE = "config.json"
SCALE_FILE = "scale.json"
PROJECT_FILE = "project.json"
ASSETS_DIR = "assets"

GSI_DEFAULT_LAT = 33.839
GSI_DEFAULT_LON = 132.765
GSI_DEFAULT_ZOOM = 18
GSI_DEFAULT_TILE_TYPE = "std"

PRINT_PAPER_SIZES_MM = {
    "A4横": (297, 210),
    "A3横": (420, 297),
}

PRINT_MARGIN_MM = 10
CIVIL_STANDARD_SCALES = [250, 500, 1000, 2500, 5000]
A3_FOCUS_SCALE = 500

CONFIRMED_LINE_WIDTH = 1.1
CURRENT_LINE_WIDTH = 1.6
SCALE_LINE_WIDTH = 1.4

CONFIRMED_MARKER_SIZE = 3.5
CURRENT_MARKER_SIZE = 6
SELECTED_MARKER_SIZE = 7
SCALE_MARKER_SIZE = 7
MARKER_EDGE_WIDTH = 1.2

CROSSHAIR_LINE_WIDTH = 0.65
CROSSHAIR_CENTER_SIZE = 8
CROSSHAIR_DOT_SIZE = 3
CROSSHAIR_MODE = "small"
CROSSHAIR_UPDATE_INTERVAL_MS = 30
CROSSHAIR_MODES = {"off", "small", "full"}
CROSSHAIR_SMALL_OFFSET_PX = (16, 16)

LAYERS = {
    "1": {"name": "ROAD", "label": "道路", "color": 1, "plot_color": "#d62728"},
    "2": {"name": "SITE", "label": "敷地", "color": 3, "plot_color": "#2ca02c"},
    "3": {"name": "SLOPE", "label": "法面", "color": 5, "plot_color": "#1f77b4"},
    "4": {"name": "STRUCTURE", "label": "構造物", "color": 2, "plot_color": "#ffbf00"},
}

BACKGROUND_LAYER = {
    "name": "IMAGE",
    "label": "背景画像",
    "color": 8,
}
