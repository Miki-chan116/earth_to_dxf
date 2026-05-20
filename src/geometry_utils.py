"""Geometry helpers for approximate CAD measurements."""

import math


def pixel_to_meter(pixel_value, meters_per_pixel):
    """Convert pixels to meters."""

    return float(pixel_value) * float(meters_per_pixel)


def meter_to_pixel(meter_value, meters_per_pixel):
    """Convert meters to pixels."""

    meters_per_pixel = float(meters_per_pixel)
    if meters_per_pixel <= 0:
        raise ValueError("meters_per_pixel must be greater than 0")
    return float(meter_value) / meters_per_pixel


def calculate_distance_m(point1, point2, meters_per_pixel):
    """Return the distance between two pixel points in meters."""

    dx = float(point2[0]) - float(point1[0])
    dy = float(point2[1]) - float(point1[1])
    distance_px = math.sqrt((dx * dx) + (dy * dy))
    return pixel_to_meter(distance_px, meters_per_pixel)


def calculate_polyline_length_m(points, meters_per_pixel, closed=False):
    """Return total polyline length in meters."""

    if not isinstance(points, (list, tuple)) or len(points) < 2:
        return 0.0

    total = 0.0
    for start, end in zip(points, points[1:]):
        total += calculate_distance_m(start, end, meters_per_pixel)

    if closed and len(points) >= 3:
        total += calculate_distance_m(points[-1], points[0], meters_per_pixel)

    return total


def calculate_polygon_area_m2(points, meters_per_pixel):
    """Return polygon area in square meters using the shoelace formula."""

    if not isinstance(points, (list, tuple)) or len(points) < 3:
        return 0.0

    point_list = list(points)
    area_px2 = 0.0
    for (x1, y1), (x2, y2) in zip(point_list, point_list[1:] + point_list[:1]):
        area_px2 += (float(x1) * float(y2)) - (float(x2) * float(y1))

    area_px2 = abs(area_px2) / 2.0
    meters_per_pixel = float(meters_per_pixel)
    return area_px2 * meters_per_pixel * meters_per_pixel
