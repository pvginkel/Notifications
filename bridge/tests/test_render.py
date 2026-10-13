import base64
import hashlib
import re
from email.header import decode_header
from html.parser import HTMLParser

import pytest
from notification_bridge.render import (
    TELEGRAM_LIMIT,
    Priority,
    ntfy_headers,
    ntfy_id,
    telegram_html,
)

# ntfy's sequence id rule (server/server.go, sequenceIDRegex = topicRegex).
NTFY_SEQUENCE_ID = re.compile(r"[-_A-Za-z0-9]{1,64}")
TELEGRAM_TAGS = {"b", "i", "s", "code", "pre", "a", "blockquote"}


class TelegramMarkup(HTMLParser):
    """Checks that a text is well-formed in Telegram's tag set, and collects its visible text."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.open: list[str] = []
        self.visible = ""
        self.feed(text)
        self.close()
        assert not self.open, f"unclosed {self.open}"

    def handle_starttag(self, tag, attrs):
        assert tag in TELEGRAM_TAGS, tag
        assert [name for name, _ in attrs] == (["href"] if tag == "a" else [])
        # Telegram's nesting rules (core.telegram.org/bots/api, Formatting options).
        enclosing = set(self.open)
        assert not {"code", "pre"} & enclosing, f"{tag} in {self.open}"
        if tag in {"code", "pre"}:
            assert not {"b", "i", "s", "a"} & enclosing, f"{tag} in {self.open}"
        if tag in {"a", "blockquote"}:
            assert tag not in enclosing, f"{tag} in {self.open}"
        self.open.append(tag)

    def handle_endtag(self, tag):
        assert self.open.pop() == tag

    def handle_data(self, data):
        self.visible += data


def visible_units(html: str) -> int:
    return len(TelegramMarkup(html).visible.encode("utf-16-le")) // 2


# §3.7's renderer table: Markdown in, Telegram HTML out, at `default` with no title.
RENDERER_TABLE = [
    ("hello", "hello"),
    ("a < b & c > d", "a &lt; b &amp; c &gt; d"),
    ("<b>raw</b> html", "&lt;b&gt;raw&lt;/b&gt; html"),
    ("<div>\nblock\n</div>", "&lt;div&gt;\nblock\n&lt;/div&gt;"),
    ("&copy; &amp; &lt;", "© &amp; &lt;"),
    (r"\*not emphasis\*", "*not emphasis*"),
    ("*i* _i_ **b** __b__ ~~s~~", "<i>i</i> <i>i</i> <b>b</b> <b>b</b> <s>s</s>"),
    ("**bold *both***", "<b>bold <i>both</i></b>"),
    ("`<x> & y`", "<code>&lt;x&gt; &amp; y</code>"),
    ("**bold `code`** *`i`* ~~`s`~~", "<b>bold code</b> <i>i</i> <s>s</s>"),
    ("[`code`](https://e.invalid)", '<a href="https://e.invalid">code</a>'),
    ("# A `code` heading", "<b>A code heading</b>"),
    ("**![alt](https://e.invalid/p.png)**", '<b><a href="https://e.invalid/p.png">alt</a></b>'),
    ("[text](https://e.invalid/a)", '<a href="https://e.invalid/a">text</a>'),
    (
        '[q](https://e.invalid/?a=1&b="2")',
        '<a href="https://e.invalid/?a=1&amp;b=%222%22">q</a>',
    ),
    ("[**b**](https://e.invalid)", '<a href="https://e.invalid"><b>b</b></a>'),
    ("[](https://e.invalid)", '<a href="https://e.invalid">https://e.invalid</a>'),
    ("<https://e.invalid>", '<a href="https://e.invalid">https://e.invalid</a>'),
    ("[x](javascript:alert(1))", "[x](javascript:alert(1))"),
    ("![alt](https://e.invalid/p.png)", '<a href="https://e.invalid/p.png">alt</a>'),
    (
        r"![*a* &amp; `b` [c](https://e.invalid) d\_e](https://e.invalid/p.png)",
        '<a href="https://e.invalid/p.png">a &amp; b c d_e</a>',
    ),
    (
        "![](https://e.invalid/p.png)",
        '<a href="https://e.invalid/p.png">https://e.invalid/p.png</a>',
    ),
    ("[![alt](https://e.invalid/p.png)](https://e.invalid)", '<a href="https://e.invalid">alt</a>'),
    (
        "[a <https://x.invalid> **b**](https://e.invalid)",
        '<a href="https://e.invalid">a https://x.invalid <b>b</b></a>',
    ),
    ("# Heading\n\ntext", "<b>Heading</b>\n\ntext"),
    ("Setext *h*\n===", "<b>Setext <i>h</i></b>"),
    ("one\n\ntwo", "one\n\ntwo"),
    ("soft\nbreak  \nhard", "soft\nbreak\nhard"),
    ("- a\n- b", "• a\n• b"),
    ("* a\n\n* b", "• a\n• b"),
    ("- a\n  - b\n    - c\n- d", "• a\n  • b\n    • c\n• d"),
    ("3. x\n4. y", "3. x\n4. y"),
    ("1) x", "1) x"),
    ("- item\n\n  more", "• item\nmore"),
    ("text\n- a", "text\n\n• a"),
    ("> quote\n> on", "<blockquote>quote\non</blockquote>"),
    ("> a\n>\n> > b", "<blockquote>a\n\nb</blockquote>"),
    ("> - a", "<blockquote>• a</blockquote>"),
    ("```py\n<x> & y\n```", "<pre>&lt;x&gt; &amp; y</pre>"),
    ("    indented\n    code", "<pre>indented\ncode</pre>"),
    ("a\n\n---\n\nb", "a\n\n———\n\nb"),
    ("| a | b |\n| - | - |", "| a | b |\n| - | - |"),
]


@pytest.mark.parametrize(("markdown", "html"), RENDERER_TABLE)
def test_renders_markdown_to_telegram_html(markdown, html):
    rendered = telegram_html(None, markdown, Priority.DEFAULT)

    assert rendered == html
    TelegramMarkup(rendered)


MARKED = [
    (Priority.MIN, "⚪ "),
    (Priority.LOW, "🔵 "),
    (Priority.DEFAULT, ""),
    (Priority.HIGH, "🟠 "),
    (Priority.MAX, "🚨 "),
]


@pytest.mark.parametrize(("priority", "mark"), MARKED)
def test_the_first_line_is_the_mark_and_the_bold_title(priority, mark):
    assert telegram_html("Build 61", "It *failed*.", priority) == (
        f"{mark}<b>Build 61</b>\nIt <i>failed</i>."
    )


@pytest.mark.parametrize(("priority", "mark"), MARKED)
def test_with_no_title_the_mark_prefixes_the_first_line(priority, mark):
    assert telegram_html(None, "It *failed*.\n\nAgain.", priority) == (
        f"{mark}It <i>failed</i>.\n\nAgain."
    )


def test_the_title_is_plain_text():
    assert telegram_html("*a* <b> & c", "m", Priority.DEFAULT) == "<b>*a* &lt;b&gt; &amp; c</b>\nm"


def test_a_message_at_the_limit_is_whole():
    message = "a" * TELEGRAM_LIMIT

    assert telegram_html(None, message, Priority.DEFAULT) == message


def test_a_message_past_the_limit_is_cut_in_its_visible_text():
    rendered = telegram_html(None, "a" * (TELEGRAM_LIMIT + 1), Priority.DEFAULT)

    assert rendered == "a" * (TELEGRAM_LIMIT - 1) + "…"


def test_the_mark_and_the_title_count_towards_the_limit():
    rendered = telegram_html("T", "a" * TELEGRAM_LIMIT, Priority.HIGH)

    assert rendered.startswith("🟠 <b>T</b>\naaa")
    assert rendered.endswith("a…")
    assert visible_units(rendered) == TELEGRAM_LIMIT


def test_markup_does_not_count_towards_the_limit():
    message = "**" + "&" * TELEGRAM_LIMIT + "**"

    assert telegram_html(None, message, Priority.DEFAULT) == f"<b>{'&amp;' * TELEGRAM_LIMIT}</b>"


def test_a_cut_inside_markup_closes_it():
    message = "a" * 4000 + " **" + "b" * 50 + " [" + "c" * 100 + "](https://e.invalid)** tail"

    rendered = telegram_html(None, message, Priority.DEFAULT)

    assert rendered == (
        "a" * 4000
        + " <b>"
        + "b" * 50
        + ' <a href="https://e.invalid">'
        + "c" * (TELEGRAM_LIMIT - 4000 - 1 - 50 - 1 - 1)
        + "…</a></b>"
    )
    assert visible_units(rendered) == TELEGRAM_LIMIT


def test_a_cut_at_an_element_boundary_puts_the_ellipsis_inside_it():
    rendered = telegram_html(None, "a" * (TELEGRAM_LIMIT - 1) + "`code`", Priority.DEFAULT)

    assert rendered == "a" * (TELEGRAM_LIMIT - 1) + "<code>…</code>"


def test_a_cut_inside_a_code_block_keeps_it_valid():
    rendered = telegram_html(None, "```\n" + "<" * (TELEGRAM_LIMIT + 10) + "\n```", Priority.LOW)

    assert rendered == "🔵 <pre>" + "&lt;" * (TELEGRAM_LIMIT - 4) + "…</pre>"
    assert visible_units(rendered) == TELEGRAM_LIMIT


def test_the_limit_counts_utf16_units_and_never_splits_a_character():
    fits = "🙂" * (TELEGRAM_LIMIT // 2)
    assert telegram_html(None, fits, Priority.DEFAULT) == fits

    rendered = telegram_html(None, fits + "🙂", Priority.DEFAULT)

    assert rendered == "🙂" * (TELEGRAM_LIMIT // 2 - 1) + "…"
    assert visible_units(rendered) == TELEGRAM_LIMIT - 1


def test_the_ntfy_id_is_the_keyed_blake2b_of_the_generation_and_the_sequence_id():
    expected = hashlib.blake2b(b"0:slice-061", digest_size=16, key=b"jenkins").hexdigest()

    assert ntfy_id("jenkins", "slice-061", 0) == expected
    assert re.fullmatch(r"[0-9a-f]{32}", expected)


@pytest.mark.parametrize(
    "sequence_id", ["slice-061", "", "a" * 256, "ünïcode / spaces: and colons", "🙂"]
)
def test_the_ntfy_id_is_a_valid_ntfy_sequence_id(sequence_id):
    assert NTFY_SEQUENCE_ID.fullmatch(ntfy_id("é" * 32, sequence_id, 12))


def test_two_registrations_on_one_topic_get_distinct_ntfy_ids():
    assert ntfy_id("kubecoder", "alert-1", 0) != ntfy_id("kubecoder-dev", "alert-1", 0)


def test_distinct_generations_of_a_sequence_get_distinct_ntfy_ids():
    ids = {ntfy_id("jenkins", "slice-061", generation) for generation in range(100)}

    assert len(ids) == 100


def test_the_generation_and_the_sequence_id_never_join_to_one_input():
    assert ntfy_id("jenkins", "a1", 0) != ntfy_id("jenkins", "a", 10)
    assert ntfy_id("jenkins", "1:a", 0) != ntfy_id("jenkins", "a", 1)
    assert ntfy_id("jenkins", "0:a", 1) != ntfy_id("jenkins", "a", 10)


def headers(**overrides):
    arguments = {
        "icon_base_url": "https://icons.invalid/registration-icons",
        "icon": "jenkins.png",
        "priority": Priority.HIGH,
        "title": "Build 61",
        "sequence": "0123456789abcdef0123456789abcdef",
    }
    return ntfy_headers(**(arguments | overrides))


def decoded_title(value: str) -> str:
    [(raw, charset)] = decode_header(value)
    return raw.decode(charset)


def test_a_publish_carries_markdown_the_title_the_priority_the_icon_and_the_id():
    assert headers() == {
        "X-Markdown": "yes",
        "X-Priority": "4",
        "X-Icon": "https://icons.invalid/registration-icons/jenkins.png",
        "X-Title": "=?utf-8?b?" + base64.b64encode(b"Build 61").decode() + "?=",
        "X-Sequence-ID": "0123456789abcdef0123456789abcdef",
    }
    assert decoded_title(headers()["X-Title"]) == "Build 61"


@pytest.mark.parametrize(
    ("priority", "value"),
    [
        (Priority.MIN, "1"),
        (Priority.LOW, "2"),
        (Priority.DEFAULT, "3"),
        (Priority.HIGH, "4"),
        (Priority.MAX, "5"),
    ],
)
def test_the_priority_is_on_ntfys_scale(priority, value):
    assert headers(priority=priority)["X-Priority"] == value


def test_a_title_of_any_text_travels_as_an_ascii_header():
    title = "Prüfung 🙂\nline two =?utf-8?q?not_a_word?="

    value = headers(title=title)["X-Title"]

    assert value.isascii() and "\n" not in value
    assert decoded_title(value) == title


def test_a_one_shot_carries_no_id_and_no_title_when_it_has_none():
    published = headers(title=None, sequence=None)

    assert "X-Sequence-ID" not in published
    assert "X-Title" not in published


def test_the_icon_name_is_quoted_into_the_url():
    assert headers(icon="my icon.png")["X-Icon"] == (
        "https://icons.invalid/registration-icons/my%20icon.png"
    )
