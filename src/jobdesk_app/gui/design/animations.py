"""Animation helpers for page transitions."""

from __future__ import annotations

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from .tokens import Animation


def fade_in(widget: QWidget, duration: int = Animation.NORMAL) -> QPropertyAnimation:
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    effect.setOpacity(0.0)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.start(QAbstractAnimation.DeleteWhenStopped)
    return anim


def fade_switch(stack, index: int, duration: int = Animation.FAST) -> None:
    """Switch QStackedWidget page with fade."""
    target = stack.widget(index)
    if target is None:
        return
    stack.setCurrentIndex(index)
    fade_in(target, duration)
