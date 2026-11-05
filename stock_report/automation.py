"""Windows scheduler registration and a logged, locked update worker."""

import sys

if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12+ is required. Please use py -3 with a recent Python installation.")

import argparse
import contextlib
import hashlib
import json
import locale
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from . import app
from .report_html import render_html
from .storage import atomic_write

ROOT = Path(__file__).resolve().parent.parent
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


@contextlib.contextmanager
def update_lock(path):
    """OS releases the lock even if a worker is killed; no stale-PID guesses."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            acquired = False
        else:
            acquired = True
        try:
            yield acquired
        finally:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def refresh_view(output, status):
    data = read_json(output / "report.json")
    atomic_write(output / "report.html", render_html(data, status))


def run_update(root=ROOT, demo=False):
    output = root / "reports" / ("demo" if demo else "")
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with update_lock(output / ".update.lock") as acquired:
        if not acquired:
            print("An update is already running; this attempt was skipped.")
            return 0
        log_path = logs / ("demo.log" if demo else "update.log")
        # Bound disk use; retain the preceding log as well.
        if log_path.exists() and log_path.stat().st_size > 1_000_000:
            log_path.replace(log_path.with_suffix(".previous.log"))
        status_path = output / "status.json"
        previous = read_json(status_path)
        status = {"state": "running", "attempted_at": datetime.now().astimezone().isoformat(),
                  "last_success_at": previous.get("last_success_at")}
        atomic_write(status_path, json.dumps(status, ensure_ascii=False, indent=2))
        refresh_view(output, status)
        with log_path.open("a", encoding="utf-8") as log:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                print(f"\n=== Update started {status['attempted_at']} ===", flush=True)
                try:
                    args = ["--config", str(root / "config.json"), "--output", str(output)]
                    code = app.main(args + (["--demo"] if demo else []))
                except Exception as exc:
                    # Unexpected library errors must not expose credential-containing bodies.
                    print(f"Unexpected failure: {type(exc).__name__}")
                    code = 1
                status.update(state={0: "success", 2: "partial"}.get(code, "failed"),
                              exit_code=code, finished_at=datetime.now().astimezone().isoformat())
                if code == 0:
                    status["last_success_at"] = status["finished_at"]
                if code in (0, 2):
                    try:
                        history = output / "history"
                        history.mkdir(exist_ok=True)
                        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                        atomic_write(history / f"{stamp}.json", (output / "report.json").read_text(encoding="utf-8"))
                        for old in sorted(history.glob("*.json"))[:-30]:
                            old.unlink()
                    except OSError as exc:
                        print(f"History backup failed: {type(exc).__name__}")
                atomic_write(status_path, json.dumps(status, ensure_ascii=False, indent=2))
                refresh_view(output, status)
                print(f"=== Update finished: {status['state']}, exit={code} ===", flush=True)
        print(f"Update: {status['state']}. Report: {output / 'report.html'}; log: {log_path}")
        return code


def task_identity(root):
    digest = hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:12]
    return f"StockReport-{digest}", f"Stock report updater ({digest})"


def task_xml(root, python, user, at="08:00", every_minutes=None):
    ET.register_namespace("", NS)
    def add(parent, name, text=None, **attrs):
        child = ET.SubElement(parent, f"{{{NS}}}{name}", attrs)
        if text is not None:
            child.text = str(text)
        return child

    task = ET.Element(f"{{{NS}}}Task", version="1.3")
    registration = add(task, "RegistrationInfo")
    add(registration, "Description", task_identity(root)[1])
    triggers = add(task, "Triggers")
    calendar = add(triggers, "CalendarTrigger")
    add(calendar, "Enabled", "true")
    add(calendar, "StartBoundary", f"{datetime.now():%Y-%m-%d}T{at}:00")
    if every_minutes:
        repetition = add(calendar, "Repetition")
        add(repetition, "Interval", f"PT{every_minutes}M")
        add(repetition, "Duration", "P1D")
        add(repetition, "StopAtDurationEnd", "false")
    if every_minutes:
        add(add(calendar, "ScheduleByDay"), "DaysInterval", "1")
    else:
        weekly = add(calendar, "ScheduleByWeek")
        add(weekly, "WeeksInterval", "1")
        days = add(weekly, "DaysOfWeek")
        for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
            add(days, day)
    logon = add(triggers, "LogonTrigger")
    add(logon, "Enabled", "true")
    add(logon, "UserId", user)
    add(logon, "Delay", "PT1M")
    principal = add(add(task, "Principals"), "Principal", id="Author")
    add(principal, "UserId", user)
    add(principal, "LogonType", "InteractiveToken")
    add(principal, "RunLevel", "LeastPrivilege")
    settings = add(task, "Settings")
    for key, value in (("MultipleInstancesPolicy", "IgnoreNew"), ("DisallowStartIfOnBatteries", "false"),
                       ("StopIfGoingOnBatteries", "false"), ("StartWhenAvailable", "true"),
                       ("RunOnlyIfNetworkAvailable", "false"), ("Enabled", "true"),
                       ("ExecutionTimeLimit", "PT15M")):
        add(settings, key, value)
    retry = add(settings, "RestartOnFailure")
    add(retry, "Interval", "PT3M")
    add(retry, "Count", "2")
    action = add(add(task, "Actions", Context="Author"), "Exec")
    # pythonw avoids a console window; all worker output is captured in log files.
    pythonw = Path(python).with_name("pythonw.exe")
    add(action, "Command", pythonw if pythonw.exists() else python)
    add(action, "Arguments", "-X utf8 -m stock_report.automation run")
    add(action, "WorkingDirectory", root)
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def scheduler_command(*args):
    return subprocess.run(["schtasks.exe", *args], capture_output=True, check=False)


def inspect_task(root):
    name, marker = task_identity(root)
    result = scheduler_command("/Query", "/TN", name, "/XML")
    if result.returncode != 0:
        return False
    raw = result.stdout
    encoding = "utf-16" if b"\x00" in raw[:100] or raw.startswith((b"\xff\xfe", b"\xfe\xff")) else locale.getpreferredencoding(False)
    try:
        document = ET.fromstring(raw.decode(encoding, errors="replace").lstrip("\ufeff"))
    except (ValueError, ET.ParseError):
        raise RuntimeError("Cannot inspect existing task; no task was changed.") from None
    if document.findtext(f"{{{NS}}}RegistrationInfo/{{{NS}}}Description") != marker:
        raise RuntimeError("Task name belongs to another program; no task was changed.")
    return True


def install(root, at="08:00", every_minutes=None, initial_run=True):
    if os.name != "nt":
        raise RuntimeError("Task installation requires Windows 10/11. The update worker also runs on other platforms.")
    # Validate before changing any persistent scheduler configuration.
    app.load_config(root / "config.json")
    name, _ = task_identity(root)
    inspect_task(root)
    user = subprocess.run(["whoami.exe"], capture_output=True, check=True, text=True).stdout.strip()
    payload = task_xml(root, sys.executable, user, at, every_minutes)
    with tempfile.TemporaryDirectory(prefix="stock-task-") as temporary:
        path = Path(temporary) / "task.xml"
        path.write_bytes(payload)
        result = scheduler_command("/Create", "/TN", name, "/XML", str(path), "/F")
    if result.returncode != 0:
        raise RuntimeError("Windows refused task registration. Check Task Scheduler permissions; no password is stored.")
    if not inspect_task(root):
        raise RuntimeError("Task registration could not be verified.")
    schedule = f"every {every_minutes} minutes (all days)" if every_minutes else f"weekdays at {at}"
    print(f"Installed {name}: {schedule}, plus one update after login. Times use the Windows system timezone.")
    print("Runs while this user is logged in; a locked screen is fine. Missed runs are caught up when available.")
    return run_update(root) if initial_run else 0


def uninstall(root):
    if os.name != "nt":
        raise RuntimeError("Task removal requires Windows.")
    name, _ = task_identity(root)
    if not inspect_task(root):
        print("No installed task was found for this folder.")
        return 0
    if scheduler_command("/Delete", "/TN", name, "/F").returncode != 0:
        raise RuntimeError("Windows refused task removal.")
    print(f"Removed {name}. Existing reports and configuration were kept.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install or run automatic local stock updates")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--demo", action="store_true")
    setup = sub.add_parser("install")
    setup.add_argument("--time", default="08:00", help="Windows local time, HH:MM")
    setup.add_argument("--every-minutes", type=int, help="Update all days at this interval instead of weekday-only")
    setup.add_argument("--skip-initial-run", action="store_true", help="Register only, without fetching prices now")
    sub.add_parser("uninstall")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            if len(args.time) != 5:
                raise ValueError("--time must be HH:MM")
            datetime.strptime(args.time, "%H:%M")
            if args.every_minutes is not None and not 5 <= args.every_minutes <= 1440:
                raise ValueError("--every-minutes must be between 5 and 1440")
            return install(ROOT, args.time, args.every_minutes, not args.skip_initial_run)
        if args.command == "uninstall":
            return uninstall(ROOT)
        return run_update(ROOT, args.demo)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Automation failed: {exc if type(exc) in (ValueError, RuntimeError) else type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
