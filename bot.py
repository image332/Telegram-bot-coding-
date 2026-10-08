# VIDEO & MUSIC DOWNLOADS — Telegram bot (python-telegram-bot, polling)
# Start command: python bot.py   (the Dockerfile runs it for you)
# Configuration lives in config.py and is read from environment variables.

import asyncio
import re
import sqlite3
import time

import config
import downloader
import health
from downloader import BotError, human_size, redact, register_secret
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, InvalidToken, NetworkError, RetryAfter, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

DB_PATH = config.DB_PATH
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
DOWNLOADS = {}
USER_STATE = {}
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)

# ------------------------- Database -------------------------


def db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def db_init():
    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at INTEGER)")
        con.execute("CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY)")
        con.commit()


def get_setting(key, default=""):
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with db() as con:
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        con.commit()


def save_user(user):
    with db() as con:
        con.execute(
            "INSERT INTO users(user_id,username,first_name,joined_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
            (user.id, user.username or "", user.first_name or "", int(time.time())),
        )
        con.commit()


def is_admin(uid):
    return uid in config.ADMIN_IDS


def is_banned(uid):
    with db() as con:
        return con.execute("SELECT 1 FROM bans WHERE user_id=?", (uid,)).fetchone() is not None


def users_count():
    with db() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def banned_count():
    with db() as con:
        return con.execute("SELECT COUNT(*) FROM bans").fetchone()[0]


def ensure_defaults():
    defaults = {
        "welcome_text": "✨ Welcome to **VIDEO & MUSIC DOWNLOADS**!\n\n📥 Download videos and audio from supported platforms quickly and easily.",
        "welcome_photo": "",
        "welcome_photo_enabled": "0",
        "developer_link": "",
        "developer_name": "👨‍💻 Developer",
        "developer_enabled": "0",
        "force_join_enabled": "0",
        "force_join_chat": "",
        "force_join_url": "",
    }
    for k, v in defaults.items():
        if get_setting(k, "") == "":
            set_setting(k, v)

# ------------------------- UI -------------------------


def user_keyboard():
    rows = [
        [KeyboardButton("📥 Download"), KeyboardButton("ℹ️ Help")],
    ]
    if get_setting("developer_enabled") == "1" and get_setting("developer_link"):
        rows.append([KeyboardButton(get_setting("developer_name", "👨‍💻 Developer"))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistics", callback_data="admin:stats")],
        [InlineKeyboardButton("🖼 Welcome Photo", callback_data="admin:photo"), InlineKeyboardButton("✏️ Welcome Text", callback_data="admin:text")],
        [InlineKeyboardButton("👨‍💻 Developer", callback_data="admin:developer")],
        [InlineKeyboardButton("📢 Force Join", callback_data="admin:force")],
        [InlineKeyboardButton("📣 Broadcast", callback_data="admin:broadcast")],
        [InlineKeyboardButton("🚫 Ban User", callback_data="admin:ban")],
        [InlineKeyboardButton("♻️ Unban User", callback_data="admin:unban")],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:back")]])


def developer_keyboard():
    link = get_setting("developer_link")
    enabled = get_setting("developer_enabled") == "1"
    state = "🟢 ON" if enabled and link else "🔴 OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Status: {state}", callback_data="noop")],
        [InlineKeyboardButton("🔗 Set / Change Link", callback_data="dev:setlink")],
        [InlineKeyboardButton("✏️ Change Button Name", callback_data="dev:name")],
        [InlineKeyboardButton("🟢 Enable", callback_data="dev:on"), InlineKeyboardButton("🔴 Disable", callback_data="dev:off")],
        [InlineKeyboardButton("🗑 Remove Link", callback_data="dev:remove")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin:back")],
    ])


def photo_keyboard():
    enabled = get_setting("welcome_photo_enabled") == "1" and bool(get_setting("welcome_photo"))
    state = "🟢 ON" if enabled else "🔴 OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Status: {state}", callback_data="noop")],
        [InlineKeyboardButton("🖼 Set / Change Photo", callback_data="photo:set")],
        [InlineKeyboardButton("🔴 Turn OFF", callback_data="photo:off")],
        [InlineKeyboardButton("🗑 Remove Photo", callback_data="photo:remove")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin:back")],
    ])


def force_keyboard():
    enabled = get_setting("force_join_enabled") == "1" and bool(get_setting("force_join_chat")) and bool(get_setting("force_join_url"))
    state = "🟢 ON" if enabled else "🔴 OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Status: {state}", callback_data="noop")],
        [InlineKeyboardButton("🔗 Set / Change Channel", callback_data="force:set")],
        [InlineKeyboardButton("🟢 Enable", callback_data="force:on"), InlineKeyboardButton("🔴 Disable", callback_data="force:off")],
        [InlineKeyboardButton("🗑 Remove", callback_data="force:remove")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin:back")],
    ])

# ------------------------- Helpers -------------------------


def clean_url(url):
    return url.strip().rstrip(".,!?)]}")


def site_name(info):
    extractor = (info.get("extractor_key") or info.get("extractor") or "Website").replace("IE", "")
    return extractor or "Website"


def safe_name(value):
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", str(value or "Media"))
    return value[:160].strip() or "Media"


def media_caption(info, mode):
    title = safe_name(info.get("title") or "Media")
    uploader = info.get("uploader") or info.get("channel") or info.get("creator")
    lines = [f"🎬 **{title}**"]
    if uploader:
        lines.append(f"👤 **{safe_name(uploader)}**")
    lines.append("🎵 **Audio**" if mode == "audio" else "🎬 **Video**")
    return "\n".join(lines)


async def set_text(message, text, parse_mode=None):
    """Edit a status message; Telegram refusals (unchanged text, deleted message) are ignored."""
    try:
        await message.edit_text(text, parse_mode=parse_mode)
    except TelegramError:
        pass


async def force_join_ok(update):
    if not get_setting("force_join_enabled") == "1":
        return True
    chat = get_setting("force_join_chat")
    link = get_setting("force_join_url")
    if not chat or not link:
        return True
    try:
        member = await update.effective_chat.get_member(update.effective_user.id)
        status = str(member.status)
        if status in ("member", "administrator", "owner"):
            return True
    except Exception:
        pass
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=link)], [InlineKeyboardButton("🔄 Check Again", callback_data="force:check")]])
    await update.effective_message.reply_text("🔒 **Join the required channel first.**\n\nAfter joining, tap **Check Again**.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    return False

# ------------------------- User handlers -------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    save_user(update.effective_user)
    if is_banned(uid):
        return
    if not await force_join_ok(update):
        return
    text = get_setting("welcome_text")
    photo = get_setting("welcome_photo")
    if get_setting("welcome_photo_enabled") == "1" and photo:
        try:
            await update.message.reply_photo(photo=photo, caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=user_keyboard())
            return
        except TelegramError:
            pass
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=user_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ **How to use**\n\n1. Tap 📥 Download\n2. Send a public video/audio URL\n3. Choose 🎬 Video or 🎵 Audio\n4. For video, choose an available quality\n5. Wait for the file to upload\n\nSupported sites depend on yt-dlp and the media being publicly accessible.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=user_keyboard(),
    )


async def download_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_banned(update.effective_user.id):
        return
    if not await force_join_ok(update):
        return
    USER_STATE[update.effective_user.id] = "url"
    await update.message.reply_text("🔗 **Send the video or audio link now.**", parse_mode=ParseMode.MARKDOWN, reply_markup=ReplyKeyboardRemove())


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    save_user(update.effective_user)
    if is_banned(uid) or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text == "📥 Download":
        return await download_button(update, context)
    if text == "ℹ️ Help":
        return await help_cmd(update, context)
    if text == get_setting("developer_name", "👨‍💻 Developer") and get_setting("developer_link"):
        await update.message.reply_text(f"👨‍💻 {get_setting('developer_link')}", disable_web_page_preview=True)
        return
    if USER_STATE.get(uid) != "url":
        return
    match = URL_RE.search(text)
    if not match:
        await update.message.reply_text("❌ Please send a valid `http://` or `https://` link.", parse_mode=ParseMode.MARKDOWN)
        return
    url = clean_url(match.group(0))
    USER_STATE.pop(uid, None)
    wait = await update.message.reply_text("🔎 **Checking the link and available formats…**", parse_mode=ParseMode.MARKDOWN)
    try:
        info = await asyncio.to_thread(downloader.extract_info, url)
        title = safe_name(info.get("title") or "Media")
        uploader = info.get("uploader") or info.get("channel") or info.get("creator") or "Unknown"
        site = site_name(info)
        DOWNLOADS[uid] = {"url": url, "info": info}
        text = f"🎬 **{title}**\n👤 **{safe_name(uploader)}**\n🌐 `{site}`\n\nChoose the format:"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 Video", callback_data="dl:video"), InlineKeyboardButton("🎵 Audio", callback_data="dl:audio")], [InlineKeyboardButton("✖️ Cancel", callback_data="dl:cancel")]])
        await wait.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    except Exception as exc:
        print(f"[EXTRACT] {type(exc).__name__}: {redact(exc)[:300]}", flush=True)
        await set_text(wait, downloader.user_message(exc, url))

# ------------------------- Download callbacks -------------------------


async def download_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    item = DOWNLOADS.get(uid)
    action = q.data.split(":", 1)[1]
    if action == "cancel":
        DOWNLOADS.pop(uid, None)
        await q.edit_message_text("✖️ Cancelled.")
        return
    if not item:
        await q.answer("Request expired. Send the link again.", show_alert=True)
        return
    if action == "audio":
        DOWNLOADS.pop(uid, None)
        await q.edit_message_text("🎵 **Audio selected. Preparing download…**", parse_mode=ParseMode.MARKDOWN)
        await perform_download(q.message, item, "audio", None, context)
        return
    heights = downloader.available_qualities(item["info"])
    if not heights:
        await q.answer("No video quality was found.", show_alert=True)
        return
    rows = []
    for i in range(0, len(heights), 2):
        rows.append([InlineKeyboardButton(f"{h}p", callback_data=f"quality:{h}") for h in heights[i:i + 2]])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="dl:back"), InlineKeyboardButton("✖️ Cancel", callback_data="dl:cancel")])
    await q.edit_message_text("🎬 **Choose video quality**\n\nOnly qualities found in this media are shown.", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))


async def quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    item = DOWNLOADS.pop(uid, None)
    if not item:
        await q.answer("Request expired. Send the link again.", show_alert=True)
        return
    quality = int(q.data.split(":")[1])
    await q.edit_message_text(f"🎬 **{quality}p selected. Preparing download…**", parse_mode=ParseMode.MARKDOWN)
    await perform_download(q.message, item, "video", quality, context)


async def quality_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    item = DOWNLOADS.get(q.from_user.id)
    if not item:
        await q.edit_message_text("Request expired. Send the link again.")
        return
    await q.edit_message_text("Choose the format:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎬 Video", callback_data="dl:video"), InlineKeyboardButton("🎵 Audio", callback_data="dl:audio")], [InlineKeyboardButton("✖️ Cancel", callback_data="dl:cancel")]]))


async def perform_download(message, item, mode, quality, context):
    async with DOWNLOAD_SEMAPHORE:
        status = await message.reply_text("⏳ **Starting download…**", parse_mode=ParseMode.MARKDOWN)
        loop = asyncio.get_running_loop()
        progress = {"open": True}

        def push(text):
            # Called from yt-dlp's worker thread with REAL progress; stops once upload begins.
            if progress["open"]:
                asyncio.run_coroutine_threadsafe(set_text(status, text, parse_mode=ParseMode.MARKDOWN), loop)

        try:
            # The temporary folder (media + cookie copy) is removed when this block exits,
            # i.e. only after Telegram has accepted the file.
            with downloader.request_workspace(item["url"]) as (media_dir, cookiefile):
                path = await asyncio.to_thread(
                    downloader.download_media, item["url"], mode, quality, media_dir, cookiefile, push
                )
                size = path.stat().st_size
                limit = config.MAX_UPLOAD_MB * 1024 * 1024
                if size > limit:
                    raise BotError(
                        f"❌ File too large for Telegram.\n\nThe file is {human_size(size)}. "
                        f"This bot's limit is {config.MAX_UPLOAD_MB} MB."
                    )
                progress["open"] = False
                await status.edit_text("📤 **Uploading to Telegram…**", parse_mode=ParseMode.MARKDOWN)
                caption = media_caption(item["info"], mode)
                with path.open("rb") as handle:
                    if mode == "video":
                        await message.reply_video(video=handle, caption=caption, supports_streaming=True, read_timeout=120, write_timeout=120)
                    else:
                        await message.reply_audio(
                            audio=handle, caption=caption,
                            title=item["info"].get("title"), performer=item["info"].get("uploader"),
                            read_timeout=120, write_timeout=120,
                        )
            progress["open"] = False
            try:
                await status.delete()
            except TelegramError:
                pass
        except RetryAfter as exc:
            progress["open"] = False
            wait = exc.retry_after.total_seconds() if hasattr(exc.retry_after, "total_seconds") else exc.retry_after
            await set_text(status, f"⏳ Telegram asked to wait {int(wait)} seconds. Please try again shortly.")
        except (Forbidden, BadRequest) as exc:
            progress["open"] = False
            print(f"[SEND] {type(exc).__name__}: {redact(exc)[:300]}", flush=True)
            await set_text(status, f"❌ Telegram could not accept the file.\n\n{redact(exc)[-400:]}")
        except Exception as exc:
            progress["open"] = False
            print(f"[DOWNLOAD] {type(exc).__name__}: {redact(exc)[:500]}", flush=True)
            await set_text(status, downloader.user_message(exc, item["url"]))

# ------------------------- Admin -------------------------


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("🛠 **Admin Control Panel**", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        await q.answer("Admin only.", show_alert=True)
        return
    await q.answer()
    action = q.data.split(":", 1)[1]
    if action == "back":
        await q.edit_message_text("🛠 **Admin Control Panel**", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())
    elif action == "stats":
        await q.edit_message_text(f"📊 **Statistics**\n\n👥 Users: `{users_count()}`\n🚫 Banned: `{banned_count()}`", parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
    elif action == "photo":
        await q.edit_message_text("🖼 **Welcome Photo**", reply_markup=photo_keyboard())
    elif action == "text":
        USER_STATE[q.from_user.id] = "admin_text"
        await q.edit_message_text("✏️ Send the new welcome text now.", reply_markup=back_keyboard())
    elif action == "developer":
        await q.edit_message_text("👨‍💻 **Developer Settings**", reply_markup=developer_keyboard())
    elif action == "force":
        await q.edit_message_text("📢 **Force Join Settings**", reply_markup=force_keyboard())
    elif action in ("broadcast", "ban", "unban"):
        USER_STATE[q.from_user.id] = "admin_" + action
        prompts = {"broadcast": "📣 Send the message to broadcast.", "ban": "🚫 Send the user ID to ban.", "unban": "♻️ Send the user ID to unban."}
        await q.edit_message_text(prompts[action], reply_markup=back_keyboard())


async def photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        return
    await q.answer()
    action = q.data.split(":")[1]
    if action == "set":
        USER_STATE[q.from_user.id] = "admin_photo"
        await q.edit_message_text("🖼 Send the welcome photo now.", reply_markup=back_keyboard())
    elif action == "off":
        set_setting("welcome_photo_enabled", "0")
        await q.edit_message_text("🖼 Welcome photo is OFF.", reply_markup=photo_keyboard())
    elif action == "remove":
        set_setting("welcome_photo", "")
        set_setting("welcome_photo_enabled", "0")
        await q.edit_message_text("🗑 Welcome photo removed.", reply_markup=photo_keyboard())


async def developer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        return
    await q.answer()
    action = q.data.split(":")[1]
    if action == "setlink":
        USER_STATE[q.from_user.id] = "dev_link"
        await q.edit_message_text("🔗 Send the Developer URL now.", reply_markup=back_keyboard())
    elif action == "name":
        USER_STATE[q.from_user.id] = "dev_name"
        await q.edit_message_text("✏️ Send the new button name now.", reply_markup=back_keyboard())
    elif action == "on":
        if get_setting("developer_link"):
            set_setting("developer_enabled", "1")
        await q.edit_message_text("👨‍💻 Developer settings updated.", reply_markup=developer_keyboard())
    elif action == "off":
        set_setting("developer_enabled", "0")
        await q.edit_message_text("👨‍💻 Developer button is OFF.", reply_markup=developer_keyboard())
    elif action == "remove":
        set_setting("developer_link", "")
        set_setting("developer_enabled", "0")
        await q.edit_message_text("🗑 Developer link removed.", reply_markup=developer_keyboard())


async def force_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        return
    await q.answer()
    action = q.data.split(":")[1]
    if action == "set":
        USER_STATE[q.from_user.id] = "force_config"
        await q.edit_message_text("📢 Send the channel username and invite URL in one message.\n\nExample:\n`@mychannel https://t.me/mychannel`", parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
    elif action == "on":
        if get_setting("force_join_chat") and get_setting("force_join_url"):
            set_setting("force_join_enabled", "1")
        await q.edit_message_text("📢 Force Join settings updated.", reply_markup=force_keyboard())
    elif action == "off":
        set_setting("force_join_enabled", "0")
        await q.edit_message_text("📢 Force Join is OFF.", reply_markup=force_keyboard())
    elif action == "remove":
        for k in ("force_join_chat", "force_join_url"):
            set_setting(k, "")
        set_setting("force_join_enabled", "0")
        await q.edit_message_text("🗑 Force Join configuration removed.", reply_markup=force_keyboard())


async def force_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if await force_join_ok(update):
        await q.edit_message_text("✅ Membership check passed. You can use the bot now.")


async def admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid) or not update.message:
        return
    state = USER_STATE.get(uid)
    text = update.message.text or ""
    if state == "admin_text":
        set_setting("welcome_text", text)
        USER_STATE.pop(uid, None)
        await update.message.reply_text("✅ Welcome text saved.", reply_markup=admin_keyboard())
    elif state == "dev_link":
        if not URL_RE.fullmatch(text.strip()):
            await update.message.reply_text("❌ Send a valid URL starting with http:// or https://")
            return
        set_setting("developer_link", text.strip())
        set_setting("developer_enabled", "1")
        USER_STATE.pop(uid, None)
        await update.message.reply_text("✅ Developer link saved and enabled.", reply_markup=admin_keyboard())
    elif state == "dev_name":
        set_setting("developer_name", text[:50])
        USER_STATE.pop(uid, None)
        await update.message.reply_text("✅ Developer button name saved.", reply_markup=admin_keyboard())
    elif state == "force_config":
        parts = text.split()
        if len(parts) < 2 or not URL_RE.fullmatch(parts[1]):
            await update.message.reply_text("❌ Format: `@channel https://t.me/channel`", parse_mode=ParseMode.MARKDOWN)
            return
        set_setting("force_join_chat", parts[0])
        set_setting("force_join_url", parts[1])
        set_setting("force_join_enabled", "1")
        USER_STATE.pop(uid, None)
        await update.message.reply_text("✅ Force Join channel saved and enabled.", reply_markup=admin_keyboard())
    elif state in ("admin_ban", "admin_unban"):
        try:
            target = int(text.strip())
        except ValueError:
            await update.message.reply_text("❌ Send a numeric Telegram user ID.")
            return
        with db() as con:
            if state == "admin_ban":
                con.execute("INSERT OR IGNORE INTO bans(user_id) VALUES(?)", (target,))
            else:
                con.execute("DELETE FROM bans WHERE user_id=?", (target,))
            con.commit()
        USER_STATE.pop(uid, None)
        await update.message.reply_text("✅ Done.", reply_markup=admin_keyboard())
    elif state == "admin_broadcast":
        USER_STATE.pop(uid, None)
        with db() as con:
            ids = [r[0] for r in con.execute("SELECT user_id FROM users").fetchall()]
        sent = 0
        for target in ids:
            try:
                await context.bot.send_message(target, text)
                sent += 1
                await asyncio.sleep(0.05)
            except TelegramError:
                pass
        await update.message.reply_text(f"📣 Broadcast complete. Sent: {sent}", reply_markup=admin_keyboard())


async def photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid) or USER_STATE.get(uid) != "admin_photo":
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Please send an actual Telegram photo.")
        return
    set_setting("welcome_photo", update.message.photo[-1].file_id)
    set_setting("welcome_photo_enabled", "1")
    USER_STATE.pop(uid, None)
    await update.message.reply_text("✅ Welcome photo saved and enabled.", reply_markup=admin_keyboard())

# ------------------------- Global error handler -------------------------


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, NetworkError):
        print(f"[NETWORK] {redact(err)}", flush=True)
    else:
        print(f"[ERROR] {type(err).__name__}: {redact(err)}", flush=True)

# ------------------------- Startup -------------------------


async def post_init(application: Application):
    me = await application.bot.get_me()
    print("[BOT] Telegram connection: OK", flush=True)
    print(f"[BOT] Username: @{me.username or 'unknown'}", flush=True)
    if me.username:
        print(f"[BOT] Link: https://t.me/{me.username}", flush=True)


def build_application():
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(download_type, pattern=r"^dl:(video|audio|cancel)$"))
    app.add_handler(CallbackQueryHandler(quality_callback, pattern=r"^quality:\d+$"))
    app.add_handler(CallbackQueryHandler(quality_back, pattern=r"^dl:back$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin:"))
    app.add_handler(CallbackQueryHandler(photo_callback, pattern=r"^photo:"))
    app.add_handler(CallbackQueryHandler(developer_callback, pattern=r"^dev:"))
    app.add_handler(CallbackQueryHandler(force_callback, pattern=r"^force:(set|on|off|remove)$"))
    app.add_handler(CallbackQueryHandler(force_check, pattern=r"^force:check$"))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, photo_input), group=1)
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, admin_text_input), group=1)
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, text_handler), group=2)
    app.add_error_handler(error_handler)
    return app


def main():
    print(f"[BOOT] Starting {config.BOT_NAME} v{config.APP_VERSION}", flush=True)
    if not config.BOT_TOKEN or ":" not in config.BOT_TOKEN:
        raise SystemExit("[BOT] TELEGRAM_BOT_TOKEN is missing or invalid. Set it in your environment (see README.md).")
    register_secret(config.BOT_TOKEN)
    print("[BOT] Token configured (from TELEGRAM_BOT_TOKEN)", flush=True)
    if config.GENERIC_BOT_TOKEN_PRESENT:
        print("[BOT] A generic BOT_TOKEN variable is set and is IGNORED. Only TELEGRAM_BOT_TOKEN is used.", flush=True)
    if not config.ADMIN_IDS:
        print("[BOT] ADMIN_IDS is empty: the /admin panel is disabled.", flush=True)

    config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db_init()
    ensure_defaults()
    for line in downloader.startup_report():
        print(line, flush=True)

    _server, health_status = health.start_health_server(config.HOST, config.PORT)
    print(f"[BOOT] Health server: {health_status}", flush=True)

    print("[BOOT] Starting Telegram polling...", flush=True)
    while True:
        app = build_application()
        try:
            app.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
            return
        except InvalidToken:
            raise SystemExit("[BOT] Telegram rejected TELEGRAM_BOT_TOKEN. Check its value in your environment.")
        except Exception as exc:
            print(f"[BOT] Connection problem ({redact(type(exc).__name__)}). Retrying in 15 seconds.", flush=True)
            time.sleep(15)


if __name__ == "__main__":
    main()
