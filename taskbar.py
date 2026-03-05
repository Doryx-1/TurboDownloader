"""
taskbar.py — Barre de progression dans la barre des tâches Windows.
Utilise comtypes pour accéder à ITaskbarList3.
Fallback silencieux si non disponible ou hors Windows.
"""

import sys
import ctypes

_TASKBAR_OK = False

# États ITaskbarList3
TBPF_NOPROGRESS    = 0x0
TBPF_INDETERMINATE = 0x1
TBPF_NORMAL        = 0x2
TBPF_ERROR         = 0x4
TBPF_PAUSED        = 0x8

if sys.platform == "win32":
    try:
        import comtypes
        import comtypes.client

        # ── Définition complète de ITaskbarList3 ──────────────────────────
        # On déclare l'interface COM avec tous ses ancêtres pour que
        # CreateObject(..., interface=ITaskbarList3) fonctionne correctement.

        class ITaskbarList3(comtypes.IUnknown):
            _case_insensitive_ = True
            _iid_ = comtypes.GUID("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")
            _methods_ = [
                # ITaskbarList
                comtypes.COMMETHOD([], ctypes.HRESULT, "HrInit"),
                comtypes.COMMETHOD([], ctypes.HRESULT, "AddTab",
                    (["in"], ctypes.c_ulong, "hwnd")),
                comtypes.COMMETHOD([], ctypes.HRESULT, "DeleteTab",
                    (["in"], ctypes.c_ulong, "hwnd")),
                comtypes.COMMETHOD([], ctypes.HRESULT, "ActivateTab",
                    (["in"], ctypes.c_ulong, "hwnd")),
                comtypes.COMMETHOD([], ctypes.HRESULT, "SetActiveAlt",
                    (["in"], ctypes.c_ulong, "hwnd")),
                # ITaskbarList2
                comtypes.COMMETHOD([], ctypes.HRESULT, "MarkFullscreenWindow",
                    (["in"], ctypes.c_ulong, "hwnd"),
                    (["in"], ctypes.c_int,   "fFullscreen")),
                # ITaskbarList3
                comtypes.COMMETHOD([], ctypes.HRESULT, "SetProgressValue",
                    (["in"], ctypes.c_ulong,     "hwnd"),
                    (["in"], ctypes.c_ulonglong,  "ullCompleted"),
                    (["in"], ctypes.c_ulonglong,  "ullTotal")),
                comtypes.COMMETHOD([], ctypes.HRESULT, "SetProgressState",
                    (["in"], ctypes.c_ulong, "hwnd"),
                    (["in"], ctypes.c_int,   "tbpFlags")),
            ]

        CLSID_TaskbarList = comtypes.GUID("{56FDF344-FD6D-11d0-958A-006097C9A090}")
        _TASKBAR_OK = True
        print("[taskbar] comtypes disponible, ITaskbarList3 défini")

    except Exception as e:
        print(f"[taskbar] non disponible: {e}")
else:
    print("[taskbar] non-Windows, désactivé")


class TaskbarProgress:
    """
    Contrôle la barre de progression de la taskbar Windows.
    Toutes les méthodes sont no-op silencieux si comtypes est absent.
    """

    def __init__(self, hwnd: int):
        self._hwnd   = hwnd
        self._tbl    = None
        self._active = False

        if not _TASKBAR_OK or hwnd == 0:
            return

        try:
            obj = comtypes.client.CreateObject(
                CLSID_TaskbarList,
                interface=ITaskbarList3,
            )
            obj.HrInit()
            self._tbl    = obj
            self._active = True
            print(f"[taskbar] initialisé (hwnd={hwnd})")
        except Exception as e:
            print(f"[taskbar] échec init: {e}")

    # ---------------------------------------------------------------- API publique

    def set_progress(self, ratio: float):
        """Barre verte — ratio entre 0.0 et 1.0."""
        if not self._active:
            return
        try:
            completed = max(0, min(10000, int(ratio * 10000)))
            self._tbl.SetProgressValue(self._hwnd, completed, 10000)
            self._tbl.SetProgressState(self._hwnd, TBPF_NORMAL)
        except Exception as e:
            print(f"[taskbar] set_progress error: {e}")
            self._active = False

    def set_indeterminate(self):
        """Barre animée (taille inconnue)."""
        if not self._active:
            return
        try:
            self._tbl.SetProgressState(self._hwnd, TBPF_INDETERMINATE)
        except Exception as e:
            print(f"[taskbar] set_indeterminate error: {e}")
            self._active = False

    def set_error(self):
        """Barre rouge."""
        if not self._active:
            return
        try:
            self._tbl.SetProgressState(self._hwnd, TBPF_ERROR)
        except Exception as e:
            print(f"[taskbar] set_error error: {e}")
            self._active = False

    def set_paused(self):
        """Barre jaune."""
        if not self._active:
            return
        try:
            self._tbl.SetProgressState(self._hwnd, TBPF_PAUSED)
        except Exception as e:
            print(f"[taskbar] set_paused error: {e}")
            self._active = False

    def clear(self):
        """Supprime la barre (idle)."""
        if not self._active:
            return
        try:
            self._tbl.SetProgressState(self._hwnd, TBPF_NOPROGRESS)
        except Exception as e:
            print(f"[taskbar] clear error: {e}")
            self._active = False