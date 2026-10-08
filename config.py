# =====================================================================
#  VIDEO & MUSIC DOWNLOADS — CONFIGURATION
#  ---------------------------------------------------------------
#  Every setting is read from an ENVIRONMENT VARIABLE.
#  No secret (bot token, cookies, admin IDs) is stored in this file.
#  See .env.example for the full list and README.md for details.
# =====================================================================

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _text(name, default=""):
    return os.environ.get(name, default).strip()


def _number(name, default):
    raw = _text(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"Environment variable {name} must be a whole number.") from None


def _id_list(name):
    ids = []
    for part in _text(name).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise RuntimeError(
                f"Environment variable {name} must contain only numeric Telegram user IDs separated by commas."
            )
        ids.append(int(part))
    return ids


# ---------------- Bot identity ----------------
# The bot token is read ONLY from TELEGRAM_BOT_TOKEN.
# A generic BOT_TOKEN variable (often injected by hosting panels such as Render)
# is deliberately ignored, so it can never silently replace the intended token.
BOT_TOKEN = _text("TELEGRAM_BOT_TOKEN")
GENERIC_BOT_TOKEN_PRESENT = bool(_text("BOT_TOKEN"))
BOT_NAME = "VIDEO & MUSIC DOWNLOADS"
APP_VERSION = "1.0.1"
ADMIN_IDS = _id_list("ADMIN_IDS")

# ---------------- Limits ----------------
MAX_UPLOAD_MB = _number("MAX_UPLOAD_MB", 50)  # Telegram Bot API upload limit for bots is 50 MB
MAX_CONCURRENT_DOWNLOADS = _number("MAX_CONCURRENT_DOWNLOADS", 2)

# ---------------- Storage ----------------
# Render Free uses an ephemeral filesystem: these files do not survive a restart or redeploy.
DOWNLOAD_DIR = Path(_text("DOWNLOAD_DIR") or BASE_DIR / "downloads")
DB_PATH = _text("DB_PATH") or str(BASE_DIR / "bot.db")

# ---------------- YouTube / cookies ----------------
# Cookies are used for YouTube requests only. Empty or missing file = public requests only.
COOKIES_PATH = Path(_text("COOKIES_PATH") or BASE_DIR / "cookies.txt")
# Optional yt-dlp extractor arguments, e.g. a PO token supplied by YOU. Never hardcoded.
# Format: youtube:po_token=web.gvs+YOUR_TOKEN   (see YOUTUBE_SETUP.md)
YT_EXTRACTOR_ARGS = _text("YT_EXTRACTOR_ARGS")

# ---------------- External tools (empty = auto-detect on PATH) ----------------
FFMPEG_PATH = _text("FFMPEG_PATH")
DENO_PATH = _text("DENO_PATH")

# ---------------- HTTP health endpoint (required by Render Web Services) ----------------
HOST = "0.0.0.0"
PORT = _number("PORT", 10000)
