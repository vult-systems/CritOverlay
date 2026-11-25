"""System tray icon: the way back to a hidden overlay, and the only Exit
while there is a tray (#14)."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import constants as C


class TrayIcon(QSystemTrayIcon):
    toggle_visibility_requested = Signal()
    toggle_pass_through_requested = Signal()
    screenshot_requested = Signal()
    next_screen_requested = Signal()
    autostart_toggled = Signal(bool)
    diagnostics_requested = Signal()
    open_logs_requested = Signal()
    quit_requested = Signal()

    def __init__(self, icon, parent=None):
        super().__init__(icon, parent)
        self.setToolTip(C.APP_NAME)

        # Kept on self: a QMenu with no parent can be garbage-collected out
        # from under the tray icon by PySide.
        self._menu = menu = QMenu()
        self.show_hide_action = menu.addAction("Show/Hide")
        self.show_hide_action.triggered.connect(self.toggle_visibility_requested.emit)
        menu.addAction("Toggle Pass-through (F8)").triggered.connect(self.toggle_pass_through_requested.emit)
        menu.addAction("Screenshot (F9)").triggered.connect(self.screenshot_requested.emit)
        menu.addAction("Move to Next Monitor (F7)").triggered.connect(self.next_screen_requested.emit)
        menu.addSeparator()
        self.autostart_action = menu.addAction("Start with Windows")
        self.autostart_action.setCheckable(True)
        # Signal to signal: connecting to .emit would drop the checked state.
        self.autostart_action.triggered.connect(self.autostart_toggled)
        troubleshooting = menu.addMenu("Troubleshooting")
        troubleshooting.addAction("Save Diagnostics Report").triggered.connect(self.diagnostics_requested.emit)
        troubleshooting.addAction("Open Log Folder").triggered.connect(self.open_logs_requested.emit)
        menu.addSeparator()
        menu.addAction("Exit").triggered.connect(self.quit_requested.emit)
        self.setContextMenu(menu)

        self.activated.connect(self._on_activated)

    def set_hotkey(self, text):
        """Show the working global hotkey in the menu and tooltip, or
        nothing if it's off or couldn't be registered."""
        self.show_hide_action.setText(f"Show/Hide ({text})" if text else "Show/Hide")
        self.setToolTip(f"{C.APP_NAME} ({text} shows/hides)" if text else C.APP_NAME)

    def set_autostart_checked(self, checked):
        self.autostart_action.setChecked(bool(checked))

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visibility_requested.emit()
