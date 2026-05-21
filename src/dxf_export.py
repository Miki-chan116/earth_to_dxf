"""DXF export helpers for earth_to_dxf."""

from datetime import datetime
import os

import ezdxf

from src.constants import BACKGROUND_LAYER, LAYERS, OUTPUT_PREFIX

SCALE_BAR_LAYER = {"name": "SCALE_BAR", "color": 7}
SCALE_NOTE_LAYER = {"name": "SCALE_NOTE", "color": 8}


def build_output_filename(output_prefix=OUTPUT_PREFIX):
    """Build the timestamped DXF output filename."""

    return f"{output_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.dxf"


def convert_pixel_to_world(x, y, image_height, meters_per_pixel):
    """Convert image pixel coordinates to CAD world coordinates."""

    cad_x = x * meters_per_pixel
    cad_y = (image_height - y) * meters_per_pixel

    return cad_x, cad_y


def create_layers(doc, layers=LAYERS, background_layer=BACKGROUND_LAYER):
    """Create the app's DXF layers if they do not already exist."""

    for layer in layers.values():
        if layer["name"] not in doc.layers:
            doc.layers.add(name=layer["name"], color=layer["color"])

    if background_layer["name"] not in doc.layers:
        doc.layers.add(
            name=background_layer["name"],
            color=background_layer["color"],
        )

    for layer in (SCALE_BAR_LAYER, SCALE_NOTE_LAYER):
        if layer["name"] not in doc.layers:
            doc.layers.add(name=layer["name"], color=layer["color"])


def add_background_image(
    doc,
    msp,
    background_image_source_path,
    background_image_reference_path,
    image_width,
    image_height,
    meters_per_pixel,
    background_layer=BACKGROUND_LAYER,
):
    """Add the background image as an external IMAGEDEF reference."""

    if not os.path.exists(background_image_source_path):
        print("背景画像が見つからないため、DXF画像参照は追加しません")
        return

    image_def = doc.add_image_def(
        filename=background_image_reference_path,
        size_in_pixel=(image_width, image_height),
    )

    msp.add_image(
        image_def,
        insert=(0, 0),
        size_in_units=(
            image_width * meters_per_pixel,
            image_height * meters_per_pixel,
        ),
        dxfattribs={"layer": background_layer["name"]},
    )


def add_lines(
    msp,
    lines,
    image_height,
    meters_per_pixel,
):
    """Add confirmed lines and closed areas to the DXF modelspace."""

    for line in lines:
        layer_name = line["layer"]["name"]

        cad_points = [
            convert_pixel_to_world(x, y, image_height, meters_per_pixel)
            for x, y in line["points"]
        ]

        if len(cad_points) < 2:
            continue

        msp.add_lwpolyline(
            cad_points,
            close=line.get("closed", False),
            dxfattribs={"layer": layer_name},
        )


def choose_scale_bar_length_m(image_width_m):
    """Choose a readable scale bar length for the exported drawing."""

    candidates = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]
    target = max(float(image_width_m) * 0.15, 1.0)
    usable = [candidate for candidate in candidates if candidate <= target]
    return usable[-1] if usable else candidates[0]


def add_text(msp, text, insert, height, layer_name):
    """Add single-line text with a conservative ezdxf attribute set."""

    msp.add_text(
        text,
        dxfattribs={
            "insert": insert,
            "height": height,
            "layer": layer_name,
        },
    )


def add_scale_annotations(
    msp,
    image_width,
    image_height,
    meters_per_pixel,
    scale_bar_length_m=None,
    approximate_scale_denominator=None,
):
    """Add a simple scale bar and approximate-scale notes to modelspace."""

    image_width_m = float(image_width) * float(meters_per_pixel)
    image_height_m = float(image_height) * float(meters_per_pixel)
    if image_width_m <= 0 or image_height_m <= 0:
        return

    bar_length = float(scale_bar_length_m or choose_scale_bar_length_m(image_width_m))
    margin_x = max(image_width_m * 0.035, 2.0)
    margin_y = max(image_height_m * 0.035, 2.0)
    tick_height = max(min(bar_length * 0.16, 3.0), 0.8)
    text_height = max(min(image_width_m * 0.012, 3.0), 0.8)

    x0 = margin_x
    y0 = margin_y
    x1 = x0 + bar_length

    msp.add_line(
        (x0, y0),
        (x1, y0),
        dxfattribs={"layer": SCALE_BAR_LAYER["name"]},
    )
    msp.add_line(
        (x0, y0 - (tick_height / 2)),
        (x0, y0 + (tick_height / 2)),
        dxfattribs={"layer": SCALE_BAR_LAYER["name"]},
    )
    msp.add_line(
        (x1, y0 - (tick_height / 2)),
        (x1, y0 + (tick_height / 2)),
        dxfattribs={"layer": SCALE_BAR_LAYER["name"]},
    )

    add_text(
        msp,
        f"{bar_length:g}m",
        (x0, y0 + tick_height),
        text_height,
        SCALE_NOTE_LAYER["name"],
    )

    note_y = y0 + tick_height + (text_height * 1.8)
    if approximate_scale_denominator:
        add_text(
            msp,
            f"概算縮尺 1/{int(approximate_scale_denominator)}",
            (x0, note_y),
            text_height,
            SCALE_NOTE_LAYER["name"],
        )
        note_y += text_height * 1.6

    add_text(
        msp,
        "参考図・正式測量成果ではありません",
        (x0, note_y),
        text_height,
        SCALE_NOTE_LAYER["name"],
    )


def export_dxf(
    lines,
    background_image_path,
    image_width,
    image_height,
    meters_per_pixel,
    output_dir=".",
    background_image_reference_path=None,
    include_scale_annotations=False,
    scale_bar_length_m=None,
    approximate_scale_denominator=None,
):
    """Export the current drawing to a DXF file and return the filename."""

    doc = ezdxf.new("R2010")
    doc.units = ezdxf.units.M

    msp = doc.modelspace()
    create_layers(doc)
    add_background_image(
        doc,
        msp,
        background_image_path,
        background_image_reference_path or background_image_path,
        image_width,
        image_height,
        meters_per_pixel,
    )
    add_lines(msp, lines, image_height, meters_per_pixel)
    if include_scale_annotations:
        add_scale_annotations(
            msp,
            image_width,
            image_height,
            meters_per_pixel,
            scale_bar_length_m=scale_bar_length_m,
            approximate_scale_denominator=approximate_scale_denominator,
        )

    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, build_output_filename())
    doc.saveas(filename)

    return filename
