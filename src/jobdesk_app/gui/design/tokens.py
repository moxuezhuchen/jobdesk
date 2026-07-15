"""Design tokens — single source of truth for all visual parameters."""

from __future__ import annotations


class Colors:
    # ── Modern Surface System ─────────────────────────────────────────────
    BG_BASE = "#f0f2f5"
    BG_SURFACE = "#ffffff"

    # ── Gradient Accent (primary) ─────────────────────────────────────────
    PRIMARY = "#3b82f6"
    PRIMARY_HOVER = "#2563eb"
    PRIMARY_PRESSED = "#1d4ed8"
    PRIMARY_TEXT = "#ffffff"

    # ── Sidebar (dark slate with depth) ──────────────────────────────────
    SIDEBAR_BG = "#1e293b"
    SIDEBAR_BG_LIGHT = "#334155"
    SIDEBAR_TEXT = "#94a3b8"
    SIDEBAR_TEXT_ACTIVE = "#ffffff"
    SIDEBAR_INDICATOR = "#3b82f6"
    SIDEBAR_HOVER = "#334155"
    SIDEBAR_ACTIVE_BG = "#1e3a5f"

    # ── Semantic Colors ──────────────────────────────────────────────────
    SUCCESS = "#10b981"
    SUCCESS_BG = "#ecfdf5"
    SUCCESS_BORDER = "#6ee7b7"
    WARNING = "#f59e0b"
    WARNING_BG = "#fffbeb"
    WARNING_BORDER = "#fcd34d"
    ERROR = "#ef4444"
    ERROR_BG = "#fef2f2"
    ERROR_BORDER = "#fca5a5"
    INFO = "#3b82f6"
    INFO_BG = "#eff6ff"
    INFO_BORDER = "#93c5fd"

    # ── Text Hierarchy ───────────────────────────────────────────────────
    TEXT = "#1e293b"
    TEXT_SECONDARY = "#475569"
    TEXT_MUTED = "#94a3b8"

    # ── Borders & Dividers ───────────────────────────────────────────────
    BORDER = "#e2e8f0"
    BORDER_SUBTLE = "#f1f5f9"
    BORDER_FOCUS = "#3b82f6"

    # ── Cards & Surfaces ─────────────────────────────────────────────────
    CARD_BG = "#ffffff"
    CARD_HOVER = "#f8fafc"
    CARD_SHADOW = "rgba(15, 23, 42, 0.08)"

    # ── Table ─────────────────────────────────────────────────────────────
    TABLE_HEADER_BG = "#f8fafc"
    TABLE_ALT_ROW = "#fafbfc"
    TABLE_SELECTION = "#dbeafe"
    TABLE_HOVER = "#f1f5f9"


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
    XL = 16


class Shadow:
    """Qt stylesheets don't support box-shadow. These are kept for reference only."""
    SM = ""
    MD = ""
    LG = ""
    XL = ""


class Animation:
    FAST = 150
    NORMAL = 250
    SLOW = 400


class Metrics:
    SIDEBAR_WIDTH = 72
    SIDEBAR_ICON_SIZE = 26
    SIDEBAR_ITEM_HEIGHT = 56
    CONTROL_HEIGHT = 44
    TABLE_ROW_HEIGHT = 48
    TABLE_HEADER_HEIGHT = 52
    PAGE_PADDING = 24
