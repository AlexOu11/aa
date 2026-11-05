# 台股與美股每日分析系統

[![Tests](https://github.com/AlexOu11/aa/actions/workflows/test.yml/badge.svg)](https://github.com/AlexOu11/aa/actions/workflows/test.yml)
[![Daily stock report](https://github.com/AlexOu11/aa/actions/workflows/daily-report.yml/badge.svg)](https://github.com/AlexOu11/aa/actions/workflows/daily-report.yml)

**[開啟每日報告與執行紀錄](https://github.com/AlexOu11/aa/actions/workflows/daily-report.yml)**：點選最新完成的執行，即可在 Summary 閱讀報告，或從 Artifacts 下載 Markdown／JSON。

Python 3.12 標準函式庫實作，不需要 Colab、額外 Python 套件或常駐伺服器。
流程：Yahoo Finance 日線 → SMA／RSI／MACD → 選用 AI 解讀 → Markdown／JSON 報告 → Gmail／SMTP Email。
GitHub Actions 預設在**台北時間週一至週五 08:00**執行。

## 本機執行

在儲存庫根目錄執行：

```bash
python -m unittest discover -s tests -v
python -m stock_report --demo       # 完全離線，合成資料，不呼叫 AI、不寄信
python -m stock_report              # 真實行情，產生 reports/report.md 和 report.json
python -m stock_report --send-email # 真實行情並實際寄信，需先設定 SMTP 環境變數
```

示範報告預設儲存於 `reports/demo/`，真實報告預設於 `reports/`；可用 `--output` 指定其他目錄。
每次成功產生報告後覆寫該目錄內的 report.md／report.json；若取得行情失敗，舊檔可能仍在，請確認產生時間及 `demo` 欄位。
Email 預設關閉，只有 `--send-email` 開啟。
GitHub 平日排程會在 SMTP Secrets 齊全時自動寄信；缺少設定時只產生報告並顯示警告。
推送 main 也會產生報告，但不寄信。手動執行預設不寄信；若勾選寄信但設定不足，該次執行會失敗。示範模式禁止寄信。

## 股票與 AI 設定

修改 `config.json`：

- `stocks`：預設 `2330.TW`、`0050.TW`、`AAPL`、`MSFT`；台灣上市股用 `.TW`，上櫃股通常用 `.TWO`。最多 30 支。
- `timezone`：報告時間預設 `Asia/Taipei`。**不會改變 Actions 的 cron 排程**；調整排程請修改 workflow。
- `ai_provider`：`none`、`openai`、`gemini`、`deepseek` 或 `grok`。預設 `none`，先驗證行情，尚未啟用 AI。
- `ai_model`：啟用 AI 時指定該供應商帳戶可使用的模型；預設 OpenAI 的 `gpt-4.1-mini`。更換供應商也必須更換模型名稱。
- `max_data_age_days`：最新有效資料可距今幾天，預設 7，容許週末與休市；長假可能需要調整。

| AI 供應商 | 所需 GitHub Secret／本機環境變數 | API 網域 |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `api.openai.com` |
| Gemini | `GEMINI_API_KEY` | `generativelanguage.googleapis.com` |
| DeepSeek | `DEEPSEEK_API_KEY` | `api.deepseek.com` |
| Grok | `XAI_API_KEY` | `api.x.ai` |

只需設定選用的供應商，不必準備四組金鑰。AI 會收到股票代號及計算出的指標，不會收到郵件帳密。
API 可能產生費用。模型可用性依供應商與帳戶而異。

## 啟用 GitHub 每日通知

1. 將程式提交至 GitHub 預設分支（通常是 main），並啟用 GitHub Actions。
2. 到儲存庫 **Settings → Secrets and variables → Actions** 設定下表的 Secrets。請勿將密碼或 API Key 寫進程式、設定檔、聊天訊息或 Git。
3. 若需 AI，設定選用供應商的 Secret，並調整 `config.json` 的 `ai_provider` 和 `ai_model`。
4. 在 **Actions → Daily stock report → Run workflow**，先保持 `send_email=false` 測試真實行情，再勾選寄信測試。
5. 從該次執行的 Summary 閱讀報告，或從 Artifacts 下載報告；確認 Email 收到後，平日排程會使用同一設定。

| GitHub Secret | 用途 |
|---|---|
| `SMTP_USERNAME` | SMTP 登入帳號，Gmail 為完整信箱 |
| `SMTP_PASSWORD` | Gmail 應用程式密碼，或其他 SMTP 服務專用密碼 |
| `EMAIL_FROM` | 寄件地址，Gmail 通常與 SMTP_USERNAME 相同 |
| `EMAIL_TO` | 收件地址，多人以逗號分隔 |

GitHub **Variables** 可設定 `SMTP_HOST`（預設 `smtp.gmail.com`）、`SMTP_PORT`（預設 `465`，也支援 `587` 的 STARTTLS）。
使用 Gmail 時，需先開啟兩步驟驗證並建立「應用程式密碼」；部分組織帳號可能不允許使用。
程式不讀取 `.env`；本機請透過 shell 或密碼管理工具注入環境變數。

排程採 UTC `0 0 * * 1-5`。GitHub Actions 排程可能延遲，尤其整點；它不是準點保證服務。
排程僅執行預設分支，公開儲存庫長期無活動時可能被 GitHub 停用，需在 Actions 重新啟用。
平日 08:00 的報告通常包含台股前一交易日及美股最近交易日資料；各市場假日不同，請看每列資料日期。
此版本按平日寄送，並未以交易所行事曆跳過假日。

## 指標、失敗處理與限制

- SMA20／SMA60 為最近 20／60 筆有效日線的平均值。
- RSI14 使用 Wilder 平滑；MACD 為 EMA12 − EMA26，訊號線為 EMA9，EMA 以首筆價格初始化。不同平台的歷史長度可能造成小幅差異。
- 優先使用調整後收盤價，因此報告價格可能與未調整的成交價格不同。不含新聞、基本面、持倉或自動下單。
- Yahoo Finance 公開 chart 端點無服務保證，可能限流或變動；僅取六個月資料，至少需 60 筆有效日線。當日未收盤資料可能被包含。
- 部分股票失敗或 AI 失敗時，仍輸出／寄送其餘技術報告，標示缺漏，並以退出碼 `2` 讓 Actions 顯示失敗。AI 失敗不會被冒充為成功分析。
- 全部股票失敗、SMTP 缺設定或寄送失敗時退出碼為 `1`。全部行情失敗不會寄空白報告。
- HTTPS 與 SMTP TLS 驗證保持開啟。HTTP 暫時性失敗最多重試三次；SMTP 不自動重試，以降低重複寄信風險。
- SMTP 接受郵件不等於收件匣成功投遞；需自行確認垃圾信件匣。手動重跑仍可能重複寄信，本版沒有跨執行去重儲存。
- Actions 報告保存 7 天，儲存庫可存取者可能下載，請依需求調整存取權限。

受限制的雲端環境需允許 `query1.finance.yahoo.com` 及所選 AI 網域。寄信需環境支援至 SMTP 主機的 TCP 465／587；一般 HTTPS 網域白名單不等於 SMTP TCP 權限。GitHub 託管 runner 與本機雲端的網路政策各自獨立。

此工具用於研究與學習，不構成投資建議。
