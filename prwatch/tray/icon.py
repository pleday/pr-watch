"""The picture of the status icon: the PR Watch logo with a small disc of the state's colour in the bottom right corner.

Drawn here, in plain Python, from the same shapes as `prwatch/web/static/icon.svg`, because the StatusNotifierItem protocol
wants raw pixels and the standard library has no SVG renderer. Shapes are in the 64 x 64 coordinates of the logo.
"""
import functools
import math

BLUE = (31, 111, 235)
WHITE = (255, 255, 255)
STROKE = 2.0  # half of the width of the strokes of the logo
RINGS = ((20, 18), (20, 46), (46, 46))  # centres of the three nodes, radius 6 (stroke 4)
SEGMENTS = (((20, 24), (20, 40)), ((46, 40), (46, 26)), ((38, 18), (32, 18)), ((36, 11), (30, 18)), ((30, 18), (36, 25)))
ARC = ((38, 26), 8)  # the corner of the branch line: a quarter of circle, upper right
BADGE = ((52, 52), 9.5, 12.0)  # centre, radius of the colour, radius with its white rim


def _segment_distance(x, y, a, b):
    (ax, ay), (bx, by) = a, b
    length2 = (bx - ax) ** 2 + (by - ay) ** 2
    t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / length2))
    return math.hypot(x - (ax + t * (bx - ax)), y - (ay + t * (by - ay)))


def _arc_distance(x, y):
    (cx, cy), radius = ARC
    dx, dy = x - cx, y - cy
    if dx >= 0 and dy <= 0:
        return abs(math.hypot(dx, dy) - radius)
    return min(math.hypot(x - cx - radius, y - cy), math.hypot(x - cx, y - cy + radius))


def _in_logo_glyph(x, y):
    if any(abs(math.hypot(x - cx, y - cy) - 6) <= STROKE for cx, cy in RINGS):
        return True
    return _arc_distance(x, y) <= STROKE or any(_segment_distance(x, y, a, b) <= STROKE for a, b in SEGMENTS)


def _in_rounded_square(x, y, radius=14):
    cx, cy = min(max(x, radius), 64 - radius), min(max(y, radius), 64 - radius)
    return 0 <= x <= 64 and 0 <= y <= 64 and math.hypot(x - cx, y - cy) <= radius


def _sample(x, y, badge):
    """The colour at one point of the logo, None where it is transparent."""
    if badge:
        (cx, cy), inner, outer = BADGE
        distance = math.hypot(x - cx, y - cy)
        if distance <= inner:
            return badge
        if distance <= outer:
            return WHITE
    if not _in_rounded_square(x, y):
        return None
    return WHITE if _in_logo_glyph(x, y) else BLUE


@functools.lru_cache(maxsize=32)
def pixmap(size, badge=None, samples=3):
    """(width, height, ARGB32 bytes) of the icon; `badge` is the (r, g, b) of the disc, or None for the logo alone.

    Every pixel is averaged over samples x samples points, which gives smooth edges.
    """
    scale = 64 / size
    data = bytearray()
    for row in range(size):
        for col in range(size):
            red = green = blue = covered = 0
            for sub_y in range(samples):
                for sub_x in range(samples):
                    colour = _sample((col + (sub_x + 0.5) / samples) * scale, (row + (sub_y + 0.5) / samples) * scale, badge)
                    if colour:
                        covered += 1
                        red, green, blue = red + colour[0], green + colour[1], blue + colour[2]
            if covered:
                data += bytes((round(255 * covered / samples**2), red // covered, green // covered, blue // covered))
            else:
                data += bytes(4)
    return (size, size, bytes(data))
