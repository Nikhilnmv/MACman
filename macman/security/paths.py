"""Whether one path is inside another, decided by identity rather than spelling.

Extracted so there is exactly one implementation. Two callers need it and they
fail in opposite directions, which is precisely why they must not drift apart:

* `agent/tools/typed._safe_folder` uses it to **refuse** access. A check that is
  too loose leaks a credential.
* `security/egress` uses it to match a **pre-approval**. A check that is too
  loose sends data to a cloud model without asking.

The naive version of this — `str.startswith` — is wrong in both directions on
macOS, and both errors have been observed here rather than imagined:

1. `~/.SSH/id_ed25519` opened the same file as `~/.ssh/…` and returned real
   private key material, because the filesystem is case-insensitive and the
   check compared spelling (RELIABILITY.md).
2. `/Users/me/projects` is a string prefix of `/Users/me/projects-secret`, so a
   pre-approval for one directory would silently cover an unrelated sibling.
"""

from __future__ import annotations

import os
from pathlib import Path


def within(candidate: Path, parent: Path) -> bool:
    """Whether `candidate` is, or is inside, `parent`.

    Three checks, because each catches something the others miss:

    1. **Resolved containment** — `is_relative_to` compares path *components*,
       so `projects` never matches `projects-secret`, and `resolve()` has
       already followed `..` and symlinks.
    2. **Case-folded prefix** — macOS filesystems are case-insensitive by
       default, so `~/.SSH` and `~/.ssh` are one directory. The separator is
       required in the comparison, keeping the component rule intact.
    3. **Identity** — where both paths exist, compare inode rather than text,
       which also catches hard links and firmlinks.
    """
    try:
        if candidate == parent or candidate.is_relative_to(parent):
            return True
    except ValueError:
        pass

    folded = str(candidate).casefold()
    parent_folded = str(parent).casefold()
    # The trailing separator is what stops `projects` matching `projects-secret`.
    if folded == parent_folded or folded.startswith(parent_folded + os.sep):
        return True

    # Walk upward: the file itself may not exist, but a parent will.
    for ancestor in [candidate, *candidate.parents]:
        try:
            if ancestor.exists() and parent.exists() and ancestor.samefile(parent):
                return True
        except OSError:
            continue
        if ancestor == ancestor.parent:      # reached the root
            break
    return False
