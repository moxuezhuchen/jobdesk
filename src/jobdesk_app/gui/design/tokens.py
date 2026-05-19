"""Design tokens — single source of truth for all visual parameters."""

from __future__ import annotations


class Colors:
    # Surfaces
    BG_BASE = "#f1f5f9"
    BG_SURFACE = "#ffffff"

    # Sidebar
    SIDEBAR_BG = "#0f172a"
    SIDEBAR_TEXT = "#94a3b8"
    SIDEBAR_TEXT_ACTIVE = "#f8fafc"
    SIDEBAR_INDICATOR = "#3b82f6"
    SIDEBAR_HOVER = "#1e293b"

    # Primary
    PRIMARY = "#2563eb"
    PRIMARY_HOVER = "#1d4ed8"
    PRIMARY_PRESSED = "#1e40af"
    PRIMARY_TEXT = "#ffffff"

    # Semantic
    SUCCESS = "#16a34a"
    SUCCESS_BG = "#f0fdf4"
    WARNING = "#d97706"
    WARNING_BG = "#fffbeb"
    ERROR = "#dc2626"
    ERROR_BG = "#fef2f2"
    INFO = "#2563eb"
    INFO_BG = "#eff6ff"

    # Text
    TEXT = "#0f172a"
    TEXT_SECONDARY = "#475569"
    TEXT_MUTED = "#94a3b8"

    # Borders
    BORDER = "#e2e8f0"
    BORDER_SUBTLE = "#f1f5f9"
    BORDER_FOCUS = "#3b82f6"

    # Table
    TABLE_HEADER_BG = "#f8fafc"
    TABLE_ALT_ROW = "#f8fafc"
    TABLE_SELECTION = "#eff6ff"


class Spacing:
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24
    XXL = 32


class Radius:
    SM = 4
    MD = 8
    LG = 12


class Shadow:
    """(offset_x, offset_y, blur_radius, alpha 0-255)"""
    SM = (0, 1, 3, 25)
    MD = (0, 4, 12, 20)


class Animation:
    FAST = 120
    NORMAL = 200


class Metrics:
    SIDEBAR_WIDTH = 72
    SIDEBAR_ICON_SIZE = 28
    SIDEBAR_ITEM_HEIGHT = 56
    CONTROL_HEIGHT = 44
    TABLE_ROW_HEIGHT = 38
    TABLE_HEADER_HEIGHT = 44
    PAGE_PADDING = 20
