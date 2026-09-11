"""Minimal Markdown to HTML for the bundled admin guide.

Deliberately small and dependency-free: the guide uses a fixed, known subset
(headings, fenced code, tables, lists, paragraphs, inline code/emphasis).
Every value is HTML-escaped first, so nothing in the document can inject markup.
"""

import html
import re

_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_FENCE = re.compile(r"^```")
_LIST_ITEM = re.compile(r"^([-*]|\d+\.)\s+(.*)$")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_TABLE_ROW = re.compile(r"^\|(.*)\|\s*$")
_TABLE_DIVIDER = re.compile(r"^\|[\s:|-]+\|\s*$")


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = _INLINE_CODE.sub(r"<code>\1</code>", out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return out


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def render(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    out: list[str] = []
    index = 0
    in_code = False
    code_buffer: list[str] = []
    list_open = None
    table_open = False

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            out.append(f"</{list_open}>")
            list_open = None

    def close_table() -> None:
        nonlocal table_open
        if table_open:
            out.append("</tbody></table>")
            table_open = False

    while index < len(lines):
        line = lines[index]

        if _FENCE.match(line):
            if in_code:
                out.append(
                    "<pre><code>" + html.escape("\n".join(code_buffer)) + "</code></pre>"
                )
                code_buffer = []
                in_code = False
            else:
                close_list()
                close_table()
                in_code = True
            index += 1
            continue

        if in_code:
            code_buffer.append(line)
            index += 1
            continue

        if not line.strip():
            close_list()
            close_table()
            index += 1
            continue

        # Table: a header row followed by a divider row starts one.
        if (
            _TABLE_ROW.match(line)
            and index + 1 < len(lines)
            and _TABLE_DIVIDER.match(lines[index + 1])
        ):
            close_list()
            close_table()
            out.append("<table><thead><tr>")
            for cell in _cells(line):
                out.append(f"<th>{_inline(cell)}</th>")
            out.append("</tr></thead><tbody>")
            table_open = True
            index += 2
            continue

        if table_open and _TABLE_ROW.match(line):
            out.append("<tr>")
            for cell in _cells(line):
                out.append(f"<td>{_inline(cell)}</td>")
            out.append("</tr>")
            index += 1
            continue

        close_table()

        heading = _HEADING.match(line)
        if heading:
            close_list()
            level = len(heading.group(1)) + 1  # h1 -> h2: the page keeps one h1
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if line.strip() in {"---", "***"}:
            close_list()
            out.append("<hr>")
            index += 1
            continue

        if line.startswith(">"):
            close_list()
            out.append(f'<p class="note">{_inline(line.lstrip("> "))}</p>')
            index += 1
            continue

        item = _LIST_ITEM.match(line)
        if item:
            wanted = "ol" if item.group(1)[0].isdigit() else "ul"
            if list_open != wanted:
                close_list()
                out.append(f"<{wanted}>")
                list_open = wanted
            out.append(f"<li>{_inline(item.group(2))}</li>")
            index += 1
            continue

        close_list()
        out.append(f"<p>{_inline(line)}</p>")
        index += 1

    if in_code and code_buffer:
        out.append("<pre><code>" + html.escape("\n".join(code_buffer)) + "</code></pre>")
    close_list()
    close_table()
    return "\n".join(out)
