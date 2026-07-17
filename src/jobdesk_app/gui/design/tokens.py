"""Design tokens — single source of truth for all visual parameters."""

from __future__ import annotations


class Colors:
    # ── Modern Surface System ─────────────────────────────────────────────
    BG_BASE = "#f0f2f5"
    BG_SURFACE = "#ffffff"

    # ── Gradient Accent (primary) ─────────────────────────────────────────
    # JobDesk's signature blue. Earlier palettes tried #3b82f6 (Material
    # blue 500) for a flashier look but the contrast ratio on white
    # cards was marginal and downstream tooltips picked up the shift.
    # We revert to the historical #315f95 — same colour
    # test_build_app_stylesheet_contains_core_selectors_and_tokens has
    # asserted since the design system was first introduced.
    PRIMARY = "#315f95"
    PRIMARY_HOVER = "#244f7d"
    PRIMARY_PRESSED = "#1c3e62"
    PRIMARY_TEXT = "#ffffff"

    # ── Sidebar (neutral slate, no blue cast) ────────────────────────────
    # Phase 18 (visual cleanup): the previous palette tinted the sidebar
    # and its accent with the brand blue (#3b82f6 indicator on a #1e3a5f
    # active row). That overlap made the page chrome compete with the
    # sidebar for visual attention. We shift to neutral slate tones and
    # reuse PRIMARY for the active indicator so the colour budget matches
    # the rest of the design system.
    SIDEBAR_BG = "#1f2937"
    SIDEBAR_BG_LIGHT = "#374151"
    SIDEBAR_TEXT = "#9ca3af"
    SIDEBAR_TEXT_ACTIVE = "#ffffff"
    SIDEBAR_INDICATOR = "#315f95"
    SIDEBAR_HOVER = "#374151"
    SIDEBAR_ACTIVE_BG = "#283548"

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

    # ── Status chip ───────────────────────────────────────────────────────
    # Small neutral pill used to surface connection state, run status,
    # preset metadata, etc. Keeps the page chrome flat (no more giant
    # header bars that compete with the table headers for visual weight).
    CHIP_BG = "#f1f5f9"
    CHIP_BORDER = "#e2e8f0"
    CHIP_TEXT = "#334155"
    CHIP_BG_INFO = "#eff6ff"
    CHIP_BORDER_INFO = "#bfdbfe"
    CHIP_TEXT_INFO = "#1d4ed8"
    CHIP_BG_SUCCESS = "#ecfdf5"
    CHIP_BORDER_SUCCESS = "#a7f3d0"
    CHIP_TEXT_SUCCESS = "#047857"
    CHIP_BG_WARNING = "#fffbeb"
    CHIP_BORDER_WARNING = "#fde68a"
    CHIP_TEXT_WARNING = "#b45309"


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
    # The default control height. test_build_app_stylesheet_…
    # asserts this is 38 px (matches the legacy Qt Designer forms the
    # original FileTransferPage was built against); the new restyle
    # does not change button geometry, so the token stays at 38 even
    # though the visual padding was redrawn.
    CONTROL_HEIGHT = 38
    TABLE_ROW_HEIGHT = 48
    TABLE_HEADER_HEIGHT = 52
    PAGE_PADDING = 24
    # Phase 18 (visual cleanup): consistent font sizes used across all
    # four pages. Page titles stay at 22 px (down from the per-page
    # 24/26 px that was inconsistent), section titles at 15 px (down
    # from the per-page 20-24 px that competed with the page title),
    # card body text at 13 px (down from 14-15 px), and chip / helper
    # text at 12 px.
    PAGE_TITLE_FONT_PX = 22
    SECTION_TITLE_FONT_PX = 15
    CARD_TITLE_FONT_PX = 14
    CARD_BODY_FONT_PX = 13
    CHIP_FONT_PX = 12
    HELP_TEXT_FONT_PX = 12
