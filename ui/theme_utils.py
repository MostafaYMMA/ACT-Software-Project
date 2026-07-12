"""
Small helper for widgets whose styling isn't fully covered by the global
objectName-based QSS in ui/theme.py (e.g. an inline setStyleSheet() call
built from an f-string). Those need to be told explicitly when the theme
changes - this wraps that boilerplate in one place instead of repeating
"connect to theme_changed and re-apply" in every file.

NOTE: this connects a plain function (not a bound method on a QObject) to
theme_manager.theme_changed, so Qt won't auto-disconnect it if the widget
is destroyed. That's fine for this app's current architecture (pages are
built once at startup and live for the whole session, never recreated),
but would need a manual disconnect if that ever changes.
"""

from ui.theme_manager import theme_manager


def apply_live_style(widget, style_fn):
    """style_fn receives the current colors dict and returns a QSS string.
    Applies it immediately, and again every time the theme changes."""
    def _apply(_mode=None):
        widget.setStyleSheet(style_fn(theme_manager.colors()))
    _apply()
    theme_manager.theme_changed.connect(_apply)
    return _apply