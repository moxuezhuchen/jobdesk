"""Result file pattern suggestion based on command template."""

from __future__ import annotations


def suggest_result_file_patterns(command_template: str = "") -> list[str]:
    """Suggest download patterns based on the command used."""
    cmd = command_template.lower().strip().split()[0] if command_template.strip() else ""
    if cmd in ("g16", "g09", "gaussian"):
        return ["{stem}.log", "*.log", ".jobdesk_submit.log"]
    if cmd == "orca":
        return ["{stem}.out", "*.out", "*.log", ".jobdesk_submit.log"]
    if cmd == "bash":
        return ["{stem}.log", "*.log", "*.out", ".jobdesk_submit.log"]
    return ["{stem}.log", "output.log", "result.log", "*.log", ".jobdesk_submit.log"]


def format_result_patterns(patterns: list[str]) -> str:
    """Format patterns for display in a text field."""
    return ", ".join(patterns)


def render_result_pattern(pattern: str, stem: str) -> str:
    """Replace {stem} placeholder with actual file stem."""
    return pattern.replace("{stem}", stem)


def is_control_output_pattern(pattern: str) -> bool:
    """Check if pattern is a jobdesk control file."""
    return pattern.startswith(".jobdesk_")
