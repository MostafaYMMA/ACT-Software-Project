"""
Settings page: light/dark toggle, plus the stale-threshold controls
(enable/disable + how many hours old counts as "late", both persisted
via ui/notification_settings.py). That threshold drives both the
banner in ui/app.py and the Late tab (ui/Pages/Late.py).

Sync section: an email address + enable/disable switch for Update/Finalize
on the Export History page. The email itself is still persisted via
ui/sync_partner_settings.py (untouched, unchanged). The on/off switch is a
separate "sync_enabled" flag persisted directly via QSettings in this file
-- when off, the email field is disabled here so it reads as "sync is
turned off" rather than just an empty field.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QComboBox, QFrame, QLineEdit,
    QPushButton, QFileDialog,
)
from PySide6.QtCore import Qt, QSettings

from ui.theme_manager import theme_manager
from ui.theme_utils import apply_live_style
from ui.toggle_switch import ToggleSwitch, SUN_ICON, MOON_ICON
from ui.switch import Switch
from ui.notification_settings import notification_settings
from ui.sync_partner_settings import sync_partner_settings
from ui.profile_circle import SETTINGS_ORG, SETTINGS_APP
from ui.sharepoint_settings import sharepoint_settings
from onedrive_link_resolver import resolve_local_path_from_link, OneDriveLinkResolutionError

BELL_ICON = "\U0001F514"
SYNC_ICON = "\U0001F501"
FOLDER_ICON = "\U0001F4C1"
LINK_ICON = "\U0001F517"
HOURS_PER_DAY = 24

# Persisted the same way every other per-user toggle in this app already is
# (see Records.py's search history for the same QSettings(org, app) pattern).
SYNC_ENABLED_KEY = "sync_enabled"


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

        title = QLabel("Settings")
        apply_live_style(title, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        # -- Appearance --------------------------------------------------
        appearance_label = QLabel("Appearance")
        apply_live_style(appearance_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 11px; font-weight: 700;")
        layout.addWidget(appearance_label)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(10)

        self._icon_label = QLabel()
        self._icon_label.setStyleSheet("font-size: 15px;")
        theme_row.addWidget(self._icon_label)

        self._mode_label = QLabel()
        apply_live_style(self._mode_label, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 13px;")
        theme_row.addWidget(self._mode_label)

        theme_row.addStretch()
        theme_row.addWidget(ToggleSwitch())

        layout.addLayout(theme_row)

        divider = QFrame()
        divider.setFixedHeight(1)
        apply_live_style(divider, lambda c: f"background-color: {c['BORDER']};")
        layout.addWidget(divider)

        # -- Stale pending/rejected threshold -----------------------------
        # No section header here on purpose -- this threshold isn't
        # notification-specific anymore, it also defines what counts as
        # "late" on the Late tab (see ui/Pages/Late.py).
        notify_row = QHBoxLayout()
        notify_row.setSpacing(10)

        icon_label = QLabel(BELL_ICON)
        icon_label.setStyleSheet("font-size: 15px;")
        notify_row.addWidget(icon_label)

        notify_text = QLabel("Notify me about pending/rejected requests that are taking a while")
        notify_text.setWordWrap(True)
        apply_live_style(notify_text, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 13px;")
        notify_row.addWidget(notify_text, stretch=1)

        self._notify_switch = Switch(checked=notification_settings.enabled)
        self._notify_switch.toggled.connect(self._on_notify_toggled)
        notify_row.addWidget(self._notify_switch)

        layout.addLayout(notify_row)

        threshold_row = QHBoxLayout()
        threshold_row.setSpacing(10)

        self._threshold_label = QLabel("Notify when waiting for at least")
        apply_live_style(self._threshold_label, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 12px;")
        threshold_row.addWidget(self._threshold_label)

        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(1, 999)
        self._threshold_spin.valueChanged.connect(self._on_threshold_changed)
        threshold_row.addWidget(self._threshold_spin)

        self._threshold_unit = QComboBox()
        self._threshold_unit.addItems(["Hours", "Days"])
        self._threshold_unit.currentIndexChanged.connect(self._on_unit_changed)
        threshold_row.addWidget(self._threshold_unit)

        threshold_row.addStretch()
        layout.addLayout(threshold_row)

        sync_divider = QFrame()
        sync_divider.setFixedHeight(1)
        apply_live_style(sync_divider, lambda c: f"background-color: {c['BORDER']};")
        layout.addWidget(sync_divider)

        # -- Cross-device sync partner ------------------------------------
        # Where Update/Finalize on the Export History page send their sync
        # mail (see services/sync_service.py + services/outlook_service.py).
        # The email is persisted via ui/sync_partner_settings.py so it isn't
        # re-typed every session; the on/off switch below is a separate flag
        # (SYNC_ENABLED_KEY) that just controls whether that field -- and
        # therefore sync -- is currently active.
        sync_label = QLabel("Sync")
        apply_live_style(sync_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 11px; font-weight: 700;")
        layout.addWidget(sync_label)

        sync_row = QHBoxLayout()
        sync_row.setSpacing(10)

        self._sync_icon_label = QLabel(SYNC_ICON)
        self._sync_icon_label.setStyleSheet("font-size: 15px;")
        sync_row.addWidget(self._sync_icon_label)

        self._sync_text = QLabel("Other user's email (for Update/Finalize on Export History)")
        self._sync_text.setWordWrap(True)
        apply_live_style(self._sync_text, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 13px;")
        sync_row.addWidget(self._sync_text, stretch=1)

        sync_enabled = self._settings.value(SYNC_ENABLED_KEY, True, type=bool)
        self._sync_switch = Switch(checked=sync_enabled)
        self._sync_switch.toggled.connect(self._on_sync_toggled)
        sync_row.addWidget(self._sync_switch)

        layout.addLayout(sync_row)

        self._partner_email_edit = QLineEdit(sync_partner_settings.partner_email)
        self._partner_email_edit.setPlaceholderText("their.email@company.com")
        self._partner_email_edit.editingFinished.connect(self._on_partner_email_edited)
        apply_live_style(self._partner_email_edit, lambda c: f"""
            QLineEdit {{
                border: 1px solid {c['BORDER']};
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 13px;
                background: {c['SURFACE']};
                color: {c['TEXT_PRIMARY']};
            }}
            QLineEdit:focus {{ border: 1px solid {c['ACCENT']}; }}
        """)
        layout.addWidget(self._partner_email_edit)

        sharepoint_divider = QFrame()
        sharepoint_divider.setFixedHeight(1)
        apply_live_style(sharepoint_divider, lambda c: f"background-color: {c['BORDER']};")
        layout.addWidget(sharepoint_divider)

        # -- SharePoint folder (Update / View Current / Finalize on Export History) --
        # A local, OneDrive/SharePoint-synced folder PATH is what the sync
        # channel actually uses -- transport for it is plain file copies
        # into this path (see services/sharepoint_service.py); there is no
        # Graph API/OAuth involved, so nothing here ever "uploads" over
        # the internet directly -- the OneDrive desktop client does that
        # on its own once a file lands in the synced folder. The link
        # field below is a convenience for FILLING IN this path -- it
        # doesn't replace it.
        sharepoint_label = QLabel("SharePoint Sync")
        apply_live_style(sharepoint_label, lambda c: f"color: {c['TEXT_SECONDARY']}; font-size: 11px; font-weight: 700;")
        layout.addWidget(sharepoint_label)

        # -- Resolve the folder from a pasted link (convenience) ----------
        # Purely local (see services/onedrive_link_resolver.py): matches
        # the pasted link against OneDrive's OWN record, in the Windows
        # registry, of what it has already synced on this PC, and fills
        # in the folder field below automatically. No Graph API, no
        # OAuth, no sign-in, no network call -- if OneDrive hasn't
        # already synced this library on this machine, this can't find
        # it (Browse/manual entry below always still works as a fallback).
        link_row = QHBoxLayout()
        link_row.setSpacing(10)

        link_icon_label = QLabel(LINK_ICON)
        link_icon_label.setStyleSheet("font-size: 15px;")
        link_row.addWidget(link_icon_label)

        link_text = QLabel("Paste a SharePoint/OneDrive link to fill in the folder below automatically")
        link_text.setWordWrap(True)
        apply_live_style(link_text, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 13px;")
        link_row.addWidget(link_text, stretch=1)

        layout.addLayout(link_row)

        link_input_row = QHBoxLayout()
        link_input_row.setSpacing(10)

        self._sharepoint_link_edit = QLineEdit(sharepoint_settings.link)
        self._sharepoint_link_edit.setPlaceholderText("https://yourcompany.sharepoint.com/sites/...")
        self._sharepoint_link_edit.setToolTip(
            "A normal SharePoint/OneDrive web link. Resolved to the matching LOCAL synced folder using "
            "OneDrive's own sync records on this PC -- no sign-in, no network call, works offline."
        )
        apply_live_style(self._sharepoint_link_edit, lambda c: f"""
            QLineEdit {{
                border: 1px solid {c['BORDER']};
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 13px;
                background: {c['SURFACE']};
                color: {c['TEXT_PRIMARY']};
            }}
            QLineEdit:focus {{ border: 1px solid {c['ACCENT']}; }}
        """)
        link_input_row.addWidget(self._sharepoint_link_edit, stretch=1)

        resolve_btn = QPushButton("Resolve")
        resolve_btn.setObjectName("primaryButton")
        resolve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        resolve_btn.setToolTip("Find the local synced folder that matches this link and fill it in below")
        resolve_btn.clicked.connect(self._on_resolve_sharepoint_link)
        link_input_row.addWidget(resolve_btn)

        layout.addLayout(link_input_row)

        self._link_status_is_error = False
        self._sharepoint_link_status = QLabel("")
        self._sharepoint_link_status.setWordWrap(True)
        # apply_live_style returns the callable it wired up to
        # theme_changed -- kept so _on_resolve_sharepoint_link can re-run
        # it immediately after flipping _link_status_is_error, instead of
        # waiting for the next theme toggle to pick up the new color.
        self._refresh_link_status_style = apply_live_style(
            self._sharepoint_link_status,
            lambda c: f"color: {'#E05252' if self._link_status_is_error else c['TEXT_SECONDARY']}; font-size: 11px;",
        )
        layout.addWidget(self._sharepoint_link_status)

        # -- ...or set/verify the folder path directly ---------------------
        sharepoint_row = QHBoxLayout()
        sharepoint_row.setSpacing(10)

        sharepoint_icon_label = QLabel(FOLDER_ICON)
        sharepoint_icon_label.setStyleSheet("font-size: 15px;")
        sharepoint_row.addWidget(sharepoint_icon_label)

        sharepoint_text = QLabel("...or set the local folder path directly (not a web link)")
        sharepoint_text.setWordWrap(True)
        apply_live_style(sharepoint_text, lambda c: f"color: {c['TEXT_PRIMARY']}; font-size: 13px;")
        sharepoint_row.addWidget(sharepoint_text, stretch=1)

        layout.addLayout(sharepoint_row)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(10)

        self._sharepoint_folder_edit = QLineEdit(sharepoint_settings.folder)
        self._sharepoint_folder_edit.setPlaceholderText(r"C:\Users\you\Company\Timecards - Current")
        self._sharepoint_folder_edit.setToolTip(
            "A local folder path, not a SharePoint web URL -- e.g. "
            r"C:\Users\you\CompanyName\Timecards - Current"
        )
        self._sharepoint_folder_edit.editingFinished.connect(self._on_sharepoint_folder_edited)
        apply_live_style(self._sharepoint_folder_edit, lambda c: f"""
            QLineEdit {{
                border: 1px solid {c['BORDER']};
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 13px;
                background: {c['SURFACE']};
                color: {c['TEXT_PRIMARY']};
            }}
            QLineEdit:focus {{ border: 1px solid {c['ACCENT']}; }}
        """)
        folder_row.addWidget(self._sharepoint_folder_edit, stretch=1)

        browse_btn = QPushButton("Browse...")
        browse_btn.setObjectName("secondaryButton")
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.setToolTip("Pick the local synced folder instead of typing/pasting the path")
        browse_btn.clicked.connect(self._on_browse_sharepoint_folder)
        folder_row.addWidget(browse_btn)

        layout.addLayout(folder_row)

        self._sharepoint_folder_warning = QLabel("")
        self._sharepoint_folder_warning.setWordWrap(True)
        apply_live_style(self._sharepoint_folder_warning, lambda c: "color: #E05252; font-size: 11px;")
        self._sharepoint_folder_warning.setVisible(False)
        layout.addWidget(self._sharepoint_folder_warning)

        layout.addStretch()

        self._load_threshold_into_controls(notification_settings.threshold_hours)
        self._update_threshold_enabled(notification_settings.enabled)
        self._update_sync_enabled(sync_enabled)

        self._update_labels(theme_manager.mode)
        theme_manager.theme_changed.connect(self._update_labels)

    def _update_labels(self, mode):
        if mode == "dark":
            self._icon_label.setText(MOON_ICON)
            self._mode_label.setText("Dark mode")
        else:
            self._icon_label.setText(SUN_ICON)
            self._mode_label.setText("Light mode")

    def _load_threshold_into_controls(self, hours):
        if hours % HOURS_PER_DAY == 0 and hours >= HOURS_PER_DAY:
            self._threshold_spin.blockSignals(True)
            self._threshold_unit.blockSignals(True)
            self._threshold_spin.setValue(int(hours // HOURS_PER_DAY))
            self._threshold_unit.setCurrentText("Days")
            self._threshold_spin.blockSignals(False)
            self._threshold_unit.blockSignals(False)
        else:
            self._threshold_spin.blockSignals(True)
            self._threshold_unit.blockSignals(True)
            self._threshold_spin.setValue(int(hours))
            self._threshold_unit.setCurrentText("Hours")
            self._threshold_spin.blockSignals(False)
            self._threshold_unit.blockSignals(False)

    def _update_threshold_enabled(self, enabled):
        self._threshold_label.setEnabled(enabled)
        self._threshold_spin.setEnabled(enabled)
        self._threshold_unit.setEnabled(enabled)

    def _update_sync_enabled(self, enabled):
        self._sync_icon_label.setEnabled(enabled)
        self._sync_text.setEnabled(enabled)
        self._partner_email_edit.setEnabled(enabled)

    def _on_notify_toggled(self, checked):
        notification_settings.set_enabled(checked)
        self._update_threshold_enabled(checked)

    def _on_sync_toggled(self, checked):
        self._settings.setValue(SYNC_ENABLED_KEY, checked)
        self._update_sync_enabled(checked)

    def _on_partner_email_edited(self):
        sync_partner_settings.set_partner_email(self._partner_email_edit.text())
    def _on_resolve_sharepoint_link(self):
        link = self._sharepoint_link_edit.text()
        sharepoint_settings.set_link(link)

        try:
            folder = resolve_local_path_from_link(link)
        except OneDriveLinkResolutionError as exc:
            self._link_status_is_error = True
            self._sharepoint_link_status.setText(str(exc))
            self._refresh_link_status_style()
            return

        self._link_status_is_error = False
        self._sharepoint_link_status.setText(f"Resolved to: {folder}")
        self._refresh_link_status_style()

        self._sharepoint_folder_edit.setText(folder)
        self._warn_if_sharepoint_folder_looks_like_url(folder)
        sharepoint_settings.set_folder(folder)

    def _on_sharepoint_folder_edited(self):
        value = self._sharepoint_folder_edit.text()
        self._warn_if_sharepoint_folder_looks_like_url(value)
        sharepoint_settings.set_folder(value)

    def _warn_if_sharepoint_folder_looks_like_url(self, value):
        """Catches the most common mistake here -- pasting a SharePoint
        "Copy Link" web URL instead of the local synced folder path --
        right where it's entered, instead of letting it silently fail
        much later when SharePoint Update can't find/write the folder."""
        looks_like_url = value.strip().lower().startswith(("http://", "https://"))
        self._sharepoint_folder_warning.setText(
            "That looks like a web link, not a local folder path. This field needs the path to the "
            "folder on THIS PC where OneDrive already syncs that SharePoint library -- see the note above."
            if looks_like_url else ""
        )
        self._sharepoint_folder_warning.setVisible(looks_like_url)

    def _on_browse_sharepoint_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select the local OneDrive-synced SharePoint folder", sharepoint_settings.folder or ""
        )
        if not folder:
            return  # user cancelled
        self._sharepoint_folder_edit.setText(folder)
        self._warn_if_sharepoint_folder_looks_like_url(folder)
        sharepoint_settings.set_folder(folder)

    def _on_threshold_changed(self, _value):
        multiplier = HOURS_PER_DAY if self._threshold_unit.currentText() == "Days" else 1
        notification_settings.set_threshold_hours(self._threshold_spin.value() * multiplier)

    def _on_unit_changed(self, _index):
        # Re-express the ALREADY-STORED threshold in the newly selected
        # unit, rather than reinterpreting the same displayed number under
        # the new unit -- without this, switching "48 Hours" to "Days"
        # silently became "48 Days" (1152 hours), a 24x jump nobody asked
        # for. The number changes so the underlying hours value doesn't.
        hours = notification_settings.threshold_hours
        display_value = hours / HOURS_PER_DAY if self._threshold_unit.currentText() == "Days" else hours
        self._threshold_spin.blockSignals(True)
        self._threshold_spin.setValue(max(1, round(display_value)))
        self._threshold_spin.blockSignals(False)
        self._on_threshold_changed(self._threshold_spin.value())