"""Tiny Markdown subset -> HTML for the chat log. Stdlib only, no Qt.

Supports: fenced code blocks (with language label), inline code,
**bold**, *italic*, headings (#), bullet/numbered lists, paragraphs.
Everything is HTML-escaped first, so model output can never inject
markup into the chat view.
"""

from __future__ import annotations

import html
import re

_PLACEHOLDER = "\x00codeblock:{}\x00"


def render(text: str) -> str:
    """Render *text* (Markdown subset) to an HTML fragment."""
    text = html.escape(text or "")

    # 1. Fenced code blocks -> placeholders (protect from inline rules).
    blocks: list[str] = []

    def _stash(match: re.Match) -> str:
        lang = (match.group(1) or "").strip()
        code = match.group(2)
        label = f'<div class="codelang">{html.escape(lang)}</div>' if lang else ""
        blocks.append(f'<pre class="code">{label}{code}</pre>')
        return _PLACEHOLDER.format(len(blocks) - 1)

    text = re.sub(r"```(\w*)\s*\n(.*?)```", _stash, text, flags=re.S)

    # 2. Inline markup (code spans first so * inside code is untouched).
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"<i>\1</i>", text)

    # 3. Block structure: lists, headings, paragraphs.
    out: list[str] = []
    in_list: str | None = None
    for line in text.split("\n"):
        stripped = line.strip()
        bullet = re.match(r"^([-*])\s+(.*)$", stripped)
        numbered = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if bullet or numbered:
            tag = "ol" if numbered else "ul"
            item = (numbered or bullet).group(2)
            if in_list != tag:
                if in_list:
                    out.append(f"</{in_list}>")
                out.append(f"<{tag}>")
                in_list = tag
            out.append(f"<li>{item}</li>")
            continue
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            out.append(f'<p class="heading">{heading}</p>')
        elif _PLACEHOLDER[:11] in stripped:
            out.append(stripped)  # code-block placeholder, own line
        else:
            out.append(f"<p>{stripped}</p>")
    if in_list:
        out.append(f"</{in_list}>")
    text = "\n".join(out)

    # 4. Restore code blocks (also when wrapped in <p> by step 3).
    for i, block in enumerate(blocks):
        ph = _PLACEHOLDER.format(i)
        text = text.replace(f"<p>{ph}</p>", block).replace(ph, block)
    return text
