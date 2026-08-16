"""iMessage channel — read `chat.db`, send via Messages.app.

Two things make this harder than it looks, and both are handled defensively
because the failure mode is silently missing messages:

* **The schema moves between macOS releases.** Columns are detected at runtime
  rather than assumed.
* **`message.text` is often NULL on modern macOS.** The body lives in
  `attributedBody` as a `typedstream` archive instead. A poller that reads only
  `text` looks like it works and quietly drops most messages.

Reading requires Full Disk Access. The database is opened **read-only and
immutable** so MACman can never corrupt Messages, and so a concurrent write by
Messages.app can't block the poll.
"""

from __future__ import annotations

import sqlite3
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from macman import config
from macman.agent.tools import applescript

CHAT_DB = Path.home() / "Library/Messages/chat.db"

#: Apple's epoch for `message.date`.
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

#: macOS 10.13+ stores `date` in nanoseconds; older releases used seconds.
#: Anything above this threshold is unambiguously nanoseconds.
_NANOSECOND_THRESHOLD = 1e11

#: Where the last-seen ROWID is persisted, so a restart doesn't replay history.
_CURSOR_FILE = config.STATE_DIR / "imessage_cursor"


class ChatDBUnavailable(RuntimeError):
    """Raised when `chat.db` cannot be read — almost always Full Disk Access."""


@dataclass(frozen=True)
class Message:
    rowid: int
    handle: str
    text: str
    sent_at: datetime
    is_from_me: bool


# --------------------------------------------------------------------------- #
# attributedBody decoding
# --------------------------------------------------------------------------- #


def _decode_attributed_body(blob: bytes | None) -> str | None:
    """Extract plain text from a `typedstream`-archived NSAttributedString.

    Not a general typedstream parser — it locates the `NSString` payload and
    reads its length-prefixed UTF-8 bytes, which covers ordinary messages.
    Returns None rather than raising if the layout is unfamiliar, so an exotic
    message degrades to "no text" instead of killing the poller.
    """
    if not blob:
        return None

    marker = blob.find(b"NSString")
    if marker == -1:
        return None

    # Skip the class name and the four type bytes that follow it.
    cursor = marker + len("NSString") + 5
    if cursor >= len(blob):
        return None

    try:
        length = blob[cursor]
        cursor += 1
        # 0x81 signals a two-byte little-endian length.
        if length == 0x81:
            length = struct.unpack_from("<H", blob, cursor)[0]
            cursor += 2
        text = blob[cursor:cursor + length].decode("utf-8", errors="replace")
    except (IndexError, struct.error):
        return None

    return text.strip() or None


def _to_datetime(raw: int | None) -> datetime:
    if not raw:
        return APPLE_EPOCH
    seconds = raw / 1e9 if raw > _NANOSECOND_THRESHOLD else raw
    return APPLE_EPOCH + timedelta(seconds=seconds)


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def _connect() -> sqlite3.Connection:
    """Open `chat.db` read-only.

    Deliberately **not** `immutable=1`. `chat.db` is WAL-journaled (there is a
    `chat.db-wal` alongside it), and SQLite's immutable mode explicitly skips
    the WAL — it assumes the file "cannot be changed... even by a process with
    higher privilege" and ignores it entirely. Messages.app writes new rows
    into the WAL before they are checkpointed into the main file, so an
    immutable reader is structurally blind to recent messages: not a
    permission problem, not a timing problem, just never seeing them.

    Plain `mode=ro` reads WAL content correctly. The "database is locked"
    errors immutable mode was meant to dodge are a rollback-journal concern;
    WAL mode was designed for concurrent readers alongside a writer and
    doesn't have that failure mode.
    """
    if not CHAT_DB.exists():
        raise ChatDBUnavailable(f"{CHAT_DB} does not exist.")
    try:
        return sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise ChatDBUnavailable(
            f"Cannot open chat.db ({exc}). This is almost always missing Full Disk "
            f"Access — grant it to the app running MACman under System Settings → "
            f"Privacy & Security → Full Disk Access."
        ) from exc


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def read_since(rowid: int, *, limit: int = 50) -> list[Message]:
    """Return messages newer than `rowid`, oldest first.

    Args:
        rowid: Exclusive lower bound; pass 0 for "everything available".
        limit: Cap, so a long absence doesn't produce an enormous batch.
    """
    with _connect() as connection:
        available = _columns(connection, "message")
        # Detected rather than assumed — `attributedBody` predates our support
        # for it but isn't present on every release.
        has_attributed = "attributedBody" in available

        columns = ["message.ROWID", "message.text", "message.is_from_me", "message.date",
                   "handle.id"]
        if has_attributed:
            columns.append("message.attributedBody")

        query = f"""
            SELECT {', '.join(columns)}
            FROM message
            LEFT JOIN handle ON message.handle_id = handle.ROWID
            WHERE message.ROWID > ?
            ORDER BY message.ROWID ASC
            LIMIT ?
        """
        rows = connection.execute(query, (rowid, limit)).fetchall()

    messages: list[Message] = []
    for row in rows:
        row_id, text, is_from_me, date, handle = row[0], row[1], row[2], row[3], row[4]
        body = (text or "").strip()
        if not body and has_attributed:
            body = _decode_attributed_body(row[5]) or ""
        if not body:
            continue  # attachments-only, reactions, and unsupported payloads
        messages.append(Message(
            rowid=row_id, handle=handle or "(unknown)", text=body,
            sent_at=_to_datetime(date), is_from_me=bool(is_from_me),
        ))
    return messages


def latest_rowid() -> int:
    """Highest ROWID present, so a first run starts at 'now' not at history."""
    with _connect() as connection:
        row = connection.execute("SELECT MAX(ROWID) FROM message").fetchone()
    return row[0] or 0


# --------------------------------------------------------------------------- #
# Cursor
# --------------------------------------------------------------------------- #


def load_cursor() -> int | None:
    if not _CURSOR_FILE.exists():
        return None
    try:
        return int(_CURSOR_FILE.read_text().strip())
    except ValueError:
        return None


def save_cursor(rowid: int) -> None:
    _CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CURSOR_FILE.write_text(str(rowid))


# --------------------------------------------------------------------------- #
# Polling
# --------------------------------------------------------------------------- #


#: Messages older than this when first seen are advanced past, never executed.
#: See `poll` for why a ROWID cursor alone is not enough.
MAX_MESSAGE_AGE_SECONDS = 300


def poll(interval: float = 2.0, *, allowlist: frozenset[str] | None = None,
         max_age_seconds: float = MAX_MESSAGE_AGE_SECONDS) -> Iterator[Message]:
    """Yield incoming messages from allowed senders as they arrive.

    Filtering happens here, before anything reaches an engine: a message from an
    unknown handle is never seen by a model at all.

    **Two independent guards, because the ROWID cursor is not sufficient.** The
    cursor stops history being replayed on restart, but it assumes ROWIDs only
    ever appear at the head. A `chat.db` that gets *backfilled* — enabling
    Messages in iCloud on a Mac with no local history, restoring a backup,
    signing into a new Apple ID — breaks that assumption: thousands of old
    messages arrive above the cursor at once and would every one be executed as
    a task. The age check makes that harmless.

    Args:
        interval: Seconds between polls.
        allowlist: Permitted handles. Defaults to `config.ALLOWED_HANDLES`.
            An empty allowlist permits nothing, which is the safe default.
        max_age_seconds: Messages older than this are skipped. A backfill
            advances the cursor past them without running anything.
    """
    permitted = config.ALLOWED_HANDLES if allowlist is None else allowlist

    cursor = load_cursor()
    if cursor is None:
        # Start at the present so a first run doesn't replay your entire history.
        cursor = latest_rowid()
        save_cursor(cursor)

    while True:
        for message in read_since(cursor):
            cursor = message.rowid
            save_cursor(cursor)
            if message.is_from_me or message.handle not in permitted:
                continue
            age = (datetime.now(timezone.utc) - message.sent_at).total_seconds()
            if age > max_age_seconds:
                continue  # backfilled history, not a live request
            yield message
        time.sleep(interval)


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #


#: Where attachments are staged before sending.
#:
#: Messages will not read files from ``~/Library/Application Support`` — the
#: send silently reports success and the message arrives marked "Not
#: Delivered". Staging under ``~/Pictures`` is what makes attachments actually
#: send; the original FaceTimeOS project moved its screenshots there for the
#: same reason.
ATTACHMENT_DIR = Path.home() / "Pictures" / "MACman"


def stage_attachment(data: bytes, name: str = "last_reply.png") -> Path:
    """Write an attachment somewhere Messages is willing to read from."""
    ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    path = ATTACHMENT_DIR / name
    path.write_bytes(data)
    return path


def send(handle: str, text: str, attachment: Path | None = None) -> bool:
    """Send an iMessage, optionally with an attachment.

    Text and attachment are sent as two separate messages, because a failure
    to attach should not also lose the reply.

    Args:
        handle: Recipient, in the form Messages stores it.
        text: Message body.
        attachment: File to send. Must live under `ATTACHMENT_DIR` or another
            location Messages can read — see that constant.
    """
    escaped_text = text.replace("\\", "\\\\").replace('"', '\\"')
    escaped_handle = handle.replace('"', '\\"')

    preamble = (
        'tell application "Messages"\n'
        '  set targetService to 1st account whose service type = iMessage\n'
        f'  set targetBuddy to participant "{escaped_handle}" of targetService\n'
    )

    result = applescript.run(
        f'{preamble}  send "{escaped_text}" to targetBuddy\nend tell'
    )
    if not result.ok:
        return False

    if attachment is not None:
        # Coerced to an alias first: `send POSIX file "..."` passes a file
        # *reference* that Messages accepts and then fails to deliver, which is
        # indistinguishable from success at this layer.
        attach_result = applescript.run(
            f'{preamble}'
            f'  set theFile to POSIX file "{attachment}" as alias\n'
            f'  send theFile to targetBuddy\n'
            f'end tell'
        )
        if not attach_result.ok:
            logger.warning("Attachment failed to send: %s", attach_result.output[:200])

    return True
