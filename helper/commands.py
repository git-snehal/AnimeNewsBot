"""The slash-command menu, hardcoded.  Everyone sees PUBLIC_COMMANDS; admins see ADMIN_COMMANDS."""
import logging

from pyrogram.types import BotCommand, BotCommandScopeChat

from config import ADMIN_IDS

log = logging.getLogger(__name__)

PUBLIC_COMMANDS = [
    BotCommand("start", "Show what this bot does"),
    BotCommand("help", "List the available commands"),
    BotCommand("ping", "Check that the bot is alive"),
]

ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand("stats", "Posts today (or: yesterday, week)"),
    BotCommand("status", "Bot health, feeds and next check"),
    BotCommand("fetchnow", "Check the feeds right now"),
    BotCommand("pause", "Pause posting"),
    BotCommand("resume", "Resume posting"),
    BotCommand("setcookies", "Reply to a cookies.txt file to update yt-dlp cookies"),
]


def help_text(is_admin: bool) -> str:
    cmds = ADMIN_COMMANDS if is_admin else PUBLIC_COMMANDS
    return "\n".join(f"/{c.command} — {c.description}" for c in cmds)


async def set_admin_menu(app, chat_id: int) -> bool:
    try:
        await app.set_bot_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id))
        return True
    except Exception as e:
        # Telegram only allows this once the admin has opened the bot (pressed Start)
        log.warning("Could not set the admin menu for %s yet (%s). It is set when they send /start.", chat_id, e)
        return False


async def register_commands(app):
    await app.set_bot_commands(PUBLIC_COMMANDS)  # default scope: everybody
    for admin_id in ADMIN_IDS:
        await set_admin_menu(app, admin_id)
