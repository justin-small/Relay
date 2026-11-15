"""A Windows job object that ends livekit-server when Relay does, however Relay ends (#238).

`taskkill /T` stops LiveKit on a normal stop, but a crash or a kill of Relay leaves it running and holding Relay's
ports. A job object with "kill on close" is held by Relay alone: when Relay's process goes, Windows closes the
handle and ends everything in the job. Does nothing off Windows, where the process group does this job.
"""
from __future__ import annotations

import sys

_job = None          # the job's handle, kept for the life of Relay: closing it ends the processes in it

_KILL_ON_JOB_CLOSE = 0x2000
_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _make():
    """Create the job (Windows only). Returns its handle, or None when Windows refuses."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes",
                                                   "OtherBytes")]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.Basic.LimitFlags = _KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(job)
        return None
    return job


def attach(pid: int) -> bool:
    """Put process `pid` (and so everything it starts) in Relay's kill-on-close job. False when it could not be."""
    global _job
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    try:
        if _job is None:
            _job = _make()
            if _job is None:
                return False
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        proc = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not proc:
            return False
        try:
            return bool(k32.AssignProcessToJobObject(_job, proc))
        finally:
            k32.CloseHandle(proc)
    except (OSError, AttributeError):
        return False
