"""Talks to the privileged daemon over D-Bus; falls back to read-only local access."""
from __future__ import annotations

import json
import os
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage

NAME = "org.surfacecompanion.Daemon"
PATH = "/org/surfacecompanion/Daemon"
IFACE = "org.surfacecompanion.Daemon1"


class NoDaemon(Exception):
    pass


class _Dispatcher(QObject):
    """Lives in the GUI thread; workers emit into it and Qt queues delivery to the GUI thread."""
    done = pyqtSignal(int, bool, object)


class _Task(QRunnable):
    def __init__(self, fn, disp: _Dispatcher, tid: int):
        super().__init__()
        self.fn, self.disp, self.tid = fn, disp, tid      # task holds disp so it can't be freed mid-emit

    def run(self):
        try:
            res, ok = self.fn(), True
        except Exception as e:  # noqa: BLE001 - surfaced to the UI
            if os.environ.get("SC_DEBUG"):
                traceback.print_exc()
            res, ok = str(e) or e.__class__.__name__, False
        self.disp.done.emit(self.tid, ok, res)


class Backend(QObject):
    jobOutput = pyqtSignal(str, str)
    jobFinished = pyqtSignal(str, bool, str)
    daemonChanged = pyqtSignal(bool)

    def __init__(self, session_bus: bool = False):
        super().__init__()
        self.bus = QDBusConnection.sessionBus() if session_bus else QDBusConnection.systemBus()
        self.pool = QThreadPool.globalInstance()
        self._disp = _Dispatcher()
        self._disp.done.connect(self._finish)
        self._callbacks: dict[int, tuple] = {}
        self._next_id = 0
        self._buffer: dict[str, list] = {}
        self._tracked: dict[str, tuple] = {}
        self.has_daemon = False
        for sig, slot in (("JobOutput", self._on_output), ("JobFinished", self._on_finished)):
            self.bus.connect(NAME, PATH, IFACE, sig, slot)

    # -- D-Bus signal slots
    @pyqtSlot(str, str)
    def _on_output(self, job, line):
        t = self._tracked.get(job)
        if t:
            t[0](line)
        else:
            self._buffer.setdefault(job, []).append(("o", line))

    @pyqtSlot(str, bool, str)
    def _on_finished(self, job, ok, msg):
        t = self._tracked.get(job)
        if t:
            t[1](ok, msg)
            self._tracked.pop(job, None)
        else:
            self._buffer.setdefault(job, []).append(("f", ok, msg))

    def track(self, job: str, on_line, on_done) -> None:
        """Attach to a running job, replaying anything that arrived before we asked."""
        done = False
        for ev in self._buffer.pop(job, []):
            if ev[0] == "o":
                on_line(ev[1])
            else:
                on_done(ev[1], ev[2])
                done = True
        if not done:
            self._tracked[job] = (on_line, on_done)

    # -- calls
    def _blocking_call(self, action: str, args: dict):
        msg = QDBusMessage.createMethodCall(NAME, PATH, IFACE, "Call")
        msg.setArguments([action, json.dumps(args or {})])
        reply = self.bus.call(msg, QDBus.CallMode.Block, 180_000)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            name = reply.errorName()
            if name in ("org.freedesktop.DBus.Error.ServiceUnknown", "org.freedesktop.DBus.Error.NameHasNoOwner",
                        "org.freedesktop.DBus.Error.NoServer", "org.freedesktop.DBus.Error.Disconnected"):
                raise NoDaemon()
            if name.endswith("NotAuthorized"):
                raise PermissionError("Not authorised")
            raise RuntimeError(reply.errorMessage() or name)
        return json.loads(reply.arguments()[0])

    def _call(self, action: str, args: dict):
        try:
            res = self._blocking_call(action, args)
            if not self.has_daemon:
                self.has_daemon = True
                self.daemonChanged.emit(True)
            return res
        except NoDaemon:
            if self.has_daemon:
                self.has_daemon = False
                self.daemonChanged.emit(False)
            return self._local(action, args)

    def _local(self, action: str, args: dict):
        from .. import actions
        a = actions.build(actions.Settings(path=Path("/dev/null")))
        act = a.get(action)
        if act is None:
            raise ValueError(f"Unknown action: {action}")
        if act.polkit or act.job:
            raise RuntimeError("The Surface Companion background service is not running, so changes can't be "
                               "made. Install it with the installer, or start it with: "
                               "sudo systemctl start surface-companion")
        return act.fn(args)

    def _submit(self, fn, ok, err) -> None:
        self._next_id += 1
        self._callbacks[self._next_id] = (ok, err)
        self.pool.start(_Task(fn, self._disp, self._next_id))

    @pyqtSlot(int, bool, object)
    def _finish(self, tid, ok, payload):
        ok_cb, err_cb = self._callbacks.pop(tid, (None, None))
        cb = ok_cb if ok else err_cb
        if cb:
            cb(payload)

    def call(self, action: str, args: dict | None = None, ok=None, err=None) -> None:
        self._submit(lambda: self._call(action, args or {}), ok, err)

    def run_local(self, fn, ok=None, err=None) -> None:
        """Run a function in a worker thread as the current user (used for per-user fixes)."""
        self._submit(fn, ok, err)
