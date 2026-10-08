# Deploying on Render Free (Web Service)

This guide deploys the bot as one **free Web Service** using the Dockerfile. The bot runs with Telegram polling and also serves a health endpoint, which Render needs.

## 1. Upload the project to GitHub

1. Create a **private** GitHub repository.
2. Upload the project files to its root (the files, not a folder inside a folder).
3. Confirm that `cookies.txt` is **not** the real file. The repository should contain only the placeholder. `.gitignore` already prevents committing real cookies.

## 2. Create a Render account

Sign up at https://render.com and connect your GitHub account.

## 3. Create the service

1. In the Render dashboard choose **New → Web Service**.
2. Select your GitHub repository.
3. Render detects `render.yaml` and the `Dockerfile`. You can also set the values by hand:
   - **Runtime:** Docker
   - **Dockerfile path:** `./Dockerfile`
   - **Instance type:** **Free**

## 4. Build and start commands

You do not enter these manually for Docker:

- **Build:** Render runs the Dockerfile. It installs FFmpeg, Deno and the Python packages during the build.
- **Start:** The Dockerfile runs `python bot.py`. Leave the start command empty.

Do not use a native Python runtime for this bot. It cannot guarantee FFmpeg and Deno.

## 5. Health check

`render.yaml` sets the health check path to `/health`. The bot answers `OK` there. The check is handled by Render automatically.

## 6. Environment variables

In the service's **Environment** tab, add:

| Key | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your token from @BotFather (required) |
| `ADMIN_IDS` | Your numeric Telegram user ID, e.g. `123456789` (required for the admin panel) |

The other variables are already defined in `render.yaml` with safe defaults. Do not add a variable named `BOT_TOKEN`. If Render sets one, the bot ignores it. It only uses `TELEGRAM_BOT_TOKEN`.

To find your numeric ID, message @userinfobot in Telegram.

## 7. Deploy

Click **Create Web Service** or **Manual Deploy → Deploy latest commit**. The first build can take several minutes.

## 8. Check the logs

Open the service's **Logs** tab. A healthy start shows lines like these:

```
[BOOT] Starting VIDEO & MUSIC DOWNLOADS
[BOT] Token configured (from TELEGRAM_BOT_TOKEN)
[BOOT] FFmpeg: OK
[BOOT] yt-dlp: OK (version …)
[BOOT] Deno: OK
[BOOT] cookies.txt: not found. YouTube uses public requests only.
[BOOT] Health server: OK on 0.0.0.0:10000
[BOOT] Starting Telegram polling...
[BOT] Telegram connection: OK
[BOT] Username: @your_bot
```

If a line says `MISSING`, check the Dockerfile build log. The bot keeps running even then, and it reports the problem to users.

## 9. Verify the bot is online

Open your bot in Telegram and send `/start`. You should see the welcome message. Send `/admin` from your admin account to open the panel.

## 10. Upload cookies.txt (YouTube verification)

YouTube sometimes asks for verification. A `cookies.txt` exported from a logged-in browser solves this. Full instructions are in `YOUTUBE_SETUP.md`.

**Where to put it on Render:** use a **Secret File**, which Render mounts read-only.

1. In your service, open **Environment**.
2. Scroll to **Secret Files** and click **Add Secret File**.
3. Filename: `cookies.txt`
4. Contents: paste the full text of your exported cookies file.
5. Save. Render mounts it at `/etc/secrets/cookies.txt`.
6. The Blueprint already sets `COOKIES_PATH=/etc/secrets/cookies.txt`. If you changed it, set it back to that path.
7. Render redeploys the service. The log will show `[BOOT] cookies.txt: N entries loaded`.

Secret Files are the supported way to supply cookies on Render. Your cookies are not stored in GitHub or in the image.

**Recreation:** cookies come from your Secret File, so they survive restarts. If you delete the Secret File or recreate the service, add it again.

## 11. FFmpeg, yt-dlp and Deno status

| Component | How it is provided | Checked at |
|---|---|---|
| FFmpeg (ffmpeg and ffprobe) | Installed by the Dockerfile from Debian packages | Build and startup log |
| yt-dlp | Installed from `requirements.txt` during the build | Build and startup log |
| Deno | Installed by the Dockerfile during the build, at the version set by `DENO_VERSION` | Build and startup log |

Nothing is downloaded or installed when the bot starts.

## 12. Where cookies.txt goes in the repository

Nowhere. The repository has only the placeholder `cookies.txt`, which contains no cookies. Your real cookies belong only in the Render Secret File from step 10. For local runs, put your file anywhere and set `COOKIES_PATH` to its path.

## 13. Render Free spin-down

Render Free Web Services **spin down after a period without inbound HTTP traffic** (about 15 minutes). The bot uses outbound Telegram polling, which does not count as inbound traffic. Your bot may therefore be asleep when you send a message.

- A Telegram message does **not** wake a sleeping Render service. The service starts again only when Render receives an HTTP request, for example a browser visit to `https://YOUR-SERVICE.onrender.com/health` or a monitor's ping. Messages sent while it sleeps wait in Telegram, and they are processed once the bot is running again (Telegram keeps them for up to 24 hours).
- The first reply after a sleep can take longer.
- The bot cannot promise to stay awake. If you want it awake all the time, point an external uptime monitor (for example UptimeRobot, free plan) at `https://YOUR-SERVICE.onrender.com/health` every 5 minutes. This is your choice and is not configured by the project.

## 14. Local files are not persistent

Render Free uses an ephemeral filesystem. The following are **lost on every restart or redeploy**:

- temporary downloads (they are always deleted after a successful upload anyway)
- `bot.db`, which holds the welcome text and photo, developer and force-join settings, the user list, statistics and bans
- anything you upload directly to the server

The bot recreates its folders at startup, so a restart is safe. Admin settings and bans must be set up again after a redeploy. Use a paid plan with a persistent disk if you need them to survive.

## 15. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Deploy fails at "Health check" | The service did not listen on `PORT`. Check the logs for `Health server: OK`. |
| `TELEGRAM_BOT_TOKEN is missing` in logs | Add the variable in the Environment tab. |
| `Telegram rejected TELEGRAM_BOT_TOKEN` | The token is wrong or was revoked. Create a new one with @BotFather (`/revoke`). |
| `Conflict: terminated by other getUpdates request` | Two instances are polling at once, which happens briefly during a redeploy. It resolves on its own. If it lasts, suspend the extra instance. |
| `FFmpeg: MISSING` | Rebuild the image with **Clear build cache & deploy**. |
| YouTube "not a bot" errors | Upload a fresh cookies file (step 10). |
| "The page needs to be reloaded" | Usually temporary; the bot retries once. If it continues: Manual Deploy → **Clear build cache & deploy** (updates yt-dlp), then refresh cookies (step 10). |

## Security reminders

- Never paste your token, API hash or cookies into GitHub, chat messages or screenshots.
- If a secret was exposed, revoke it: for the bot token, use @BotFather `/revoke`. Update the value in Render afterwards.
