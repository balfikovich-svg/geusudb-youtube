import time
import os
import logging
import glob
import requests
import yt_dlp

# ─────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────

TOKEN = os.environ.get("BOT_TOKEN", "")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set")

API          = f"https://api.telegram.org/bot{TOKEN}"
DOWNLOAD_DIR = "/tmp/yt_downloads"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# chat_id → {"videos": [...], "mode": "youtube"|"test", "query": str}
user_state: dict = {}

# ─────────────────────────────────────────
#  TELEGRAM HELPERS
# ─────────────────────────────────────────

def get_updates(offset=None):
    try:
        r = requests.get(
            f"{API}/getUpdates",
            params={"timeout": 30, "offset": offset},
            timeout=35
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"get_updates error: {e}")
        return {}


def send_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    # Pass reply_markup as dict — json= serializes the whole payload once
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(f"{API}/sendMessage", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        log.warning(f"send_message error: {e}")
        return {}


def edit_message(chat_id, message_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(f"{API}/editMessageText", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        log.warning(f"edit_message error: {e}")
        return {}


def answer_callback(callback_id, text=""):
    try:
        requests.post(
            f"{API}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
            timeout=5
        )
    except Exception as e:
        log.warning(f"answer_callback error: {e}")


def send_audio(chat_id, file_path, title):
    """Send MP3 via multipart/form-data (correct way for binary files)."""
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                f"{API}/sendAudio",
                data={"chat_id": str(chat_id), "title": title},
                files={"audio": (os.path.basename(file_path), f, "audio/mpeg")},
                timeout=180
            )
        return r.json()
    except Exception as e:
        log.warning(f"send_audio error: {e}")
        return {}


def send_video(chat_id, file_path, caption=""):
    """Send MP4 via multipart/form-data."""
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                f"{API}/sendVideo",
                data={"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"},
                files={"video": (os.path.basename(file_path), f, "video/mp4")},
                timeout=300
            )
        return r.json()
    except Exception as e:
        log.warning(f"send_video error: {e}")
        return {}


def send_chat_action(chat_id, action="typing"):
    try:
        requests.post(
            f"{API}/sendChatAction",
            json={"chat_id": chat_id, "action": action},
            timeout=5
        )
    except Exception:
        pass

# ─────────────────────────────────────────
#  UI TEXTS
# ─────────────────────────────────────────

def text_welcome(test_mode=False):
    badge = "\n\n🧪 <b>Тестовый режим активен</b> — доступны все платформы." if test_mode else ""
    return (
        "🎵 <b>Music Downloader Bot</b>\n\n"
        "Отправь мне:\n"
        "• 🔍 Название трека или исполнителя\n"
        "• 🔗 Прямую ссылку на YouTube\n\n"
        "Я найду и отдам тебе <b>MP3</b> или <b>MP4</b> на выбор 👇"
        + badge
    )


def text_test_on():
    return (
        "🧪 <b>Тестовый режим включён</b>\n\n"
        "Теперь поддерживаются:\n"
        "▸ YouTube\n"
        "▸ SoundCloud\n"
        "▸ Bandcamp\n"
        "▸ Vimeo\n"
        "▸ И другие сайты через yt-dlp\n\n"
        "Чтобы вернуться к обычному режиму — /test_off"
    )


def text_test_off():
    return (
        "✅ <b>Тестовый режим выключен</b>\n\n"
        "Бот снова работает только с <b>YouTube</b>.\n"
        "Отправь запрос или ссылку 👇"
    )


def main_menu():
    return {
        "keyboard": [[{"text": "🏠 Главное меню"}]],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }

# ─────────────────────────────────────────
#  YT-DLP HELPERS
# ─────────────────────────────────────────

YDL_COMMON = {
    "quiet": True,
    "no_warnings": True,
    "nocheckcertificate": True,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    },
}


def is_url(text: str) -> bool:
    return text.startswith(("http://", "https://", "www."))


def is_youtube_url(text: str) -> bool:
    return any(d in text for d in ("youtube.com", "youtu.be"))


def search_videos(query: str, test_mode: bool = False, max_results: int = 5) -> list:
    """
    Returns list of info dicts with full metadata including webpage_url.
    Does NOT use extract_flat=True — that strips the URL we need for download.
    """
    if is_url(query):
        opts = {**YDL_COMMON, "skip_download": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
        if info.get("_type") == "playlist" and info.get("entries"):
            return [e for e in info["entries"] if e][:max_results]
        return [info]

    # Text search — returns full metadata per entry (not flat)
    opts = {
        **YDL_COMMON,
        "default_search": f"ytsearch{max_results}",
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)

    entries = info.get("entries") or [info]
    return [e for e in entries if e][:max_results]


def get_video_url(entry: dict) -> str | None:
    """Get canonical URL from entry — prefer webpage_url over raw stream url."""
    return entry.get("webpage_url") or entry.get("original_url") or entry.get("url")


def download_audio(url: str) -> tuple[str, str]:
    """Download best audio, convert to MP3 192kbps. Returns (filepath, title)."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    opts = {
        **YDL_COMMON,
        "format": "bestaudio/best",
        "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
        "noplaylist": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info   = ydl.extract_info(url, download=True)
        vid_id = info.get("id", "audio")
        title  = info.get("title", "audio")

    mp3 = f"{DOWNLOAD_DIR}/{vid_id}.mp3"
    if not os.path.exists(mp3):
        found = glob.glob(f"{DOWNLOAD_DIR}/{vid_id}.*")
        if found:
            mp3 = found[0]

    return mp3, title


def download_video(url: str) -> tuple[str, str]:
    """Download best video ≤720p merged to MP4. Returns (filepath, title)."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    opts = {
        **YDL_COMMON,
        "format": (
            "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo[height<=720]+bestaudio"
            "/best[height<=720]"
            "/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info   = ydl.extract_info(url, download=True)
        vid_id = info.get("id", "video")
        title  = info.get("title", "video")

    mp4 = f"{DOWNLOAD_DIR}/{vid_id}.mp4"
    if not os.path.exists(mp4):
        found = glob.glob(f"{DOWNLOAD_DIR}/{vid_id}.*")
        if found:
            mp4 = found[0]

    return mp4, title


def cleanup(*paths):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

# ─────────────────────────────────────────
#  KEYBOARDS
# ─────────────────────────────────────────

def build_results_keyboard(videos: list) -> dict:
    kb = []
    for i, v in enumerate(videos[:5]):
        title    = (v.get("title") or "Без названия")[:45]
        duration = v.get("duration")
        dur_str  = f"  [{int(duration // 60)}:{int(duration % 60):02d}]" if duration else ""
        kb.append([{"text": f"🎵 {title}{dur_str}", "callback_data": f"sel:{i}"}])
    return {"inline_keyboard": kb}


def build_format_keyboard(index: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🎧 MP3 (аудио)", "callback_data": f"dl:mp3:{index}"},
                {"text": "🎬 MP4 (видео)", "callback_data": f"dl:mp4:{index}"},
            ],
            [{"text": "« Назад к результатам", "callback_data": "back"}],
        ]
    }

# ─────────────────────────────────────────
#  HANDLERS
# ─────────────────────────────────────────

def handle_message(msg: dict):
    chat_id = msg["chat"]["id"]
    text    = msg.get("text", "").strip()

    if not text:
        return

    state = user_state.setdefault(chat_id, {"mode": "youtube"})

    # ── Commands ─────────────────────────────────────────────────────
    if text in ("/start", "🏠 Главное меню"):
        send_message(chat_id, text_welcome(state["mode"] == "test"), main_menu())
        return

    if text == "/test":
        state["mode"] = "test"
        send_message(chat_id, text_test_on(), main_menu())
        return

    if text == "/test_off":
        state["mode"] = "youtube"
        send_message(chat_id, text_test_off(), main_menu())
        return

    # ── Block non-YouTube URLs in normal mode ────────────────────────
    if state["mode"] == "youtube" and is_url(text) and not is_youtube_url(text):
        send_message(
            chat_id,
            "⚠️ В обычном режиме поддерживается <b>только YouTube</b>.\n"
            "Для других платформ включи /test",
            main_menu(),
        )
        return

    # ── Search ───────────────────────────────────────────────────────
    send_chat_action(chat_id, "typing")
    status    = send_message(chat_id, "🔍 <i>Ищу треки, подожди...</i>")
    status_id = (status.get("result") or {}).get("message_id")

    try:
        videos = search_videos(text, test_mode=(state["mode"] == "test"))
    except Exception as e:
        log.error(f"search error: {e}", exc_info=True)
        err_text = "😕 <b>Не удалось найти видео</b>\nПопробуй другой запрос."
        if status_id:
            edit_message(chat_id, status_id, err_text)
        else:
            send_message(chat_id, err_text)
        return

    if not videos:
        empty = "😕 <b>Ничего не найдено</b>"
        if status_id:
            edit_message(chat_id, status_id, empty)
        else:
            send_message(chat_id, empty)
        return

    state["videos"] = videos
    state["query"]  = text

    result_text = (
        f"🎵 <b>Результаты по запросу:</b> <i>{text[:60]}</i>\n\n"
        f"Найдено <b>{len(videos)}</b> трек(ов). Выбери нужный 👇"
    )
    if status_id:
        edit_message(chat_id, status_id, result_text, build_results_keyboard(videos))
    else:
        send_message(chat_id, result_text, build_results_keyboard(videos))


def handle_callback(cb: dict):
    chat_id    = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    data       = cb.get("data", "")
    cb_id      = cb["id"]

    answer_callback(cb_id)

    state  = user_state.get(chat_id, {})
    videos = state.get("videos", [])

    # ── Select track ─────────────────────────────────────────────────
    if data.startswith("sel:"):
        index = int(data.split(":")[1])
        if index >= len(videos):
            edit_message(chat_id, message_id, "⚠️ Трек не найден")
            return

        v       = videos[index]
        title   = v.get("title") or "Без названия"
        channel = v.get("uploader") or v.get("channel") or "—"
        dur     = v.get("duration")
        dur_s   = f"{int(dur // 60)}:{int(dur % 60):02d}" if dur else "—"
        views   = v.get("view_count")
        views_s = f"{views:,}".replace(",", " ") if views else "—"

        state["selected_index"] = index
        info_text = (
            f"🎵 <b>{title}</b>\n"
            f"👤 {channel}   ⏱ {dur_s}   👁 {views_s}\n\n"
            "Выбери формат скачивания:"
        )
        edit_message(chat_id, message_id, info_text, build_format_keyboard(index))
        return

    # ── Back to results ───────────────────────────────────────────────
    if data == "back":
        query = state.get("query", "запрос")
        result_text = (
            f"🎵 <b>Результаты по запросу:</b> <i>{query[:60]}</i>\n\n"
            f"Найдено <b>{len(videos)}</b> трек(ов). Выбери нужный 👇"
        )
        edit_message(chat_id, message_id, result_text, build_results_keyboard(videos))
        return

    # ── Download ──────────────────────────────────────────────────────
    if data.startswith("dl:"):
        _, fmt, idx_s = data.split(":")
        index = int(idx_s)

        if index >= len(videos):
            edit_message(chat_id, message_id, "⚠️ Трек не найден")
            return

        v     = videos[index]
        url   = get_video_url(v)
        title = v.get("title") or "audio"

        if not url:
            edit_message(chat_id, message_id, "⚠️ Не удалось получить ссылку на видео")
            return

        if fmt == "mp3":
            edit_message(
                chat_id, message_id,
                f"⏳ <b>Загружаю аудио...</b>\n"
                f"🎵 {title[:60]}\n\n"
                f"<i>Это может занять до минуты</i>"
            )
            send_chat_action(chat_id, "upload_audio")

            try:
                file_path, title = download_audio(url)
            except Exception as e:
                log.error(f"audio download error: {e}", exc_info=True)
                edit_message(
                    chat_id, message_id,
                    f"⚠️ <b>Ошибка загрузки</b>\n<code>{str(e)[:300]}</code>"
                )
                return

            result = send_audio(chat_id, file_path, title)
            cleanup(file_path)

            if result.get("ok"):
                edit_message(
                    chat_id, message_id,
                    "✅ <b>Готово!</b> Аудио отправлено 🎧\n\n"
                    "💛 Поддержи проект: @xyessos3000usdt"
                )
            else:
                log.warning(f"sendAudio failed: {result}")
                edit_message(chat_id, message_id, "⚠️ Не удалось отправить аудио-файл")

        elif fmt == "mp4":
            edit_message(
                chat_id, message_id,
                f"⏳ <b>Загружаю видео...</b>\n"
                f"🎬 {title[:60]}\n\n"
                f"<i>Это может занять несколько минут</i>"
            )
            send_chat_action(chat_id, "upload_video")

            try:
                file_path, title = download_video(url)
            except Exception as e:
                log.error(f"video download error: {e}", exc_info=True)
                edit_message(
                    chat_id, message_id,
                    f"⚠️ <b>Ошибка загрузки</b>\n<code>{str(e)[:300]}</code>"
                )
                return

            result = send_video(chat_id, file_path, caption=f"🎬 <b>{title}</b>")
            cleanup(file_path)

            if result.get("ok"):
                edit_message(
                    chat_id, message_id,
                    "✅ <b>Готово!</b> Видео отправлено 🎬\n\n"
                    "💛 Поддержи проект: @xyessos3000usdt"
                )
            else:
                log.warning(f"sendVideo failed: {result}")
                edit_message(chat_id, message_id, "⚠️ Не удалось отправить видео-файл")

# ─────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────

def run():
    offset = None
    log.info("Bot started ✅")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    while True:
        updates = get_updates(offset)

        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            try:
                if "message" in update:
                    handle_message(update["message"])
                elif "callback_query" in update:
                    handle_callback(update["callback_query"])
            except Exception as e:
                log.error(f"Unhandled error: {e}", exc_info=True)

        time.sleep(0.3)


if __name__ == "__main__":
    run()
