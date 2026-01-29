import os
import re
import requests
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

BOT_TOKEN = "8253736025:AAHmMPac7DmA_fi01urRtI0wwAfd7SAYArE"

MATIN = "📥 Yuklab olindi ushbu bot orqali"


def bosh_menu(botname):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Guruhga qo‘shish",
         url=f"https://t.me/{botname}?startgroup=new")]
    ])

def ortga_menu(botname):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌", callback_data="del")],
        [InlineKeyboardButton("➕ Guruhga qo‘shish",
         url=f"https://t.me/{botname}?startgroup=new")]
    ])

# ===================== /start =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    botname = context.bot.username
    await update.message.reply_html(
        f"""🔥 Assalomu alaykum. @{botname} ga xush kelibsiz!

• Instagram – video
• TikTok – suv belgisiz
• YouTube – video

🎵 Shazam:
• Qo‘shiq nomi yoki ijrochi

😎 Bot guruhlarda ham ishlaydi!""",
        reply_markup=bosh_menu(botname)
    )

# ===================== INSTAGRAM =====================
async def instagram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    api = f"https://igram.world/api/ig?url={text}"

    try:
        data = requests.get(api, timeout=15).json()
        video = data["links"][0]["url"]
    except:
        await update.message.reply_text("❌ Instagram video topilmadi")
        return

    msg = await update.message.reply_text("📥")
    await msg.delete()

    await update.message.reply_video(
        video=video,
        caption=f"{MATIN} @{context.bot.username}",
        reply_markup=ortga_menu(context.bot.username)
    )

# ===================== TIKTOK =====================
async def tiktok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = f"https://tikwm.com/api/?url={update.message.text}"

    try:
        data = requests.get(api).json()["data"]["play"]
    except:
        await update.message.reply_text("❌ TikTok video topilmadi")
        return

    msg = await update.message.reply_text("📥")
    await msg.delete()

    await update.message.reply_video(
        video=data,
        caption=f"{MATIN} @{context.bot.username}",
        reply_markup=ortga_menu(context.bot.username)
    )

# ===================== YOUTUBE =====================
async def youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    api = f"https://buyapi.68d7a3db504c5.xvest6.ru/API/YouTube/?key=mTuN5&url={url}"

    try:
        data = requests.get(api, timeout=15).json()
    except:
        await update.message.reply_text("❌ API javob bermadi")
        return

    video = None
    title = data.get("title", "YouTube video")

    if "video_with_audio" in data:
        video = data["video_with_audio"][0]["url"]
    elif "video" in data:
        video = data["video"]
    elif "url" in data:
        video = data["url"]

    if not video:
        await update.message.reply_text("❌ Video topilmadi")
        return

    msg = await update.message.reply_text("📥")
    await msg.delete()

    await update.message.reply_video(
        video=video,
        caption=f"{title}\n\n{MATIN} @{context.bot.username}",
        reply_markup=ortga_menu(context.bot.username)
    )

# ===================== CALLBACK =====================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "del":
        await query.message.delete()
        return

    if "-" in query.data:
        search, index = query.data.rsplit("-", 1)
        api = f"https://4503091-gf96974.twc1.net/Api/mega.php?search={search}"
        data = requests.get(api).json()
        music = data[int(index) + 1]

        now = datetime.now()
        caption = (
            f"<b>{music['artist']}</b> - <i>{music['title']}</i>\n\n"
            f"@{context.bot.username} orqali yuklab olindi\n\n"
            f"⏰{now.strftime('%H:%M')} 📅{now.strftime('%d.%m.%Y')}"
        )

        await query.message.reply_audio(
            audio=music["Musiqa_linki"],
            caption=caption,
            parse_mode="HTML",
            reply_markup=ortga_menu(context.bot.username)
        )

# ===================== SEARCH =====================
async def search_music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    api = f"https://4503091-gf96974.twc1.net/Api/mega.php?search={text}"

    try:
        data = requests.get(api).json()
    except:
        await update.message.reply_html("❌ API ishlamayapti")
        return

    if not data:
        await update.message.reply_html("😔 Hech narsa topilmadi")
        return

    keyboard = []
    caption = ""

    for i, m in enumerate(data[:10]):
        caption += f"<b>{i+1}</b>. <i>{m['artist']} - {m['title']}</i>\n"
        keyboard.append(
            InlineKeyboardButton(str(i+1), callback_data=f"{text}-{i}")
        )

    markup = InlineKeyboardMarkup([
        keyboard[:5],
        keyboard[5:],
        [InlineKeyboardButton("❌", callback_data="del")]
    ])

    await update.message.reply_photo(
        photo="https://t.me/malumotlarombor",
        caption=f"<b>🎙 {text}</b>\n\n{caption}",
        parse_mode="HTML",
        reply_markup=markup
    )

# ===================== MAIN =====================
app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(callbacks))
app.add_handler(MessageHandler(filters.TEXT & filters.Regex("instagram.com"), instagram))
app.add_handler(MessageHandler(filters.TEXT & filters.Regex("vt.tiktok.com"), tiktok))
app.add_handler(MessageHandler(filters.TEXT & filters.Regex("youtube.com|youtu.be"), youtube))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_music))

app.run_polling()
