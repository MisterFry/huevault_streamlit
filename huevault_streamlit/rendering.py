"""Rendering and formatting helpers for the Streamlit UI."""

from __future__ import annotations

import json
from typing import Any


_D65_WHITE = (95.047, 100.0, 108.883)


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def lab_to_xyz(lab: tuple[float, float, float]) -> tuple[float, float, float]:
    l_value, a_value, b_value = lab
    fy = (l_value + 16.0) / 116.0
    fx = fy + (a_value / 500.0)
    fz = fy - (b_value / 200.0)

    def invert(component: float) -> float:
        cubic = component**3
        if cubic > 216 / 24389:
            return cubic
        return (116 * component - 16) / (24389 / 27)

    return (
        _D65_WHITE[0] * invert(fx),
        _D65_WHITE[1] * invert(fy),
        _D65_WHITE[2] * invert(fz),
    )


def xyz_to_rgb(xyz: tuple[float, float, float]) -> tuple[int, int, int]:
    x_value = xyz[0] / 100.0
    y_value = xyz[1] / 100.0
    z_value = xyz[2] / 100.0

    red = x_value * 3.2404542 + y_value * -1.5371385 + z_value * -0.4985314
    green = x_value * -0.9692660 + y_value * 1.8760108 + z_value * 0.0415560
    blue = x_value * 0.0556434 + y_value * -0.2040259 + z_value * 1.0572252

    def delinearize(component: float) -> float:
        if component <= 0.0031308:
            return 12.92 * component
        return 1.055 * (component ** (1.0 / 2.4)) - 0.055

    return tuple(
        int(round(clamp(delinearize(component), 0.0, 1.0) * 255.0))
        for component in (red, green, blue)
    )


def lab_to_hex(lab: tuple[float, float, float]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*xyz_to_rgb(lab_to_xyz(lab)))


def chip_html(hex_value: str, label: str = "", height: int = 56) -> str:
    safe_label = label.replace("<", "&lt;").replace(">", "&gt;")
    return (
        f"<div style='display:flex;align-items:center;gap:0.9rem;padding:0.55rem 0.65rem;"
        f"background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.10);"
        f"border-radius:1rem;box-shadow:0 8px 20px rgba(0,0,0,0.18);'>"
        f"<div style='width:100%;max-width:6rem;height:{height}px;border-radius:0.95rem;"
        f"border:1px solid rgba(255,255,255,0.10);background:{hex_value};"
        f"box-shadow:inset 0 0 0 1px rgba(255,255,255,0.18), 0 10px 24px rgba(0,0,0,0.18);'></div>"
        f"<div style='font-size:0.9rem;line-height:1.35;color:#b9b1a8;'>"
        f"<strong style='display:block;color:#f5f1eb;font-weight:650;'>{safe_label}</strong>"
        f"<code style='color:#ffb5c0;background:rgba(255,255,255,0.06);padding:0.1rem 0.35rem;border-radius:0.35rem;'>{hex_value}</code>"
        f"</div>"
        f"</div>"
    )


def hue_difference(left: float, right: float) -> float:
    distance = abs((left - right) % 360.0)
    return min(distance, 360.0 - distance)


def format_triplet(values: tuple[float, float, float], digits: int = 3) -> str:
    return ", ".join(f"{value:.{digits}f}" for value in values)


def provenance_summary(value: Any) -> str:
    if not value:
        return "None"
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
