"""A small, deterministic PDF writer — Epic 9.14.

WHY THERE IS NO PDF DEPENDENCY HERE
-----------------------------------
product-spec.md §5.1 names "React-PDF or WeasyPrint". Both were assessed against
the dependency-licensing rule rather than assumed, the same discipline Epic 9.13 applied to the
`openai` and `resend` SDKs, and both were declined:

**WeasyPrint** is BSD-3-Clause itself, but it hard-requires **Pyphen**, whose
PyPI classifiers are `GPLv2+`, `LGPLv2+` and `MPL 1.1`. Every one of those trips
`license_audit.py`'s BLOCKING pattern, and the dependency-licensing rule requires an explicit
sign-off for GPL/LGPL — MPL is not in ALLOWED either. Read off PyPI metadata,
not from memory. It also needs system pango/cairo/harfbuzz, which is a native
stack the deployment does not currently carry.

**React-PDF** (`@react-pdf/renderer`, MIT) is licence-clean but architecturally
wrong for this endpoint. It is a Node library and this is a FastAPI service, so
it would need a Node subprocess in the request path — a second runtime in the
deploy. And the reuse argument that would justify that cost does not survive
contact with the library: React-PDF renders its own `<Text>`/`<View>` primitives
through its own `StyleSheet`, not HTML elements with Tailwind classes, so
`ReportView` could not be rendered by it in any case. The report would have to
be re-authored in a second component DSL either way.

Two others were checked while there: **fpdf2** is `LGPL-3.0-only` — blocked
outright. **reportlab** is BSD with clean transitive licences (Pillow,
charset-normalizer) and would pass the audit; it was declined because this
document has no image, no embedded font and no vector artwork, so it would buy a
native Pillow wheel for nothing.

So: **zero dependencies added**, which is what the brief asks for when the
existing stack covers the job. It does. A PDF is a documented byte format, the
14 standard fonts need no embedding, and `zlib` is in the standard library.

WHAT THIS DELIBERATELY IS NOT
-----------------------------
Not a layout engine. There are no floats, no columns, no tables with spanning
cells, no images, and no font embedding. It lays out a single column of text and
rules, top to bottom, breaking pages when it runs out of room — which is exactly
the shape of the document above it and nothing more. If a future report needs a
chart, that is the moment to revisit the assessment above, not a reason to grow
this file into a renderer.

DETERMINISM
-----------
Identical input produces identical bytes. There is no `/CreationDate`, no
`/ID` derived from a clock, and no compression level that varies. That is the
same property `scoring-spec.md` rule 1 requires of the report's own ordering,
for the same reason: a document that is shown to a client twice must be the same
document both times.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

# A4 in PostScript points (1/72"). A4 rather than US Letter because the product
# has no US-only assumption anywhere else and A4 is the wider default.
PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89

MARGIN_X = 56.0
MARGIN_TOP = 64.0
MARGIN_BOTTOM = 56.0

CONTENT_WIDTH = PAGE_WIDTH - (2 * MARGIN_X)

REGULAR = "F1"
BOLD = "F2"
ITALIC = "F3"

_FONT_RESOURCES = {
    REGULAR: "Helvetica",
    BOLD: "Helvetica-Bold",
    ITALIC: "Helvetica-Oblique",
}

# ---------------------------------------------------------------------------
# Glyph metrics
# ---------------------------------------------------------------------------
#
# Widths in 1/1000 em, from the Adobe Core 14 AFM tables. These are frozen by
# the PDF specification — the standard 14 fonts are the ones a conforming
# reader supplies itself, so the numbers cannot drift under us and there is
# nothing to keep in sync with a package.
#
# They are here because wrapping needs to know how wide a string is. Without a
# real table the only options are a monospace face (a client-facing document
# should not look like a 1994 line printer) or a guessed average width, which
# produces lines that overrun the measure.

_HELVETICA = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 "
    "556 556 556 556 556 556 556 556 556 556 278 278 584 584 584 556 "
    "1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 "
    "667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556 "
    "333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 "
    "556 556 333 500 278 556 500 722 500 500 500 334 260 334 584"
)

_HELVETICA_BOLD = (
    "278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 "
    "556 556 556 556 556 556 556 556 556 556 333 333 584 584 584 611 "
    "975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 "
    "667 778 722 667 611 722 667 944 667 667 611 333 278 333 584 556 "
    "333 556 611 556 611 556 333 611 611 278 278 556 278 889 611 611 "
    "611 611 389 556 333 611 556 778 556 556 500 389 280 389 584"
)


def _ascii_widths(table: str) -> dict[str, int]:
    """ASCII 32..126, in order."""
    values = [int(v) for v in table.split()]
    return {chr(32 + i): w for i, w in enumerate(values)}


# The non-ASCII characters this product's own copy actually uses. Everything
# else is transliterated (see `_encode`), so the table does not have to be
# exhaustive — it has to cover the punctuation the string tables contain.
_WINANSI_EXTRA: dict[str, tuple[int, int, int]] = {
    # char: (WinAnsi code, Helvetica width, Helvetica-Bold width)
    "–": (0o226, 556, 556),   # en dash
    "—": (0o227, 1000, 1000),  # em dash
    "‘": (0o221, 222, 278),   # left single quote
    "’": (0o222, 222, 278),   # right single quote / apostrophe
    "“": (0o223, 333, 500),   # left double quote
    "”": (0o224, 333, 500),   # right double quote
    "•": (0o225, 350, 350),   # bullet
    "·": (0o267, 278, 278),   # middle dot
    "…": (0o205, 1000, 1000),  # ellipsis
    "£": (0o243, 556, 556),   # pound
    "é": (0o351, 556, 611),   # e-acute — common in agency and brand names
}

# Anything outside the two tables above becomes its nearest printable ASCII.
# A '?' in a client's report is worse than a slightly plainer character.
_TRANSLITERATE = {
    " ": " ",
    "‑": "-",
    "−": "-",
    "«": '"',
    "»": '"',
    "‹": "'",
    "›": "'",
    "ʼ": "'",
    "′": "'",
    "″": '"',
    "€": "EUR",
    "→": "->",
    "×": "x",
}

_WIDTHS: dict[str, dict[str, int]] = {
    REGULAR: _ascii_widths(_HELVETICA),
    BOLD: _ascii_widths(_HELVETICA_BOLD),
}
_WIDTHS[ITALIC] = dict(_WIDTHS[REGULAR])  # Oblique is Helvetica, slanted.

for _char, (_code, _reg, _bold) in _WINANSI_EXTRA.items():
    _WIDTHS[REGULAR][_char] = _reg
    _WIDTHS[BOLD][_char] = _bold
    _WIDTHS[ITALIC][_char] = _reg

_FALLBACK_WIDTH = 556


def text_width(text: str, font: str, size: float) -> float:
    """Width of `text` in points. Used for wrapping and for right-alignment."""
    widths = _WIDTHS.get(font, _WIDTHS[REGULAR])
    total = 0
    for char in normalise(text):
        total += widths.get(char, _FALLBACK_WIDTH)
    return total * size / 1000.0


def normalise(text: str) -> str:
    """Map a string onto characters this writer can measure and emit.

    Applied identically by `text_width` and `_encode`, so what is measured is
    always exactly what is drawn. Measuring the original and drawing a
    substitute is how a wrapper starts overrunning its measure.
    """
    out: list[str] = []
    for char in text:
        if char in _WINANSI_EXTRA or (32 <= ord(char) <= 126):
            out.append(char)
        elif char in _TRANSLITERATE:
            out.append(_TRANSLITERATE[char])
        elif char in ("\t",):
            out.append(" ")
        else:
            # Unknown, and not worth guessing at. Dropped rather than rendered
            # as a box or a question mark in a document a client will read.
            continue
    return "".join(out)


def _encode(text: str) -> bytes:
    r"""A PDF literal string: `(...)` with `\`, `(` and `)` escaped."""
    out = bytearray()
    for char in normalise(text):
        if char in _WINANSI_EXTRA:
            out.extend(f"\\{_WINANSI_EXTRA[char][0]:03o}".encode("ascii"))
        elif char in ("\\", "(", ")"):
            out.extend(b"\\" + char.encode("ascii"))
        else:
            out.extend(char.encode("ascii"))
    return bytes(out)


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy word wrap to `width` points.

    A word longer than the measure is broken mid-word rather than allowed to
    overrun — a URL or a long domain is the realistic case, and both appear in
    this report.
    """
    words = normalise(text).split()
    if not words:
        return [""]

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if text_width(candidate, font, size) <= width or not current:
            if text_width(candidate, font, size) <= width:
                current = candidate
                continue
            # `not current` and still too wide: break the word itself.
            head = ""
            for char in word:
                if text_width(head + char, font, size) > width and head:
                    lines.append(head)
                    head = char
                else:
                    head += char
            current = head
            continue
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# document
# ---------------------------------------------------------------------------


@dataclass
class _Page:
    ops: list[bytes] = field(default_factory=list)


class PdfDocument:
    """A single-column flowing document.

    The cursor walks down the page; anything that would cross the bottom margin
    starts a new page first. Callers describe content, never coordinates.
    """

    def __init__(self) -> None:
        self._pages: list[_Page] = []
        self._y = 0.0
        self._new_page()

    # --- page management ---------------------------------------------------

    def _new_page(self) -> None:
        self._pages.append(_Page())
        self._y = PAGE_HEIGHT - MARGIN_TOP

    def _ensure(self, needed: float) -> None:
        if self._y - needed < MARGIN_BOTTOM:
            self._new_page()

    @property
    def page_count(self) -> int:
        return len(self._pages)

    # --- primitives --------------------------------------------------------

    def _draw(self, op: bytes) -> None:
        self._pages[-1].ops.append(op)

    def _line(self, text: str, font: str, size: float, x: float, y: float) -> None:
        self._draw(
            b"BT /"
            + font.encode("ascii")
            + b" "
            + _num(size)
            + b" Tf "
            + _num(x)
            + b" "
            + _num(y)
            + b" Td ("
            + _encode(text)
            + b") Tj ET"
        )

    def space(self, points: float) -> None:
        """Vertical space. Never at the top of a fresh page — leading blank
        space at a page break reads as a mistake rather than as rhythm."""
        if self._y < PAGE_HEIGHT - MARGIN_TOP:
            self._y -= points

    def rule(self, *, space_above: float = 6.0, space_below: float = 10.0) -> None:
        self._ensure(space_above + space_below + 1)
        self.space(space_above)
        self._draw(
            b"0.82 0.82 0.82 rg "
            + _num(MARGIN_X)
            + b" "
            + _num(self._y)
            + b" "
            + _num(CONTENT_WIDTH)
            + b" 0.6 re f 0 0 0 rg"
        )
        self._y -= space_below

    def text(
        self,
        body: str,
        *,
        font: str = REGULAR,
        size: float = 10.0,
        leading: float = 14.0,
        indent: float = 0.0,
        space_after: float = 0.0,
        grey: bool = False,
    ) -> None:
        """A wrapped paragraph."""
        width = CONTENT_WIDTH - indent
        for line in wrap(body, font, size, width):
            self._ensure(leading)
            self._y -= leading
            if grey:
                self._draw(b"0.42 0.42 0.42 rg")
            self._line(line, font, size, MARGIN_X + indent, self._y)
            if grey:
                self._draw(b"0 0 0 rg")
        self._y -= space_after

    def eyebrow(self, label: str) -> None:
        """A small structural label. Upper-cased here rather than by the caller
        so every one of them is treated the same way."""
        self.text(label.upper(), font=BOLD, size=7.5, leading=11, grey=True)

    def heading(self, body: str, *, size: float = 15.0) -> None:
        # Kept with what follows: a heading alone at the foot of a page is a
        # widow, and a report is a document before it is a data dump.
        self._ensure(size * 1.4 + 26)
        self.space(4)
        self.text(body, font=BOLD, size=size, leading=size * 1.32, space_after=4)

    def subheading(self, body: str) -> None:
        self._ensure(34)
        self.space(6)
        self.text(body, font=BOLD, size=10.5, leading=14, space_after=2)

    def bullet(self, body: str, *, marker: str = "•") -> None:
        marker_width = 14.0
        lines = wrap(body, REGULAR, 10.0, CONTENT_WIDTH - marker_width)
        for index, line in enumerate(lines):
            self._ensure(14)
            self._y -= 14
            if index == 0:
                self._line(marker, REGULAR, 10.0, MARGIN_X, self._y)
            self._line(line, REGULAR, 10.0, MARGIN_X + marker_width, self._y)

    def key_value(self, label: str, value: str) -> None:
        """A label and its value on one line, the value right-aligned.

        Right-aligned because these are read as a column of figures, and a
        ragged right edge on a column of numbers is what makes it not read as
        one.
        """
        self._ensure(15)
        self._y -= 15
        self._draw(b"0.42 0.42 0.42 rg")
        self._line(label, REGULAR, 9.5, MARGIN_X, self._y)
        self._draw(b"0 0 0 rg")
        value_width = text_width(value, BOLD, 9.5)
        self._line(value, BOLD, 9.5, MARGIN_X + CONTENT_WIDTH - value_width, self._y)

    def table(
        self,
        headers: list[str],
        rows: list[list[str]],
        *,
        widths: list[float] | None = None,
    ) -> None:
        """A plain column table. No borders, no spanning, no wrapping inside a
        cell — a cell that does not fit is truncated with an ellipsis, because a
        row that silently reflows into two makes a table stop being scannable.
        """
        columns = len(headers)
        if widths is None:
            widths = [CONTENT_WIDTH / columns] * columns

        def row_line(values: list[str], font: str, size: float, grey: bool) -> None:
            self._ensure(14)
            self._y -= 14
            if grey:
                self._draw(b"0.42 0.42 0.42 rg")
            x = MARGIN_X
            for index in range(columns):
                cell = values[index] if index < len(values) else ""
                self._line(_truncate(cell, font, size, widths[index] - 6), font, size, x, self._y)
                x += widths[index]
            if grey:
                self._draw(b"0 0 0 rg")

        # A header with no rows under it is not a table, it is a promise.
        self._ensure(14 * (min(len(rows), 3) + 2))
        row_line(headers, BOLD, 8.5, True)
        self._y -= 2
        self._draw(
            b"0.82 0.82 0.82 rg "
            + _num(MARGIN_X)
            + b" "
            + _num(self._y)
            + b" "
            + _num(CONTENT_WIDTH)
            + b" 0.6 re f 0 0 0 rg"
        )
        self._y -= 2
        for row in rows:
            row_line(row, REGULAR, 9.5, False)

    # --- output ------------------------------------------------------------

    def render(self) -> bytes:
        """Serialise to PDF bytes.

        Objects are written in a fixed order and the cross-reference table is
        built from the actual byte offsets, so the file is valid rather than
        approximately valid. Content streams are Flate-compressed at a fixed
        level; a document is mostly repeated font operators and compresses well.
        """
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        catalog_num = add(b"")  # 1 — patched below, needs the Pages number
        pages_num = add(b"")  # 2 — needs the Kids list
        font_nums = {
            key: add(
                b"<< /Type /Font /Subtype /Type1 /BaseFont /"
                + name.encode("ascii")
                + b" /Encoding /WinAnsiEncoding >>"
            )
            for key, name in _FONT_RESOURCES.items()
        }

        resources = (
            b"<< /Font << "
            + b" ".join(
                b"/" + key.encode("ascii") + b" " + str(num).encode("ascii") + b" 0 R"
                for key, num in font_nums.items()
            )
            + b" >> >>"
        )

        page_nums: list[int] = []
        for page in self._pages:
            stream = zlib.compress(b"\n".join(page.ops), 9)
            content_num = add(
                b"<< /Length "
                + str(len(stream)).encode("ascii")
                + b" /Filter /FlateDecode >>\nstream\n"
                + stream
                + b"\nendstream"
            )
            page_nums.append(
                add(
                    b"<< /Type /Page /Parent "
                    + str(pages_num).encode("ascii")
                    + b" 0 R /MediaBox [0 0 "
                    + _num(PAGE_WIDTH)
                    + b" "
                    + _num(PAGE_HEIGHT)
                    + b"] /Resources "
                    + resources
                    + b" /Contents "
                    + str(content_num).encode("ascii")
                    + b" 0 R >>"
                )
            )

        objects[catalog_num - 1] = (
            b"<< /Type /Catalog /Pages " + str(pages_num).encode("ascii") + b" 0 R >>"
        )
        objects[pages_num - 1] = (
            b"<< /Type /Pages /Count "
            + str(len(page_nums)).encode("ascii")
            + b" /Kids ["
            + b" ".join(str(n).encode("ascii") + b" 0 R" for n in page_nums)
            + b"] >>"
        )

        out = bytearray(b"%PDF-1.7\n")
        # A binary comment line, so a transport that sniffs the first bytes
        # treats the file as binary rather than as text and mangles the newlines.
        out.extend(b"%\xe2\xe3\xcf\xd3\n")

        offsets: list[int] = []
        for index, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out.extend(str(index).encode("ascii") + b" 0 obj\n" + body + b"\nendobj\n")

        xref_at = len(out)
        out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        out.extend(b"0000000000 65535 f \n")
        for offset in offsets:
            out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        out.extend(
            b"trailer\n<< /Size "
            + str(len(objects) + 1).encode("ascii")
            + b" /Root "
            + str(catalog_num).encode("ascii")
            + b" 0 R >>\nstartxref\n"
            + str(xref_at).encode("ascii")
            + b"\n%%EOF\n"
        )
        return bytes(out)


def _num(value: float) -> bytes:
    """A PDF number: no exponent, no trailing zeros, no locale."""
    return f"{value:.3f}".rstrip("0").rstrip(".").encode("ascii") or b"0"


def _truncate(text: str, font: str, size: float, width: float) -> str:
    text = normalise(text)
    if text_width(text, font, size) <= width:
        return text
    ellipsis = "…"
    out = ""
    for char in text:
        if text_width(out + char + ellipsis, font, size) > width:
            break
        out += char
    return out + ellipsis
