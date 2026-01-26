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

def identify_song_audd(audio_or_video_path: str):
    try:
        url = "https://api.audd.io/"

        with open(audio_or_video_path, "rb") as f:
            files = {"file": f}
            data = {
                "api_token": AUDD_API_TOKEN,
                "return": "apple_music,spotify"
            }

            r = requests.post(url, data=data, files=files, timeout=120)

        result = r.json()

        if result.get("status") != "success":
            logger.error(f"AudD status error: {result}")
            return None

        song = result.get("result")
        if not song:
            return None

        return {
            "title": song.get("title"),
            "artist": song.get("artist"),
            "album": song.get("album"),
            "release_date": song.get("release_date"),
        }

    except Exception as e:
        logger.error(f"AudD error: {e}", exc_info=True)
        return None





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
# FORMAT TANLASH
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
    # ODDIY VIDEO / AUDIO YO‘LI
    # ======================
    status = await cb.message.answer(f"⏬ {platform} dan yuklanmoqda...")

    # ⚡ CACHE TEKSHIRISH
    file_type = "audio" if mode == "audio" else "video"
    cached_id = get_cached_file(url, file_type)

    if cached_id:
        await status.edit_text("📤 Cache’dan yuborilmoqda...")

        if file_type == "audio":
            await cb.message.answer_audio(cached_id)
        else:
            msg = await cb.message.answer_video(cached_id, supports_streaming=True)

            # 🔥 FAQAT INSTAGRAM BO‘LSA — SHAZAM TUGMASI
            if "instagram" in url:
                shazam_id = uuid.uuid4().hex[:8]
                SHAZAM_FILE_CACHE[shazam_id] = msg.video.file_id

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
    # YUKLAB OLISH
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
                SHAZAM_FILE_CACHE[shazam_id] = msg.video.file_id

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


# ==========================
# SHAZAM FROM INSTAGRAM VIDEO (BACKGROUND MUSIC TUNED)
# ==========================
@dp.callback_query(F.data.startswith("shazam_file|"))
async def shazam_from_instagram(cb: CallbackQuery):
    try:
        _, shazam_id = cb.data.split("|", 1)
        file_id = SHAZAM_FILE_CACHE.get(shazam_id)
    except Exception:
        await cb.answer("Xato", show_alert=True)
        return

    if not file_id:
        await cb.answer("Bu video eskirib ketgan", show_alert=True)
        return

    status = await cb.message.answer("🎧 Videodagi musiqa aniqlanmoqda...")

    # 🛑 LIMIT
    if not can_use_shazam():
        await status.edit_text("⛔ Shazam limiti tugadi.")
        return

    try:
        # 1️⃣ Videoni Telegramdan yuklaymiz (real format bilan)
        file = await bot.get_file(file_id)

        ext = os.path.splitext(file.file_path)[1] or ".mp4"
        video_path = os.path.join(TEMP_DIR, f"shazam_{uuid.uuid4().hex}{ext}")

        await bot.download_file(file.file_path, video_path)

        # 2️⃣ 🔥 Background music’ni kuchaytirib audio ajratamiz
        audio_path = os.path.join(TEMP_DIR, f"shazam_{uuid.uuid4().hex}_bg.mp3")

        ffmpeg = FFMPEG_PATH if FFMPEG_PATH else "ffmpeg"

        cmd = [
            ffmpeg, "-y",

            # 🔥 Videoning o‘rtasidan boshlaymiz
            "-ss", "15",
            "-i", video_path,

            # 🔥 12 soniya — ideal
            "-t", "12",
            "-vn",

            # 🔥 KUCHLI LEKIN AUDDGA MOS FILTR
            "-af",
            "highpass=f=200,"          # juda past shovqinni kesadi
            "lowpass=f=5000,"          # juda yuqori shovqinni kesadi
            "acompressor=threshold=-18dB:ratio=4:attack=5:release=200,"  # nutqni bosadi
            "volume=2.0",             # fon musiqani ko‘taradi

            # 🔥 FORMAT — hali ham toza fingerprint
            "-acodec", "mp3",
            "-ab", "192k",
            "-ac", "2",               # STEREO
            "-ar", "44100",

            audio_path
        ]



        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if result.returncode != 0 or not os.path.exists(audio_path):
            logger.error("FFmpeg bg-music filter error:")
            logger.error(result.stderr.decode())
            await status.edit_text("❌ Videodan audio ajratib bo‘lmadi.")
            try:
                os.unlink(video_path)
            except:
                pass
            return

        # 3️⃣ 🔥 FAQAT FILTRLANGAN AUDIO’NI AudD GA YUBORAMIZ
        info = identify_song_audd(audio_path)

        # 4️⃣ Tozalash
        try:
            os.unlink(video_path)
            os.unlink(audio_path)
        except:
            pass

        SHAZAM_FILE_CACHE.pop(shazam_id, None)

        if not info:
            await status.edit_text(
                "❌ Qo‘shiq topilmadi.\n\n"
                "Iltimos, musiqa aniq eshitiladigan video bo‘lsin."
            )
            return

        log_shazam_use()

        await status.edit_text(
            "🎵 Qo‘shiq topildi:\n\n"
            f"🎤 Artist: {info.get('artist')}\n"
            f"🎶 Nomi: {info.get('title')}\n"
            f"💿 Album: {info.get('album')}\n"
            f"📅 Sana: {info.get('release_date')}"
        )

    except Exception as e:
        logger.error(f"Shazam from instagram error: {e}", exc_info=True)
        await status.edit_text("❌ Xatolik yuz berdi. Keyinroq urinib ko‘ring.")


# ======================
# RUN
# ======================
async def main():
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
