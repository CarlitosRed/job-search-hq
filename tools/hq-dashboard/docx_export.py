"""Document converters and marker rules."""
import re
from pathlib import Path
import docx
from docx.shared import Pt
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

def plain(s):
    return re.sub("[\ud800-\udfff]", "", ILLEGAL_CHARACTERS_RE.sub("", s.replace("\x0b", "\n").replace("\x0c", "\n")))


# Clean output (Claude · HQ Builder (Opus), 2026-11-05): a document holds only the document. Marker lines are EXACT
# forms only, so real content ("Signed 3 partners", "Sentiment analysis pipeline", "Note that ...", "[RAG]") is never
# touched: a line starting "Sentiment:", "Signed :", "Note:", "Notes:", "Word count:" or "Draft:" (colon required),
# a line that is only "(About N words)" or "(N words)", a line made only of known tags ([HQ], [/HQ], [DRAFT],
# [REVIEWED], [TODO], [PLACEHOLDER]), or a lone DRAFT banner. Leading Markdown emphasis or heading marks are ignored.
MARKER_LABEL = re.compile(r"^(?:Sentiment|Signed|Notes?|Word count|Draft)\s*:", re.I)
MARKER_WORDS = re.compile(r"^\((?:about\s+)?~?\d[\d,]*\s+words\)$", re.I)
MARKER_TAGS = re.compile(r"^(?:\[(?:/?HQ|DRAFT|REVIEWED|TODO|PLACEHOLDER)\]\s*)+$")
MARKER_BANNER = re.compile(r"^[^\w]*DRAFT[^\w]*$")  # case-sensitive: the word alone, e.g. "DRAFT" or "— DRAFT —"


KNOWN_TAG = re.compile(r"\[(?:/?HQ|DRAFT|REVIEWED|TODO|PLACEHOLDER)\]")


def marker_line(line):
    s = line.strip()
    if MARKER_TAGS.match(s) or MARKER_BANNER.match(s) or MARKER_WORDS.match(s):
        return True
    # Ignore leading heading/quote marks, one bullet, and emphasis: "- Sentiment: x", "_Signed : Codex_", "**Signed :**".
    s = re.sub(r"^[#>\s]+", "", s)
    s = re.sub(r"^[-*•+]\s+", "", s)
    s = re.sub(r"\*\*|__|(?<![\w])[*_]|[*_](?![\w])", "", s).strip()
    return bool(MARKER_LABEL.match(s) or MARKER_WORDS.match(s) or MARKER_TAGS.match(s) or MARKER_BANNER.match(s))


def has_known_tag(line):
    return bool(KNOWN_TAG.search(line))


def draft_problem(text, kind):
    """Why a drafted CV or letter is not clean, in plain English, or None. Used before a draft is saved."""
    name = "cover letter" if kind == "letter" else "CV"
    for line in str(text or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s:
            continue
        if marker_line(s) or has_known_tag(s):
            return f"The {name} came back with a marker line (a tag like [DRAFT], a DRAFT banner, a word count, or a line starting Sentiment:, Signed\x3a, Note: or Draft:). The document must hold only its own text. Try again."
        if kind == "letter" and (s.startswith("#") or s.startswith(">") or s.startswith("---")):
            return f"The {name} came back with a heading, quote or divider line. A letter is plain paragraphs only. Try again."
    return None


def readability_problems(text, blocked_terms=()):
    """Explain internal references in a draft; never change its wording."""
    text = str(text or '')
    notes = []
    rules = (
        (r'#\d+|\bpull/\d+', 'a pull request or issue reference'),
        (r'(?:https?://|\bgithub\.com/)[^\s<>\[\]()]*?/(?:pull|issues|blob|tree|commit)/[^\s<>\[\]()]*',
         'a link to internal project detail'),
        (r'\bE-\d{3}\b', 'an evidence identifier'),
        (r'\b(?:rule|leak)\s*#?\s*\d+\b', 'an internal rule or leak identifier'),
    )
    for pattern, reason in rules:
        for match in re.finditer(pattern, text, re.I):
            notes.append(f'"{match[0]}" is {reason}; a CV should describe the work in plain words without internal references.')
    for term in blocked_terms:
        if not term:
            continue
        for match in re.finditer(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text, re.I):
            notes.append(f'"{match[0]}" is a blocked technical term; a CV should explain the work in words a recruiter can understand.')
    return notes


def clean_text(md):
    # Drops exact marker lines and removes the closed list of tags ([HQ], [DRAFT], ...) inside a line, collapsing
    # the space they leave. Every other bracket and every other word is kept exactly as written.
    out = []
    for line in plain(md or "").replace("\r\n", "\n").split("\n"):
        if marker_line(line):
            continue
        if has_known_tag(line):
            lead = re.match(r"^\s*", line).group(0)
            line = lead + re.sub(r" {2,}", " ", KNOWN_TAG.sub("", line[len(lead):])).strip()
        out.append(line.rstrip())
    return "\n".join(out)


def _plain_document():
    # Calibri 11, no header or footer text, neutral file properties (the default template carries its own).
    d = docx.Document()
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(11)
    for section in d.sections:
        for part in (section.header, section.footer):
            for p in part.paragraphs:
                p.text = ""
    cv_layout().neutral_properties(d)
    return d


def letter_to_docx(md, path):
    # A business letter: plain paragraphs, Normal style only. Blank lines split paragraphs; lines inside one
    # paragraph (a sign-off and the name) stay as line breaks. Markdown syntax (#, >, **, rules) becomes plain text;
    # no wording is removed except exact marker lines.
    d = _plain_document()
    blocks, cur = [], []
    for line in clean_text(md).split("\n"):
        s = line.strip()
        if re.fullmatch(r"[-*_]{3,}", s):
            s = ""  # a rule line has no words
        s = re.sub(r"^#{1,6}\s+", "", s)
        s = re.sub(r"^>\s?", "", s)
        b = re.match(r"^[-*•]\s+(.*)", s)
        if b:
            s = "• " + b.group(1)
        if not s:
            if cur:
                blocks.append(cur)
                cur = []
            continue
        cur.append(s.replace("**", "").replace("__", ""))
    if cur:
        blocks.append(cur)
    for block in blocks:
        p = d.add_paragraph()
        p.paragraph_format.space_after = Pt(10)
        for i, s in enumerate(block):
            run = p.add_run(s)
            if i < len(block) - 1:
                run.add_break()
    d.save(path)


_layout = None


def cv_layout():
    # The one CV style, shared with cv_onepage.py (Claude · CV Layout Builder (Opus), 2026-11-05).
    global _layout
    if _layout is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("hq_cv_layout", Path(__file__).resolve().parent / "cv_layout.py")
        _layout = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_layout)
    return _layout


def md_to_docx(md, path):
    # A drafted CV in the same scannable style as the tailored CVs: Arial, name block, section headings with a thin
    # rule, roles with right-aligned dates, real bullets. Exact marker lines are dropped first; no other word changes.
    cv_layout().md_to_docx(clean_text(md), path)


LINE_CAP = 2000  # a rendered report line or paragraph never exceeds this many characters


def _inline(text):
    # Links and images become their text (a URL stays visible as text), backticks go; **bold** becomes runs.
    # Every repeat is bounded, so hostile input (40 KB of "[") stays linear. Only http(s) URLs are shown.
    target = r"\((?:[^()\n]|\([^()\n]{0,300}\)){0,2000}\)"
    t = re.sub(r"!\[([^\]\n]{0,300})\]" + target, r"\1", text[:LINE_CAP])
    t = re.sub(r"\[([^\]\n]{1,300})\]\((https?://[^()\s]{1,2000})\)", lambda m: m[1] if m[1] == m[2] else f"{m[1]} ({m[2]})", t)
    t = re.sub(r"\[([^\]\n]{1,300})\]" + target, r"\1", t).replace("`", "")
    runs = []
    for part in re.split(r"(\*\*[^*]+\*\*)", t):
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            runs.append([part[2:-2], True])
        elif part:
            runs.append([part, False])
    return runs


def _text(runs):
    return "".join(r[0] for r in runs)


def report_blocks(md):
    """A Markdown report as plain data: headings, paragraphs, bullets and tables flattened to labelled rows.
    Nothing here is HTML: the page builds every node with textContent, so a <script> in a report stays text."""
    lines = [l[:LINE_CAP] for l in plain(md or "").replace("\r\n", "\n").split("\n")]
    blocks, para, i = [], [], 0

    def flush():
        if para:
            runs = []
            for k, line in enumerate(para):  # line by line, so each regex pass sees at most LINE_CAP characters
                runs += ([[" ", False]] if k else []) + _inline(line)
            blocks.append({"type": "p", "runs": runs})
            para.clear()

    while i < len(lines):
        s = lines[i].strip()
        if not s or s.startswith("```") or re.fullmatch(r"[-*_]{3,}", s):
            flush()
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            flush()
            blocks.append({"type": "h", "level": min(len(m[1]), 3), "runs": _inline(m[2].strip().rstrip("#").strip())})
            i += 1
            continue
        if s.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) or not c for c in cells):
                    rows.append(cells)
                i += 1
            head = [_text(_inline(h)) for h in rows[0]] if len(rows) > 1 else []
            body = rows[1:] if len(rows) > 1 else rows
            blocks.append({"type": "table", "rows": [[{"label": head[k] if k < len(head) else "", "runs": _inline(c)}
                                                      for k, c in enumerate(r) if c] for r in body]})
            continue
        if re.match(r"^(?:[-*•+]|\d+[.)])\s+", s):
            flush()
            items = []
            while i < len(lines) and re.match(r"^\s*(?:[-*•+]|\d+[.)])\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*(?:[-*•+]|\d+[.)])\s+", "", lines[i]).strip()))
                i += 1
            blocks.append({"type": "ul", "items": items})
            continue
        para.append(re.sub(r"^>\s?", "", s))
        i += 1
    flush()
    return blocks


def report_to_docx(md, stream):
    # Same blocks as the page shows. A table row becomes one paragraph of "Label: value" lines.
    d = _plain_document()

    def runs(p, rs):
        for text, bold in rs:
            p.add_run(text).bold = bold or None

    for b in report_blocks(md):
        if b["type"] == "h":
            d.add_heading(_text(b["runs"]), level=b["level"])
        elif b["type"] == "p":
            runs(d.add_paragraph(), b["runs"])
        elif b["type"] == "ul":
            for item in b["items"]:
                runs(d.add_paragraph(style="List Bullet"), item)
        else:
            for row in b["rows"]:
                p = d.add_paragraph()
                p.paragraph_format.space_after = Pt(8)
                for k, cell in enumerate(row):
                    if cell["label"]:
                        p.add_run(cell["label"] + ": ").bold = True
                    runs(p, cell["runs"])
                    if k < len(row) - 1 and p.runs:
                        p.runs[-1].add_break()
    d.save(stream)

