"""Scale and print-size helper functions."""


def print_scale_denominator(
    image_width_px,
    image_height_px,
    meters_per_pixel,
    paper_width_mm,
    paper_height_mm,
    margin_mm,
):
    """Return the denominator needed to fit the whole image on paper."""

    printable_width_mm = paper_width_mm - margin_mm * 2
    printable_height_mm = paper_height_mm - margin_mm * 2

    if printable_width_mm <= 0 or printable_height_mm <= 0:
        return None

    real_width_mm = image_width_px * meters_per_pixel * 1000
    real_height_mm = image_height_px * meters_per_pixel * 1000

    width_scale = real_width_mm / printable_width_mm
    height_scale = real_height_mm / printable_height_mm

    return max(width_scale, height_scale)


def printed_image_size_mm(image_width_px, image_height_px, meters_per_pixel, scale_denominator):
    """Return image size on paper at the given scale denominator."""

    if scale_denominator <= 0:
        return None

    real_width_mm = image_width_px * meters_per_pixel * 1000
    real_height_mm = image_height_px * meters_per_pixel * 1000

    return (
        real_width_mm / scale_denominator,
        real_height_mm / scale_denominator,
    )


def image_fits_paper_at_scale(
    image_width_px,
    image_height_px,
    meters_per_pixel,
    paper_width_mm,
    paper_height_mm,
    margin_mm,
    scale_denominator,
):
    """Return whether the whole image fits on paper at the given scale."""

    printed_size = printed_image_size_mm(
        image_width_px,
        image_height_px,
        meters_per_pixel,
        scale_denominator,
    )
    if printed_size is None:
        return False

    printed_width_mm, printed_height_mm = printed_size
    printable_width_mm = paper_width_mm - margin_mm * 2
    printable_height_mm = paper_height_mm - margin_mm * 2

    return (
        printed_width_mm <= printable_width_mm
        and printed_height_mm <= printable_height_mm
    )


def nearest_standard_scale(denominator, standard_scales):
    """Return the standard scale denominator closest to denominator."""

    if denominator is None:
        return None

    return min(
        standard_scales,
        key=lambda standard: abs(standard - denominator),
    )


def standard_scale_pair(denominator, standard_scales):
    """Return standard scale denominators around denominator."""

    if denominator is None:
        return None, None

    smaller = None
    larger = None

    for standard in standard_scales:
        if standard <= denominator:
            smaller = standard
        if standard >= denominator and larger is None:
            larger = standard

    return smaller, larger


def next_fitting_standard_scale(
    image_width_px,
    image_height_px,
    meters_per_pixel,
    paper_width_mm,
    paper_height_mm,
    margin_mm,
    standard_scales,
):
    """Return the smallest standard scale denominator that fits the image."""

    for standard in standard_scales:
        if image_fits_paper_at_scale(
            image_width_px,
            image_height_px,
            meters_per_pixel,
            paper_width_mm,
            paper_height_mm,
            margin_mm,
            standard,
        ):
            return standard

    return None

