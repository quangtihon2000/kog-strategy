"""/logs (one-shot tail) and /tail (streaming, batched every 5s).

`/tail` registers a per-chat JobQueue job that polls the active log file
every 5s. New bytes are accumulated in `bot_data["tail_buffers"]` and
flushed as one Telegram message per tick — keeps message rate well under
Telegram's 30 msg/s limit even with chatty agents. `/tailstop` cancels.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from telegram import Message, Update
from telegram.ext import Application, ContextTypes

from ..config import Service, Settings, Vps
from ..transports import LogFile, Transport
from .auth import auth_required
from .keyboards import service_keyboard

log = logging.getLogger(__name__)

DEFAULT_LOG_LINES = 30
MAX_LOG_LINES = 200
TAIL_INTERVAL_S = 5.0
# Telegram hard cap is 4096 chars; leave headroom for the ```code fence```.
TAIL_CHUNK_CHARS = 3500


@dataclass
class TailSession:
    chat_id: int
    vps_name: str
    service_name: str
    log_path: str
    byte_offset: int
    job_name: str


# ---------- helpers ----------

async def _pick_active_log(transport: Transport, log_dir: str) -> LogFile | None:
    files = await transport.list_log_files(log_dir)
    return files[0] if files else None


def _resolve(settings: Settings, name: str) -> tuple[Vps, Service] | None:
    return settings.fleet.find_service(name)


def _fence(text: str) -> str:
    return f"```\n{text[-TAIL_CHUNK_CHARS:]}\n```"


# ---------- inner ops (callable from command + callback paths) ----------

async def send_logs(message: Message, transport: Transport, vps: Vps, svc: Service, n: int) -> None:
    active = await _pick_active_log(transport, svc.log_dir)
    if not active:
        await message.reply_text(f"no log files in {svc.log_dir}")
        return
    lines = await transport.read_log_tail(active.path, n)
    if not lines:
        await message.reply_text(f"`{active.name}` is empty")
        return
    body = "\n".join(lines)
    await message.reply_markdown(
        f"_{vps.name}/{svc.name}_ — `{active.name}` (last {len(lines)})\n{_fence(body)}"
    )


async def start_tail(message: Message, application: Application, transport: Transport, vps: Vps, svc: Service) -> None:
    active = await _pick_active_log(transport, svc.log_dir)
    if not active:
        await message.reply_text(f"no log files in {svc.log_dir}")
        return

    chat_id = message.chat_id
    sessions: dict[int, TailSession] = application.bot_data.setdefault("tails", {})
    # Cancel previous tail in this chat — one tail per chat.
    if chat_id in sessions:
        prev = sessions.pop(chat_id)
        for job in application.job_queue.get_jobs_by_name(prev.job_name):
            job.schedule_removal()

    job_name = f"tail:{chat_id}"
    session = TailSession(
        chat_id=chat_id,
        vps_name=vps.name,
        service_name=svc.name,
        log_path=active.path,
        byte_offset=active.size_bytes,   # only stream new bytes from now
        job_name=job_name,
    )
    sessions[chat_id] = session
    application.job_queue.run_repeating(
        _tail_tick, interval=TAIL_INTERVAL_S, first=TAIL_INTERVAL_S,
        name=job_name, chat_id=chat_id, data=session,
    )
    await message.reply_markdown(
        f"streaming `{active.name}` ({vps.name}/{svc.name}) — /tailstop to end"
    )


# ---------- /logs ----------

@auth_required
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    transports: dict[str, Transport] = context.application.bot_data["transports"]
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Pick a service for /logs:",
            reply_markup=service_keyboard(settings, "logs"),
        )
        return
    found = _resolve(settings, args[0])
    if not found:
        await update.effective_message.reply_text(f"unknown service: {args[0]}")
        return
    n = DEFAULT_LOG_LINES
    if len(args) >= 2:
        try:
            n = max(1, min(MAX_LOG_LINES, int(args[1])))
        except ValueError:
            pass

    vps, svc = found
    await send_logs(update.effective_message, transports[vps.name], vps, svc, n)


# ---------- /tail ----------

@auth_required
async def cmd_tail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    transports: dict[str, Transport] = context.application.bot_data["transports"]
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Pick a service for /tail:",
            reply_markup=service_keyboard(settings, "tail"),
        )
        return
    found = _resolve(settings, args[0])
    if not found:
        await update.effective_message.reply_text(f"unknown service: {args[0]}")
        return
    vps, svc = found
    await start_tail(
        update.effective_message, context.application,
        transports[vps.name], vps, svc,
    )


@auth_required
async def cmd_tailstop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    sessions: dict[int, TailSession] = context.application.bot_data.setdefault("tails", {})
    session = sessions.pop(chat_id, None)
    if not session:
        await update.effective_message.reply_text("no active tail in this chat")
        return
    for job in context.application.job_queue.get_jobs_by_name(session.job_name):
        job.schedule_removal()
    await update.effective_message.reply_text("tail stopped")


async def _tail_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    session: TailSession = context.job.data
    transports: dict[str, Transport] = context.application.bot_data["transports"]
    transport = transports[session.vps_name]
    new_data, new_offset = await transport.read_log_since(session.log_path, session.byte_offset)
    session.byte_offset = new_offset
    if not new_data.strip():
        return
    try:
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=_fence(new_data),
            parse_mode="Markdown",
        )
    except Exception as e:
        log.warning("tail send failed (chat=%s): %s", session.chat_id, e)
