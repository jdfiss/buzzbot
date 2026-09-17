# 課堂錄音助理

用 Telegram Bot 收錄音檔，自動存檔、（可選）依課程分類，再丟給 [Buzz](https://github.com/chidiwilliams/buzz)
（本機 Whisper GUI/CLI）轉錄成逐字稿，順便產生一份可以直接貼給 AI 整理筆記的 prompt。

用手機把上課錄音傳給你自己的 Bot，桌機那邊就會自動存檔、轉錄、把逐字稿傳回來。

## 功能

- 傳 Voice Memo / 音檔給 Bot，自動下載存檔（`pending/`）
- 兩種模式（用 `.env` 的 `COURSE_SELECTION_ENABLED` 切換）：
  - **開（預設）**：Bot 用 inline keyboard 問「這是哪一門課」，選完歸檔到
    `courses/<course_id>/<date>/`，並寫一份 `job.json` 記錄課程/模式
  - **關**：不問課程，直接歸檔到 `inbox/<date>/`，並馬上呼叫 Buzz CLI 轉錄，
    完成後把逐字稿 `.txt` 和一份整理好的 `_AI_INPUT.md`（可直接貼給 ChatGPT/Gemini 產生課堂筆記）
    透過 Telegram 傳回來
- Bot 重啟時，會自動把還沒選課程的錄音重新推播一次按鈕，不會漏處理
- 用自架的 **Local Bot API Server**，繞過官方 Bot API 20MB 的下載檔案上限
  （課堂錄音檔很容易超過）

轉錄跟課程分類目前是兩條獨立路徑（詳見〈已知限制〉），還沒完全接在一起。

## 系統需求

- Windows（`start.ps1` / `start.bat` 是寫給 Windows 的；Local Bot API Server 也是抓 Windows 預編譯版）
- Python 3.10+
- 一個 Telegram 帳號（申請 Bot 用）
- [Buzz](https://github.com/chidiwilliams/buzz)（要自己另外安裝，用來做本機語音轉文字；
  課程分類功能本身不需要它，只有「不分課程直接轉錄」模式會用到）

## 安裝

```powershell
git clone <這個 repo 的網址>
cd buzz
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
```

## 設定（第一次使用才需要做）

以下幾步都要用「你自己的」帳號/金鑰，沒辦法幫你代做。

1. **拿 Bot Token**：Telegram 找 [@BotFather](https://t.me/BotFather) → `/newbot`，
   照指示取名字，拿到一組 Token。
2. **申請 `api_id` / `api_hash`**：登入 <https://my.telegram.org> → API development tools，
   建一個 App 拿到這兩個值。跟 Bot Token 是兩回事，兩個都要留著。
3. **架 Local Bot API Server**（官方 Bot API 下載檔案上限 20MB，錄音檔常常超過，
   所以要自架一個本機版本繞過限制）：
   - 官方原始碼：<https://github.com/tdlib/telegram-bot-api>（沒有 Windows 預編譯檔，要自己 build）
   - 第三方 Windows 預編譯版（省掉 build 流程）：<https://github.com/SwissCore92/telegram-bot-api-bin>
   - 下載後整包解壓到這個專案的 `bot-api-server/` 資料夾（`telegram-bot-api.exe` 要直接在
     `bot-api-server\telegram-bot-api.exe`，`start.ps1` 是照這個路徑找的）
4. **登出官方 API**（換到 local server 前必做，不然可能收不到完整 update）：
   ```
   curl https://api.telegram.org/bot<你的TOKEN>/logOut
   ```
5. **設定 `.env`**：複製 `.env.example` 成 `.env`，照裡面的註解填：
   - `BOT_TOKEN`：步驟 1 拿到的
   - `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`：步驟 2 拿到的
   - `TELEGRAM_API_BASE`：本機 Local Bot API Server 的位址，預設 `http://localhost:8081` 免改
   - 如果要用轉錄功能，把 `COURSE_SELECTION_ENABLED` 設成 `false`，並把 `BUZZ_EXE`
     指到你安裝 Buzz 的實際路徑
6. **設定課程清單**：編輯 `courses.yaml`，每一門課給一個 `course_id`（英數字，會拿來當資料夾名稱，
   避免中文路徑編碼問題）、顯示名稱 `name`、跟複習模式 `mode`（自己定義，例如 `STUDY` / `SURVIVAL`）。
   `COURSE_SELECTION_ENABLED=false` 時這份設定不會用到。

## 執行

雙擊 `start.bat`（或直接跑 `start.ps1`）：會先啟動 Local Bot API Server，等它就緒後
再啟動 Python 主程式；視窗關掉（或 Ctrl+C）時兩個一起收掉。

```
start.bat
```

或手動分開跑：

```powershell
.venv\Scripts\python.exe class_assistant.py
```

（手動跑的話要自己先把 Local Bot API Server 開起來，不然檔案下載會受 20MB 限制、且 log
會出現警告。）

## 使用方式

1. 用手機 Telegram 傳一段錄音（Voice Memo 分享 / 音檔）給你的 Bot
2. `COURSE_SELECTION_ENABLED=true`：Bot 回傳課程按鈕，點你要歸檔的課程，
   完成後檔案在 `courses/<course_id>/<日期>/audio.<副檔名>`
3. `COURSE_SELECTION_ENABLED=false`：Bot 直接開始轉錄，完成後把逐字稿 `.txt` 跟
   `_AI_INPUT.md` 傳回 Telegram（`_AI_INPUT.md` 可以整份複製貼上給 AI 產生課堂筆記）

## 目錄結構

```
class_assistant.py     ← 主程式
courses.yaml           ← 課程清單設定
.env                    ← 你的機密設定（不會進版控）
pending_jobs.json      ← 所有錄音 job 的狀態紀錄
pending/job_00031.m4a  ← 已下載、還沒選課程的暫存檔
courses/OS/2026-09-17/
  audio.m4a
  job.json             ← 這筆錄音的課程/模式/來源訊息
inbox/2026-09-17/       ← COURSE_SELECTION_ENABLED=false 時的收件夾，含轉錄產物
bot-api-server/         ← 你自己下載解壓的 Local Bot API Server（不進版控）
```

## 疑難排解

| 症狀 | 可能原因 |
| --- | --- |
| 啟動時說缺少 `BOT_TOKEN` 等變數 | `.env` 沒設好，對照 `.env.example` 補上 |
| `Local Bot API Server 10 秒內沒有起來` | `api_id`/`api_hash` 填錯，或忘記對舊 Bot 做 `logOut`（見設定步驟 4） |
| log 印出 `TELEGRAM_API_BASE not set to a local server` | `.env` 的 `TELEGRAM_API_BASE` 沒指向 local server，檔案下載會被 20MB 上限卡住 |
| 傳錄音後 Bot 回「轉錄失敗」 | 確認 `BUZZ_EXE` 路徑正確、Buzz 已安裝且對應的 Whisper 模型已下載過一次 |
| Bot 收不到任何訊息 | 確認 Local Bot API Server 真的在跑（看 `bot-api-server\data\server.log`），以及步驟 4 的 `logOut` 有做 |

## 已知限制 / 待做

- [x] 收音檔、存 `pending/`、寫 `pending_jobs.json`
- [x] Inline keyboard 動態從 `courses.yaml` 生成、選課後歸檔
- [x] Bot 重啟時重新推播還沒選課的錄音
- [x] `COURSE_SELECTION_ENABLED=false` 時直接轉錄並回傳逐字稿 + AI 筆記素材
- [ ] 課程分類模式（`true`）目前還沒接轉錄，選完課只會歸檔，不會自動轉錄
- [ ] STUDY / SURVIVAL 兩種模式目前只是分類標籤，還沒有各自套用不同的筆記 prompt
- [ ] Local Bot API Server 的開機自動啟動、掛了自動重啟（目前設計是用量低，手動雙擊
      `start.bat` 就好，需要更穩定的話可以自己接 Windows 工作排程器）

## 用到的第三方元件

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)、
  PyYAML、python-dotenv（`requirements.txt`，pip 安裝，不含在這個 repo 裡）
- [tdlib/telegram-bot-api](https://github.com/tdlib/telegram-bot-api)（Local Bot API Server，
  Boost Software License，需自行下載，不含在這個 repo 裡，見〈設定〉步驟 3）
- [Buzz](https://github.com/chidiwilliams/buzz)（本機語音轉文字，需自行安裝，不含在這個 repo 裡）
