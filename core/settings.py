"""Owner-configurable copy transformations (word/link replace, caption, thumbnail).

Settings are global for the single owner and persisted as JSON in the sessions
directory. They are applied while copying each message.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

CAPTION_MODES = ("off", "append", "prepend", "replace")


@dataclass
class Settings:
    # list of [find, replace] pairs applied to visible text/caption (HTML)
    word_replace: list[list[str]] = field(default_factory=list)
    # list of [old_url, new_url] pairs applied to links in text/caption (HTML)
    link_replace: list[list[str]] = field(default_factory=list)
    # extra caption text and how to combine it with the original
    caption_text: str = ""
    caption_mode: str = "off"  # off | append | prepend | replace
    # path to a prepared JPEG thumbnail (<=320px), or None
    thumbnail_path: Optional[str] = None

    # ------------------------------------------------------------------ #
    def has_transforms(self) -> bool:
        return bool(
            self.word_replace
            or self.link_replace
            or (self.caption_mode != "off" and self.caption_text)
            or self.thumbnail_path
        )

    def has_text_transforms(self) -> bool:
        return bool(
            self.word_replace
            or self.link_replace
            or (self.caption_mode != "off" and self.caption_text)
        )

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, path: Path) -> "Settings":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                word_replace=[list(p) for p in data.get("word_replace", [])],
                link_replace=[list(p) for p in data.get("link_replace", [])],
                caption_text=str(data.get("caption_text", "")),
                caption_mode=str(data.get("caption_mode", "off")),
                thumbnail_path=data.get("thumbnail_path"),
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.warning("Could not read settings (%s); using defaults.", exc)
            return cls()

    def save(self, path: Path) -> None:
        try:
            path.write_text(
                json.dumps(
                    {
                        "word_replace": self.word_replace,
                        "link_replace": self.link_replace,
                        "caption_text": self.caption_text,
                        "caption_mode": self.caption_mode,
                        "thumbnail_path": self.thumbnail_path,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Could not save settings: %s", exc)


# --------------------------------------------------------------------------- #
# Transformations
# --------------------------------------------------------------------------- #
def _apply_pairs(text: str, pairs: list[list[str]]) -> str:
    for pair in pairs:
        if len(pair) == 2 and pair[0]:
            text = text.replace(pair[0], pair[1])
    return text


def transform_text(original_html: str, settings: Settings) -> str:
    """Apply link-replace, word-replace, and caption combine to an HTML string.

    ``original_html`` should be the message's HTML representation (so link URLs
    are visible as ``href="..."`` and can be replaced). The returned string is
    meant to be sent with ``parse_mode=HTML``.
    """
    text = original_html or ""
    text = _apply_pairs(text, settings.link_replace)
    text = _apply_pairs(text, settings.word_replace)

    if settings.caption_mode != "off" and settings.caption_text:
        extra = settings.caption_text
        if settings.caption_mode == "replace":
            text = extra
        elif settings.caption_mode == "append":
            text = f"{text}\n\n{extra}" if text.strip() else extra
        elif settings.caption_mode == "prepend":
            text = f"{extra}\n\n{text}" if text.strip() else extra
    return text


def summary(settings: Settings) -> str:
    """Human-readable summary of the active settings for the /settings menu."""
    lines = []
    wr = settings.word_replace
    lines.append(
        f"✏️ **Word replace:** {len(wr)} rule(s)"
        + ("" if not wr else "\n   " + "\n   ".join(f"`{a}` → `{b}`" for a, b in wr[:8]))
    )
    lr = settings.link_replace
    lines.append(
        f"🔗 **Link replace:** {len(lr)} rule(s)"
        + ("" if not lr else "\n   " + "\n   ".join(f"`{a}` → `{b}`" for a, b in lr[:8]))
    )
    if settings.caption_mode != "off" and settings.caption_text:
        preview = settings.caption_text if len(settings.caption_text) <= 60 else settings.caption_text[:59] + "…"
        lines.append(f"🏷 **Caption:** {settings.caption_mode} — `{preview}`")
    else:
        lines.append("🏷 **Caption:** off")
    lines.append(f"🖼 **Thumbnail:** {'set' if settings.thumbnail_path else 'off'}")
    return "\n".join(lines)
