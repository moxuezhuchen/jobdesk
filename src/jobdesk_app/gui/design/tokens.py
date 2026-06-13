"""Design tokens — single source of truth for all visual parameters."""

from __future__ import annotations


class Colors:
    # Surfaces
    BG_BASE = "#eef3f8"
    BG_SURFACE = "#f7f9fc"

    # Sidebar
    SIDEBAR_BG = "#243244"
    SIDEBAR_TEXT = "#b7c3d1"
    SIDEBAR_TEXT_ACTIVE = "#ffffff"
    SIDEBAR_INDICATOR = "#6f91b7"
    SIDEBAR_HOVER = "#34465d"

    # Primary
    PRIMARY = "#315f95"
    PRIMARY_HOVER = "#e8eef5"
    PRIMARY_PRESSED = "#d3dce7"
    PRIMARY_TEXT = "#111827"

    # Semantic
    SUCCESS = "#2f6f3e"
    SUCCESS_BG = "#edf5ee"
    WARNING = "#7a5d1a"
    WARNING_BG = "#faf2d8"
    ERROR = "#9b2b2b"
    ERROR_BG = "#f6eeee"
    INFO = "#315f95"
    INFO_BG = "#e7eef6"

    # Text
    TEXT = "#111827"
    TEXT_SECONDARY = "#2f3b49"
    TEXT_MUTED = "#758293"

    # Borders
    BORDER = "#9aaec4"
    BORDER_SUBTLE = "#d7e0ea"
    BORDER_FOCUS = "#5c7fa6"

    # Table
    TABLE_HEADER_BG = "#dfe7f0"
    TABLE_ALT_ROW = "#eef3f8"
    TABLE_SELECTION = "#cfe0f4"


class Spacing:
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24
    XXL = 32


class Radius:
    SM = 2
    MD = 3
    LG = 4


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
    CONTROL_HEIGHT = 38
    TABLE_ROW_HEIGHT = 32
    TABLE_HEADER_HEIGHT = 36
    PAGE_PADDING = 20
