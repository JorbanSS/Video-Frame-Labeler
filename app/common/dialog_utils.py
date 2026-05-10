import ctypes

from PyQt5.QtCore import QTimer, Qt


def force_dialog_focus(widget):
    """Bring a dialog to the foreground after it is shown."""
    if widget is None:
        return

    def _activate():
        try:
            if widget.isMinimized():
                widget.showNormal()
        except Exception:
            pass

        try:
            widget.raise_()
            widget.activateWindow()
            widget.setFocus(Qt.ActiveWindowFocusReason)
        except Exception:
            pass

        try:
            hwnd = int(widget.winId())
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    QTimer.singleShot(0, _activate)


def exec_modal_dialog(dialog):
    force_dialog_focus(dialog)
    return dialog.exec()
