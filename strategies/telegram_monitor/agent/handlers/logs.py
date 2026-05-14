"""/logs (one-shot tail) and /tail (streaming, batched every 5s).

`/tail` registers a per-chat JobQueue job that polls the active log file
every 5s. New bytes are accumulated in `bot_data["tail_buffers"]` and
flushed as one Telegram message per tick — keeps message rate well under
Telegram's 30 msg/s limit even with chatty agents. `/tailstop` cancels.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass

from telegram import Message, Update
from telegram.ext import Application, ContextTypes

from ..config import Mt5LogTarget, Service, Settings, Vps
from ..transports import LogFile, Transport
from .auth import auth_required
from .keyboards import account_keyboard, service_keyboard

log = logging.getLogger(__name__)

DEFAULT_LOG_LINES = 10
MAX_LOG_LINES = 200
TAIL_INTERVAL_S = 5.0
# Telegram hard cap is 4096 chars; leave headroom for the <pre>...</pre> tags.
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
    return await transport.latest_log_file(log_dir)


def _resolve(settings: Settings, name: str) -> tuple[Vps, Service] | None:
    return settings.fleet.find_service(name)


def _pre(text: str) -> str:
    """HTML <pre> block, tail-trimmed and escaped. Service/file names contain
    `_` which breaks Telegram's legacy Markdown parser, so the whole bot uses
    HTML parse mode."""
    return f"<pre>{html.escape(text[-TAIL_CHUNK_CHARS:])}</pre>"


# ---------- inner ops (callable from command + callback paths) ----------

async def _render_log_block(
    transport: Transport, label: str, log_dir: str, n: int,
) -> str:
    """One <pre> block for a single log_dir; returns header+body or a stub."""
    active = await _pick_active_log(transport, log_dir)
    if not active:
        return f"<i>{html.escape(label)}</i> — <code>(no log files in {html.escape(log_dir)})</code>"
    lines = await transport.read_log_tail(active.path, n)
    header = (
        f"<i>{html.escape(label)}</i> — "
        f"<code>{html.escape(active.name)}</code> (last {len(lines)})"
    )
    if not lines:
        return f"{header}\n<i>(empty)</i>"
    return f"{header}\n{_pre(chr(10).join(lines))}"


async def send_logs(
    message: Message, transport: Transport, vps: Vps, svc: Service, n: int,
    *, mt5_account: str | None = None,
) -> None:
    """Show Python agent log + (optionally) MT5 Expert log for the same strategy.

    - 0 mt5_logs            → agent log only.
    - 1 mt5_log             → agent + that account's MT5 log.
    - N and `mt5_account`   → agent + the picked account's MT5 log.
    - N and no `mt5_account`→ reply with an account picker, no logs sent.
    """
    if svc.mt5_logs and mt5_account is None and len(svc.mt5_logs) > 1:
        await message.reply_text(
            f"{svc.name} runs on multiple accounts — pick one:",
            reply_markup=account_keyboard(vps.name, svc.name, svc.mt5_logs),
        )
        return

    target: Mt5LogTarget | None = None
    if svc.mt5_logs:
        if mt5_account is not None:
            target = next((m for m in svc.mt5_logs if m.account == mt5_account), None)
            if target is None:
                await message.reply_text(f"unknown account: {mt5_account}")
                return
        else:
            target = svc.mt5_logs[0]

    py_label = f"{svc.name} agent"
    py_block = await _render_log_block(transport, py_label, svc.log_dir, n)

    if target is None:
        await message.reply_html(py_block)
        return

    mt5_label = f"{svc.name} MT5 acct {target.account}"
    mt5_block = await _render_log_block(transport, mt5_label, target.log_dir, n)
    # Two messages — each <pre> is independently size-bounded by TAIL_CHUNK_CHARS.
    await message.reply_html(py_block)
    await message.reply_html(mt5_block)


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
    await message.reply_html(
        f"streaming <code>{html.escape(active.name)}</code> "
        f"({html.escape(svc.name)}) — /tailstop to end"
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
    sessions.pop(chat_id, None)

    job_name = f"tail:{chat_id}"
    jq = context.application.job_queue
    jobs = list(jq.get_jobs_by_name(job_name)) if jq is not None else []
    if not jobs:
        await update.effective_message.reply_text("no active tail in this chat")
        return
    for job in jobs:
        job.schedule_removal()
    await update.effective_message.reply_text(f"tail stopped ({len(jobs)})")


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
            text=_pre(new_data),
            parse_mode="HTML",
        )
    except Exception as e:
        log.warning("tail send failed (chat=%s): %s", session.chat_id, e)
