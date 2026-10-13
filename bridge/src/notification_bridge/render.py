"""What each channel receives for a submit: pure functions, no I/O.

ntfy takes the Markdown as it is, under a per-registration id per sequence and generation, with its
title, priority and icon as headers. Telegram takes the Markdown parsed as CommonMark and rendered
to the HTML subset its `parse_mode=HTML` accepts, headed by the priority mark and the title, and cut
in its visible text to Telegram's limit.
"""

import base64
import hashlib
from dataclasses import dataclass, replace
from enum import IntEnum
from html import escape
from urllib.parse import quote

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode


class Priority(IntEnum):
    """The five levels. The values are ntfy's 1–5 scale, and their order is what a rise compares."""

    MIN = 1
    LOW = 2
    DEFAULT = 3
    HIGH = 4
    MAX = 5


# The Telegram priority marks (ruled). `default` has none.
MARKS = {
    Priority.MIN: "\N{MEDIUM WHITE CIRCLE}",
    Priority.LOW: "\N{LARGE BLUE CIRCLE}",
    Priority.HIGH: "\N{LARGE ORANGE CIRCLE}",
    Priority.MAX: "\N{POLICE CARS REVOLVING LIGHT}",
}

# Telegram's limit on a message's text after entity parsing.
TELEGRAM_LIMIT = 4096
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
RULE = "\N{EM DASH}" * 3

# Raw HTML is off, so it parses as text and is escaped. Strikethrough is the one extension, the
# Markdown source of Telegram's <s>.
_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("strikethrough")


def ntfy_id(registration: str, sequence_id: str, generation: int) -> str:
    """The ntfy sequence id: a 128-bit BLAKE2b keyed by the registration, 32 hex characters.

    The decimal generation never holds a ":", so the first ":" ends it, and no two
    (sequence id, generation) pairs hash the same input.
    """
    data = f"{generation}:{sequence_id}".encode()
    return hashlib.blake2b(data, digest_size=16, key=registration.encode()).hexdigest()


def ntfy_headers(
    *,
    icon_base_url: str,
    icon: str,
    priority: Priority,
    title: str | None,
    sequence: str | None,
) -> dict[str, str]:
    """The headers of a publish whose body is the message.

    `sequence` is the `ntfy_id`, None for a one-shot.
    """
    headers = {
        "X-Markdown": "yes",
        "X-Priority": str(priority.value),
        "X-Icon": f"{icon_base_url}/{quote(icon)}",
    }
    if title:
        # An RFC 2047 encoded word, which ntfy decodes (server/util.go, maybeDecodeHeader): it keeps
        # any text, line breaks and non-ASCII included, within an ASCII header value.
        headers["X-Title"] = f"=?utf-8?b?{base64.b64encode(title.encode()).decode()}?="
    if sequence:
        headers["X-Sequence-ID"] = sequence
    return headers


@dataclass(frozen=True)
class _Element:
    tag: str
    children: tuple["_Node", ...]
    href: str | None = None


_Node = str | _Element


def telegram_html(title: str | None, message: str, priority: Priority) -> str:
    """The message as Telegram HTML: the priority mark and the bold title, then the message."""
    blocks = SyntaxTreeNode(_MARKDOWN.parse(message)).children
    body = _join([_block(node, 0, False) for node in blocks], "\n\n")
    mark = (MARKS[priority] + " ",) if priority in MARKS else ()
    if title:
        nodes = (*mark, _Element("b", (title,)), *(("\n", *body) if body else ()))
    else:
        nodes = (*mark, *body)
    return _html(_fit(nodes, TELEGRAM_LIMIT))


def _join(parts: list[tuple[_Node, ...]], separator: str) -> tuple[_Node, ...]:
    joined: list[_Node] = []
    for part in parts:
        if joined:
            joined.append(separator)
        joined.extend(part)
    return tuple(joined)


def _block(node: SyntaxTreeNode, depth: int, quoted: bool) -> tuple[_Node, ...]:
    """One block. `depth` is the list nesting, `quoted` whether a blockquote encloses it."""
    match node.type:
        case "paragraph":
            return _inlines(node.children[0].children, frozenset())
        case "heading":
            return (_Element("b", _inlines(node.children[0].children, frozenset({"b"}))),)
        case "bullet_list" | "ordered_list":
            items = []
            for item in node.children:
                marker = "\N{BULLET}" if node.type == "bullet_list" else item.info + item.markup
                content = _join([_block(child, depth + 1, quoted) for child in item.children], "\n")
                items.append((f"{'  ' * depth}{marker} ", *content))
            return _join(items, "\n")
        case "blockquote":
            content = _join([_block(child, depth, True) for child in node.children], "\n\n")
            # Telegram does not nest blockquotes: an inner one joins the outer.
            return content if quoted else (_Element("blockquote", content),)
        case "fence" | "code_block":
            return (_Element("pre", (node.content.removesuffix("\n"),)),)
        case "hr":
            return (RULE,)


def _inlines(nodes: list[SyntaxTreeNode], within: frozenset[str]) -> tuple[_Node, ...]:
    """The inline nodes, `within` the tags that enclose them.

    Telegram nests no code in bold, italic, strikethrough or a link, and no link in a link
    (core.telegram.org/bots/api, Formatting options): enclosed, code is plain text, and an image
    in a link is its alt text.
    """
    rendered: list[_Node] = []
    for node in nodes:
        match node.type:
            case "text":
                rendered.append(node.content)
            case "softbreak" | "hardbreak":
                rendered.append("\n")
            case "code_inline":
                rendered.append(node.content if within else _Element("code", (node.content,)))
            case "em" | "strong" | "s":
                tag = {"em": "i", "strong": "b", "s": "s"}[node.type]
                rendered.append(_Element(tag, _inlines(node.children, within | {tag})))
            case "link":
                href = node.attrs["href"]
                text = _inlines(node.children, within | {"a"}) or (href,)
                rendered.append(_Element("a", text, href=href))
            case "image":
                src = node.attrs["src"]
                alt = node.content or src
                rendered.append(alt if "a" in within else _Element("a", (alt,), href=src))
    return tuple(rendered)


def _fit(nodes: tuple[_Node, ...], limit: int) -> tuple[_Node, ...]:
    """The nodes with their text cut to `limit` units, an ellipsis marking the cut."""
    if _units_in(nodes) <= limit:
        return nodes
    kept, _ = _cut(nodes, limit - _units(ELLIPSIS))
    return kept


def _cut(nodes: tuple[_Node, ...], budget: int) -> tuple[tuple[_Node, ...], int | None]:
    """The nodes' first `budget` units of text, and the budget left, None once the cut is made."""
    kept: list[_Node] = []
    for node in nodes:
        if isinstance(node, str):
            if _units(node) <= budget:
                kept.append(node)
                budget -= _units(node)
                continue
            kept.append(_prefix(node, budget) + ELLIPSIS)
            return tuple(kept), None
        children, left = _cut(node.children, budget)
        kept.append(replace(node, children=children))
        if left is None:
            return tuple(kept), None
        budget = left
    return tuple(kept), budget


def _units(text: str) -> int:
    """The length in UTF-16 code units, the unit of Telegram's entity offsets. It is never less than
    the code point count, so a text cut to it fits either count."""
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


def _units_in(nodes: tuple[_Node, ...]) -> int:
    return sum(
        _units(node) if isinstance(node, str) else _units_in(node.children) for node in nodes
    )


def _prefix(text: str, budget: int) -> str:
    units = 0
    for index, char in enumerate(text):
        units += _units(char)
        if units > budget:
            return text[:index]
    return text


def _html(nodes: tuple[_Node, ...]) -> str:
    parts = []
    for node in nodes:
        if isinstance(node, str):
            parts.append(escape(node, quote=False))
        else:
            href = "" if node.href is None else f' href="{escape(node.href)}"'
            parts.append(f"<{node.tag}{href}>{_html(node.children)}</{node.tag}>")
    return "".join(parts)
