"""System tray icon so the overlay can be hidden without quitting the app."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import constants as C


class TrayIcon(QSystemTrayIcon):
    toggle_visibility_requested = Signal()
    toggle_pass_through_requested = Signal()
    screenshot_requested = Signal()
    next_screen_requested = Signal()
    diagnostics_requested = Signal()
    open_logs_requested = Signal()
    quit_requested = Signal()

    def __init__(self, icon, parent=None):
        super().__init__(icon, parent)
        self.setToolTip(C.APP_NAME)

        # Kept on self: a QMenu with no parent can be garbage-collected out
        # from under the tray icon by PySide.
        self._menu = menu = QMenu()
        menu.addAction("Show/Hide").triggered.connect(self.toggle_visibility_requested.emit)
        menu.addAction("Toggle Pass-through (F8)").triggered.connect(self.toggle_pass_through_requested.emit)
        menu.addAction("Screenshot (F9)").triggered.connect(self.screenshot_requested.emit)
        menu.addAction("Move to Next Monitor (F7)").triggered.connect(self.next_screen_requested.emit)
        menu.addSeparator()
        troubleshooting = menu.addMenu("Troubleshooting")
        troubleshooting.addAction("Save Diagnostics Report").triggered.connect(self.diagnostics_requested.emit)
        troubleshooting.addAction("Open Log Folder").triggered.connect(self.open_logs_requested.emit)
        menu.addSeparator()
        menu.addAction("Exit").triggered.connect(self.quit_requested.emit)
        self.setContextMenu(menu)

        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visibility_requested.emit()
