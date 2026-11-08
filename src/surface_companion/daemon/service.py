"""D-Bus front end for the privileged daemon (Gio only, no extra dependencies).

Bus name:  org.surfacecompanion.Daemon
Object:    /org/surfacecompanion/Daemon
Interface: org.surfacecompanion.Daemon1
  Call(action, json_args) -> json_result
  signals: JobOutput(job, line), JobFinished(job, ok, message), StateChanged()
Authorisation is checked per call through polkit.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
import uuid

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .. import actions  # noqa: E402

NAME = "org.surfacecompanion.Daemon"
PATH = "/org/surfacecompanion/Daemon"
IFACE = "org.surfacecompanion.Daemon1"
XML = f"""
<node>
  <interface name="{IFACE}">
    <method name="Call">
      <arg type="s" name="action" direction="in"/>
      <arg type="s" name="args" direction="in"/>
      <arg type="s" name="result" direction="out"/>
    </method>
    <signal name="JobOutput"><arg type="s" name="job"/><arg type="s" name="line"/></signal>
    <signal name="JobFinished"><arg type="s" name="job"/><arg type="b" name="ok"/><arg type="s" name="message"/></signal>
    <property name="Version" type="s" access="read"/>
  </interface>
</node>
"""


class Daemon:
    def __init__(self, bus_type, insecure: bool):
        self.bus_type = bus_type
        self.insecure = insecure
        self.settings = actions.Settings()
        self.actions = actions.build(self.settings)
        self.job_lock = threading.Lock()
        self.conn: Gio.DBusConnection | None = None
        self.loop = GLib.MainLoop()

    # -- polkit
    def authorised(self, sender: str, polkit_id: str) -> bool:
        if not polkit_id or self.insecure:
            return True
        proxy = Gio.DBusProxy.new_sync(
            self.conn, Gio.DBusProxyFlags.NONE, None, "org.freedesktop.PolicyKit1",
            "/org/freedesktop/PolicyKit1/Authority", "org.freedesktop.PolicyKit1.Authority", None)
        subject = ("system-bus-name", {"name": GLib.Variant("s", sender)})
        res = proxy.call_sync(
            "CheckAuthorization",
            GLib.Variant("((sa{sv})sa{ss}us)", (subject, polkit_id, {}, 1, "")),   # 1 = allow user interaction
            Gio.DBusCallFlags.NONE, 120_000, None)
        return bool(res.unpack()[0][0])

    # -- dispatch
    def on_call(self, conn, sender, path, iface, method, params, invocation):
        if method != "Call":
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
            return
        action, raw = params.unpack()
        threading.Thread(target=self._handle, args=(sender, action, raw, invocation), daemon=True).start()

    def _handle(self, sender, name, raw, invocation):
        try:
            act = self.actions.get(name)
            if act is None:
                raise ValueError(f"Unknown action: {name}")
            args = json.loads(raw) if raw else {}
            if not self.authorised(sender, act.polkit):
                invocation.return_dbus_error("org.surfacecompanion.Error.NotAuthorized", "Not authorised")
                return
            if act.job:
                job = uuid.uuid4().hex[:8]
                threading.Thread(target=self._run_job, args=(job, act, args), daemon=True).start()
                invocation.return_value(GLib.Variant("(s)", (json.dumps({"job": job}),)))
            else:
                result = act.fn(args)
                invocation.return_value(GLib.Variant("(s)", (json.dumps(result),)))
        except Exception as e:  # noqa: BLE001 - always answer the caller
            if not isinstance(e, (ValueError, RuntimeError, KeyError, PermissionError)):
                sys.stderr.write(traceback.format_exc())   # unexpected: keep the details for bug reports
            else:
                sys.stderr.write(f"{name}: {e}\n")
            invocation.return_dbus_error("org.surfacecompanion.Error.Failed", str(e) or e.__class__.__name__)

    def _emit(self, signal: str, fmt: str, values: tuple):
        self.conn.emit_signal(None, PATH, IFACE, signal, GLib.Variant(fmt, values))

    def _run_job(self, job: str, act, args: dict):
        if not self.job_lock.acquire(blocking=False):
            self._emit("JobFinished", "(sbs)", (job, False, "Another change is already in progress"))
            return
        try:
            msg = act.fn(args, lambda line: self._emit("JobOutput", "(ss)", (job, line)))
            self._emit("JobFinished", "(sbs)", (job, True, msg if isinstance(msg, str) else json.dumps(msg)))
        except Exception as e:  # noqa: BLE001
            self._emit("JobFinished", "(sbs)", (job, False, str(e) or e.__class__.__name__))
        finally:
            self.job_lock.release()

    def _tick(self):
        self.settings.tick_all()          # automatic profile and keyboard backlight timeout; no-ops unless enabled
        return True

    # -- sleep/resume: firmware often resets performance mode, so put saved settings back
    def on_login1(self, conn, sender, path, iface, signal, params):
        if signal == "PrepareForSleep" and not params.unpack()[0]:
            errs = self.settings.reapply()
            if errs:
                sys.stderr.write("reapply after resume: " + "; ".join(errs) + "\n")

    def run(self):
        info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]

        def bus_acquired(conn, name):
            self.conn = conn
            conn.register_object(PATH, info, self.on_call, lambda *a: GLib.Variant("s", "0.2.2"), None)
            if self.bus_type == Gio.BusType.SYSTEM:
                conn.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
                                      "/org/freedesktop/login1", None, Gio.DBusSignalFlags.NONE, self.on_login1)

        def name_acquired(conn, name):
            for e in self.settings.reapply():
                sys.stderr.write(f"reapply at start: {e}\n")
            self.settings.kbd.start_if_configured()
            GLib.timeout_add_seconds(1, self._tick)

        def name_lost(conn, name):
            sys.stderr.write(f"Could not own {name}\n")
            self.loop.quit()

        Gio.bus_own_name(self.bus_type, NAME, Gio.BusNameOwnerFlags.NONE, bus_acquired, name_acquired, name_lost)
        self.loop.run()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="surface-companiond")
    ap.add_argument("--session", action="store_true", help="use the session bus (development)")
    ap.add_argument("--insecure-no-auth", action="store_true", help="skip polkit (development only)")
    a = ap.parse_args(argv)
    if a.insecure_no_auth and not a.session:
        sys.exit("--insecure-no-auth is only allowed together with --session")
    Daemon(Gio.BusType.SESSION if a.session else Gio.BusType.SYSTEM, a.insecure_no_auth).run()


if __name__ == "__main__":
    main()
