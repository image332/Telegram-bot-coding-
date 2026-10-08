# Changelog

## 1.0.1
- Added a clear message for YouTube's "The page needs to be reloaded" error, with the steps to fix it.
- Temporary YouTube and network errors (page reload, extractor failure, HTTP 429/503, timeouts) are retried once automatically, for both extraction and download.
- Startup log now shows the version number.
- Documentation: troubleshooting rows added to YOUTUBE_SETUP.md and RENDER_DEPLOYMENT.md.
- Tests: 38 offline tests (added retry and error-message tests).

## 1.0.0
- First complete version: YouTube cookies.txt support, Deno and FFmpeg checks, quality detection, real progress, Render Docker deployment with a single health endpoint, secrets moved to environment variables.
