"""Offline tests. Run from the project root:  python -m unittest discover -s tests -v

They run without network access. If yt-dlp or python-telegram-bot are not installed,
lightweight stand-ins are used, so the logic can still be checked.
"""

import asyncio
import os
import re
import sys
import tempfile
import types
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="vmd_test_"))
os.environ.update({
    "TELEGRAM_BOT_TOKEN": "123456789:" + "T" * 30,  # fake value built at runtime
    "ADMIN_IDS": "42",
    "COOKIES_PATH": str(TMP / "missing-cookies.txt"),
    "DOWNLOAD_DIR": str(TMP / "downloads"),
    "DB_PATH": str(TMP / "test.db"),
    "PORT": "0",
})

try:
    import yt_dlp  # noqa: F401
except ImportError:
    _stub = types.ModuleType("yt_dlp")
    _stub.YoutubeDL = None
    _stub.version = types.SimpleNamespace(__version__="stub")
    sys.modules["yt_dlp"] = _stub

try:
    import telegram  # noqa: F401
except ImportError:
    class _Any:
        def __init__(self, *a, **k): pass
        def __and__(self, other): return self
        def __invert__(self): return self
        def __getattr__(self, name): return _Any()
        def __call__(self, *a, **k): return _Any()

    def _mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    class TelegramError(Exception): pass
    class BadRequest(TelegramError): pass
    class Forbidden(TelegramError): pass
    class NetworkError(TelegramError): pass
    class RetryAfter(TelegramError): pass
    class InvalidToken(TelegramError): pass

    # Every name is an INSTANCE so attribute access like Update.ALL_TYPES works.
    _mod("telegram", Update=_Any(), InlineKeyboardButton=_Any(), InlineKeyboardMarkup=_Any(),
         KeyboardButton=_Any(), ReplyKeyboardMarkup=_Any(), ReplyKeyboardRemove=_Any())
    _mod("telegram.constants", ParseMode=_Any())
    _mod("telegram.error", TelegramError=TelegramError, BadRequest=BadRequest, Forbidden=Forbidden,
         NetworkError=NetworkError, RetryAfter=RetryAfter, InvalidToken=InvalidToken)
    _mod("telegram.ext", Application=_Any(), CallbackQueryHandler=_Any(), CommandHandler=_Any(),
         ContextTypes=_Any(), MessageHandler=_Any(), filters=_Any())

import config  # noqa: E402
import downloader  # noqa: E402
import health  # noqa: E402

COOKIE_HEADER = "# Netscape HTTP Cookie File"
FUTURE = 4102444800  # year 2100
PAST = 1000000000    # 2001


def _cookie_line(domain, name, value, expiry, tab=True, httponly=False):
    fields = [domain, "TRUE", "/", "TRUE", str(expiry), name, value]
    body = "\t".join(fields) if tab else " ".join(fields)
    return ("#HttpOnly_" + body) if httponly else body


class CookieTests(unittest.TestCase):
    def setUp(self):
        self.path = TMP / "cookies_case.txt"
        self.orig = config.COOKIES_PATH

    def tearDown(self):
        config.COOKIES_PATH = self.orig

    def _write(self, lines):
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        config.COOKIES_PATH = self.path

    def test_missing_file_means_public_mode(self):
        config.COOKIES_PATH = TMP / "does-not-exist.txt"
        summary = downloader.inspect_cookies()
        self.assertFalse(summary["exists"])
        self.assertIsNone(downloader.write_cookie_copy(TMP / "copy1.txt"))
        self.assertFalse(downloader.cookies_in_use("https://www.youtube.com/watch?v=x"))

    def test_placeholder_file_is_ignored(self):
        self._write([COOKIE_HEADER, "# comment only"])
        self.assertEqual(downloader.inspect_cookies()["entries"], 0)
        self.assertIsNone(downloader.write_cookie_copy(TMP / "copy2.txt"))

    def test_space_separated_entries_are_repaired_in_copy(self):
        self._write([
            COOKIE_HEADER,
            _cookie_line(".youtube.com", "A", "valueAAAA1", FUTURE, tab=False),
            _cookie_line(".youtube.com", "B", "valueBBBB2", FUTURE, tab=True, httponly=True),
        ])
        summary = downloader.inspect_cookies()
        self.assertEqual(summary["entries"], 2)
        self.assertEqual(summary["space_separated"], 1)
        dest = TMP / "copy3.txt"
        self.assertEqual(downloader.write_cookie_copy(dest), dest)
        text = dest.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(COOKIE_HEADER))
        self.assertIn("#HttpOnly_.youtube.com\tTRUE", text)
        data_lines = [l for l in text.splitlines() if l and not l.startswith("# ")]
        self.assertTrue(all(l.count("\t") == 6 for l in data_lines), "every entry must be tab-separated")
        self.assertEqual(oct(dest.stat().st_mode & 0o777), oct(0o600))

    def test_expired_entries_are_counted_not_printed(self):
        self._write([_cookie_line(".youtube.com", "OLD", "oldvalue99", PAST),
                     _cookie_line(".youtube.com", "NEW", "newvalue99", FUTURE)])
        summary = downloader.inspect_cookies()
        self.assertEqual((summary["entries"], summary["expired"]), (2, 1))

    def test_original_file_is_never_modified(self):
        original = "\n".join([COOKIE_HEADER, _cookie_line(".youtube.com", "A", "valueAAAA1", FUTURE, tab=False)]) + "\n"
        self._write(original.splitlines())
        downloader.write_cookie_copy(TMP / "copy4.txt")
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)


class RedactionTests(unittest.TestCase):
    def test_registered_cookie_value_is_redacted(self):
        downloader.register_secret("supersecretcookie123")
        self.assertNotIn("supersecretcookie123", downloader.redact("error near supersecretcookie123 here"))

    def test_bot_token_pattern_is_redacted(self):
        sample = "123456789" + ":" + "Q" * 30  # fake token, assembled at runtime
        out = downloader.redact("token " + sample + " leaked")
        self.assertNotIn("Q" * 30, out)
        self.assertIn("[redacted]", out)

    def test_user_message_never_contains_cookie_value(self):
        downloader.register_secret("cookievalue_XYZ_12345")
        msg = downloader.user_message(Exception("Sign in to confirm you're not a bot cookievalue_XYZ_12345"))
        self.assertNotIn("cookievalue_XYZ_12345", msg)


class FormatTests(unittest.TestCase):
    def test_video_format_never_exceeds_selected_height(self):
        fmt = downloader.video_format(720)
        self.assertEqual(fmt, "bestvideo[height<=720]+bestaudio/best[height<=720]/bestvideo[height<=720]")
        for alternative in fmt.split("/"):
            self.assertIn("height<=720", alternative, "every fallback must be capped at the selected height")

    def test_only_real_heights_are_offered(self):
        info = {"formats": [
            {"height": 1080, "vcodec": "avc1"},
            {"height": 720, "vcodec": "avc1"},
            {"height": 720, "vcodec": "vp9"},
            {"height": None, "vcodec": "none"},
            {"height": 480, "vcodec": "vp9"},
        ]}
        self.assertEqual(downloader.available_qualities(info), [1080, 720, 480])

    def test_no_video_formats_gives_no_fake_buttons(self):
        self.assertEqual(downloader.available_qualities({"formats": [{"vcodec": "none", "height": None}]}), [])

    def test_is_youtube_matches_only_real_youtube_hosts(self):
        for url in ("https://www.youtube.com/watch?v=a", "https://m.youtube.com/x", "https://youtu.be/a",
                    "https://www.youtube.com/shorts/abc"):
            self.assertTrue(downloader.is_youtube(url), url)
        for url in ("https://www.tiktok.com/@a/video/1", "https://notyoutube.com.evil.test/x",
                    "https://example.com/youtube.com"):
            self.assertFalse(downloader.is_youtube(url), url)

    def test_extractor_args_parsing(self):
        self.assertEqual(downloader._parse_extractor_args("youtube:po_token=web.gvs+ABC;player_client=web"),
                         {"youtube": {"po_token": ["web.gvs+ABC"], "player_client": ["web"]}})
        self.assertIsNone(downloader._parse_extractor_args(""))


class ErrorMessageTests(unittest.TestCase):
    def test_common_youtube_errors_map_to_clean_messages(self):
        cases = {
            "Sign in to confirm you’re not a bot": "not a bot",
            "Requested PO token for this client": "PO token",
            "Private video. Sign in if you've been granted access": "private",
            "This video may be inappropriate for some users. confirm your age": "age",
            "This video has been removed by the uploader": "unavailable",
            "HTTP Error 429: Too Many Requests": "rate-limiting",
            "Unsupported URL: https://example.com": "Unsupported URL",
            "ERROR: Unable to extract initial data": "extractor failed",
            "FFmpeg not found": "FFmpeg",
            "ERROR: [youtube] TGh1T8GtkrY: The page needs to be reloaded.": "reloaded",
        }
        for raw, needle in cases.items():
            msg = downloader.user_message(Exception(raw))
            self.assertTrue(msg.startswith(("❌", "⏳")), raw)
            self.assertIn(needle.lower(), msg.lower(), f"{raw!r} -> {msg!r}")

    def test_expired_cookie_message(self):
        msg = downloader.user_message(Exception("cookies are expired or invalid"))
        self.assertIn("expired or invalid", msg)

    def test_unknown_error_is_still_clean(self):
        msg = downloader.user_message(Exception("something odd happened"))
        self.assertIn("Download failed", msg)

    def test_bot_error_passes_message_through(self):
        self.assertEqual(downloader.user_message(downloader.BotError("❌ custom")), "❌ custom")


class RetryTests(unittest.TestCase):
    def test_transient_error_is_retried_then_succeeds(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise Exception("ERROR: [youtube] X: The page needs to be reloaded.")
            return "ok"

        with mock.patch.object(downloader.time, "sleep"):
            self.assertEqual(downloader.retry_transient(flaky, attempts=2), "ok")
        self.assertEqual(len(calls), 2)

    def test_permanent_error_is_not_retried(self):
        calls = []

        def private():
            calls.append(1)
            raise Exception("Private video. Sign in if you've been granted access")

        with mock.patch.object(downloader.time, "sleep"):
            with self.assertRaises(Exception):
                downloader.retry_transient(private, attempts=2)
        self.assertEqual(len(calls), 1)

    def test_transient_error_gives_up_after_last_attempt(self):
        calls = []

        def always_reloading():
            calls.append(1)
            raise Exception("The page needs to be reloaded")

        with mock.patch.object(downloader.time, "sleep"):
            with self.assertRaises(Exception):
                downloader.retry_transient(always_reloading, attempts=2)
        self.assertEqual(len(calls), 2)

    def test_reloaded_page_message_mentions_retry_and_fix(self):
        msg = downloader.user_message(Exception("ERROR: [youtube] abc: The page needs to be reloaded."))
        self.assertIn("retried once", msg)
        self.assertIn("Clear build cache", msg)


class HealthServerTests(unittest.TestCase):
    def test_single_health_server_responds_ok(self):
        server, status = health.start_health_server("127.0.0.1", 0)
        self.assertIsNotNone(server)
        self.assertTrue(status.startswith("OK on"))
        port = server.server_address[1]
        for path in ("/", "/health"):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                self.assertEqual(r.status, 200)
                self.assertEqual(r.read(), b"OK")
        # A second call must NOT start another server.
        server2, status2 = health.start_health_server("127.0.0.1", 0)
        self.assertIs(server2, server)
        self.assertEqual(status2, status)

    def test_port_conflict_does_not_crash(self):
        import socket
        saved = dict(health._state)
        try:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                busy = s.getsockname()[1]
                health._state.update(started=False, server=None, status="")
                server, status = health.start_health_server("127.0.0.1", busy)
                self.assertIsNone(server)
                self.assertIn("FAILED", status)
                self.assertIn("keeps running", status)
        finally:
            health._state.clear()
            health._state.update(saved)


class StartupAndBotTests(unittest.TestCase):
    def test_bot_module_imports_and_database_round_trip(self):
        import bot
        bot.db_init()
        bot.ensure_defaults()
        bot.set_setting("welcome_text", "hello test")
        self.assertEqual(bot.get_setting("welcome_text"), "hello test")
        self.assertTrue(bot.is_admin(42))
        self.assertFalse(bot.is_admin(7))

    def test_clean_url_and_safe_name(self):
        import bot
        self.assertEqual(bot.clean_url("https://a.test/x).,"), "https://a.test/x")
        self.assertNotIn("/", bot.safe_name("a/b:c"))

    def test_main_starts_without_polling_network(self):
        import bot
        fake_app = mock.MagicMock()
        fake_app.run_polling.return_value = None
        with mock.patch.object(bot, "build_application", return_value=fake_app):
            bot.main()
        fake_app.run_polling.assert_called_once()

    def test_main_refuses_missing_token(self):
        import bot
        with mock.patch.object(config, "BOT_TOKEN", ""):
            with self.assertRaises(SystemExit):
                bot.main()

    def test_generic_bot_token_variable_is_ignored(self):
        self.assertEqual(config.BOT_TOKEN, os.environ["TELEGRAM_BOT_TOKEN"])


def _project_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or "tests" in path.parts or "__pycache__" in path.parts:
            continue
        yield path


class ProjectHygieneTests(unittest.TestCase):
    def test_no_runtime_package_install_code(self):
        bad = re.compile(r"(pip|npm)\s+install|apt(-get)?\s+install|subprocess|os\.system|wget\s|curl\s", re.I)
        for path in ROOT.rglob("*.py"):
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(bad.search(text), f"runtime install pattern found in {path.name}")

    def test_no_hardcoded_secrets_from_original_project(self):
        # Shapes of real secrets: a Telegram bot token and a 32-hex Telegram API hash.
        token_shape = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")
        hash_shape = re.compile(r"\b[0-9a-f]{32}\b")
        for path in _project_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertIsNone(token_shape.search(text), f"token-shaped value in {path.relative_to(ROOT)}")
            self.assertIsNone(hash_shape.search(text), f"hash-shaped value in {path.relative_to(ROOT)}")

    def test_single_http_server_in_codebase(self):
        hits = []
        for path in _project_files():
            if path.suffix != ".py":
                continue
            text = path.read_text(encoding="utf-8")
            hits += [f"{path.name}:{m}" for m in re.findall(r"\b(HTTPServer|ThreadingHTTPServer|Flask)\(", text)]
        self.assertEqual(hits, ["health.py:ThreadingHTTPServer"], hits)

    def test_render_blueprint_has_no_secret_values(self):
        text = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\d{6,}:[A-Za-z0-9_-]{20,}")
        self.assertIn("healthCheckPath: /health", text)
        self.assertIn("plan: free", text)


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL: records options and writes a real file."""
    last_opts = None

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.last_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        name = "Title [abc]." + ("mp3" if self.opts.get("postprocessors") else "mp4")
        target = Path(self.opts["outtmpl"].replace("%(title).120B [%(id)s].%(ext)s", name))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * 1000)
        for hook in self.opts.get("progress_hooks", []):
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100, "speed": 1000, "eta": 1})


class PipelineTests(unittest.TestCase):
    def test_video_options_mp4_capped_and_real_progress(self):
        media = TMP / "pipe_video"
        media.mkdir(parents=True, exist_ok=True)
        seen = []
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", FakeYDL):
            path = downloader.download_media("https://www.youtube.com/watch?v=1", "video", 720, media, None, seen.append)
        opts = FakeYDL.last_opts
        self.assertEqual(path.suffix, ".mp4")
        self.assertEqual(opts["merge_output_format"], "mp4")
        self.assertEqual(opts["remux_video"], "mp4")
        self.assertIn("height<=720", opts["format"])
        self.assertNotIn("postprocessors", opts)
        self.assertTrue(any("Downloading… 50%" in s for s in seen), seen)

    def test_audio_options_mp3_postprocessing(self):
        media = TMP / "pipe_audio"
        media.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", FakeYDL):
            path = downloader.download_media("https://youtu.be/abc", "audio", None, media, None, lambda t: None)
        self.assertEqual(path.suffix, ".mp3")
        self.assertEqual(FakeYDL.last_opts["postprocessors"][0]["key"], "FFmpegExtractAudio")
        self.assertEqual(FakeYDL.last_opts["postprocessors"][0]["preferredcodec"], "mp3")

    def test_cookies_only_go_to_youtube(self):
        media = TMP / "pipe_cookie_scope"
        media.mkdir(parents=True, exist_ok=True)
        fake_cookie = TMP / "cookie_copy_scope.txt"
        fake_cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", FakeYDL):
            downloader.download_media("https://www.tiktok.com/@a/video/1", "video", 720, media, fake_cookie, lambda t: None)
            self.assertNotIn("cookiefile", FakeYDL.last_opts)
            downloader.download_media("https://www.youtube.com/watch?v=1", "video", 720, media, fake_cookie, lambda t: None)
            self.assertEqual(FakeYDL.last_opts["cookiefile"], str(fake_cookie))


class DeliveryTests(unittest.TestCase):
    ITEM = {"url": "https://www.youtube.com/watch?v=1", "info": {"title": "Title", "uploader": "Uploader"}}

    def _fake_message(self):
        status = mock.MagicMock()
        status.edit_text = mock.AsyncMock()
        status.delete = mock.AsyncMock()
        message = mock.MagicMock()
        message.reply_text = mock.AsyncMock(return_value=status)
        message.reply_video = mock.AsyncMock()
        message.reply_audio = mock.AsyncMock()
        return message, status

    def test_file_exists_during_send_and_workspace_removed_after(self):
        import bot
        message, status = self._fake_message()
        seen = {}
        roots = []

        async def fake_send(**kwargs):
            seen["file_exists_during_send"] = Path(kwargs["video"].name).exists()

        message.reply_video.side_effect = fake_send

        def fake_download(url, mode, height, media_dir, cookiefile, on_progress):
            roots.append(Path(media_dir).parent)
            path = Path(media_dir) / "Title [abc].mp4"
            path.write_bytes(b"x" * 100)
            return path

        with mock.patch.object(downloader, "download_media", side_effect=fake_download):
            asyncio.run(bot.perform_download(message, self.ITEM, "video", 720, None))
        self.assertTrue(seen["file_exists_during_send"])
        self.assertFalse(roots[0].exists(), "temporary folder must be removed after the upload")
        message.reply_video.assert_awaited_once()
        status.delete.assert_awaited_once()

    def test_error_is_clean_no_secret_and_workspace_removed(self):
        import bot
        downloader.register_secret("hunter2hunter2")
        message, status = self._fake_message()
        roots = []

        def failing(url, mode, height, media_dir, cookiefile, on_progress):
            roots.append(Path(media_dir).parent)
            raise Exception("Sign in to confirm you're not a bot hunter2hunter2")

        with mock.patch.object(downloader, "download_media", side_effect=failing):
            asyncio.run(bot.perform_download(message, self.ITEM, "video", 720, None))
        text = status.edit_text.await_args_list[-1].args[0]
        self.assertIn("not a bot", text)
        self.assertNotIn("hunter2hunter2", text)
        self.assertFalse(roots[0].exists())
        message.reply_video.assert_not_awaited()

    def test_oversized_file_gets_clear_message(self):
        import bot
        message, status = self._fake_message()

        def big(url, mode, height, media_dir, cookiefile, on_progress):
            path = Path(media_dir) / "Big [abc].mp4"
            path.write_bytes(b"x" * 2048)
            return path

        with mock.patch.object(config, "MAX_UPLOAD_MB", 0), mock.patch.object(downloader, "download_media", side_effect=big):
            asyncio.run(bot.perform_download(message, self.ITEM, "video", 720, None))
        text = status.edit_text.await_args_list[-1].args[0]
        self.assertIn("too large", text.lower())
        message.reply_video.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
