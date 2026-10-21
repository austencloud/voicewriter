"""Closes other dictation programs so your words are never typed twice.

Voice Access is part of Windows and can't be uninstalled; Win+Ctrl+S or a settings
reset can start it again. Talon is uninstalled, but could be reinstalled. If either
shows up while VoiceWriter runs, VoiceWriter closes it and says so on the bar.
"""

import ctypes
import ctypes.wintypes as wt
import logging

log = logging.getLogger("voicewriter.guard")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.OpenProcess.restype = wt.HANDLE

RIVALS = {"voiceaccess.exe": "Voice Access", "talon.exe": "Talon"}
TH32CS_SNAPPROCESS = 0x2
PROCESS_TERMINATE = 0x1
INVALID_HANDLE = wt.HANDLE(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


def running_rivals() -> list[tuple[int, str]]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE:
        return []
    found = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            name = RIVALS.get(entry.szExeFile.lower())
            if name:
                found.append((entry.th32ProcessID, name))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return found


def close_rivals() -> list[str]:
    """Closes any running rival and returns the names it closed."""
    closed = []
    for pid, name in running_rivals():
        h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if h and kernel32.TerminateProcess(h, 0):
            log.info("closed %s (pid %d) so words aren't typed twice", name, pid)
            closed.append(name)
        else:
            log.warning("could not close %s (pid %d), error %d", name, pid, ctypes.get_last_error())
        if h:
            kernel32.CloseHandle(h)
    return closed
