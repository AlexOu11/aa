"""Offline report viewer; no server or external scripts required."""

from html import escape


def render_html(data, status=None):
    status = status or {}
    esc = lambda value: escape(str(value), quote=True)
    demo = data.get("demo", False)
    title = "示範股票報告" if demo else "每日股票分析"
    banner = ""
    if demo:
        banner += '<aside>示範資料：以下為合成行情，不是真實市場報價。</aside>'
    if status.get("state") in ("failed", "partial"):
        banner += '<aside>最近一次更新失敗或有資料缺漏。請查看資料日期與 logs/update.log；下方可能保留上次報告。</aside>'
    if status.get("state") == "running":
        banner += '<aside>正在更新資料，下方暫時顯示上次完成的報告。</aside>'
    failures = data.get("failures", {})
    if failures:
        banner += '<aside>資料缺漏：' + "；".join(f"{esc(k)}：{esc(v)}" for k, v in failures.items()) + '</aside>'
    cards, rows = [], []
    for stock in data.get("stocks", []):
        positive = stock["change_pct"] >= 0
        color = "up" if positive else "down"
        cards.append(f'''<article><span>{esc(stock['symbol'])} · {esc(stock['currency'])}</span>
<h2>{stock['close']:,.2f}</h2><b class="{color}">{stock['change_pct']:+.2f}%</b>
<p>{esc(stock['trend'])} · 行情日期 {esc(stock['date'])}</p></article>''')
        values = [esc(stock["symbol"]), esc(stock["date"]), *[f"{stock[key]:.2f}" for key in
            ("sma20", "sma60", "rsi14", "macd", "macd_signal", "macd_histogram")]]
        rows.append('<tr>' + ''.join(f'<td>{v}</td>' for v in values) + '</tr>')
    return f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>{title}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#0b1220;color:#e4eaf5;font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1150px;margin:auto;padding:32px 20px}}header p,footer,article span{{color:#a8b7ce}}
h1{{margin:0;font-size:32px}}h2{{margin:10px 0;font-size:30px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}}
article,section{{background:#141f32;border:1px solid #2b3a52;border-radius:14px;padding:20px;margin:18px 0}}
aside{{padding:14px;background:#41331b;border:1px solid #b58a39;border-radius:8px;margin:14px 0}}
.up{{color:#fd9d9d}}.down{{color:#79dcb3}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}
th,td{{padding:12px;text-align:right;border-bottom:1px solid #2b3a52}}th:first-child,td:first-child{{text-align:left}}
.analysis{{white-space:pre-wrap;overflow-wrap:anywhere}}a{{color:#a4c5ff}}
</style></head><body><main><header><h1>{title}</h1>
<p>報告產生：{esc(data.get('generated_at', '尚未成功更新'))}<br>
最近更新嘗試：{esc(status.get('attempted_at', '—'))} · 狀態：{esc(status.get('state', '已產生報告'))}</p></header>
{banner}<div class="cards">{''.join(cards)}</div>
<section><h3>技術指標</h3><div class="scroll"><table><thead><tr>
<th>股票</th><th>資料日期</th><th>SMA20</th><th>SMA60</th><th>RSI14</th><th>MACD</th><th>訊號線</th><th>柱狀值</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section><h3>分析說明</h3><div class="analysis">{esc(data.get('summary', '尚無報告，請查看執行紀錄。'))}</div></section>
<footer>頁面每 60 秒重新載入檔案；行情由排程程式更新。行情日期與報告產生時間可能不同。<br>
Yahoo Finance 日線，優先使用調整後收盤價；可能含未收盤資料，並非即時行情。僅供研究，不構成投資建議。</footer>
</main></body></html>'''
