"""Public daily prices and reproducible technical indicators."""

import json
import math
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def get_json(url, *, data=None, headers=None):
    request = Request(url, data=data, headers={"User-Agent": "StockDailyReport/1.0", **(headers or {})})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                # Do not expose response bodies or authorization headers in logs.
                raise RuntimeError(f"服務回應 HTTP {exc.code}") from None
        except (URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError("服務連線失敗或逾時") from None
        time.sleep(2 ** attempt)


def parse_chart(payload, symbol, now, max_age):
    chart = payload.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise ValueError("找不到股票行情")
    result = chart["result"][0]
    indicators = result.get("indicators", {})
    adjusted = indicators.get("adjclose") or []
    prices = adjusted[0].get("adjclose", []) if adjusted else indicators.get("quote", [{}])[0].get("close", [])
    observations = []
    for stamp, price in zip(result.get("timestamp", []), prices):
        if price is not None and math.isfinite(float(price)) and float(price) > 0:
            observations.append((int(stamp), float(price)))
    observations.sort()
    if len(observations) < 60:
        raise ValueError("有效日線不足 60 筆，無法計算指標")
    stamp = observations[-1][0]
    if stamp > now.timestamp() + 86400 or now.timestamp() - stamp > max_age * 86400:
        raise ValueError("行情日期過舊或異常，請檢查資料來源")
    meta = result.get("meta", {})
    return {
        "symbol": symbol,
        "currency": meta.get("currency", "未知"),
        "date": datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d"),
        "prices": [p for _, p in observations],
    }


def fetch_stock(symbol, now, max_age=7):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?range=6mo&interval=1d"
    return parse_chart(get_json(url), symbol, now, max_age)


def ema(values, period):
    alpha = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def analyze(stock):
    prices = stock["prices"]
    deltas = [b - a for a, b in zip(prices, prices[1:])]
    gain = sum(max(d, 0) for d in deltas[:14]) / 14
    loss = sum(max(-d, 0) for d in deltas[:14]) / 14
    for delta in deltas[14:]:
        gain = (gain * 13 + max(delta, 0)) / 14
        loss = (loss * 13 + max(-delta, 0)) / 14
    rsi = 50.0 if gain == loss == 0 else (100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    macd = [a - b for a, b in zip(ema(prices, 12), ema(prices, 26))]
    signal = ema(macd, 9)[-1]
    sma20, sma60 = sum(prices[-20:]) / 20, sum(prices[-60:]) / 60
    return {
        **{key: stock[key] for key in ("symbol", "currency", "date")},
        "close": prices[-1],
        "change_pct": (prices[-1] / prices[-2] - 1) * 100,
        "sma20": sma20,
        "sma60": sma60,
        "rsi14": rsi,
        "macd": macd[-1],
        "macd_signal": signal,
        "macd_histogram": macd[-1] - signal,
        "trend": "偏強" if prices[-1] > sma20 > sma60 else "偏弱" if prices[-1] < sma20 < sma60 else "盤整／訊號分歧",
    }


def demo_stock(symbol, now):
    # Deterministic fictional prices: never present these as market observations.
    return {"symbol": symbol, "currency": "DEMO", "date": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
            "prices": [100 + i * 0.2 + math.sin(i / 4) * 2 for i in range(120)]}
