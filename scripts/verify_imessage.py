#!/usr/bin/env python3
"""Verify the iMessage channel against your real `chat.db`.

Run from a terminal that has Full Disk Access.

    .venv/bin/python scripts/verify_imessage.py
    .venv/bin/python scripts/verify_imessage.py --send-test '+15551234567'

Output is split into two sections on purpose:

* **SAFE TO PASTE** — schema, counts, and decode rates. No message content, and
  handles are masked. This is what I need to confirm the channel works.
* **LOCAL ONLY** — your actual handles, so you can fill in `ALLOWED_HANDLES`.
  Do not paste this section anywhere.

The point of the split: verifying the decoder requires reading your real
messages, but confirming *that it worked* only requires counts. Nothing in the
pasteable section reveals what anyone said.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macman.channels import imessage  # noqa: E402


def _mask(handle: str) -> str:
    """Mask a phone number or email enough to be unidentifiable but countable."""
    if "@" in handle:
        name, _, domain = handle.partition("@")
        return f"{name[:2]}***@{domain}"
    return f"{handle[:2]}***{handle[-2:]}" if len(handle) > 4 else "***"


def _rule(title: str) -> None:
    print(f"\n{'─' * 64}\n{title}\n{'─' * 64}")


def check_access() -> bool:
    _rule("1. chat.db access")
    try:
        latest = imessage.latest_rowid()
    except imessage.ChatDBUnavailable as exc:
        print(f"  FAIL  {exc}")
        return False
    print(f"  PASS  readable, highest ROWID = {latest}")

    # A WAL file bigger than a couple KB with ROWID still at 0 is exactly the
    # symptom of reading the checkpointed file while recent writes sit
    # unseen in the WAL — the bug that cost a round-trip earlier. Surfacing
    # it here means it can't happen silently again.
    wal = imessage.CHAT_DB.with_name(imessage.CHAT_DB.name + "-wal")
    if wal.exists():
        wal_kb = wal.stat().st_size / 1024
        print(f"  WAL file present: {wal_kb:.0f} KB", end="")
        if wal_kb > 4 and latest == 0:
            print("  ⚠️  WAL has content but ROWID is 0 — would indicate an "
                  "immutable-mode-style blind spot. Should not happen now.")
        else:
            print()
    return True


def check_schema() -> bool:
    """Confirm the columns the poller depends on are present in this release."""
    _rule("2. Schema")
    with imessage._connect() as connection:
        message_columns = imessage._columns(connection, "message")
        handle_columns = imessage._columns(connection, "handle")

    required = {"ROWID", "text", "handle_id", "is_from_me", "date"}
    missing = required - message_columns
    has_attributed = "attributedBody" in message_columns

    print(f"  message columns : {len(message_columns)}")
    print(f"  handle columns  : {len(handle_columns)}")
    print(f"  attributedBody  : {'present' if has_attributed else 'ABSENT'}")
    if missing:
        print(f"  FAIL  missing required columns: {sorted(missing)}")
        return False
    print("  PASS  all required columns present")
    return True


def check_decoder(sample: int = 400) -> bool:
    """The critical test: does the attributedBody decoder work on real data?

    It was written against synthetic blobs. This measures it against yours,
    reporting only counts — never content.
    """
    _rule("3. Message decoding (the real test)")

    with imessage._connect() as connection:
        has_attributed = "attributedBody" in imessage._columns(connection, "message")
        columns = "ROWID, text" + (", attributedBody" if has_attributed else "")
        rows = connection.execute(
            f"SELECT {columns} FROM message ORDER BY ROWID DESC LIMIT ?", (sample,)
        ).fetchall()

    plain = blob_ok = blob_failed = empty = 0
    for row in rows:
        text = (row[1] or "").strip()
        if text:
            plain += 1
        elif has_attributed and row[2]:
            if imessage._decode_attributed_body(row[2]):
                blob_ok += 1
            else:
                blob_failed += 1
        else:
            empty += 1

    total = len(rows)
    recovered = plain + blob_ok
    print(f"  sampled                    : {total}")
    print(f"  text column populated      : {plain}")
    print(f"  attributedBody decoded     : {blob_ok}")
    print(f"  attributedBody FAILED      : {blob_failed}")
    print(f"  no body (reactions/attach) : {empty}")

    if total:
        print(f"\n  Recovered {recovered}/{total} ({100 * recovered / max(1, total):.0f}%)")
    if blob_failed:
        print(f"  ⚠️  {blob_failed} messages had a body the decoder could not read.")
        print("     Paste this section — the decoder needs work.")
        return False
    if blob_ok:
        print("  PASS  decoder handled every attributedBody message.")
    else:
        print("  NOTE  no attributedBody messages in the sample; decoder untested "
              "on real data.")
    return True


def check_directionality(sample: int = 200) -> bool:
    """Measure how self-addressed messages are recorded.

    The flow MACman needs is "text your own Apple ID from your phone", because
    there is no way to address a Mac directly. Whether those arrive as
    `is_from_me=0` or `is_from_me=1` decides the whole inbound filter — and if
    they are from-me, MACman's own replies look identical to your requests,
    which is a loop waiting to happen. Measured rather than assumed.
    """
    _rule("4. Message directionality (decides the inbound filter)")

    with imessage._connect() as connection:
        rows = connection.execute(
            "SELECT message.is_from_me, message.text, handle.id "
            "FROM message LEFT JOIN handle ON message.handle_id = handle.ROWID "
            "ORDER BY message.ROWID DESC LIMIT ?", (sample,)
        ).fetchall()

    if not rows:
        print("  NOTE  still no messages. Send one from your iPhone to your own")
        print("        Apple ID, wait a few seconds, and re-run.")
        return True

    from_me = sum(1 for row in rows if row[0])
    to_me = len(rows) - from_me
    no_handle = sum(1 for row in rows if not row[2])

    print(f"  sampled            : {len(rows)}")
    print(f"  is_from_me = 1     : {from_me}")
    print(f"  is_from_me = 0     : {to_me}")
    print(f"  NULL handle        : {no_handle}")

    if to_me == 0 and from_me:
        print("\n  ⚠️  Every message is from-me. Self-addressed messages are NOT")
        print("      distinguishable from MACman's own replies by direction alone —")
        print("      a command prefix will be needed. Paste this section.")
    elif to_me:
        print("\n  Inbound messages exist, so the direction filter works as written.")
    return True


def check_read_since() -> bool:
    _rule("5. Poller read path")
    latest = imessage.latest_rowid()
    messages = imessage.read_since(max(0, latest - 30))
    print(f"  read_since returned {len(messages)} message(s) with usable text")
    if messages:
        newest = messages[-1]
        print(f"  newest: rowid={newest.rowid} from={_mask(newest.handle)} "
              f"at={newest.sent_at.isoformat()} chars={len(newest.text)}")
        print("  (content deliberately not shown)")
    return True


def show_handles(limit: int = 12) -> None:
    _rule("LOCAL ONLY — do not paste")
    latest = imessage.latest_rowid()
    messages = imessage.read_since(max(0, latest - 500), limit=500)
    counts = Counter(m.handle for m in messages if not m.is_from_me)

    if not counts:
        print("  No recent inbound handles found.")
        return

    print("  Recent senders — copy your own into ALLOWED_HANDLES:\n")
    for handle, count in counts.most_common(limit):
        print(f"    {handle:<34} {count} message(s)")
    print("\n  Format them exactly as shown, e.g.:")
    print('    ALLOWED_HANDLES: frozenset[str] = frozenset({"+15551234567"})')


def send_test(handle: str) -> bool:
    """Verify outbound send, which also settles the buddy/participant question.

    Messages' AppleScript vocabulary has shifted across releases, and this is
    the cheapest way to find out which form this macOS accepts.
    """
    _rule("6. Outbound send")
    print(f"  Sending a test message to {_mask(handle)} ...")
    ok = imessage.send(handle, "MACman test message — the channel works.")
    print(f"  {'PASS  sent' if ok else 'FAIL  send failed (see below)'}")
    if not ok:
        from macman.agent.tools import applescript
        result = applescript.run(
            'tell application "Messages" to return name of every account'
        )
        print(f"  accounts probe: {result.for_model()[:220]}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send-test", metavar="HANDLE",
                        help="send a real test message to this handle")
    parser.add_argument("--no-handles", action="store_true",
                        help="skip the LOCAL ONLY section")
    args = parser.parse_args()

    print("MACman — iMessage channel verification")
    print("\n=== SAFE TO PASTE (no message content) ===")

    if not check_access():
        print("\n  Grant Full Disk Access to THIS terminal, then re-run.")
        return 1

    results = [check_schema(), check_decoder(), check_directionality(),
               check_read_since()]
    if args.send_test:
        results.append(send_test(args.send_test))

    _rule("Summary")
    print(f"  {sum(results)}/{len(results)} checks passed")

    if not args.no_handles:
        show_handles()

    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
