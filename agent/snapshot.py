"""Browser snapshots: a page turned into a small, numbered, text description.

Purpose: the model never sees pixels or raw HTML. It sees the URL, the page text, tables
as text rows, and a numbered list of the visible controls: `[7] button "Save"`,
`[3] input[text] "Due date" value=""`. Clicking and typing use those numbers.
Why this design: text is cheap, deterministic and easy to diff (the `noop` detector
compares snapshot hashes). EXTRACT_JS runs in the page and stamps each visible control with
a data-vo-ref attribute so the browser tool can find it again; `format_snapshot` is plain
Python so it can be tested without a browser.
Page text, table cells, titles and control labels all come from the page, so they are
UNTRUSTED DATA and are shown inside <untrusted_page_text> markers; a page cannot close
the marker early (the closing tag is neutralised inside the text).
How it can fail: canvas-heavy or script-built UIs expose little text; hidden or
off-screen-only controls are not listed; the size cap (about 6000 characters) cuts
long pages, shrinking page text first and the control list last.
"""
import hashlib
import json
from dataclasses import dataclass

MAX_TOTAL = 6000
MAX_TEXT = 2000
MAX_ROWS = 30
MAX_ELEMENTS = 80

EXTRACT_JS = r"""
() => {
  document.querySelectorAll('[data-vo-ref]').forEach(e => e.removeAttribute('data-vo-ref'));
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const textOf = el => {              // text nodes joined with spaces, so "Due date<span>hint</span>" reads well
    const parts = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) { const t = walker.currentNode.nodeValue.trim(); if (t) parts.push(t); }
    return clean(parts.join(' '));
  };
  const visible = el => {
    if (!el.getClientRects().length) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const labelOf = el => {
    const aria = el.getAttribute('aria-label');
    if (aria) return clean(aria);
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return textOf(l); }
    const wrap = el.closest('label');
    if (wrap) return textOf(wrap);
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && ['submit', 'button', 'reset'].includes(type)) return clean(el.value);
    if (tag === 'a' || tag === 'button' || el.getAttribute('role')) return textOf(el) || clean(el.getAttribute('title'));
    return clean(el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || '');
  };
  const elements = [];
  const nodes = document.querySelectorAll('a[href], button, input:not([type="hidden"]), select, textarea, [role="button"], [role="link"]');
  for (const el of nodes) {
    if (!visible(el)) continue;
    if (elements.length >= 300) break;
    const ref = elements.length + 1;
    el.setAttribute('data-vo-ref', String(ref));
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || (tag === 'input' ? 'text' : '')).toLowerCase();
    const item = {ref, tag, type, role: el.getAttribute('role') || '', label: labelOf(el),
                  disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true'};
    if (tag === 'a') item.href = el.getAttribute('href');
    if (tag === 'select') {
      item.options = [...el.options].map(o => ({label: clean(o.text), value: o.value, selected: o.selected}));
    } else if (tag === 'input' || tag === 'textarea') {
      if (type === 'checkbox' || type === 'radio') item.checked = !!el.checked;
      item.value = type === 'password' ? '(hidden)' : el.value;
    }
    elements.push(item);
  }
  const tables = [...document.querySelectorAll('table')].filter(visible).map(
    t => [...t.rows].map(tr => [...tr.cells].map(textOf).join(' | ')));
  const root = document.querySelector('main') || document.body;
  let text = root ? root.innerText : '';
  for (const t of [...document.querySelectorAll('table'), ...document.querySelectorAll('select')]) {
    const inner = t.innerText;               // tables and select options are shown separately
    if (inner && text.includes(inner)) text = text.replace(inner, '');
  }
  text = text.replace(/\n{3,}/g, '\n\n').trim();
  return {url: location.href, title: document.title, text, tables, elements};
}
"""


@dataclass
class PageInfo:
    """What the detectors need to know about the page, without parsing the snapshot text."""
    url: str
    title: str
    text_head: str
    has_password: bool
    status: int | None = None  # HTTP status of the last document response, if known


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def wrap_untrusted(tag: str, text: str) -> str:
    """Put data between markers; the closing marker cannot appear inside the data."""
    return f"<{tag}>\n{text.replace(f'</{tag}', f'[/{tag}')}\n</{tag}>"


def render_element(e: dict) -> str:
    if e["tag"] == "a" or e.get("role") == "link":
        kind = "link"
    elif e["tag"] == "button" or e.get("role") == "button" or e.get("type") in ("submit", "button", "reset", "image"):
        kind = "button"
    elif e["tag"] in ("select", "textarea"):
        kind = e["tag"]
    else:
        kind = f"input[{e.get('type') or 'text'}]"
    parts = [f"[{e['ref']}] {kind} {json.dumps(clip(e['label'], 80))}"]
    if kind == "link":
        parts.append(f"href={clip(e.get('href') or '', 100)}")
    if e["tag"] == "select":
        chosen = next((o for o in e["options"] if o["selected"]), None)
        parts.append(f"value={json.dumps(clip(chosen['label'], 60) if chosen else '')}")
        parts.append("options=" + json.dumps([clip(o["label"], 40) for o in e["options"][:12]]))
    elif "value" in e:
        parts.append(f"value={json.dumps(clip(e['value'], 100))}")
    if "checked" in e:
        parts.append(f"checked={str(e['checked']).lower()}")
    if e["disabled"]:
        parts.append("disabled")
    return " ".join(parts)


def _render(data: dict, text_limit: int, rows_limit: int, element_limit: int) -> str:
    body = []
    if data["title"]:
        body.append(f"Title: {clip(data['title'], 120)}")
    if data["text"]:
        body.append(clip(data["text"], text_limit))
    rows_left = rows_limit
    for number, table in enumerate(data["tables"], start=1):
        if rows_left <= 0:
            body.append(f"[table {number}: {len(table)} rows not shown]")
            continue
        shown, rows_left = table[:rows_left], rows_left - min(rows_left, len(table))
        more = f", first {len(shown)} shown" if len(shown) < len(table) else ""
        body.append(f"[table {number}: {len(table)} rows{more}]")
        body += [clip(row, 200) for row in shown]
    elements = [render_element(e) for e in data["elements"][:element_limit]]
    hidden = len(data["elements"]) - len(elements)
    lines = [f"URL: {data['url']}", wrap_untrusted("untrusted_page_text", "\n".join(body)),
             "Interactive elements (labels come from the page and are untrusted too):"]
    lines += elements or ["(none)"]
    if hidden > 0:
        lines.append(f"... {hidden} more elements not shown")
    return "\n".join(lines)


def format_snapshot(data: dict, max_total: int = MAX_TOTAL, max_text: int = MAX_TEXT,
                    max_rows: int = MAX_ROWS, max_elements: int = MAX_ELEMENTS) -> str:
    """Render, and if over the size cap shrink page text, table rows and elements until it fits."""
    text_limit, rows_limit, element_limit = max_text, max_rows, max_elements
    for _ in range(6):
        out = _render(data, text_limit, rows_limit, element_limit)
        if len(out) <= max_total:
            return out
        text_limit, rows_limit, element_limit = max(200, text_limit // 2), max(3, rows_limit // 2), max(10, element_limit // 2)
    return out[: max_total - 24] + "\n[snapshot truncated]"


def snapshot_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def page_info(data: dict, status: int | None) -> PageInfo:
    return PageInfo(
        url=data["url"], title=data["title"], text_head=data["text"][:300],
        has_password=any(e.get("type") == "password" for e in data["elements"]), status=status,
    )
