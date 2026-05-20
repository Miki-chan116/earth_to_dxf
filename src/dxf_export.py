"""DXF export helpers for earth_to_dxf."""

from datetime import datetime
import os

import ezdxf

from src.constants import BACKGROUND_LAYER, LAYERS, OUTPUT_PREFIX


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


def export_dxf(
    lines,
    background_image_path,
    image_width,
    image_height,
    meters_per_pixel,
    output_dir=".",
    background_image_reference_path=None,
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

    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, build_output_filename())
    doc.saveas(filename)

    return filename
