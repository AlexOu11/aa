"""CLI orchestration, optional AI summary, and explicit SMTP delivery."""

import argparse
import html
import json
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

from .market import analyze, demo_stock, fetch_stock, get_json

PROVIDERS = {
    "openai": ("https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "GEMINI_API_KEY"),
    "deepseek": ("https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY"),
    "grok": ("https://api.x.ai/v1/chat/completions", "XAI_API_KEY"),
}


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("設定檔需為 JSON 物件")
    stocks = config.get("stocks")
    if not isinstance(stocks, list) or not 1 <= len(stocks) <= 30 or any(
        not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9^][A-Za-z0-9.^=-]{0,24}", s) for s in stocks
    ):
        raise ValueError("stocks 需為 1–30 個有效股票代號")
    if len(set(stocks)) != len(stocks):
        raise ValueError("stocks 不可包含重複代號")
    if config.get("ai_provider", "none") not in ("none", *PROVIDERS):
        raise ValueError("不支援的 ai_provider")
    if config.get("ai_provider", "none") != "none" and (
        not isinstance(config.get("ai_model"), str) or not config["ai_model"].strip()
    ):
        raise ValueError("啟用 AI 時需指定 ai_model")
    age = config.get("max_data_age_days", 7)
    if type(age) is not int or not 1 <= age <= 30:
        raise ValueError("max_data_age_days 需為 1–30 的整數")
    ZoneInfo(config.get("timezone", "Asia/Taipei"))
    return config


def ai_summary(config, results):
    provider = config.get("ai_provider", "none")
    if provider == "none":
        return "未啟用 AI；上方為根據行情計算的技術指標。"
    url, key_name = PROVIDERS[provider]
    key = os.environ.get(key_name)
    if not key:
        raise ValueError(f"缺少 {key_name}")
    payload = {
        "model": config["ai_model"],
        "messages": [
            {"role": "system", "content": "你是審慎的股票研究助手。以繁體中文解讀提供的技術指標，說明訊號分歧與風險。不得捏造新聞、基本面、即時報價或報酬保證；不提供個人化買賣建議。限制在 600 字內。資料是日線，可能包含尚未收盤資料。"},
            {"role": "user", "content": json.dumps(results, ensure_ascii=False)},
        ],
    }
    response = get_json(url, data=json.dumps(payload).encode(), headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
    })
    content = response["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("AI 回傳空白內容")
    return content


def render_report(now, results, failures, summary, demo):
    lines = [f"# {'【示範資料】' if demo else ''}每日股票分析報告", "", f"產生時間：{now.isoformat(timespec='seconds')}", "",
             "資料來源：合成示範資料" if demo else "資料來源：Yahoo Finance 日線。指標以調整後收盤價計算（若無則用收盤價）；當日資料可能尚未收盤，並非即時交易訊號。", "",
             "| 股票 | 資料日期 | 幣別 | 調整收盤 | 日漲跌% | SMA20 | SMA60 | RSI14 | MACD | 訊號線 | 柱狀值 | 趨勢 |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in results:
        values = [r["symbol"], r["date"], r["currency"], *[f"{r[k]:.2f}" for k in (
            "close", "change_pct", "sma20", "sma60", "rsi14", "macd", "macd_signal", "macd_histogram")], r["trend"]]
        lines.append("| " + " | ".join(str(v).replace("|", "/") for v in values) + " |")
    if failures:
        lines += ["", "## 資料取得失敗（本報告不包含以下股票）", "", *[f"- {symbol}：{reason}" for symbol, reason in failures.items()]]
    lines += ["", "## AI 分析", "", summary, "", "僅供研究與學習，不構成投資建議。", ""]
    return "\n".join(lines)


def email_settings():
    required = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError("寄信缺少設定：" + ", ".join(missing))
    settings = {name: os.environ[name] for name in required}
    settings["port"] = int(os.environ.get("SMTP_PORT", "465"))
    if settings["port"] not in (465, 587):
        raise ValueError("SMTP_PORT 僅支援 TLS 465 或 STARTTLS 587")
    recipients = [item.strip() for item in settings["EMAIL_TO"].split(",")]
    for address in [settings["EMAIL_FROM"], *recipients]:
        if "\n" in address or "\r" in address or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+", address) or parseaddr(address)[1] != address:
            raise ValueError("Email 需為純郵件地址；多位收件者以逗號分隔")
    settings["recipients"] = recipients
    return settings


def send_email(settings, report, subject):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings["EMAIL_FROM"]
    message["To"] = ", ".join(settings["recipients"])
    message.set_content(report)
    message.add_alternative(f'<html><body><pre style="white-space:pre-wrap">{html.escape(report)}</pre></body></html>', subtype="html")
    context = ssl.create_default_context()
    if settings["port"] == 465:
        client = smtplib.SMTP_SSL(settings["SMTP_HOST"], 465, context=context, timeout=30)
    else:
        client = smtplib.SMTP(settings["SMTP_HOST"], 587, timeout=30)
    with client as connection:
        if settings["port"] == 587:
            connection.starttls(context=context)
        connection.login(settings["SMTP_USERNAME"], settings["SMTP_PASSWORD"])
        refused = connection.send_message(message, from_addr=settings["EMAIL_FROM"], to_addrs=settings["recipients"])
        if refused:
            raise RuntimeError("部分收件者被郵件伺服器拒絕；請檢查寄送紀錄後再重試")


def run(args):
    config = load_config(args.config)
    if args.demo and args.send_email:
        raise ValueError("示範模式不可寄送 Email")
    settings = email_settings() if args.send_email else None
    now = datetime.now(ZoneInfo(config.get("timezone", "Asia/Taipei")))
    results, failures = [], {}
    for symbol in config["stocks"]:
        try:
            stock = demo_stock(symbol, now) if args.demo else fetch_stock(symbol, now, config.get("max_data_age_days", 7))
            results.append(analyze(stock))
        except (ValueError, RuntimeError, KeyError, TypeError, IndexError, AttributeError) as exc:
            failures[symbol] = str(exc) if type(exc) is RuntimeError else "行情格式或日期驗證失敗"
    if not results:
        raise RuntimeError("全部股票行情取得失敗；未產生或寄送空白報告")
    degraded = False
    try:
        summary = "示範模式未呼叫 AI；此報告使用合成價格，僅供驗證流程。" if args.demo else ai_summary(config, results)
    except (ValueError, RuntimeError, KeyError, TypeError, IndexError, AttributeError):
        summary = "AI 分析失敗或缺少金鑰；本次僅提供技術指標。請檢查供應商設定與 API 額度。"
        degraded = True
    report = render_report(now, results, failures, summary, args.demo)
    output = Path(args.output or ("reports/demo" if args.demo else "reports"))
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.md").write_text(report, encoding="utf-8")
    (output / "report.json").write_text(json.dumps({"generated_at": now.isoformat(), "demo": args.demo,
        "stocks": results, "failures": failures, "ai_degraded": degraded, "summary": summary}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已產生報告：成功 {len(results)} 支，失敗 {len(failures)} 支；AI 降級：{degraded}")
    if settings:
        send_email(settings, report, f"{'【部分資料缺漏】' if failures or degraded else ''}每日股票分析 {now:%Y-%m-%d}")
        print("Email 已交付 SMTP 伺服器")
    # Preserve a failing CI signal while delivering useful partial reports.
    return 2 if failures or degraded else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="每日股票分析與 Email 通知")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--output", help="輸出目錄；真實報告預設 reports，示範報告預設 reports/demo")
    parser.add_argument("--demo", action="store_true", help="使用合成行情，離線驗證；不呼叫 AI 或寄信")
    parser.add_argument("--send-email", action="store_true", help="實際透過 SMTP 寄送報告")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, smtplib.SMTPException) as exc:
        # Library errors can contain server responses; log only known-safe errors.
        detail = str(exc) if type(exc) in (ValueError, RuntimeError) else type(exc).__name__
        print(f"執行失敗：{detail}", file=sys.stderr)
        return 1
