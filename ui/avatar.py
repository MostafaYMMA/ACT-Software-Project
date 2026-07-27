"""
Clickable profile avatar shown in the top bar.
Built on ProfileCircle (the shared photo-or-initials painter) - this
file adds the click -> menu behavior on top of it:
  - No photo set yet -> shows initials on an orange circle.
  - Click it -> small menu: View photo / Choose photo... / Remove photo.
  - "Choose photo..." opens the real OS file picker (QFileDialog).
The popup menu is styled to match the app's current theme (white +
orange in Light Mode, dark + orange in Dark Mode) instead of using the
plain default OS menu look, and re-styles itself if the theme changes
while it happens to be open.
The chosen photo's path is saved via QSettings (same key ProfileCircle
reads), so select_account_page.py's tiles pick it up too.
"""

from PySide6.QtWidgets import QMenu, QFileDialog
from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QPixmap

from ui.profile_circle import ProfileCircle, SETTINGS_ORG, SETTINGS_APP
from ui.theme_manager import theme_manager
from ui.photo_lightbox import PhotoLightbox


def _find_main_window(widget):
    """Walks up the parent chain to the ui.app.MainWindow instance,
    identified by its objectName ("mainWindow", set in
    ui/app.py::MainWindow.__init__) rather than an isinstance check, so
    this file doesn't need to import ui.app (which itself imports THIS
    file, to build the Avatar in its top bar -- a real circular import).

    Deliberately NOT the same as widget.window(): main.py wraps
    MainWindow in a QStackedWidget inside its own top-level RootWindow
    (a QMainWindow, for the welcome-page/main-window handoff animation),
    so window() would resolve to RootWindow -- a different, larger
    coordinate space that isn't what "cover the whole window, sidebar and
    top bar included" means here. MainWindow itself is what actually
    contains the sidebar/top bar this needs to sit above."""
    w = widget
    while w is not None:
        if w.objectName() == "mainWindow":
            return w
        w = w.parentWidget()
    return widget.window()  # fallback -- shouldn't normally be reached


def _menu_stylesheet():
    c = theme_manager.colors()
    return f"""
        QMenu {{
            background-color: {c['SURFACE']};
            color: {c['TEXT_PRIMARY']};
            border: 1px solid {c['BORDER']};
            border-radius: 8px;
            padding: 4px;
        }}
        QMenu::item {{
            padding: 8px 22px;
            border-radius: 4px;
            font-size: 13px;
        }}
        QMenu::item:selected {{
            background-color: {c['ACCENT']};
            color: {c['TEXT_ON_ACCENT']};
        }}
        QMenu::item:disabled {{
            color: {c['TEXT_SECONDARY']};
        }}
        QMenu::separator {{
            height: 1px;
            background: {c['BORDER']};
            margin: 4px 8px;
        }}
    """


class Avatar(ProfileCircle):
    def __init__(self, username, size=44, parent=None):
        super().__init__(username, size=size, parent=parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

    def _settings_key(self):
        return f"profile_photo/{self.username}"

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._show_menu()
        super().mousePressEvent(event)

    def _show_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(_menu_stylesheet())

        view_action = menu.addAction("View photo")
        view_action.setEnabled(self._pixmap is not None)
        edit_action = menu.addAction("Choose photo...")
        remove_action = menu.addAction("Remove photo")
        remove_action.setEnabled(self._pixmap is not None)

        chosen = menu.exec(self.mapToGlobal(self.rect().bottomLeft()))
        if chosen == view_action:
            self._show_view_dialog()
        elif chosen == edit_action:
            self._choose_photo()
        elif chosen == remove_action:
            self._remove_photo()

    def _choose_photo(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose profile photo", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return  # user cancelled
        pix = QPixmap(path)
        if pix.isNull():
            return  # not a valid/readable image
        self._pixmap = pix
        self._settings.setValue(self._settings_key(), path)
        self.update()

    def _remove_photo(self):
        self._pixmap = None
        self._settings.setValue(self._settings_key(), "")
        self.update()

    def _show_view_dialog(self):
        if self._pixmap is None:
            return
        main_window = _find_main_window(self)
        # Kept on self so it isn't garbage-collected mid-animation (same
        # reasoning as the *_anim attributes elsewhere in this app) --
        # overwritten, not explicitly torn down, on the next "View photo"
        # click; the previous lightbox's widgets are already deleteLater'd
        # by then regardless.
        self._lightbox = PhotoLightbox(main_window, self, self._pixmap)