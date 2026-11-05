import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from stock_report import app, market

NOW = datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)


def chart(prices, latest=NOW):
    stamps = [int((latest - timedelta(days=len(prices) - 1 - i)).timestamp()) for i in range(len(prices))]
    return {"chart": {"result": [{"meta": {"currency": "USD"}, "timestamp": stamps,
        "indicators": {"adjclose": [{"adjclose": prices}]}}], "error": None}}


class MarketTests(unittest.TestCase):
    def test_adjusted_close_and_missing_observation(self):
        data = chart([float(i) for i in range(1, 72)] + [None])
        stock = market.parse_chart(data, "AAPL", NOW, 7)
        self.assertEqual(stock["prices"][-1], 71)
        self.assertEqual(stock["date"], "2026-11-04")

    def test_insufficient_or_stale_data_rejected(self):
        for data in (chart([100] * 59), chart([100] * 80, NOW - timedelta(days=10)),
                     chart([100] * 80, NOW + timedelta(days=3)), {"chart": {"error": {"code": "Not Found"}}}):
            with self.subTest(data=str(data)[:40]), self.assertRaises(ValueError):
                market.parse_chart(data, "AAPL", NOW, 7)

    def test_invalid_prices_filtered(self):
        with self.assertRaises(ValueError):
            market.parse_chart(chart([100] * 59 + [float("nan"), float("inf"), 0, -1]), "AAPL", NOW, 7)

    def test_increasing_series_known_indicators(self):
        stock = {"symbol": "AAPL", "currency": "USD", "date": "2026-11-05", "prices": list(range(1, 121))}
        result = market.analyze(stock)
        self.assertEqual(result["sma20"], 110.5)
        self.assertEqual(result["sma60"], 90.5)
        self.assertEqual(result["rsi14"], 100)
        self.assertEqual(result["trend"], "偏強")
        self.assertAlmostEqual(result["macd"], 7, delta=0.01)
        self.assertAlmostEqual(result["macd_histogram"], 0, delta=0.01)

    def test_flat_and_decreasing_rsi(self):
        for prices, expected in (([100] * 80, 50), (list(range(120, 0, -1)), 0)):
            result = market.analyze({"symbol": "X", "currency": "USD", "date": "2026-11-05", "prices": prices})
            self.assertEqual(result["rsi14"], expected)

    @patch("stock_report.market.time.sleep")
    @patch("stock_report.market.urlopen")
    def test_rate_limit_retries(self, request, sleep):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        request.side_effect = [HTTPError("https://example.test", 429, "rate limited", {}, None), response]
        self.assertEqual(market.get_json("https://example.test"), {"ok": True})
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch("stock_report.market.time.sleep")
    @patch("stock_report.market.urlopen", side_effect=URLError("private response"))
    def test_network_error_redacted(self, request, sleep):
        with self.assertRaisesRegex(RuntimeError, "服務連線失敗"):
            market.get_json("https://example.test")
        self.assertEqual(request.call_count, 3)


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"stocks": ["2330.TW", "AAPL"], "ai_provider": "none"}), encoding="utf-8")
        self.args = ["--config", str(self.config), "--output", str(self.root / "out")]

    def test_demo_cli_creates_inspectable_outputs(self):
        with patch.object(app, "fetch_stock") as fetch, patch.object(app, "send_email") as mail:
            self.assertEqual(app.main(self.args + ["--demo"]), 0)
            fetch.assert_not_called()
            mail.assert_not_called()
        data = json.loads((self.root / "out/report.json").read_text())
        self.assertTrue(data["demo"])
        self.assertEqual(len(data["stocks"]), 2)
        self.assertIn("【示範資料】", (self.root / "out/report.md").read_text())

    def test_demo_cannot_send(self):
        self.assertEqual(app.main(self.args + ["--demo", "--send-email"]), 1)

    @patch.object(app, "fetch_stock", side_effect=RuntimeError("failed"))
    @patch.object(app, "send_email")
    def test_all_fail_no_report_or_email(self, mail, fetch):
        self.assertEqual(app.main(self.args), 1)
        self.assertFalse((self.root / "out").exists())
        mail.assert_not_called()

    def test_partial_data_generates_report_and_failure_status(self):
        with patch.object(app, "fetch_stock", side_effect=[market.demo_stock("2330.TW", NOW), ValueError("bad symbol")]):
            self.assertEqual(app.main(self.args), 2)
        data = json.loads((self.root / "out/report.json").read_text())
        self.assertIn("AAPL", data["failures"])
        self.assertEqual(len(data["stocks"]), 1)

    def test_ai_failure_still_saves_report_and_returns_failure(self):
        with patch.object(app, "fetch_stock", side_effect=lambda symbol, *args: market.demo_stock(symbol, NOW)), \
             patch.object(app, "ai_summary", side_effect=RuntimeError("do not leak")):
            self.assertEqual(app.main(self.args), 2)
        data = json.loads((self.root / "out/report.json").read_text())
        self.assertTrue(data["ai_degraded"])
        self.assertNotIn("do not leak", data["summary"])

    def test_missing_email_settings_prevents_network_work(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(app, "fetch_stock") as fetch:
            self.assertEqual(app.main(self.args + ["--send-email"]), 1)
            fetch.assert_not_called()

    def test_config_rejects_unsafe_symbols(self):
        self.config.write_text(json.dumps({"stocks": ["../x"]}))
        self.assertEqual(app.main(self.args), 1)

    def test_full_pipeline_delivers_generated_report(self):
        settings = {"SMTP_HOST": "smtp.example.test", "SMTP_USERNAME": "sender@example.test",
                    "SMTP_PASSWORD": "test-only", "EMAIL_FROM": "sender@example.test", "EMAIL_TO": "reader@example.test"}
        with patch.dict(os.environ, settings, clear=True), \
             patch.object(app, "fetch_stock", side_effect=lambda symbol, *args: market.demo_stock(symbol, NOW)), \
             patch.object(app, "send_email") as mail:
            self.assertEqual(app.main(self.args + ["--send-email"]), 0)
            mail.assert_called_once()
            self.assertIn("2330.TW", mail.call_args.args[1])
            self.assertIn("AAPL", mail.call_args.args[1])
        data = json.loads((self.root / "out/report.json").read_text())
        self.assertFalse(data["demo"])
        self.assertFalse(data["failures"])

    def test_config_rejects_non_object(self):
        self.config.write_text('[]')
        self.assertEqual(app.main(self.args), 1)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-only-placeholder"})
    @patch.object(app, "get_json", return_value={"choices": [{"message": {"content": "市場分析"}}]})
    def test_ai_uses_selected_provider_and_model(self, request):
        text = app.ai_summary({"ai_provider": "openai", "ai_model": "gpt-4.1-mini"}, [{"symbol": "AAPL"}])
        self.assertEqual(text, "市場分析")
        self.assertEqual(request.call_args.args[0], app.PROVIDERS["openai"][0])
        payload = json.loads(request.call_args.kwargs["data"])
        self.assertEqual(payload["model"], "gpt-4.1-mini")


class EmailTests(unittest.TestCase):
    def settings(self, port):
        return {"SMTP_HOST": "smtp.example.test", "SMTP_USERNAME": "user", "SMTP_PASSWORD": "test-only",
                "EMAIL_FROM": "sender@example.test", "recipients": ["reader@example.test"], "port": port}

    @patch.object(app.smtplib, "SMTP_SSL")
    def test_ssl_message_has_escaped_html_and_plain_text(self, smtp):
        smtp.return_value.__enter__.return_value.send_message.return_value = {}
        app.send_email(self.settings(465), "<script>test</script>", "Report")
        client = smtp.return_value.__enter__.return_value
        message = client.send_message.call_args.args[0]
        self.assertIn("&lt;script&gt;", message.get_body(preferencelist=("html",)).get_content())
        self.assertIn("<script>", message.get_body(preferencelist=("plain",)).get_content())
        self.assertTrue(smtp.call_args.kwargs["context"].check_hostname)

    @patch.object(app.smtplib, "SMTP")
    def test_starttls_before_login(self, smtp):
        client = smtp.return_value.__enter__.return_value
        client.send_message.return_value = {}
        app.send_email(self.settings(587), "report", "Report")
        self.assertEqual([call[0] for call in client.method_calls], ["starttls", "login", "send_message"])

    @patch.object(app.smtplib, "SMTP_SSL")
    def test_partial_refusal_is_failure(self, smtp):
        smtp.return_value.__enter__.return_value.send_message.return_value = {"reader@example.test": (550, b"rejected")}
        with self.assertRaises(RuntimeError):
            app.send_email(self.settings(465), "report", "Report")


if __name__ == "__main__":
    unittest.main()
