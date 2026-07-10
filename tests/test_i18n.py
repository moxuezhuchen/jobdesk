"""I18n coverage tests for the jobdesk GUI."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jobdesk_app.gui import i18n
from jobdesk_app.gui.i18n import tr
from jobdesk_app.gui.pages.file_transfer_helpers import (
    connection_status_text,
    format_selection_summary,
)


def test_i18n_source_uses_ascii_escapes_for_chinese_labels():
    source = Path("src/jobdesk_app/gui/i18n.py").read_text(encoding="utf-8")
    assert source.isascii()


def test_translate_core_gui_labels_to_chinese():
    assert tr("Files", "zh") == "\u6587\u4ef6"
    assert tr("Runs", "zh") == "\u8fd0\u884c"
    assert tr("Settings", "zh") == "\u8bbe\u7f6e"
    assert tr("Local Folder", "zh") == "\u672c\u5730\u76ee\u5f55"
    assert tr("Run Selected", "zh") == "\u8fd0\u884c\u6240\u9009"


def test_translate_defaults_to_english():
    assert tr("Files", "en") == "Files"
    assert tr("Unknown Label", "zh") == "Unknown Label"


def test_translate_projects_label():
    assert tr("Projects", "en") == "Projects"
    assert tr("Projects", "zh") == "\u9879\u76ee"


def test_gui_status_helpers_translate_to_chinese():
    assert connection_status_text(None, False, language="zh") == "\u672a\u9009\u62e9\u670d\u52a1\u5668"
    assert connection_status_text("demo-server", True, language="zh") == "\u5df2\u8fde\u63a5: demo-server"
    assert format_selection_summary(1, 2, "zh") == "\u672c\u5730 1 | \u8fdc\u7a0b 2"


def test_button_feedback_labels_translate_to_chinese():
    for label in [
        "Refreshing...",
        "Refreshed",
        "Refresh failed",
        "Opened",
        "Open failed",
        "Submit failed",
        "Create failed",
        "Creating...",
        "Created {n}",
        "Submitted {n}",
    ]:
        assert tr(label, "zh", n=2) != label.format(n=2)


# -- Phase 10.3 polish-pass coverage ----------------------------------


def test_zh_dict_has_no_empty_entries():
    """Every entry in :data:`i18n.ZH` must have a non-empty translation."""
    empty = [k for k, v in i18n.ZH.items() if not v]
    assert empty == [], f"Empty ZH translations for keys: {empty!r}"


def _tr_call_keys() -> set[str]:
    """Collect every literal first-argument to ``tr()`` under ``src/jobdesk_app/gui``.

    Mirrors the audit script in ``tmp_i18n_audit.py``; kept inline so the
    test doesn't require a separate script to be present.
    """
    def literal_concat(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = literal_concat(node.left)
            right = literal_concat(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    used: set[str] = set()
    for p in Path("src/jobdesk_app/gui").rglob("*.py"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr":
                if not node.args:
                    continue
                val = literal_concat(node.args[0])
                if val is not None:
                    used.add(val)
    return used


def test_zh_dict_covers_every_tr_call_key():
    """Every :func:`tr` call site must have a ZH mapping."""
    used = _tr_call_keys()
    missing = sorted(used - set(i18n.ZH.keys()))
    assert missing == [], f"tr() call keys missing from ZH dict: {missing!r}"


# ── IMP-11 i18n parity pass ─────────────────────────────────────────────────

# Some English tokens are intentionally passthrough because they are
# proper nouns (protocol directives, software brand names). The
# parity test below skips these on purpose; if you add a new entry
# here, leave a one-line comment explaining why it stays English-only.
_PASSTHROUGH_WHITELIST: frozenset[str] = frozenset({
    # The English word "English" in a language picker stays English.
    "English",
    # SSH config directives; the convention is to keep them verbatim.
    "ProxyCommand:",
    "ProxyJump:",
    # Brand-name file-type tags.
    "Gaussian (.gjf)",
    "ORCA (.inp)",
})


def test_i18n_all_zh_keys_have_translations():
    """Walk every ``tr(...)`` call site and assert the ZH entry is
    a real translation (``ZH[text] != text``).

    The whitelist holds the few English-only tokens that exist for
    branding or protocol reasons. Anything else that ships through
    ``tr()`` must have an actual Chinese counterpart in
    :data:`jobdesk_app.gui.i18n.ZH`.
    """
    used = _tr_call_keys()
    passthrough = sorted(used & _PASSTHROUGH_WHITELIST)
    # Items NOT in the ZH dict are caught by the prior test; this one
    # focuses on "key exists but the dict value is a passthrough".
    in_zh = [k for k in used if k in i18n.ZH]
    passthrough_leaks = sorted(
        k for k in in_zh if k not in _PASSTHROUGH_WHITELIST and i18n.ZH[k] == k
    )
    assert passthrough_leaks == [], (
        "tr() keys whose ZH value is still English-only "
        f"(add a real translation or whitelist with a comment): "
        f"{passthrough_leaks!r}"
    )
    assert passthrough == sorted(passthrough), (
        "whitelist drift: " f"{passthrough!r}"
    )


# -- Phase 10.3 added keys ---------------------------------------------


@pytest.mark.parametrize(
    ("english", "chinese"),
    [
        ("Connect to a server first.", "\u8bf7\u5148\u8fde\u63a5\u670d\u52a1\u5668"),
        ("Delete run {run_id} record?", "\u5220\u9664\u8fd0\u884c {run_id} \u7684\u8bb0\u5f55\uff1f"),
        ("Delete {n} run records?", "\u5220\u9664 {n} \u6761\u8fd0\u884c\u8bb0\u5f55\uff1f"),
        ("Download done: {n} files, failed: {f}", "\u4e0b\u8f7d\u5b8c\u6210: \u6210\u529f {n} \u4e2a\uff0c\u5931\u8d25 {f} \u4e2a"),
        ("No tasks awaiting download", "\u6ca1\u6709\u53ef\u4e0b\u8f7d\u7684\u4efb\u52a1"),
        ("Open Results", "\u6253\u5f00\u7ed3\u679c"),
        ("Operation recovery failed", "\u542f\u52a8\u6062\u590d\u5931\u8d25"),
        ("Output file not found", "\u627e\u4e0d\u5230\u8f93\u51fa\u6587\u4ef6"),
        ("Parse error", "\u89e3\u6790\u9519\u8bef"),
        ("Preview failed: {e}", "\u9884\u89c8\u5931\u8d25: {e}"),
        ("Refresh failed: {e}", "\u5237\u65b0\u5931\u8d25: {e}"),
        ("Remote operation already in progress", "\u8fdc\u7a0b\u64cd\u4f5c\u8fdb\u884c\u4e2d"),
        ("Result Preview - Auto Analysis", "\u7ed3\u679c\u9884\u89c8 - \u81ea\u52a8\u5206\u6790"),
        ("Result Preview - Local Files", "\u7ed3\u679c\u9884\u89c8 - \u672c\u5730\u6587\u4ef6"),
        ("Results directory not found", "\u627e\u4e0d\u5230\u7ed3\u679c\u76ee\u5f55"),
        ("Select SSH Key", "\u9009\u62e9 SSH \u5bc6\u94a5"),
        ("Select a task to see details", "\u8bf7\u9009\u62e9\u4e00\u4e2a\u4efb\u52a1\u67e5\u770b\u8be6\u60c5"),
        ("Submitting", "\u63d0\u4ea4\u4e2d"),
        ("Test failed", "\u6d4b\u8bd5\u5931\u8d25"),
    ],
)
def test_phase10_3_zh_translations(english, chinese):
    assert tr(english, "zh") == chinese


@pytest.mark.parametrize(
    "english",
    [
        "Automatic refresh failed: {e}",
        "Automatic refresh failed: {errors}",
        "Run complete; results downloaded: {run_id}",
        "Run complete; results downloaded: {ids}",
        "Operation recovery failed: {error}",
        "Delete Run invoked from context menu",
    ],
)
def test_phase10_polish_runs_results_status_translates(english):
    """Phase 10 polish: every newly-wrapped status message in
    ``RunsResultsPage`` has a ZH mapping that contains at least one
    Chinese character (and is not equal to the English source)."""
    kwargs = {}
    if "{e}" in english:
        kwargs["e"] = "boom"
    elif "{errors}" in english:
        kwargs["errors"] = "boom; boom2"
    elif "{run_id}" in english:
        kwargs["run_id"] = "demo-001"
    elif "{ids}" in english:
        kwargs["ids"] = "a, b"
    elif "{error}" in english:
        kwargs["error"] = "boom"
    translated = tr(english, "zh", **kwargs)
    assert translated != english
    assert _has_chinese(translated)


def test_submit_page_validation_message_translates():
    """The Validation log entry used by ``SubmitPage._on_submit_clicked``."""
    rendered = tr("Validation [{code}]: {message}", "zh", code="graph", message="oops")
    assert rendered != "Validation [graph]: oops"
    assert "{code}" not in rendered and "{message}" not in rendered


def test_submit_page_preview_message_translates():
    """The preview-pane fallback messages."""
    rendered_graph = tr("Graph incomplete: {exc}", "zh", exc="x")
    assert rendered_graph != "Graph incomplete: x"
    assert "{exc}" not in rendered_graph

    rendered_preview = tr("Preview failed: {exc}", "zh", exc="x")
    assert rendered_preview != "Preview failed: x"
    assert "{exc}" not in rendered_preview


# -- Phase 10.3 node-library palette tooltip coverage ------------------


def _has_chinese(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


@pytest.mark.parametrize(
    "english",
    [
        "XYZ file",
        "Conformer generation",
        "Pre-optimization",
        "Geometry optimization",
        "Single point",
        "Frequency",
        "Transition state",
        "Refine",
        "Advanced options",
        "Output",
    ],
)
def test_node_library_titles_translate_to_zh(english):
    """Phase 10.3: every node-library palette title has a ZH translation."""
    translated = tr(english, "zh")
    assert translated != english
    assert _has_chinese(translated)


@pytest.mark.parametrize(
    ("english", "fragment"),
    [
        ("Input XYZ geometry", "\u51e0\u4f55"),
        ("Generate a conformational ensemble", "\u6784\u8c61"),
        ("Cheap pre-optimization (force field)", "\u9884\u4f18\u5316"),
        ("DFT / ab-initio geometry optimization", "\u51e0\u4f55\u4f18\u5316"),
        ("Single-point energy", "\u5355\u70b9"),
        ("Vibrational frequency", "\u9891\u7387"),
        ("Transition state search", "\u8fc7\u6e21\u6001"),
        ("Refine best conformer with high accuracy", "\u7cbe\u70bc"),
        ("Free-form key=value options", "\u9009\u9879"),
        ("Workflow terminator (emits workflow.yaml)", "\u7ec8\u6b62\u8282\u70b9"),
    ],
)
def test_node_library_tooltips_translate_to_zh(english, fragment):
    """Phase 10.3: every node-library palette tooltip has a ZH translation."""
    translated = tr(english, "zh")
    assert translated != english
    assert _has_chinese(translated)
    assert fragment in translated