"""Logging in to Telegram, with retries.

`RPC_CALL_FAIL` (HTTP 500) during login is a Telegram-side error, not a problem with your token or API keys
(those give 400 errors such as ACCESS_TOKEN_INVALID). It often goes away after a few minutes, so we retry
instead of crashing.
"""
import asyncio
import glob
import logging
import os

from pyrogram.errors import AccessTokenExpired, AccessTokenInvalid, ApiIdInvalid, FloodWait, InternalServerError

log = logging.getLogger(__name__)

BAD_CONFIG = (AccessTokenInvalid, AccessTokenExpired, ApiIdInvalid)


def _forget_half_finished_login(session_name: str):
    """A login that died half way can leave an unusable session file behind; start from a clean one."""
    for path in glob.glob(f"{session_name}.session*"):
        try:
            os.remove(path)
            log.info("Removed %s so the next attempt starts a fresh login.", path)
        except OSError:
            pass


async def start_with_retry(client, session_name: str, attempts: int = 20, base_delay: int = 15,
                           max_delay: int = 120, sleep=asyncio.sleep):
    for attempt in range(1, attempts + 1):
        try:
            await client.start()
            return
        except BAD_CONFIG as e:
            raise SystemExit(f"Telegram rejected your credentials ({type(e).__name__}). "
                             "Check BOT_TOKEN, API_ID and API_HASH in config.env.") from e
        except FloodWait as e:
            log.warning("Telegram asked us to wait %ss before logging in.", e.value)
            await sleep(e.value + 1)
        except (InternalServerError, ConnectionError, TimeoutError, OSError) as e:
            if attempt == attempts:
                raise
            delay = min(base_delay * 2 ** (attempt - 1), max_delay)
            log.warning("Telegram login failed (%s: %s). That is on Telegram's side, not your token. "
                        "Attempt %d/%d, retrying in %ds.", type(e).__name__, str(e)[:120], attempt, attempts, delay)
            if "ImportBotAuthorization" in str(e):
                _forget_half_finished_login(session_name)
            await sleep(delay)
