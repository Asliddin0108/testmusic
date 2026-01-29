import os
import math
import asyncio
import requests
from urllib.parse import quote

from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

# ================== CONFIG ==================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

DOWNLOAD_DIR = "downloads"
PER_PAGE = 10

# ================== HELPERS ==================
def format_duration(ms: int) -> str:
    sec = ms // 1000
    return f"{sec//60}:{sec%60:02d}"

def get_music(query: str):
    url = f"https://api.smtv.uz/shazam/?music={quote(query)}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

# ================== /start ==================
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    bot_info = await bot.get_me()
    sent = await message.answer("_")

    text_anim = [
        "A","s","s","a","l","o","m","u"," ",
        "a","l","a","y","k","u","m"," ",
        "x","u","s","h"," ",
        "k","e","l","i","b","s","i","z"
    ]

    current = ""
    for ch in text_anim:
        current += ch
        await bot.edit_message_text(
            f"<b>{current}</b>",
            chat_id=sent.chat.id,
            message_id=sent.message_id,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Guruhga qoʻshish ⤴️",
                            url=f"https://t.me/{bot_info.username}?startgroup=add"
                        )
                    ]
                ]
            )
        )
        await asyncio.sleep(0.05)

# ================== SEARCH ==================
@dp.message_handler(lambda m: not m.text.startswith("/") and "https://" not in m.text)
async def search_music(message: types.Message):
    await bot.send_chat_action(message.chat.id, "typing")

    data = get_music(message.text)
    if not data or not data.get("results", {}).get("data"):
        await message.answer(
            f"Kechirasiz, <b>{message.text}</b> sarlavhali musiqani topa olmadim"
        )
        return

    await send_music_page(message.chat.id, message.text, 0)

async def send_music_page(chat_id, query, page):
    data = get_music(query)
    tracks = data["results"]["data"]

    total = len(tracks)
    pages = math.ceil(total / PER_PAGE)

    start = page * PER_PAGE
    sliced = tracks[start:start+PER_PAGE]

    text = ""
    kb = []

    for i, t in enumerate(sliced):
        num = start + i + 1
        text += f"{num}. <b>{t['Name']} - {', '.join(t['Artists'])}</b> {format_duration(t['Duration'])}\n"
        kb.append(
            InlineKeyboardButton(
                text=str(num),
                callback_data=f"music={query}={start+i}"
            )
        )

    keyboard = [kb[i:i+5] for i in range(0, len(kb), 5)]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page={query}=prev={page}"))
    if page < pages-1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page={query}=next={page}"))

    if nav:
        keyboard.append(nav)

    await bot.send_message(
        chat_id,
        f"{text}\n<b>Sahifa {page+1} из {pages}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

# ================== PAGE CALLBACK ==================
@dp.callback_query_handler(lambda c: c.data.startswith("page="))
async def change_page(call: types.CallbackQuery):
    _, query, direction, page = call.data.split("=")
    page = int(page)

    page = page-1 if direction == "prev" else page+1

    data = get_music(query)
    tracks = data["results"]["data"]

    total = len(tracks)
    pages = math.ceil(total / PER_PAGE)

    start = page * PER_PAGE
    sliced = tracks[start:start+PER_PAGE]

    text = ""
    kb = []

    for i, t in enumerate(sliced):
        num = start + i + 1
        text += f"{num}. <b>{t['Name']} - {', '.join(t['Artists'])}</b> {format_duration(t['Duration'])}\n"
        kb.append(
            InlineKeyboardButton(
                text=str(num),
                callback_data=f"music={query}={start+i}"
            )
        )

    keyboard = [kb[i:i+5] for i in range(0, len(kb), 5)]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page={query}=prev={page}"))
    if page < pages-1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page={query}=next={page}"))

    if nav:
        keyboard.append(nav)

    await call.message.edit_text(
        f"{text}\n<b>Sahifa {page+1} из {pages}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

# ================== MUSIC DOWNLOAD ==================
@dp.callback_query_handler(lambda c: c.data.startswith("music="))
async def send_music(call: types.CallbackQuery):
    _, query, index = call.data.split("=")
    index = int(index)

    data = get_music(query)
    track = data["results"]["data"][index]

    os.makedirs(f"{DOWNLOAD_DIR}/{call.message.chat.id}", exist_ok=True)

    safe_name = "".join(c if c.isalnum() else "_" for c in track["Name"])
    path = f"{DOWNLOAD_DIR}/{call.message.chat.id}/{safe_name}.mp3"

    r = requests.get(track["url"])
    with open(path, "wb") as f:
        f.write(r.content)

    await bot.send_audio(
        call.message.chat.id,
        types.InputFile(path),
        caption=(
            f"🎵 <b>Nomi</b>: {track['Name']}\n"
            f"💿 <b>Albomi</b>: {track['Album']}\n"
            f"🎤 <b>Ijrochi</b>: {', '.join(track['Artists'])}\n"
            f"📅 <b>Sana</b>: {track['Date']}\n"
            f"🎼 <b>Janri</b>: {track['Genre']}"
        )
    )

    os.remove(path)

# ================== /kod ==================
@dp.message_handler(commands=["kod"])
async def send_code(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await bot.send_document(
            ADMIN_ID,
            types.InputFile(__file__),
            caption=f"<b>@{(await bot.get_me()).username} kodi</b>"
        )

# ================== RUN ==================
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
