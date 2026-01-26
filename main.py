import asyncio
import logging
import os
import sqlite3
import uuid
import shutil
import subprocess 
from datetime import datetime

import yt_dlp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, CallbackQuery
)

# ======================
# CONFIG
# ======================
BOT_TOKEN = "8253736025:AAHmMPac7DmA_fi01urRtI0wwAfd7SAYArE"
ADMIN_IDS = [8238730404]
AUDD_API_TOKEN = "030ece056f7aacdcc32f1f1b7330c24e"


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp")
DB_PATH = os.path.join(BASE_DIR, "bot.db")

MAX_SIZE_MB = 50

PLATFORMS = {
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "instagram.com": "Instagram",
    "tiktok.com": "TikTok",
    "twitter.com": "Twitter",
    "x.com": "Twitter",
}

FORCE_SUBSCRIPTION = False
REQUIRED_CHANNEL = "@yourchannel"
AD_TEXT = "📢 Reklama joyi bo‘sh"

os.makedirs(TEMP_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("vidbot")

# ======================
# RAM LINK CACHE   👇 SHU YERGA
# ======================
LINK_CACHE = {}
SHAZAM_FILE_CACHE = {}

# ======================
# FFMPEG AUTO-DETECT
# ======================
FFMPEG_PATH = None

try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
    logger.info(f"Using imageio-ffmpeg: {FFMPEG_PATH}")
except Exception:
    FFMPEG_PATH = shutil.which("ffmpeg")
    if FFMPEG_PATH:
        logger.info(f"Using system ffmpeg: {FFMPEG_PATH}")
    else:
        logger.warning("FFmpeg NOT FOUND! MP3 will fail.")

# ======================
# SQLITE
# ======================
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    created_at TEXT,
    downloads INTEGER DEFAULT 0
)
""")
conn.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS cache (
    url TEXT,
    type TEXT,
    file_id TEXT,
    created_at TEXT,
    PRIMARY KEY (url, type)
)
""")
conn.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS shazam_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT
)
""")
conn.commit()



def can_use_shazam(limit: int = 900) -> bool:
    cursor.execute("SELECT COUNT(*) FROM shazam_usage")
    count = cursor.fetchone()[0]
    return count < limit


def log_shazam_use():
    cursor.execute(
        "INSERT INTO shazam_usage (created_at) VALUES (?)",
        (datetime.now().isoformat(),)
    )
    conn.commit()


def get_cached_file(url: str, file_type: str):
    cursor.execute(
        "SELECT file_id FROM cache WHERE url=? AND type=?",
        (url, file_type)
    )
    row = cursor.fetchone()
    return row[0] if row else None


def save_cached_file(url: str, file_id: str, file_type: str):
    cursor.execute(
        "INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)",
        (url, file_type, file_id, datetime.now().isoformat())
    )
    conn.commit()



def get_or_create_user(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    now = datetime.now().isoformat()

    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    if not row:
        cursor.execute(
            "INSERT INTO users VALUES (?, ?, ?, 0)",
            (user_id, username, now)
        )
        conn.commit()
        logger.info(f"New user: {user_id}")
    else:
        cursor.execute(
            "UPDATE users SET username=? WHERE user_id=?",
            (username, user_id)
        )
        conn.commit()

    return user_id


def increment_downloads(user_id: int):
    cursor.execute(
        "UPDATE users SET downloads = downloads + 1 WHERE user_id=?",
        (user_id,)
    )
    conn.commit()


def get_stats():
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(downloads) FROM users")
    total_downloads = cursor.fetchone()[0] or 0

    return total_users, total_downloads


# ======================
# FSM
# ======================
class DownloadState(StatesGroup):
    waiting_for_format = State()


class AdminState(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_ad_text = State()


# ======================
# SHAZAM (AudD PROFESSIONAL)
# ======================
import requests

def search_music_from_instagram_metadata(url: str) -> list:
    """
    Instagram linkdan yt-dlp orqali music metadata olib,
    3–5 ta ehtimoliy variant qaytaradi.
    """
    results = []

    try:
        opts = {
            "quiet": True,
            "skip_download": True,
            "nocheckcertificate": True,
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # 1️⃣ Asosiy track / artist
        track = info.get("track")
        artist = info.get("artist")

        if track and artist:
            results.append({
                "artist": artist,
                "title": track,
                "source": "Instagram metadata"
            })

        # 2️⃣ audio_name (Instagram official audio nomi)
        audio_name = info.get("audio_name")
        if audio_name:
            results.append({
                "artist": "Instagram Audio",
                "title": audio_name,
                "source": "Instagram audio_name"
            })

        # 3️⃣ description ichidan ham urinib ko‘ramiz
        description = info.get("description", "")
        if description:
            # juda sodda heuristic: " - " bilan ajratilgan bo‘lsa
            if " - " in description:
                parts = description.split(" - ", 1)
                if len(parts) == 2:
                    results.append({
                        "artist": parts[0][:50],
                        "title": parts[1][:50],
                        "source": "Description"
                    })

        # 4️⃣ Dublikatlarni olib tashlaymiz
        unique = []
        seen = set()
        for r in results:
            key = (r["artist"].lower(), r["title"].lower())
            if key not in seen:
                seen.add(key)
                unique.append(r)

        # Maksimal 5 ta variant
        return unique[:5]

    except Exception as e:
        logger.error(f"Metadata search error: {e}", exc_info=True)
        return []


# ======================
# INSTAGRAM METADATA MUSIC
# ======================
def get_instagram_music_from_metadata(url: str) -> dict | None:
    try:
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "no_warnings": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # 1. To‘g‘ridan-to‘g‘ri maydonlardan
        if info.get("track") or info.get("music_title"):
            return {
                "artist": info.get("artist"),
                "title": info.get("track") or info.get("music_title")
            }

        # 2. Description ichidan qidirish
        desc = info.get("description") or ""
        if " - " in desc:
            parts = desc.split(" - ", 1)
            if len(parts) == 2 and len(parts[0]) < 100:
                return {
                    "artist": parts[0].strip(),
                    "title": parts[1].strip()
                }

        return None

    except Exception as e:
        logger.error(f"Instagram metadata error: {e}", exc_info=True)
        return None


# ======================
# YOUTUBE SEARCH VARIANTS
# ======================
def search_youtube_variants(query: str, limit: int = 10) -> list:
    try:
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "extract_flat": True,
            "no_warnings": True,
        }

        search_query = f"ytsearch{limit}:{query}"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)

        results = []

        for entry in info.get("entries", []):
            results.append({
                "title": entry.get("title"),
                "artist": entry.get("uploader"),
                "youtube_url": entry.get("url"),
            })

        return results

    except Exception as e:
        logger.error(f"YouTube search error: {e}", exc_info=True)
        return []



# ======================
# YT-DLP
# ======================
def get_video_opts(output_path: str):
    return {
        "outtmpl": output_path,

        # 🎯 MAQSAD: tiniq + Telegramga mos
        "format": (
            "bestvideo[ext=mp4][height<=1080]/"
            "bestvideo[ext=mp4][height<=720]/"
            "best[ext=mp4]/best"
        ),

        "merge_output_format": "mp4",
        "nocheckcertificate": True,
        "geo_bypass": True,

        "extractor_args": {
            "youtube": {
                "player_client": ["android"],
                "player_skip": ["webpage"]
            }
        },

        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "http_chunk_size": 10485760,

        **({"ffmpeg_location": FFMPEG_PATH} if FFMPEG_PATH else {}),
    }



def get_audio_opts(output_path: str):
    opts = {
        "outtmpl": output_path,
        "format": "bestaudio/best",
        "nocheckcertificate": True,
        "geo_bypass": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }

    if FFMPEG_PATH:
        opts["ffmpeg_location"] = FFMPEG_PATH

    return opts


def file_size_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except Exception:
        return 0.0


async def download_video(url: str) -> str | None:
    file_id = str(uuid.uuid4())[:8]
    output_tpl = os.path.join(TEMP_DIR, f"video_{file_id}.%(ext)s")
    opts = get_video_opts(output_tpl)

    def run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        base = output_tpl.replace(".%(ext)s", "")
        for ext in [".mp4", ".webm", ".mkv", ".mov"]:
            path = base + ext
            if os.path.exists(path):
                return path
        return None

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, run)


async def download_audio(url: str) -> str | None:
    if not FFMPEG_PATH:
        logger.error("FFmpeg not found. Cannot convert to MP3.")
        return None

    file_id = str(uuid.uuid4())[:8]
    output_tpl = os.path.join(TEMP_DIR, f"audio_{file_id}.%(ext)s")
    opts = get_audio_opts(output_tpl)

    def run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        base = output_tpl.replace(".%(ext)s", "")
        mp3_path = base + ".mp3"
        if os.path.exists(mp3_path):
            return mp3_path

        return None

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, run)


# ======================
# BOT
# ======================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()


def start_text():
    return (
        "🇺🇿 *Salom!*\n"
        "Men Instagram, YouTube, TikTok va Twitter’dan video yoki audio yuklab beraman.\n"
        "Shunchaki link yuboring.\n\n"

        "🇷🇺 *Привет!*\n"
        "Я скачиваю видео или аудио с Instagram, YouTube, TikTok и Twitter.\n"
        "Просто отправьте ссылку.\n\n"

        "🇬🇧 *Hello!*\n"
        "I download video or audio from Instagram, YouTube, TikTok and Twitter.\n"
        "Just send a link."
    )


@dp.message(Command("start"))
async def cmd_start(message: Message):
    get_or_create_user(message)
    await message.answer(start_text(), parse_mode="Markdown")


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ No access")
        return

    sub_status = "ON" if FORCE_SUBSCRIPTION else "OFF"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika", callback_data="stats")],
        [InlineKeyboardButton(text=f"🔔 Majburiy obuna: {sub_status}", callback_data="toggle_sub")],
        [InlineKeyboardButton(text="📢 Reklama matni", callback_data="ads")],
        [InlineKeyboardButton(text="✉️ Habar yuborish", callback_data="broadcast")],
    ])

    await message.answer("🛠 Admin panel:", reply_markup=kb)


# ❗ FAQAT ADMIN CALLBACKLARNI USHLAYDI
@dp.callback_query(F.data.in_(["stats", "toggle_sub", "ads", "broadcast"]))
async def admin_callbacks(cb: CallbackQuery, state: FSMContext):
    global FORCE_SUBSCRIPTION, AD_TEXT

    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("No access", show_alert=True)
        return

    if cb.data == "stats":
        users, downloads = get_stats()
        await cb.message.answer(
            f"📊 Statistika:\n\n"
            f"👥 Foydalanuvchilar: {users}\n"
            f"⬇️ Yuklab olinganlar: {downloads}"
        )

    elif cb.data == "toggle_sub":
        FORCE_SUBSCRIPTION = not FORCE_SUBSCRIPTION
        status = "ON" if FORCE_SUBSCRIPTION else "OFF"
        await cb.message.answer(f"🔔 Majburiy obuna: {status}")

    elif cb.data == "ads":
        await cb.message.answer(
            "📢 Hozirgi reklama matni:\n\n"
            f"{AD_TEXT}\n\n"
            "Yangi reklama matnini yuboring:"
        )
        await state.set_state(AdminState.waiting_for_ad_text)

    elif cb.data == "broadcast":
        await cb.message.answer("✉️ Hamma userga yuboriladigan xabarni yozing:")
        await state.set_state(AdminState.waiting_for_broadcast)

    await cb.answer()


@dp.message(AdminState.waiting_for_broadcast)
async def handle_broadcast(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    text = message.text
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()

    ok = 0
    fail = 0

    for (uid,) in users:
        try:
            await bot.send_message(uid, text)
            ok += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1

    await message.answer(
        f"📢 Broadcast tugadi!\n\n"
        f"✅ Yuborildi: {ok}\n"
        f"❌ Xatolik: {fail}"
    )

    await state.clear()


@dp.message(AdminState.waiting_for_ad_text)
async def handle_ad_text(message: Message, state: FSMContext):
    global AD_TEXT

    if message.from_user.id not in ADMIN_IDS:
        return

    AD_TEXT = message.text
    await message.answer("✅ Reklama matni yangilandi!")
    await state.clear()

# ======================
# MUSIC VARIANT KEYBOARD
# ======================
def build_music_keyboard(total: int):
    buttons = []

    row1 = []
    for i in range(1, 6):
        if i <= total:
            row1.append(InlineKeyboardButton(text=str(i), callback_data=f"pick|{i-1}"))
        else:
            row1.append(InlineKeyboardButton(text=" ", callback_data="noop"))

    row2 = []
    for i in range(6, 11):
        if i <= total:
            row2.append(InlineKeyboardButton(text=str(i), callback_data=f"pick|{i-1}"))
        else:
            row2.append(InlineKeyboardButton(text=" ", callback_data="noop"))

    nav = [
        InlineKeyboardButton(text="<", callback_data="noop"),
        InlineKeyboardButton(text="x", callback_data="close"),
        InlineKeyboardButton(text=">", callback_data="noop"),
    ]

    buttons.append(row1)
    buttons.append(row2)
    buttons.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==========================
# LINK QABUL QILISH
# ==========================
@dp.message(F.text.regexp(r"https?://"))
async def handle_link(message: Message):
    get_or_create_user(message)
    url = message.text.strip()

    platform = None
    for domain, name in PLATFORMS.items():
        if domain in url:
            platform = name
            break

    if not platform:
        await message.answer("❌ Bu platforma qo‘llab-quvvatlanmaydi.")
        return

    short_id = str(uuid.uuid4())[:8]
    LINK_CACHE[short_id] = url   # URL’ni RAM’da saqlaymiz

    # ❗ Bu yerda SHAZAM YO‘Q — faqat video / audio
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Video (MP4)", callback_data=f"video|{short_id}")],
        [InlineKeyboardButton(text="🎵 Audio (MP3)", callback_data=f"audio|{short_id}")],
    ])

    await message.answer(
        f"📥 {platform} link qabul qilindi.\n\nQaysi formatda yuklaymiz?",
        reply_markup=kb
    )



# ==========================
# FORMAT TANLASH (UPDATED WITH METADATA SEARCH)
# ==========================
@dp.callback_query(F.data.startswith(("video|", "audio|")))
async def format_chosen(cb: CallbackQuery):
    try:
        mode, short_id = cb.data.split("|", 1)
        url = LINK_CACHE.get(short_id)
    except Exception:
        url = None

    if not url:
        await cb.answer("⚠️ Link eskirib ketgan. Iltimos, linkni qayta yuboring.", show_alert=True)
        return

    # platform aniqlash
    if "youtu" in url:
        platform = "YouTube"
    elif "instagram" in url:
        platform = "Instagram"
    elif "tiktok" in url:
        platform = "TikTok"
    elif "twitter" in url or "x.com" in url:
        platform = "Twitter"
    else:
        platform = "Platform"

    user_id = cb.from_user.id
    await cb.answer()

    # ======================
    # 🔥 1-BOSQICH: AGAR INSTAGRAM BO‘LSA → AVVAL METADATA QIDIRAMIZ
    # ======================
    # 🔥 1-BOSQICH: INSTAGRAM METADATA QIDIRUV
    if "instagram" in url:
        await cb.message.answer("🔍 Instagram metadata’dan musiqa qidirilmoqda...")

        variants = search_music_from_instagram_metadata(url)

        if variants:
            text = "🎧 Topilgan ehtimoliy musiqalar:\n\n"
            kb_rows = []

            for i, v in enumerate(variants, start=1):
                text += f"{i}. {v['artist']} – {v['title']}\n"
                kb_rows.append(
                    InlineKeyboardButton(
                        text=str(i),
                        callback_data=f"choose_music|{i-1}|{short_id}"
                    )
                )

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[kb_rows]
            )

            await cb.message.answer(text, reply_markup=keyboard)

            return


    # ======================
    # ODDIY VIDEO / AUDIO YO‘LI
    # ======================
    status = await cb.message.answer(f"⏬ {platform} dan yuklanmoqda...")

    # ⚡ CACHE TEKSHIRISH
    file_type = "audio" if mode == "audio" else "video"
    cached_id = get_cached_file(url, file_type)

    # ======================
    # 2️⃣ CACHE’DAN YUBORILGANDA
    # ======================
    if cached_id:
        await status.edit_text("📤 Cache’dan yuborilmoqda...")

        if file_type == "audio":
            await cb.message.answer_audio(cached_id)
        else:
            msg = await cb.message.answer_video(cached_id, supports_streaming=True)

            # 🔥 FAQAT INSTAGRAM BO‘LSA — SHAZAM TUGMASI
            if "instagram" in url:
                shazam_id = uuid.uuid4().hex[:8]

                SHAZAM_FILE_CACHE[shazam_id] = {
                    "url": url,
                    "variants": []
                }

                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🎧 Musiqani aniqlash",
                        callback_data=f"shazam_file|{shazam_id}"
                    )]
                ])

                await cb.message.answer(
                    "Agar xohlasangiz, shu videodagi musiqani aniqlab beraman 👇",
                    reply_markup=kb
                )

        increment_downloads(user_id)
        await status.edit_text("✅ Tayyor! (cache)")

        if AD_TEXT and AD_TEXT != "📢 Reklama joyi bo‘sh":
            await cb.message.answer(AD_TEXT)

        LINK_CACHE.pop(short_id, None)
        return

    # ======================
    # 3️⃣ YANGI YUKLAB OLISH
    # ======================
    try:
        if mode == "audio":
            path = await download_audio(url)
            is_audio = True
        else:
            path = await download_video(url)
            is_audio = False

        if not path:
            await status.edit_text("❌ Yuklab bo‘lmadi. FFmpeg yoki format muammo bo‘lishi mumkin.")
            return

        size = file_size_mb(path)
        if size > MAX_SIZE_MB:
            await status.edit_text(
                f"⚠️ Fayl juda katta.\n"
                f"📦 Hajmi: {size:.1f} MB\n"
                f"📉 Limit: {MAX_SIZE_MB} MB"
            )
            os.unlink(path)
            return

        await status.edit_text("📤 Telegram’ga yuborilmoqda...")

        file = FSInputFile(path)

        if is_audio:
            msg = await cb.message.answer_audio(file)
            save_cached_file(url, msg.audio.file_id, "audio")
        else:
            msg = await cb.message.answer_video(file, supports_streaming=True)
            save_cached_file(url, msg.video.file_id, "video")

            # 🔥 FAQAT INSTAGRAM BO‘LSA — SHAZAM TUGMASI
            if "instagram" in url:
                shazam_id = uuid.uuid4().hex[:8]

                SHAZAM_FILE_CACHE[shazam_id] = {
                    "url": url,
                    "variants": []
                }

                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🎧 Musiqani aniqlash",
                        callback_data=f"shazam_file|{shazam_id}"
                    )]
                ])

                await cb.message.answer(
                    "Agar xohlasangiz, shu videodagi musiqani aniqlab beraman 👇",
                    reply_markup=kb
                )

        increment_downloads(user_id)
        await status.edit_text("✅ Tayyor!")

        if AD_TEXT and AD_TEXT != "📢 Reklama joyi bo‘sh":
            await cb.message.answer(AD_TEXT)

        os.unlink(path)
        LINK_CACHE.pop(short_id, None)

    except Exception as e:
        logger.error(f"ERROR: {e}", exc_info=True)
        await status.edit_text("❌ Xatolik yuz berdi. Keyinroq urinib ko‘ring.")







@dp.callback_query(F.data.startswith("choose_music|"))
async def choose_music_variant(cb: CallbackQuery):
    try:
        _, index_str, short_id = cb.data.split("|")
        index = int(index_str)

        url = LINK_CACHE.get(short_id)
        if not url:
            await cb.answer("Link eskirib ketgan", show_alert=True)
            return

        variants = search_music_from_instagram_metadata(url)

        if index >= len(variants):
            await cb.answer("Variant topilmadi", show_alert=True)
            return

        chosen = variants[index]

        await cb.message.answer(
            "🎵 Tanlangan musiqa:\n\n"
            f"🎤 Artist: {chosen['artist']}\n"
            f"🎶 Nomi: {chosen['title']}\n"
            f"📌 Manba: {chosen['source']}"
        )

        await cb.answer("Tanlandi ✅")

    except Exception as e:
        logger.error(f"Choose music error: {e}", exc_info=True)
        await cb.answer("Xatolik", show_alert=True)





# ==========================
# SHAZAM FROM INSTAGRAM VIDEO  (VARIANT LIST SYSTEM)
# ==========================
@dp.callback_query(F.data.startswith("shazam_file|"))
async def shazam_from_instagram(cb: CallbackQuery):
    try:
        _, shazam_id = cb.data.split("|", 1)
        data = SHAZAM_FILE_CACHE.get(shazam_id)
    except Exception:
        await cb.answer("Xato", show_alert=True)
        return

    if not data:
        await cb.answer("Bu video eskirib ketgan", show_alert=True)
        return

    url = data.get("url")

    status = await cb.message.answer("🎧 Musiqa aniqlanmoqda (Instagram + YouTube)...")

    try:
        # 1️⃣ Instagram metadata’dan asosiy nomni olamiz
        base_info = get_instagram_music_from_metadata(url)

        if not base_info:
            await status.edit_text(
                "❌ Instagram bu video uchun musiqa nomini ko‘rsatmagan.\n\n"
                "Bu video original sound bo‘lishi mumkin."
            )
            return

        artist = base_info.get("artist") or ""
        title = base_info.get("title") or ""

        query = f"{artist} {title}".strip()

        # 2️⃣ YouTube’dan 10 ta variant qidiramiz
        variants = search_youtube_variants(query, limit=10)

        if not variants:
            await status.edit_text("❌ YouTube’dan mos variantlar topilmadi.")
            return

        # 3️⃣ Cache’ga variantlarni saqlaymiz
        SHAZAM_FILE_CACHE[shazam_id]["variants"] = variants

        # 4️⃣ Ro‘yxatni chiqaramiz
        text = "🎶 Topilgan musiqalar:\n\n"
        for i, item in enumerate(variants, start=1):
            text += f"{i}. {item['title']}\n"

        text += "\nKerakli raqamni tanlang 👇"

        kb = build_music_keyboard(len(variants))

        await status.edit_text(text, reply_markup=kb)

    except Exception as e:
        logger.error(f"Variant list error: {e}", exc_info=True)
        await status.edit_text("❌ Xatolik yuz berdi. Keyinroq urinib ko‘ring.")



# ======================
# RUN
# ======================
async def main():
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
