import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from stock_report import app, automation, market
from stock_report.report_html import render_html
from stock_report.storage import atomic_write
from test_report import NOW


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stock test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config.json").write_text(json.dumps({"stocks": ["AAPL"]}), encoding="utf-8")

    def status(self):
        return json.loads((self.root / "reports/status.json").read_text())

    def test_demo_worker_creates_html_status_and_history(self):
        self.assertEqual(automation.run_update(self.root, demo=True), 0)
        output = self.root / "reports/demo"
        self.assertEqual(json.loads((output / "status.json").read_text())["state"], "success")
        self.assertIn("示範資料", (output / "report.html").read_text())
        self.assertEqual(len(list((output / "history").glob("*.json"))), 1)
        self.assertIn("Update finished: success", (self.root / "logs/demo.log").read_text())

    def test_failure_keeps_last_data_but_marks_view_as_failed(self):
        with patch.object(app, "fetch_stock", return_value=market.demo_stock("AAPL", NOW)):
            self.assertEqual(automation.run_update(self.root), 0)
        data = (self.root / "reports/report.json").read_bytes()
        last_success = self.status()["last_success_at"]
        with patch.object(app, "fetch_stock", side_effect=RuntimeError("unavailable")):
            self.assertEqual(automation.run_update(self.root), 1)
        self.assertEqual((self.root / "reports/report.json").read_bytes(), data)
        self.assertEqual(self.status()["state"], "failed")
        self.assertEqual(self.status()["last_success_at"], last_success)
        self.assertIn("最近一次更新失敗", (self.root / "reports/report.html").read_text())
        self.assertEqual(len(list((self.root / "reports/history").glob("*.json"))), 1)

    def test_first_failure_creates_visible_status_without_fake_quotes(self):
        with patch.object(app, "fetch_stock", side_effect=RuntimeError("unavailable")):
            self.assertEqual(automation.run_update(self.root), 1)
        self.assertFalse((self.root / "reports/report.json").exists())
        self.assertIn("尚未成功更新", (self.root / "reports/report.html").read_text())

    def test_real_lock_skips_duplicate_and_releases(self):
        lock = self.root / "reports/.update.lock"
        with automation.update_lock(lock) as acquired:
            self.assertTrue(acquired)
            with patch.object(app, "main") as runner:
                self.assertEqual(automation.run_update(self.root), 0)
                runner.assert_not_called()
        with automation.update_lock(lock) as acquired:
            self.assertTrue(acquired)

    def test_partial_result_records_status_and_html(self):
        (self.root / "config.json").write_text(json.dumps({"stocks": ["AAPL", "BAD"]}))
        with patch.object(app, "fetch_stock", side_effect=[market.demo_stock("AAPL", NOW), RuntimeError("no data")]):
            self.assertEqual(automation.run_update(self.root), 2)
        self.assertEqual(self.status()["state"], "partial")
        self.assertIn("BAD", (self.root / "reports/report.html").read_text())

    def test_history_retention(self):
        history = self.root / "reports/demo/history"
        history.mkdir(parents=True)
        for i in range(31):
            (history / f"20000101-{i:06d}.json").write_text('{}')
        self.assertEqual(automation.run_update(self.root, demo=True), 0)
        self.assertEqual(len(list(history.glob("*.json"))), 30)

    def test_atomic_write_replaces_and_cleans_temporary_file(self):
        path = self.root / "report.json"
        atomic_write(path, "old")
        atomic_write(path, "new")
        self.assertEqual(path.read_text(), "new")
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_html_does_not_execute_ai_text(self):
        html = render_html({"summary": '<script>alert("x")</script>', "stocks": []})
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn('http-equiv="refresh"', html)


class SchedulerTests(unittest.TestCase):
    def test_weekday_task_escapes_paths_and_uses_working_directory(self):
        root = Path('D:/股票研究 & reports')
        document = ET.fromstring(automation.task_xml(root, "C:/Python313/python.exe", "MYPC/user"))
        ns = {"t": automation.NS}
        self.assertEqual(document.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=ns), str(root))
        self.assertEqual(document.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns), "-X utf8 -m stock_report.automation run")
        days = document.find("t:Triggers/t:CalendarTrigger/t:ScheduleByWeek/t:DaysOfWeek", ns)
        self.assertEqual(len(days), 5)
        self.assertIsNotNone(document.find("t:Triggers/t:LogonTrigger", ns))
        self.assertEqual(document.findtext("t:Principals/t:Principal/t:LogonType", namespaces=ns), "InteractiveToken")
        self.assertEqual(document.findtext("t:Settings/t:StartWhenAvailable", namespaces=ns), "true")
        self.assertEqual(document.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=ns), "IgnoreNew")

    def test_interval_mode_has_daily_repetition(self):
        document = ET.fromstring(automation.task_xml(Path("D:/stocks"), "python.exe", "user", every_minutes=30))
        ns = {"t": automation.NS}
        self.assertEqual(document.findtext("t:Triggers/t:CalendarTrigger/t:Repetition/t:Interval", namespaces=ns), "PT30M")
        self.assertEqual(document.findtext("t:Triggers/t:CalendarTrigger/t:ScheduleByDay/t:DaysInterval", namespaces=ns), "1")
        self.assertIsNone(document.find("t:Triggers/t:CalendarTrigger/t:ScheduleByWeek", ns))

    def test_task_names_are_stable_and_specific_to_folder(self):
        self.assertEqual(automation.task_identity(Path("/stocks/a")), automation.task_identity(Path("/stocks/a")))
        self.assertNotEqual(automation.task_identity(Path("/stocks/a")), automation.task_identity(Path("/stocks/b")))

    def test_foreign_task_is_never_overwritten_or_deleted(self):
        payload = f'<Task xmlns="{automation.NS}"><RegistrationInfo><Description>Other program</Description></RegistrationInfo></Task>'
        class Result:
            returncode = 0
            stdout = payload.encode()
        with patch.object(automation, "scheduler_command", return_value=Result()) as command:
            with self.assertRaisesRegex(RuntimeError, "another program"):
                automation.inspect_task(Path("/stocks"))
            self.assertEqual(command.call_count, 1)

    @unittest.skipUnless(os.name == "nt", "Windows Task Scheduler integration")
    def test_windows_register_verify_and_remove(self):
        with tempfile.TemporaryDirectory(prefix="stock scheduler test ") as temporary:
            root = Path(temporary)
            (root / "config.json").write_text(json.dumps({"stocks": ["AAPL"]}))
            try:
                self.assertEqual(automation.install(root, at="23:59", initial_run=False), 0)
                self.assertTrue(automation.inspect_task(root))
                # Reinstalling this folder updates its own task, without duplicates.
                self.assertEqual(automation.install(root, every_minutes=30, initial_run=False), 0)
            finally:
                automation.uninstall(root)
            self.assertFalse(automation.inspect_task(root))


if __name__ == "__main__":
    unittest.main()
