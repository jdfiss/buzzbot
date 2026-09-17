"""課堂錄音助理 — Milestone 1 + 2

Bot 收到錄音 → 存 pending/ → 依 COURSE_SELECTION_ENABLED 決定：
  - 開（預設）：問課程（inline keyboard）→ 使用者選課 → 搬進 courses/<course_id>/<date>/
    （轉錄尚未接這條路，之後要用再補）
  - 關（目前狀態）：不問課程，直接搬進 inbox/<date>/ → 呼叫 Buzz CLI 轉錄 → 逐字稿傳回 Telegram

STUDY/SURVIVAL Prompt 生成（AI_INPUT.md）依賴課程/mode，course selection 關閉期間先不做。
架構細節見 D:\\obsidian\\砸\\課堂錄音助理\\架構設計.md。
"""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PENDING_DIR = BASE_DIR / os.environ.get("PENDING_DIR", "pending")
COURSES_DIR = BASE_DIR / os.environ.get("COURSES_DIR", "courses")
INBOX_DIR = BASE_DIR / os.environ.get("INBOX_DIR", "inbox")
COURSES_YAML = BASE_DIR / os.environ.get("COURSES_YAML", "courses.yaml")
JOBS_FILE = BASE_DIR / "pending_jobs.json"

COURSE_SELECTION_ENABLED = os.environ.get("COURSE_SELECTION_ENABLED", "true").strip().lower() not in (
    "0",
    "false",
    "no",
)

# Buzz CLI（buzz/cli.py 的 `add` 子指令，2026-09-17 直接讀 v1.4.5 原始碼確認過旗標名稱）
BUZZ_EXE = Path(os.environ.get("BUZZ_EXE", r"D:\Buzz\Buzz.exe"))
BUZZ_MODEL_SIZE = os.environ.get("BUZZ_MODEL_SIZE", "medium")  # Buzz 預設是 tiny，畫質太差
BUZZ_LANGUAGE = os.environ.get("BUZZ_LANGUAGE", "zh")
BUZZ_TIMEOUT_SECONDS = int(os.environ.get("BUZZ_TIMEOUT_SECONDS", "7200"))

AUDIO_EXTENSIONS = (".m4a", ".mp3", ".wav", ".ogg", ".aac", ".flac", ".mp4")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logger = logging.getLogger("class_assistant")

jobs_lock = asyncio.Lock()


def load_courses() -> dict:
    with open(COURSES_YAML, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["courses"]


def load_jobs() -> dict:
    if not JOBS_FILE.exists():
        return {}
    with open(JOBS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_jobs(jobs: dict) -> None:
    tmp = JOBS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    tmp.replace(JOBS_FILE)


def next_job_id(jobs: dict) -> str:
    nums = [int(k.split("_")[1]) for k in jobs if k.startswith("job_")]
    return f"job_{max(nums, default=0) + 1:05d}"


def format_duration(seconds) -> str:
    if not seconds:
        return "未知長度"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def extract_audio_ref(message):
    """回傳 (file_id, file_name, duration_seconds) 或 None（不是錄音檔）。"""
    if message.voice:
        return message.voice.file_id, None, message.voice.duration
    if message.audio:
        return message.audio.file_id, message.audio.file_name, message.audio.duration
    if message.document:
        mime = message.document.mime_type or ""
        name = message.document.file_name or ""
        if mime.startswith("audio/") or name.lower().endswith(AUDIO_EXTENSIONS):
            return message.document.file_id, message.document.file_name, None
    return None


def build_course_keyboard(job_id: str, courses: dict) -> InlineKeyboardMarkup:
    rows, row = [], []
    for course_id, info in courses.items():
        row.append(
            InlineKeyboardButton(info["name"], callback_data=f"course:{course_id}:{job_id}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def transcribe_with_buzz(audio_path: Path, output_dir: Path, language: str) -> Path | None:
    """呼叫 Buzz CLI 轉錄，回傳產生的 .txt 逐字稿路徑，失敗回傳 None。

    Buzz 輸出檔名不是固定的（依 export template 帶時間戳記），所以用輸入檔名前綴去抓。
    """
    if not BUZZ_EXE.exists():
        logger.error("Buzz.exe 找不到：%s", BUZZ_EXE)
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(BUZZ_EXE),
        "add",
        "--hide-gui",
        "--txt",
        "--language",
        language,
        "--model-size",
        BUZZ_MODEL_SIZE,
        "--output-directory",
        str(output_dir),
        str(audio_path),
    ]
    logger.info("啟動 Buzz 轉錄：%s", audio_path.name)

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=BUZZ_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.error("Buzz 轉錄逾時（%ss）：%s", BUZZ_TIMEOUT_SECONDS, audio_path)
        return None

    if proc.returncode != 0:
        logger.error("Buzz 結束碼 %s：%s", proc.returncode, stderr.decode(errors="ignore"))
        return None

    matches = sorted(
        output_dir.glob(f"{audio_path.stem}*.txt"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not matches:
        logger.error("Buzz 結束但找不到輸出的 .txt：%s", output_dir)
        return None
    return matches[0]


def build_ai_input(transcript_text: str, original_filename, duration_seconds, date_str: str) -> str:
    """產生可以直接整份貼進 Gemini 的筆記素材（不分 STUDY/SURVIVAL，通用一版）。"""
    header = (
        f"# 課堂錄音筆記素材\n\n"
        f"- 錄音日期：{date_str}\n"
        f"- 長度：{format_duration(duration_seconds)}\n"
        f"- 原始檔名：{original_filename or '未知'}\n\n"
    )
    prompt = (
        "你是我的課堂筆記助理。以下是這堂課的逐字稿（Whisper 語音辨識產生，"
        "可能有錯字或同音字，請自行判斷修正明顯的辨識錯誤）。請幫我整理成清楚好讀的課堂筆記，包含：\n\n"
        "1. 重點摘要（條列這堂課教了什麼）\n"
        "2. 完整保留提到的技術內容、公式、定義、老師舉的例子\n"
        "3. 特別標出老師強調的重點、可能考試會考的地方\n"
        "4. 如果有提到作業、deadline、報告、分組等事項，另外列出來\n"
        "5. 如果內容偏少、資訊量不大，就給我精簡摘要就好，不用硬湊字數\n\n"
        "---逐字稿開始---\n"
        f"{transcript_text}\n"
        "---逐字稿結束---\n"
    )
    return header + prompt


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    ref = extract_audio_ref(message)
    if ref is None:
        await message.reply_text("看不出來這是錄音檔，麻煩直接分享 Voice Memo 的錄音（.m4a）。")
        return
    file_id, file_name, duration = ref
    ext = Path(file_name).suffix if file_name else ".m4a"
    if not ext:
        ext = ".m4a"

    async with jobs_lock:
        jobs = load_jobs()
        job_id = next_job_id(jobs)
        dest_path = PENDING_DIR / f"{job_id}{ext}"
        downloaded_at = datetime.now().isoformat(timespec="seconds")

        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(custom_path=str(dest_path))

        jobs[job_id] = {
            "job_id": job_id,
            "status": "awaiting_course",
            "telegram_chat_id": message.chat_id,
            "telegram_message_id": message.message_id,
            "downloaded_at": downloaded_at,
            "file_path": str(dest_path),
            "original_filename": file_name,
            "duration_seconds": duration,
            "course_id": None,
            "course_name": None,
            "mode": None,
        }

        if not COURSE_SELECTION_ENABLED:
            date_str = downloaded_at[:10]
            inbox_dir = INBOX_DIR / date_str
            inbox_dir.mkdir(parents=True, exist_ok=True)
            filed_path = inbox_dir / f"{job_id}{ext}"
            shutil.move(str(dest_path), str(filed_path))
            jobs[job_id].update(
                {
                    "status": "filed",
                    "filed_at": datetime.now().isoformat(timespec="seconds"),
                    "file_path": str(filed_path),
                }
            )

        save_jobs(jobs)

    if not COURSE_SELECTION_ENABLED:
        logger.info("filed %s -> %s (course selection disabled)", job_id, filed_path)
        await message.reply_text(
            f"📥 收到錄音（{format_duration(duration)}）\n已存到 {filed_path}\n開始轉錄..."
        )

        transcript_path = await transcribe_with_buzz(filed_path, filed_path.parent, BUZZ_LANGUAGE)

        ai_input_path = None
        if transcript_path:
            text = transcript_path.read_text(encoding="utf-8", errors="ignore")
            ai_input_path = filed_path.parent / f"{filed_path.stem}_AI_INPUT.md"
            ai_input_path.write_text(
                build_ai_input(text, file_name, duration, date_str), encoding="utf-8"
            )

        async with jobs_lock:
            jobs = load_jobs()
            if transcript_path:
                jobs[job_id].update(
                    {
                        "status": "transcribed",
                        "transcript_path": str(transcript_path),
                        "ai_input_path": str(ai_input_path),
                        "transcribed_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
            else:
                jobs[job_id]["status"] = "transcribe_failed"
            save_jobs(jobs)

        if transcript_path:
            preview = text[:300] + ("…" if len(text) > 300 else "")
            with open(transcript_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=message.chat_id,
                    document=f,
                    filename=transcript_path.name,
                    caption=f"✅ 轉錄完成\n{preview}",
                )
            with open(ai_input_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=message.chat_id,
                    document=f,
                    filename=ai_input_path.name,
                    caption="📎 這份可以整份直接貼給 Gemini 產生筆記",
                )
        else:
            await message.reply_text("⚠️ 轉錄失敗，檢查一下 Buzz 是否正常、模型是否下載好（log 裡有詳細錯誤）。")
        return

    logger.info("downloaded %s -> %s", job_id, dest_path)

    courses = load_courses()
    await message.reply_text(
        f"📥 收到錄音（{format_duration(duration)}）\n這是哪一門課？",
        reply_markup=build_course_keyboard(job_id, courses),
    )


async def handle_course_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, course_id, job_id = query.data.split(":", 2)
    except ValueError:
        return

    courses = load_courses()
    course = courses.get(course_id)
    if course is None:
        await query.edit_message_text("⚠️ 課程設定已變更，找不到這個課程了，麻煩重新傳一次錄音。")
        return

    async with jobs_lock:
        jobs = load_jobs()
        job = jobs.get(job_id)
        if job is None:
            await query.edit_message_text("⚠️ 找不到這筆錄音紀錄（可能已經處理過了）。")
            return
        if job["status"] != "awaiting_course":
            await query.edit_message_text(f"ℹ️ 這筆錄音已經標記為「{job.get('course_name', '?')}」了。")
            return

        src = Path(job["file_path"])
        date_str = job["downloaded_at"][:10]
        dest_dir = COURSES_DIR / course_id / date_str
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"audio{src.suffix or '.m4a'}"
        shutil.move(str(src), str(dest_path))

        job.update(
            {
                "status": "filed",
                "course_id": course_id,
                "course_name": course["name"],
                "mode": course["mode"],
                "filed_at": datetime.now().isoformat(timespec="seconds"),
                "file_path": str(dest_path),
            }
        )
        save_jobs(jobs)

        meta_path = dest_dir / "job.json"
        meta_path.write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "course": course["name"],
                    "mode": course["mode"],
                    "telegram_chat_id": job["telegram_chat_id"],
                    "telegram_message_id": job["telegram_message_id"],
                    "date": date_str,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    logger.info("filed %s -> %s (%s/%s)", job_id, dest_path, course_id, course["mode"])

    await query.edit_message_text(
        f"✅ 已標記：{course['name']}（{course['mode']}）\n"
        f"📁 已歸檔至 {dest_dir}\n"
        f"（轉錄尚未串接，下一步接 Buzz CLI）"
    )


async def resend_pending_prompts(application: Application) -> None:
    """Bot 重啟後，把還沒選課程的錄音重新推一次按鈕。"""
    async with jobs_lock:
        jobs = load_jobs()
    courses = load_courses()
    pending = [j for j in jobs.values() if j["status"] == "awaiting_course"]
    for job in pending:
        try:
            await application.bot.send_message(
                chat_id=job["telegram_chat_id"],
                text=f"🔄 還沒選課程的錄音（{format_duration(job.get('duration_seconds'))}）\n這是哪一門課？",
                reply_markup=build_course_keyboard(job["job_id"], courses),
            )
        except Exception:
            logger.exception("resend failed for %s", job["job_id"])
    if pending:
        logger.info("resent %d pending prompt(s)", len(pending))


def build_application() -> Application:
    token = os.environ["BOT_TOKEN"]
    api_base = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
    is_local = "api.telegram.org" not in api_base

    # Local Bot API Server 首次跟 Telegram 建連線（含 MTProto 交握）比 PTB 預設的 5 秒 timeout 久，
    # 拉長一點避免 getMe / 長輪詢誤判逾時。
    request = HTTPXRequest(connect_timeout=20.0, read_timeout=30.0, write_timeout=30.0, pool_timeout=20.0)
    get_updates_request = HTTPXRequest(connect_timeout=20.0, read_timeout=40.0, pool_timeout=20.0)

    builder = (
        ApplicationBuilder()
        .token(token)
        .request(request)
        .get_updates_request(get_updates_request)
        .post_init(resend_pending_prompts)
    )
    if is_local:
        builder = (
            builder.base_url(f"{api_base}/bot")
            .base_file_url(f"{api_base}/file/bot")
            .local_mode(True)
        )
        logger.info("using local Bot API server at %s", api_base)
    else:
        logger.warning(
            "TELEGRAM_API_BASE not set to a local server — file downloads are capped at 20MB "
            "by the official Bot API (see 架構設計.md)"
        )

    application = builder.build()
    application.add_handler(
        MessageHandler(filters.VOICE | filters.AUDIO | filters.Document.ALL, handle_audio)
    )
    application.add_handler(CallbackQueryHandler(handle_course_selection, pattern=r"^course:"))
    return application


def main() -> None:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    COURSES_DIR.mkdir(parents=True, exist_ok=True)
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    if not COURSE_SELECTION_ENABLED:
        logger.info("COURSE_SELECTION_ENABLED=false — 直接收進 %s，不問課程", INBOX_DIR)
    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
