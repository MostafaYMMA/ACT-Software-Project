"""
Animated stat-card dashboard (PySide6).

A self-contained demo: a grid of custom StatCard widgets with a fluid
hover glow (soft indigo in Light Mode, neon cyan/teal in Dark Mode) and
an elastic "squish & flash" animation on click, plus a toggle at the top
that swaps both the base stylesheet and the glow colors live.

Run:
    pip install PySide6
    python animated_stat_cards.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import (
    Qt, QRect, QRectF, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, Property,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication, QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGraphicsDropShadowEffect, QSizePolicy,
)


# ---------------------------------------------------------------------------
# Theme palettes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Theme:
    name: str
    window_bg: str
    card_bg: str
    card_border_resting: str
    title_color: str
    value_color: str
    positive_color: str
    negative_color: str
    hover_border: QColor
    hover_glow_color: QColor
    hover_glow_blur: float
    click_glow_color: QColor
    click_glow_blur: float


LIGHT = Theme(
    name="light",
    window_bg="#F4F5F7",
    card_bg="#FFFFFF",
    card_border_resting="#E4E6EB",
    title_color="#6B7280",
    value_color="#111827",
    positive_color="#16A34A",
    negative_color="#DC2626",
    hover_border=QColor("#6366F1"),          # soft, elegant indigo
    hover_glow_color=QColor(99, 102, 241, 90),
    hover_glow_blur=28.0,
    click_glow_color=QColor(99, 102, 241, 190),
    click_glow_blur=55.0,
)

DARK = Theme(
    name="dark",
    window_bg="#121318",
    card_bg="#1E2027",
    card_border_resting="#2B2E38",
    title_color="#9CA3AF",
    value_color="#F3F4F6",
    positive_color="#34D399",
    negative_color="#F87171",
    hover_border=QColor("#22D3EE"),           # vibrant neon cyan/teal
    hover_glow_color=QColor(34, 211, 238, 130),
    hover_glow_blur=42.0,
    click_glow_color=QColor(45, 226, 230, 235),
    click_glow_blur=75.0,
)


# ---------------------------------------------------------------------------
# StatCard
# ---------------------------------------------------------------------------

class StatCard(QFrame):
    """A single metric card: title, big value, +/- percentage change.
    Animated hover glow + an elastic click 'squish and flash'."""

    HOVER_ANIM_MS = 220
    SQUISH_DOWN_MS = 120
    SQUISH_UP_MS = 550
    SQUISH_WIDTH_GROW = 10    # px added to width while squished
    SQUISH_HEIGHT_SHRINK = 8  # px removed from height while squished

    def __init__(self, title: str, value: str, change: float, theme: Theme, parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self._title_text = title
        self._value_text = value
        self._change = change
        self._theme = theme

        self._base_geometry: QRect | None = None
        self._current_width = 0.0
        self._current_height = 0.0
        self._is_hovering = False
        self._is_pressed = False
        self._border_color = QColor(theme.card_border_resting)

        self.setFixedHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._build_ui()
        self._build_glow_effect()
        self.apply_theme(theme, animate=False)

    # -- UI ----------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(6)

        self.title_label = QLabel(self._title_text)
        self.title_label.setFont(QFont("Segoe UI", 10))
        layout.addWidget(self.title_label)

        self.value_label = QLabel(self._value_text)
        self.value_label.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
        layout.addWidget(self.value_label)

        change_row = QHBoxLayout()
        change_row.setSpacing(4)
        arrow = "\u25B2" if self._change >= 0 else "\u25BC"
        self.change_label = QLabel(f"{arrow} {abs(self._change):.1f}%")
        self.change_label.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        change_row.addWidget(self.change_label)
        change_row.addStretch()
        layout.addLayout(change_row)

    def _build_glow_effect(self):
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 0)
        self._glow.setBlurRadius(0)
        self._glow.setColor(QColor(0, 0, 0, 0))
        self.setGraphicsEffect(self._glow)

    # -- Animatable "borderColor" property, drives the stylesheet ----------
    def _get_border_color(self) -> QColor:
        return self._border_color

    def _set_border_color(self, color: QColor):
        self._border_color = color
        self._refresh_style()

    borderColor = Property(QColor, _get_border_color, _set_border_color)

    def _refresh_style(self):
        c = self._border_color
        self.setStyleSheet(f"""
            #statCard {{
                background-color: {self._theme.card_bg};
                border-radius: 14px;
                border: 1.5px solid rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()});
            }}
        """)
        self.title_label.setStyleSheet(f"color: {self._theme.title_color}; background: transparent;")
        self.value_label.setStyleSheet(f"color: {self._theme.value_color}; background: transparent;")
        change_color = self._theme.positive_color if self._change >= 0 else self._theme.negative_color
        self.change_label.setStyleSheet(f"color: {change_color}; background: transparent;")

    # -- Animatable width/height properties, used ONLY by the squish -------
    # (kept separate from Qt's built-in "geometry" property on purpose,
    # so the squish is two genuinely independent parallel animations -
    # one for width, one for height - rather than a single interpolated
    # QRect. Both write into the same _apply_squish_geometry() so the
    # card always stays centered on its original position while resizing.)
    def _get_sq_width(self) -> float:
        return self._current_width

    def _set_sq_width(self, value: float):
        self._current_width = value
        self._apply_squish_geometry()

    sqWidth = Property(float, _get_sq_width, _set_sq_width)

    def _get_sq_height(self) -> float:
        return self._current_height

    def _set_sq_height(self, value: float):
        self._current_height = value
        self._apply_squish_geometry()

    sqHeight = Property(float, _get_sq_height, _set_sq_height)

    def _apply_squish_geometry(self):
        base = self._base_geometry
        if base is None:
            return
        cx, cy = base.center().x(), base.center().y()
        w, h = self._current_width, self._current_height
        self.setGeometry(QRect(int(cx - w / 2), int(cy - h / 2), int(w), int(h)))

    # -- Theme swap ----------------------------------------------------------
    def apply_theme(self, theme: Theme, animate: bool = True):
        self._theme = theme
        target_border = theme.hover_border if self._is_hovering else QColor(theme.card_border_resting)

        if animate:
            self._animate_border_to(target_border, duration=260)
        else:
            self._set_border_color(target_border)

        if not self._is_pressed:
            target_blur = theme.hover_glow_blur if self._is_hovering else 0.0
            target_color = theme.hover_glow_color if self._is_hovering else QColor(0, 0, 0, 0)
            if animate:
                self._animate_glow_to(target_blur, target_color, duration=260)
            else:
                self._glow.setBlurRadius(target_blur)
                self._glow.setColor(target_color)

    # -- Hover -----------------------------------------------------------------
    def enterEvent(self, event):
        self._is_hovering = True
        self._animate_border_to(self._theme.hover_border, duration=self.HOVER_ANIM_MS)
        self._animate_glow_to(self._theme.hover_glow_blur, self._theme.hover_glow_color, duration=self.HOVER_ANIM_MS)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovering = False
        if not self._is_pressed:
            self._animate_border_to(QColor(self._theme.card_border_resting), duration=self.HOVER_ANIM_MS)
            self._animate_glow_to(0.0, QColor(0, 0, 0, 0), duration=self.HOVER_ANIM_MS)
        super().leaveEvent(event)

    # -- Click: instant flash + parallel squish, elastic release --------------
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        self._is_pressed = True
        self._ensure_base_geometry()

        # Instant spike - set directly (no animation) so it lands exactly
        # on the click, then the elastic release below eases it back down.
        self._glow.setBlurRadius(self._theme.click_glow_blur)
        self._glow.setColor(self._theme.click_glow_color)
        self._animate_border_to(self._theme.hover_border, duration=60)

        self._animate_squish(pressed=True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._is_pressed:
            self._is_pressed = False
            self._animate_squish(pressed=False)

            if self._is_hovering:
                self._animate_glow_to(self._theme.hover_glow_blur, self._theme.hover_glow_color, duration=self.SQUISH_UP_MS)
                self._animate_border_to(self._theme.hover_border, duration=self.SQUISH_UP_MS)
            else:
                self._animate_glow_to(0.0, QColor(0, 0, 0, 0), duration=self.SQUISH_UP_MS)
                self._animate_border_to(QColor(self._theme.card_border_resting), duration=self.SQUISH_UP_MS)
        super().mouseReleaseEvent(event)

    def _ensure_base_geometry(self):
        if self._base_geometry is None:
            self._base_geometry = QRect(self.geometry())
            self._current_width = float(self._base_geometry.width())
            self._current_height = float(self._base_geometry.height())

    def _animate_squish(self, pressed: bool):
        self._ensure_base_geometry()
        base = self._base_geometry

        if pressed:
            target_w = float(base.width() + self.SQUISH_WIDTH_GROW)
            target_h = float(base.height() - self.SQUISH_HEIGHT_SHRINK)
            duration = self.SQUISH_DOWN_MS
            curve = QEasingCurve.Type.OutQuad
        else:
            target_w = float(base.width())
            target_h = float(base.height())
            duration = self.SQUISH_UP_MS
            curve = QEasingCurve.Type.OutElastic

        group = QParallelAnimationGroup(self)

        width_anim = QPropertyAnimation(self, b"sqWidth", self)
        width_anim.setDuration(duration)
        width_anim.setStartValue(self._current_width)
        width_anim.setEndValue(target_w)
        width_anim.setEasingCurve(curve)
        group.addAnimation(width_anim)

        height_anim = QPropertyAnimation(self, b"sqHeight", self)
        height_anim.setDuration(duration)
        height_anim.setStartValue(self._current_height)
        height_anim.setEndValue(target_h)
        height_anim.setEasingCurve(curve)
        group.addAnimation(height_anim)

        group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)
        self._squish_group = group  # prevent garbage collection mid-animation

    # -- Small animation helpers -----------------------------------------------
    def _animate_border_to(self, color: QColor, duration: int):
        anim = QPropertyAnimation(self, b"borderColor", self)
        anim.setDuration(duration)
        anim.setStartValue(self._border_color)
        anim.setEndValue(color)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._border_anim = anim

    def _animate_glow_to(self, blur: float, color: QColor, duration: int):
        group = QParallelAnimationGroup(self)

        blur_anim = QPropertyAnimation(self._glow, b"blurRadius", self)
        blur_anim.setDuration(duration)
        blur_anim.setStartValue(self._glow.blurRadius())
        blur_anim.setEndValue(blur)
        blur_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(blur_anim)

        color_anim = QPropertyAnimation(self._glow, b"color", self)
        color_anim.setDuration(duration)
        color_anim.setStartValue(self._glow.color())
        color_anim.setEndValue(color)
        color_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(color_anim)

        group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)
        self._glow_anim = group  # prevent garbage collection mid-animation


# ---------------------------------------------------------------------------
# Light/Dark toggle switch
# ---------------------------------------------------------------------------

class ThemeToggle(QFrame):
    TRACK_W, TRACK_H = 56, 28
    HANDLE_MARGIN = 3

    def __init__(self, on_toggle, parent=None):
        super().__init__(parent)
        self._on_toggle = on_toggle
        self._is_dark = False
        self.setFixedSize(self.TRACK_W, self.TRACK_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._handle_x = float(self.HANDLE_MARGIN)

    def _get_handle_x(self) -> float:
        return self._handle_x

    def _set_handle_x(self, value: float):
        self._handle_x = value
        self.update()

    handleX = Property(float, _get_handle_x, _set_handle_x)

    def _handle_size(self) -> int:
        return self.TRACK_H - self.HANDLE_MARGIN * 2

    def set_dark(self, is_dark: bool, animate: bool = True):
        self._is_dark = is_dark
        target = float(self.TRACK_W - self._handle_size() - self.HANDLE_MARGIN) if is_dark \
            else float(self.HANDLE_MARGIN)

        if animate:
            anim = QPropertyAnimation(self, b"handleX", self)
            anim.setDuration(220)
            anim.setStartValue(self._handle_x)
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            self._anim = anim
        else:
            self._handle_x = target
            self.update()

    def mousePressEvent(self, event):
        self.set_dark(not self._is_dark)
        self._on_toggle(self._is_dark)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track_color = QColor("#2B2E38") if self._is_dark else QColor("#E0E7FF")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(0, 0, self.TRACK_W, self.TRACK_H, self.TRACK_H / 2, self.TRACK_H / 2)

        handle_color = QColor("#22D3EE") if self._is_dark else QColor("#6366F1")
        painter.setBrush(handle_color)
        painter.drawEllipse(QRectF(self._handle_x, self.HANDLE_MARGIN, self._handle_size(), self._handle_size()))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class DashboardWindow(QWidget):
    CARD_DEFS = [
        ("Total Revenue", "$128,430", 12.4),
        ("Active Users", "8,214", 4.1),
        ("Conversion Rate", "3.42%", -1.8),
        ("Avg. Session", "6m 12s", 7.9),
        ("Churn Rate", "2.1%", -0.6),
        ("New Signups", "412", 18.9),
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Animated Stat Cards - PySide6")
        self.resize(900, 480)

        self._theme = LIGHT
        self._cards: list[StatCard] = []

        self._build_ui()
        self._apply_window_theme(animate=False)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(20)

        header = QHBoxLayout()
        self._title_label = QLabel("Dashboard")
        self._title_label.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        header.addWidget(self._title_label)
        header.addStretch()

        self._mode_label = QLabel("Dark mode")
        self._mode_label.setFont(QFont("Segoe UI", 10))
        header.addWidget(self._mode_label)

        self._toggle = ThemeToggle(self._on_theme_toggled)
        header.addWidget(self._toggle)

        root.addLayout(header)

        grid = QGridLayout()
        grid.setSpacing(18)
        for i, (title_text, value_text, change) in enumerate(self.CARD_DEFS):
            card = StatCard(title_text, value_text, change, self._theme)
            self._cards.append(card)
            grid.addWidget(card, i // 3, i % 3)
        root.addLayout(grid)
        root.addStretch()

    def _on_theme_toggled(self, is_dark: bool):
        self._theme = DARK if is_dark else LIGHT
        self._apply_window_theme(animate=True)

    def _apply_window_theme(self, animate: bool):
        t = self._theme
        self.setStyleSheet(f"background-color: {t.window_bg};")
        self._title_label.setStyleSheet(f"color: {t.value_color};")
        self._mode_label.setStyleSheet(f"color: {t.title_color};")
        for card in self._cards:
            card.apply_theme(t, animate=animate)


def main():
    app = QApplication(sys.argv)
    window = DashboardWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()