# VIDEO & MUSIC DOWNLOADS — Telegram bot

**Version 1.0.1**

A Telegram bot that downloads public video or audio links and sends them back as an MP4 video or an MP3 file. It supports YouTube (including Shorts), TikTok, Instagram, Facebook, X/Twitter, Reddit and Pinterest through yt-dlp.

Deployment target: **Render Free Web Service** using the included Dockerfile. See `RENDER_DEPLOYMENT.md`.
YouTube setup (cookies, Deno, FFmpeg, PO tokens): see `YOUTUBE_SETUP.md`.

## Features

- Send a link, choose **Video** or **Audio**. Video shows only the qualities that really exist and downloads at or below the one you pick.
- Audio is converted to MP3 with FFmpeg. Video is delivered as MP4.
- Real download progress from yt-dlp.
- Admin panel: statistics, welcome photo and text, developer button, force-join channel, broadcast, ban and unban.
- YouTube `cookies.txt` support. Cookies are used for YouTube requests only.
- Clean Telegram error messages. A failed download never stops the bot.

## Project layout

| File | Purpose |
|---|---|
| `bot.py` | Telegram bot: handlers, admin panel, download flow, startup |
| `config.py` | **All settings**, read from environment variables |
| `downloader.py` | yt-dlp, FFmpeg, Deno, cookies, progress, error messages |
| `health.py` | The single HTTP health endpoint (`GET /` and `GET /health` return `OK`) |
| `requirements.txt` | Python packages (installed at build time only) |
| `Dockerfile` | Image with FFmpeg, Deno, yt-dlp and the bot |
| `render.yaml` | Render Blueprint for one free web service |
| `cookies.txt` | **Empty placeholder.** Replace only on your server, never commit real cookies |
| `.env.example` | Environment variable template (no values) |
| `tests/test_offline.py` | Offline tests (no network needed) |

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **Yes** | none | Your bot token from @BotFather. This is the only variable the bot reads for the token. |
| `ADMIN_IDS` | Yes, for admin | none | Comma-separated numeric Telegram user IDs |
| `PORT` | No | `10000` | HTTP port. Render sets this automatically. |
| `COOKIES_PATH` | No | `./cookies.txt` | Path to the YouTube cookies file. Missing or empty means public requests only. |
| `DOWNLOAD_DIR` | No | `./downloads` | Temporary folder. Render: `/tmp/vmd-downloads`. |
| `DB_PATH` | No | `./bot.db` | SQLite file for settings, users and bans |
| `MAX_UPLOAD_MB` | No | `50` | Upper size limit for uploads. Telegram's Bot API limit for bots is 50 MB. |
| `MAX_CONCURRENT_DOWNLOADS` | No | `2` | Simultaneous downloads |
| `FFMPEG_PATH` | No | auto | Explicit FFmpeg path |
| `DENO_PATH` | No | auto | Explicit Deno path |
| `YT_EXTRACTOR_ARGS` | No | empty | Extra YouTube arguments that you supply, such as a PO token. See `YOUTUBE_SETUP.md`. |

**Token safety.** A generic `BOT_TOKEN` variable is ignored on purpose. Hosting panels sometimes set one, and it must never replace your bot's token. The bot logs only `Token configured` and the detected username. It never prints the token, cookie values or API secrets.

## Running locally

Requirements: Python 3.12 or newer, FFmpeg on your PATH, and optionally Deno for YouTube.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="your-token-from-botfather"
export ADMIN_IDS="your-numeric-id"
python bot.py
```

On startup you will see status lines such as `[BOOT] FFmpeg: OK`, `[BOOT] Deno: OK`, `[BOOT] cookies.txt: not found` and `[BOOT] Health server: OK on 0.0.0.0:10000`.

Run the offline tests with:

```bash
python -m unittest discover -s tests -v
```

## Security

- Secrets live only in environment variables or the hosting dashboard. Nothing secret is stored in the source code.
- `cookies.txt` is in `.gitignore` and `.dockerignore`, and the committed copy is an empty placeholder.
- Cookie values are never printed, sent to Telegram or included in error messages. Each request uses a private temporary copy of the cookies, deleted when the request ends.
- If you ever pasted a bot token into a public place, revoke it in @BotFather with `/revoke` and use the new token.

## Limitations

- Downloads are limited to what yt-dlp can reach. Private, members-only, region-blocked and login-protected content is not downloaded.
- Telegram limits bot uploads to 50 MB by default. Larger files get a clear message.
- On Render Free, local files and `bot.db` are lost on restart or redeploy. See `RENDER_DEPLOYMENT.md`.
