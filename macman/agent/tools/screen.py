"""Tier 4 — screenshots.

The most expensive way to see the Mac, and deliberately the last resort. Image
tokens are roughly `width × height / 750`, so an undownscaled Retina capture of
this display costs about 7,000 tokens *per look* — and screenshot-driven loops
tend to need far more turns than scripted ones, which is where the 4–5× cost
difference in DESIGN.md §11 actually comes from.

Two consequences shape this module:

* **Everything is downscaled** before it reaches the model. A Retina capture is
  2× the logical resolution and almost never worth its token cost at full size.
* **Reading UI text is `ui_query`'s job, not this one.** The Accessibility tree
  returns labels as text, exactly and for free. Screenshots are for the cases
  where the answer is genuinely visual.

Uses the built-in `screencapture` and `sips`, so there is no image-library
dependency and nothing to keep in step with Pillow.
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: Long-edge target. 1280 on a 16:10 display is roughly 1,350 image tokens;
#: 1568 (the point above which the API downscales anyway) is closer to 2,050.
DEFAULT_MAX_EDGE = 1280

#: Above this the API downscales server-side, so paying to send more is waste.
MAX_USEFUL_EDGE = 1568

CAPTURE_TIMEOUT_SECONDS = 15


class ScreenshotError(RuntimeError):
    """Raised when capture fails — usually missing Screen Recording permission."""


@dataclass(frozen=True)
class Screenshot:
    png: bytes
    width: int
    height: int

    @property
    def estimated_tokens(self) -> int:
        return round(self.width * self.height / 750)

    def to_content_blocks(self) -> list[dict]:
        """Render as API content blocks.

        The text block is not decoration: it tells the model what it is looking
        at and what it cost, which measurably discourages reflexive re-capture.
        """
        return [
            {
                "type": "text",
                "text": (
                    f"Screenshot {self.width}×{self.height} "
                    f"(~{self.estimated_tokens} tokens). Prefer ui_query for "
                    f"reading interface text."
                ),
            },
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(self.png).decode(),
                },
            },
        ]


def _dimensions(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
        capture_output=True, text=True, timeout=CAPTURE_TIMEOUT_SECONDS,
    )
    width = height = 0
    for line in result.stdout.splitlines():
        if "pixelWidth:" in line:
            width = int(line.split(":")[1].strip())
        elif "pixelHeight:" in line:
            height = int(line.split(":")[1].strip())
    return width, height


def capture(*, max_edge: int = DEFAULT_MAX_EDGE, region: str | None = None) -> Screenshot:
    """Capture the screen, downscaled.

    Args:
        max_edge: Long-edge pixel target after downscaling.
        region: Optional `x,y,w,h` to capture instead of the whole display.

    Raises:
        ScreenshotError: capture failed. Overwhelmingly this is missing Screen
            Recording permission, which `screencapture` reports as a failure to
            create an image rather than as a permission error.
    """
    max_edge = min(max_edge, MAX_USEFUL_EDGE)

    with tempfile.TemporaryDirectory(prefix="macman-shot-") as directory:
        path = Path(directory) / "capture.png"

        command = ["/usr/sbin/screencapture", "-x", "-t", "png"]
        if region:
            command += ["-R", region]
        command.append(str(path))

        result = subprocess.run(
            command, capture_output=True, text=True, timeout=CAPTURE_TIMEOUT_SECONDS
        )
        if result.returncode != 0 or not path.exists() or path.stat().st_size == 0:
            detail = (result.stderr or result.stdout or "").strip() or "no output"
            raise ScreenshotError(
                f"Screen capture failed ({detail}). This is almost always missing "
                f"Screen Recording permission — grant it to the app running MACman "
                f"under System Settings → Privacy & Security → Screen Recording."
            )

        # `sips -Z` fits the long edge, preserving aspect ratio, and only ever
        # shrinks — a small display is never upscaled into extra token cost.
        subprocess.run(
            ["/usr/bin/sips", "-Z", str(max_edge), str(path)],
            capture_output=True, timeout=CAPTURE_TIMEOUT_SECONDS,
        )

        width, height = _dimensions(path)
        return Screenshot(png=path.read_bytes(), width=width, height=height)
