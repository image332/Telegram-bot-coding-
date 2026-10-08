"""yt-dlp, FFmpeg, Deno and cookie handling.

Security rules enforced here:
  * cookie VALUES are never printed, returned to Telegram, or put in error text;
  * every message that leaves this module passes through redact();
  * each request gets its own private temporary cookie copy, deleted afterwards.
"""

import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

import config

YOUTUBE_DOMAINS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
COOKIE_HEADER = "# Netscape HTTP Cookie File"
HTTPONLY_PREFIX = "#HttpOnly_"
MIN_SECRET_LENGTH = 8

_SECRETS = set()
_TOKEN_PATTERN = re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}")


class BotError(Exception):
    """Raised with a message that is already written for the Telegram user."""


# ----------------------------------------------------------------- secrets

def register_secret(value):
    value = (value or "").strip()
    if len(value) >= MIN_SECRET_LENGTH:
        _SECRETS.add(value)


def redact(text):
    text = str(text)
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return _TOKEN_PATTERN.sub("[redacted]", text)


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ----------------------------------------------------------------- cookies

def is_youtube(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in YOUTUBE_DOMAINS)


def _split_cookie_fields(body):
    """Accept tab-separated (standard) or space-separated (common copy/paste damage) lines."""
    fields = body.split("\t") if "\t" in body else body.split(None, 6)
    return fields if len(fields) == 7 else None


def _iter_cookie_lines(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(HTTPONLY_PREFIX):
            yield HTTPONLY_PREFIX, line[len(HTTPONLY_PREFIX):]
        elif line.startswith("#"):
            continue  # comment / header line
        else:
            yield "", line


def inspect_cookies():
    """Return a SAFE summary of the cookie file: counts only, never values."""
    path = Path(config.COOKIES_PATH)
    summary = {"exists": path.is_file(), "entries": 0, "expired": 0, "space_separated": 0}
    if not summary["exists"]:
        return summary
    now = time.time()
    try:
        for _prefix, body in _iter_cookie_lines(path):
            fields = _split_cookie_fields(body)
            if not fields:
                continue
            summary["entries"] += 1
            if "\t" not in body:
                summary["space_separated"] += 1
            register_secret(fields[6])  # value is registered for redaction only
            try:
                expiry = int(float(fields[4]))
            except ValueError:
                expiry = 0
            if expiry and expiry < now:
                summary["expired"] += 1
    except OSError:
        summary["entries"] = 0
    return summary


def write_cookie_copy(dest):
    """Write a private, repaired copy of the cookie file for ONE request.

    Returns the copy's path, or None when there are no usable entries
    (missing file, placeholder file, or unreadable file). The original file is never modified.
    """
    src = Path(config.COOKIES_PATH)
    if not src.is_file():
        return None
    lines = [COOKIE_HEADER]
    try:
        for prefix, body in _iter_cookie_lines(src):
            fields = _split_cookie_fields(body)
            if fields:
                lines.append(prefix + "\t".join(fields))
    except OSError:
        return None
    if len(lines) == 1:
        return None
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        dest.chmod(0o600)
    except OSError:
        pass
    return dest


def cookies_in_use(url):
    return is_youtube(url) and inspect_cookies()["entries"] > 0


@contextmanager
def request_workspace(url):
    """Private temp folder for one request: media in media/, cookie copy at the root.

    Everything is deleted when the block exits. Use it so files are removed only
    after Telegram has accepted the upload.
    """
    config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="vmd_", dir=str(config.DOWNLOAD_DIR)))
    try:
        media_dir = root / "media"
        media_dir.mkdir()
        cookiefile = write_cookie_copy(root / "cookies.txt") if is_youtube(url) else None
        yield media_dir, cookiefile
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------- tools

def find_ffmpeg():
    for candidate in (config.FFMPEG_PATH, "ffmpeg"):
        if candidate:
            found = shutil.which(candidate)
            if found:
                return found
    try:  # optional fallback when installed via pip (no ffprobe in this case)
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def find_deno():
    for candidate in (config.DENO_PATH, "deno"):
        if candidate:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def _ytdlp_version():
    try:
        return yt_dlp.version.__version__
    except Exception:
        return "unknown"


def startup_report():
    """Safe status lines for the console. Never prints paths to secrets or cookie values."""
    lines = []
    if find_ffmpeg():
        note = "" if shutil.which("ffprobe") else " (ffprobe not found; some conversions may be limited)"
        lines.append(f"[BOOT] FFmpeg: OK{note}")
    else:
        lines.append("[BOOT] FFmpeg: MISSING. MP4 merge and MP3 conversion will fail. Use the Docker image or install FFmpeg.")
    lines.append(f"[BOOT] yt-dlp: OK (version {_ytdlp_version()})")
    if find_deno():
        lines.append("[BOOT] Deno: OK")
    else:
        lines.append("[BOOT] Deno: MISSING. Some YouTube downloads will fail. Use the Docker image or set DENO_PATH.")
    cookies = inspect_cookies()
    if not cookies["exists"]:
        lines.append("[BOOT] cookies.txt: not found. YouTube uses public requests only.")
    elif cookies["entries"] == 0:
        lines.append("[BOOT] cookies.txt: found but has no cookie entries. It will be ignored.")
    else:
        extra = f", {cookies['expired']} expired" if cookies["expired"] else ""
        lines.append(f"[BOOT] cookies.txt: {cookies['entries']} entries loaded{extra}.")
        if cookies["space_separated"]:
            lines.append("[BOOT] cookies.txt: some entries use spaces instead of tabs; they are repaired in a private copy.")
    return lines


# ----------------------------------------------------------------- yt-dlp options

def _parse_extractor_args(raw):
    """Parse YT_EXTRACTOR_ARGS, e.g. 'youtube:po_token=web.gvs+TOKEN'. Passed through, never invented."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("youtube:"):
        raw = raw[len("youtube:"):]
    args = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        args.setdefault(key.strip(), []).append(value.strip())
    return {"youtube": args} if args else None


def _base_options(url, cookiefile):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 2,
    }
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    if is_youtube(url):
        deno = find_deno()
        if deno:
            opts["js_runtimes"] = {"deno": {"path": deno}}
        if cookiefile:
            opts["cookiefile"] = str(cookiefile)
        extractor_args = _parse_extractor_args(config.YT_EXTRACTOR_ARGS)
        if extractor_args:
            opts["extractor_args"] = extractor_args
    return opts


TRANSIENT_MARKERS = (
    "page needs to be reloaded",
    "unable to extract",
    "http error 429",
    "http error 503",
    "timed out",
    "connection reset",
    "temporarily unavailable",
)


def is_transient(exc):
    """Temporary YouTube/network problems that are often fixed by trying again shortly."""
    low = str(exc).lower()
    return any(marker in low for marker in TRANSIENT_MARKERS)


def retry_transient(action, attempts=2, delay_seconds=4):
    """Run action(); retry only on transient errors. Other errors are raised immediately."""
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:
            if attempt < attempts and is_transient(exc):
                print(f"[RETRY] Temporary error, attempt {attempt + 1} of {attempts} in {delay_seconds}s", flush=True)
                time.sleep(delay_seconds)
                continue
            raise


def extract_info(url):
    """Read metadata and formats only. Nothing is downloaded. Retries once on temporary errors."""
    with request_workspace(url) as (_media_dir, cookiefile):
        def attempt():
            opts = _base_options(url, cookiefile)
            opts["skip_download"] = True
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        return retry_transient(attempt)


def available_qualities(info):
    """Heights that really exist in the media, highest first. Never invents a quality."""
    heights = set()
    for f in info.get("formats") or []:
        if f.get("vcodec") == "none":
            continue  # audio-only stream
        try:
            height = int(f.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        if height > 0:
            heights.add(height)
    return sorted(heights, reverse=True)[:10]


def video_format(height):
    """Every branch is capped at the selected height. There is no uncapped fallback."""
    h = int(height)
    return f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/bestvideo[height<={h}]"


def make_progress_hook(on_progress):
    """Forward REAL yt-dlp progress (throttled to one update every 2 seconds)."""
    state = {"last": 0.0, "pct": -1}

    def hook(d):
        if d.get("status") != "downloading":
            return
        now = time.monotonic()
        if now - state["last"] < 2:
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        pct = int(done * 100 / total) if total else -1
        if pct >= 0 and pct == state["pct"]:
            return
        state.update(last=now, pct=pct)
        lines = [f"📥 **Downloading… {pct}%**" if pct >= 0 else "📥 **Downloading…**"]
        speed = d.get("speed")
        if speed:
            lines.append(f"⚡ {human_size(speed)}/s")
        eta = d.get("eta")
        if eta is not None:
            lines.append(f"⏳ ETA: {int(eta)}s")
        on_progress("\n".join(lines))

    return hook


def download_media(url, mode, height, media_dir, cookiefile, on_progress):
    """Download into media_dir and return the finished file (MP4 for video, MP3 for audio)."""
    expected = "mp3" if mode == "audio" else "mp4"

    def build_options():
        opts = _base_options(url, cookiefile)
        opts["outtmpl"] = str(Path(media_dir) / "%(title).120B [%(id)s].%(ext)s")
        opts["progress_hooks"] = [make_progress_hook(on_progress)]
        if mode == "audio":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ]
        else:
            opts["format"] = video_format(height)
            opts["merge_output_format"] = "mp4"
            opts["remux_video"] = "mp4"
        return opts

    def attempt():
        with yt_dlp.YoutubeDL(build_options()) as ydl:
            ydl.download([url])

    retry_transient(attempt)
    files = [
        p for p in Path(media_dir).rglob("*")
        if p.is_file() and p.suffix.lower() not in (".part", ".ytdl", ".temp")
    ]
    matching = [p for p in files if p.suffix.lower() == "." + expected]
    if not matching:
        raise BotError(
            f"❌ Could not produce the {expected.upper()} file.\n\n"
            "FFmpeg may be missing or failed. Please try again or contact the admin."
        )
    return max(matching, key=lambda p: p.stat().st_size)


# ----------------------------------------------------------------- user messages

def user_message(exc, url=""):
    """Turn any download/extraction error into a clean Telegram message. Never includes cookie values."""
    if isinstance(exc, BotError):
        return str(exc)
    raw = redact(str(exc)).strip()
    low = raw.lower()
    cookies_used = cookies_in_use(url) if url else False
    youtube = bool(url) and is_youtube(url)

    def has(*needles):
        return any(n in low for n in needles)

    if has("not a bot", "sign in to confirm you"):
        if cookies_used:
            return (
                "❌ YouTube asked for verification (\"not a bot\") and rejected the cookies.txt that was supplied.\n\n"
                "Export fresh cookies from a logged-in browser, replace the cookies file, and try again. See YOUTUBE_SETUP.md."
            )
        return (
            "❌ YouTube asked for verification (\"not a bot\").\n\n"
            "This video needs a cookies.txt exported from a logged-in browser. Upload it to COOKIES_PATH and try again. "
            "See YOUTUBE_SETUP.md."
        )
    if has("page needs to be reloaded", "needs to be reloaded"):
        return (
            "❌ YouTube returned an incomplete page (\"The page needs to be reloaded\").\n\n"
            "The bot already retried once. This is usually temporary, so try again in a few minutes.\n\n"
            "If it keeps happening: in Render choose Manual Deploy → Clear build cache & deploy (this updates yt-dlp), "
            "and upload a fresh cookies.txt. See YOUTUBE_SETUP.md."
        )
    if has("po token", "po_token", "gvs token"):
        return (
            "❌ YouTube requires a PO token for this request.\n\n"
            "Configure YT_EXTRACTOR_ARGS as described in YOUTUBE_SETUP.md."
        )
    if re.search(r"\bdeno\b", low) or has("javascript runtime", "js runtime", "js_runtime"):
        return (
            "❌ The JavaScript runtime (Deno) needed for YouTube is missing or failed.\n\n"
            "Run the Docker image, or install Deno and set DENO_PATH. See YOUTUBE_SETUP.md."
        )
    if has("cookie") and has("expired", "invalid", "malformed"):
        return (
            "❌ The cookies.txt file was rejected (expired or invalid).\n\n"
            "Export a fresh cookies.txt and upload it again. See YOUTUBE_SETUP.md."
        )
    if has("members only", "members-only", "join this channel"):
        return "❌ This video is for channel members only. The bot cannot download it."
    if has("confirm your age", "age-restricted", "age restricted", "inappropriate for some users"):
        if cookies_used:
            return (
                "❌ This video is age-restricted, and the supplied cookies are not from an age-verified account.\n\n"
                "Upload cookies from an age-verified, logged-in browser. See YOUTUBE_SETUP.md."
            )
        return (
            "❌ This video is age-restricted.\n\n"
            "Upload a cookies.txt from an age-verified, logged-in browser. See YOUTUBE_SETUP.md."
        )
    if has("private video"):
        return "❌ This video is private."
    if has(
        "video unavailable", "has been removed", "removed by the uploader", "this video is not available",
        "no longer available", "account associated with this video has been terminated",
    ):
        return "❌ This video is unavailable (deleted, removed or terminated)."
    if has("not available in your country", "geo restricted", "geo-restricted", "geo_restricted", "blocked in your country"):
        return "❌ This content is blocked in the server's region."
    if has("requested format is not available", "format is not available", "no video formats found"):
        if youtube and not find_deno():
            return (
                "❌ YouTube formats could not be read because the Deno JavaScript runtime is missing.\n\n"
                "Run the Docker image, or set DENO_PATH. See YOUTUBE_SETUP.md."
            )
        return "❌ No downloadable format is available at or below the selected quality.\n\nTry a lower quality."
    if has("sign in", "login", "log in", "authentication", "use --cookies", "cookies"):
        if youtube:
            return (
                "❌ This YouTube content requires login.\n\n"
                "Upload a cookies.txt exported from a logged-in browser. See YOUTUBE_SETUP.md."
            )
        return "❌ This content requires login and cannot be downloaded by the bot."
    if has("ffmpeg", "ffprobe", "postprocessing"):
        return "❌ FFmpeg failed or is missing.\n\nRun the Docker image, or install FFmpeg. See README.md."
    if has("http error 429", "too many requests", "rate limit"):
        return "⏳ The site is rate-limiting requests. Please wait a few minutes and try again."
    if has("unsupported url"):
        return "❌ Unsupported URL.\n\nSend a direct public video, reel or media link."
    if has("unable to extract", "extractor error"):
        return "❌ The site's extractor failed. The site may have changed. Try again later; yt-dlp may need an update."
    if has("file is larger", "file size", "too large"):
        return "❌ File too large for Telegram."
    if has("http error 403"):
        return "❌ The site refused the request (HTTP 403)."
    tail = raw[-400:] if raw else "unknown error"
    return f"❌ Download failed.\n\n{tail}"
