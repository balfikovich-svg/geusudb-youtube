import time
import os
import logging
import glob
import requests
import yt_dlp

# ─────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────

TOKEN   = os.environ.get("BOT_TOKEN", "")
YT_KEY  = os.environ.get("YOUTUBE_API_KEY", "")  # YouTube Data API v3

if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

API          = f"https://api.telegram.org/bot{TOKEN}"
DOWNLOAD_DIR = "/tmp/yt_downloads"
YT_SEARCH    = "https://www.googleapis.com/youtube/v3/search"
YT_VIDEOS    = "https://www.googleapis.com/youtube/v3/videos"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

user_state: dict = {}

# ─────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────

def get_updates(offset=None):
    try:
        r = requests.get(f"{API}/getUpdates", params={"timeout": 30, "offset": offset}, timeout=35)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"get_updates: {e}")
        return {}

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(f"{API}/sendMessage", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        log.warning(f"send_message: {e}")
        return {}

def edit_message(chat_id, message_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(f"{API}/editMessageText", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        log.warning(f"edit_message: {e}")
        return {}

def answer_callback(cb_id, text=""):
    try:
        requests.post(f"{API}/answerCallbackQuery", json={"callback_query_id": cb_id, "text": text}, timeout=5)
    except Exception:
        pass

def send_audio(chat_id, file_path, title):
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
        log.warning(f"send_audio: {e}")
        return {}

def send_video(chat_id, file_path, caption=""):
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
        log.warning(f"send_video: {e}")
        return {}

def send_chat_action(chat_id, action="typing"):
    try:
        requests.post(f"{API}/sendChatAction", json={"chat_id": chat_id, "action": action}, timeout=5)
    except Exception:
        pass

# ─────────────────────────────────────────
#  YOUTUBE API SEARCH (надёжный поиск)
# ─────────────────────────────────────────

def search_youtube_api(query: str, max_results: int = 5) -> list:
    """Поиск через YouTube Data API v3 — работает с любого IP."""
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "key": YT_KEY,
    }
    r = requests.get(YT_SEARCH, params=params, timeout=10)
    r.raise_for_status()
    items = r.json().get("items", [])

    # Получаем длительность отдельным запросом
    ids = ",".join(i["id"]["videoId"] for i in items)
    details = {}
    if ids:
        dr = requests.get(YT_VIDEOS, params={
            "part": "contentDetails,statistics",
            "id": ids,
            "key": YT_KEY,
        }, timeout=10)
        if dr.ok:
            for item in dr.json().get("items", []):
                vid_id = item["id"]
                dur_iso = item["contentDetails"]["duration"]  # PT3M45S
                details[vid_id] = {
                    "duration": parse_iso_duration(dur_iso),
                    "view_count": int(item["statistics"].get("viewCount", 0)),
                }

    result = []
    for item in items:
        vid_id = item["id"]["videoId"]
        snip   = item["snippet"]
        d      = details.get(vid_id, {})
        result.append({
            "id":          vid_id,
            "title":       snip.get("title", ""),
            "uploader":    snip.get("channelTitle", ""),
            "webpage_url": f"https://www.youtube.com/watch?v={vid_id}",
            "duration":    d.get("duration"),
            "view_count":  d.get("view_count"),
            "thumbnail":   snip.get("thumbnails", {}).get("high", {}).get("url", ""),
        })
    return result


def search_ytdlp_fallback(query: str, max_results: int = 5) -> list:
    """Fallback через yt-dlp если нет API ключа (может не работать на хостинге)."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "default_search": f"ytsearch{max_results}",
        "skip_download": True,
        "noplaylist": True,
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
    entries = info.get("entries") or [info]
    return [e for e in entries if e][:max_results]


def parse_iso_duration(iso: str) -> int:
    """PT1H3M45S → секунды."""
    import re
    h = int((re.search(r"(\d+)H", iso) or [0, 0])[1])
    m = int((re.search(r"(\d+)M", iso) or [0, 0])[1])
    s = int((re.search(r"(\d+)S", iso) or [0, 0])[1])
    return h * 3600 + m * 60 + s


def is_url(text: str) -> bool:
    return text.startswith(("http://", "https://", "www."))

def is_youtube_url(text: str) -> bool:
    return any(d in text for d in ("youtube.com", "youtu.be"))

def search_videos(query: str, test_mode: bool = False) -> list:
    if is_url(query):
        # Прямая ссылка — извлекаем через yt-dlp
        opts = {
            "quiet": True, "no_warnings": True,
            "skip_download": True, "noplaylist": True,
            "http_headers": {"User-Agent": "Mozilla/5.0 Chrome/124.0"},
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
        return [info]

    if YT_KEY and not test_mode:
        return search_youtube_api(query)

    # test mode или нет ключа — yt-dlp
    return search_ytdlp_fallback(query)

# ─────────────────────────────────────────
#  DOWNLOAD
# ─────────────────────────────────────────

YDL_OPTS_BASE = {
    "quiet": True,
    "no_warnings": True,
    "nocheckcertificate": True,
    "noplaylist": True,
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    },
}

def download_audio(url: str) -> tuple:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    opts = {
        **YDL_OPTS_BASE,
        "format": "bestaudio/best",
        "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
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

def download_video(url: str) -> tuple:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    opts = {
        **YDL_OPTS_BASE,
        "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "merge_output_format": "mp4",
        "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
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
        title   = (v.get("title") or "Без названия")[:45]
        dur     = v.get("duration")
        dur_str = f"  [{int(dur//60)}:{int(dur%60):02d}]" if dur else ""
        kb.append([{"text": f"🎵 {title}{dur_str}", "callback_data": f"sel:{i}"}])
    return {"inline_keyboard": kb}

def build_format_keyboard(index: int) -> dict:
    return {"inline_keyboard": [
        [{"text": "🎧 MP3 (аудио)", "callback_data": f"dl:mp3:{index}"},
         {"text": "🎬 MP4 (видео)", "callback_data": f"dl:mp4:{index}"}],
        [{"text": "« Назад", "callback_data": "back"}],
    ]}

def main_menu():
    return {"keyboard": [[{"text": "🏠 Главное меню"}]], "resize_keyboard": True}

# ─────────────────────────────────────────
#  HANDLERS
# ─────────────────────────────────────────

def handle_message(msg: dict):
    chat_id = msg["chat"]["id"]
    text    = msg.get("text", "").strip()
    if not text:
        return

    state = user_state.setdefault(chat_id, {"mode": "youtube"})

    if text in ("/start", "🏠 Главное меню"):
        mode_badge = "\n\n🧪 <b>Тестовый режим</b> активен." if state["mode"] == "test" else ""
        send_message(chat_id,
            "🎵 <b>Music Downloader Bot</b>\n\n"
            "Отправь название трека или ссылку на YouTube.\n"
            "Я найду и отдам <b>MP3</b> или <b>MP4</b> 👇" + mode_badge,
            main_menu()
        )
        return

    if text == "/test":
        state["mode"] = "test"
        send_message(chat_id,
            "🧪 <b>Тестовый режим включён</b>\n\n"
            "Поиск теперь через yt-dlp (все платформы).\n"
            "Выключить: /test_off", main_menu())
        return

    if text == "/test_off":
        state["mode"] = "youtube"
        send_message(chat_id, "✅ <b>Обычный режим</b>. Работаем с YouTube 👇", main_menu())
        return

    if state["mode"] == "youtube" and is_url(text) and not is_youtube_url(text):
        send_message(chat_id,
            "⚠️ В обычном режиме только <b>YouTube</b>.\nДля других платформ: /test",
            main_menu())
        return

    send_chat_action(chat_id, "typing")
    status    = send_message(chat_id, "🔍 <i>Ищу треки...</i>")
    status_id = (status.get("result") or {}).get("message_id")

    try:
        videos = search_videos(text, test_mode=(state["mode"] == "test"))
    except Exception as e:
        log.error(f"search error: {e}", exc_info=True)
        err = f"😕 <b>Ошибка поиска</b>\n<code>{str(e)[:200]}</code>"
        if status_id:
            edit_message(chat_id, status_id, err)
        else:
            send_message(chat_id, err)
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
        f"🎵 <b>Результаты:</b> <i>{text[:60]}</i>\n"
        f"Найдено <b>{len(videos)}</b> трек(ов) 👇"
    )
    if status_id:
        edit_message(chat_id, status_id, result_text, build_results_keyboard(videos))
    else:
        send_message(chat_id, result_text, build_results_keyboard(videos))


def handle_callback(cb: dict):
    chat_id    = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    data       = cb.get("data", "")
    answer_callback(cb["id"])

    state  = user_state.get(chat_id, {})
    videos = state.get("videos", [])

    if data.startswith("sel:"):
        index = int(data.split(":")[1])
        if index >= len(videos):
            edit_message(chat_id, message_id, "⚠️ Трек не найден")
            return
        v       = videos[index]
        title   = v.get("title") or "Без названия"
        channel = v.get("uploader") or v.get("channel") or "—"
        dur     = v.get("duration")
        dur_s   = f"{int(dur//60)}:{int(dur%60):02d}" if dur else "—"
        views   = v.get("view_count")
        views_s = f"{views:,}".replace(",", " ") if views else "—"
        state["selected_index"] = index
        edit_message(chat_id, message_id,
            f"🎵 <b>{title}</b>\n👤 {channel}   ⏱ {dur_s}   👁 {views_s}\n\nВыбери формат:",
            build_format_keyboard(index))
        return

    if data == "back":
        query = state.get("query", "—")
        edit_message(chat_id, message_id,
            f"🎵 <b>Результаты:</b> <i>{query[:60]}</i>\nНайдено <b>{len(videos)}</b> трек(ов) 👇",
            build_results_keyboard(videos))
        return

    if data.startswith("dl:"):
        _, fmt, idx_s = data.split(":")
        index = int(idx_s)
        if index >= len(videos):
            edit_message(chat_id, message_id, "⚠️ Трек не найден")
            return

        v     = videos[index]
        url   = v.get("webpage_url") or v.get("original_url") or v.get("url")
        title = v.get("title") or "audio"

        if not url:
            edit_message(chat_id, message_id, "⚠️ Нет ссылки")
            return

        if fmt == "mp3":
            edit_message(chat_id, message_id,
                f"⏳ <b>Загружаю аудио...</b>\n🎵 {title[:60]}\n<i>До минуты</i>")
            send_chat_action(chat_id, "upload_audio")
            try:
                fp, title = download_audio(url)
            except Exception as e:
                log.error(f"dl audio: {e}", exc_info=True)
                edit_message(chat_id, message_id, f"⚠️ <b>Ошибка</b>\n<code>{str(e)[:300]}</code>")
                return
            res = send_audio(chat_id, fp, title)
            cleanup(fp)
            if res.get("ok"):
                edit_message(chat_id, message_id, "✅ <b>Готово!</b> 🎧\n\n💛 @xyessos3000usdt")
            else:
                edit_message(chat_id, message_id, f"⚠️ Не отправилось\n<code>{res}</code>")

        elif fmt == "mp4":
            edit_message(chat_id, message_id,
                f"⏳ <b>Загружаю видео...</b>\n🎬 {title[:60]}\n<i>Несколько минут</i>")
            send_chat_action(chat_id, "upload_video")
            try:
                fp, title = download_video(url)
            except Exception as e:
                log.error(f"dl video: {e}", exc_info=True)
                edit_message(chat_id, message_id, f"⚠️ <b>Ошибка</b>\n<code>{str(e)[:300]}</code>")
                return
            res = send_video(chat_id, fp, caption=f"🎬 <b>{title}</b>")
            cleanup(fp)
            if res.get("ok"):
                edit_message(chat_id, message_id, "✅ <b>Готово!</b> 🎬\n\n💛 @xyessos3000usdt")
            else:
                edit_message(chat_id, message_id, f"⚠️ Не отправилось\n<code>{res}</code>")

# ─────────────────────────────────────────
#  LOOP
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
                log.error(f"Unhandled: {e}", exc_info=True)
        time.sleep(0.3)

if __name__ == "__main__":
    run()
