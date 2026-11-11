"""One visual style for every CV Word file: the trimmed tailored CVs (cv_onepage.py) and the website's AI-drafted CV
export (docx_export.md_to_docx).

Layout only: this module never adds, rewords or drops a word. It splits a role line "**Title | Company** | dates" into
a bold title, the company in regular weight and the dates on a right tab stop; " | " separators in the headline,
contact line and role line show as " · ". A project title ending in a date in parentheses, "**Title (Oct 2026)**",
shows the date on the right tab without its parentheses. Every other character of the Markdown is kept (only
** bold marks and the #, ## and bullet markers become formatting).

Safe on an iPhone (Pages, Files preview, Word) and in ATS parsers: Arial, one column, no tables, text boxes, images,
headers or footers; real paragraph styles; real Word bullets ("•" in Arial, hanging indent).

page_fill() is an estimate from real Arial metrics (PIL), not Word itself: always glance at the file on the phone.
"""
# Resolve settings beside this module under isolated execution.
import importlib.util as _settings_util
from pathlib import Path as _SettingsPath
_settings_spec = _settings_util.spec_from_file_location('hq_owner_settings', _SettingsPath(__file__).with_name('hq_settings.py'))
hq_settings = _settings_util.module_from_spec(_settings_spec)
_settings_spec.loader.exec_module(hq_settings)
SETTINGS = hq_settings.load()

import copy
import re
from pathlib import Path

import docx
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Twips

# Page: US Letter, 0.75 in side margins, 0.6 in top and bottom.
PAGE_W, PAGE_H, MARGIN_X, MARGIN_Y = 12240, 15840, 1080, 864  # twips
TEXT_W = (PAGE_W - 2 * MARGIN_X) / 20  # points
TEXT_H = (PAGE_H - 2 * MARGIN_Y) / 20
SAFETY = 0.93  # Word, Pages and the phone preview wrap a little differently from this estimate
FONT = 'Arial'
ACCENT = '1F3A5F'  # dark navy: name and section headings only; prints as a dark grey
RULE = '9AA5B4'    # the thin line under section headings
MUTED = '4A4A4A'   # headline, contact line and dates (plain dark grey, not the accent)
LINE = 1.05        # line spacing multiple for every paragraph
ARIAL_LINE = 1.15  # Arial's single line height in Word, as a multiple of the font size
SEP = ' · '

# style name: (size pt, bold, colour, space before, space after, keep with next, all caps)
STYLES = {
    'CV Name': (20, True, ACCENT, 0, 2, True, False),
    'CV Headline': (11, False, MUTED, 0, 2, True, False),
    'CV Contact': (9.5, False, MUTED, 0, 4, True, False),
    'CV Section': (10.5, True, ACCENT, 10, 3, True, True),
    'CV Role': (10.5, True, None, 5, 1.5, True, False),
    'CV Bullet': (10, False, None, 0, 1, False, False),
    'CV Body': (10, False, None, 0, 3, False, False),
}
DATE_SIZE = 9.5
BULLET_LEFT, BULLET_HANG = 300, 200  # twips: text starts 0.21 in in, the bullet hangs 0.14 in to its left
BORDER_SZ, BORDER_SPACE = 6, 2       # eighths of a point (0.75 pt line), points between text and line
NUM_ID = 90
BREAK = '+break'  # suffix on a 'CV Section' block: that section starts a new page

MONTH = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?'
POINT = rf'(?:{MONTH}\s+\d{{4}}|\d{{4}})'
DATES = re.compile(rf'{POINT}(?:\s*(?:to|-|–|—)\s*(?:{POINT}|Present|present|Current|current|now))?')
CONTACT = re.compile(r'@|linkedin|github\.com|\+?\d[\d ()-]{6,}\d|\[(?:email|phone)\]', re.I)


# ---------------------------------------------------------------- Markdown to paragraphs

def parse(md):
    """Head lines (before the first section) and sections [{'name', 'items'}]. A '#'/'##' line after the name, or any
    '##' line, opens a section; blank lines and --- rules are dropped (they hold no words)."""
    head, sections, seen_name = [], [], False
    for raw in md.replace('\r\n', '\n').split('\n'):
        line = raw.rstrip()
        if not line.strip() or re.fullmatch(r'\s*[-*_]{3,}\s*', line):
            continue
        m = re.match(r'^(#{1,2})\s+(.*)', line)
        if m and (m.group(1) == '##' or seen_name or sections):
            sections.append({'name': m.group(2).strip(), 'items': []})
        elif sections:
            sections[-1]['items'].append(line)
        else:
            seen_name = seen_name or bool(m)
            head.append(line)
    return head, sections


def inline(text):
    """[(text, bold)] from **bold** marks; every other character stays as written."""
    out = []
    for part in re.split(r'(\*\*[^*]+\*\*)', text):
        if part.startswith('**') and part.endswith('**') and len(part) > 4:
            out.append((part[2:-2], True))
        elif part:
            out.append((part, False))
    return out


def plain(text):
    return ''.join(t for t, _ in inline(text))


def role(line):
    """(title, company, dates) for a role or project line, or None for any other line.
    '**Title | Company** | Aug 2025 to May 2026', '**Title | Company | Aug 2025 to May 2026**',
    '**Project title (Oct 2026)**' (dates 'Oct 2026', parentheses dropped), '### Title | Company | 2024'."""
    h = re.match(r'^###\s+(.*)', line)
    if h:
        bold, rest = h.group(1).strip(), ''
    else:
        m = re.fullmatch(r'\*\*([^*]+)\*\*(.*)', line.strip())
        if not m or (m.group(2) and not m.group(2).startswith(' |')):
            return None
        bold, rest = m.group(1).strip(), m.group(2)
    parts = [p.strip() for p in bold.split(' | ')]
    date = ''
    if rest:
        date = rest.strip()[1:].strip()
    elif len(parts) > 1 and DATES.fullmatch(parts[-1]):
        date = parts.pop()
    else:
        p = re.fullmatch(r'(.*\S)\s+\((' + DATES.pattern + r')\)', parts[-1])
        if p:
            parts[-1], date = p.group(1), p.group(2)
    return parts[0], SEP.join(parts[1:]), date


def blocks(head, sections):
    """Paragraph plan: [(style, payload)]. Payload is runs [(text, bold)] or, for 'CV Role', (title, company, dates)."""
    out = []
    has_name = any(re.match(r'^#\s', l) for l in head)
    headline_done = False
    for line in head:
        if not has_name:
            out.append(('CV Body', inline(line)))
        elif re.match(r'^#\s', line):
            out.append(('CV Name', [(plain(re.sub(r'^#\s+', '', line)), False)]))
        else:
            text = plain(line).replace(' | ', SEP)
            if CONTACT.search(text):
                out.append(('CV Contact', [(text, False)]))
            elif not headline_done:
                out.append(('CV Headline', [(text, False)]))
                headline_done = True
            else:
                out.append(('CV Body', inline(line)))
    for sec in sections:
        out.append(('CV Section' + (BREAK if sec.get('page_break') else ''), [(sec['name'], False)]))
        for line in sec['items']:
            b = re.match(r'^\s*[-*•+]\s+(.*)', line)
            r = None if b else role(line)
            if b:
                out.append(('CV Bullet', inline(b.group(1))))
            elif r:
                out.append(('CV Role', r))
            else:
                out.append(('CV Body', inline(line)))
    return out


# ---------------------------------------------------------------- Word file

def neutral_properties(d):
    cp = d.core_properties
    cp.author, cp.last_modified_by, cp.title, cp.subject, cp.keywords, cp.comments, cp.category = (
        SETTINGS['owner_name'], SETTINGS['owner_name'], '', '', '', '', '')


def _fonts(rpr_owner):
    rpr = rpr_owner.get_or_add_rPr()
    rf = rpr.find(qn('w:rFonts'))
    if rf is None:
        rf = OxmlElement('w:rFonts')
        rpr.insert(0, rf)
    for k in list(rf.attrib):
        if k.endswith('Theme'):
            del rf.attrib[k]
    for k in ('ascii', 'hAnsi', 'eastAsia', 'cs'):
        rf.set(qn('w:' + k), FONT)


def _bullets(d):
    """A one-level '•' list in Arial with a hanging indent, added to the document's numbering part."""
    numbering = d.part.numbering_part.element
    a = OxmlElement('w:abstractNum')
    a.set(qn('w:abstractNumId'), str(NUM_ID))
    for tag, val in (('w:multiLevelType', 'singleLevel'),):
        e = OxmlElement(tag)
        e.set(qn('w:val'), val)
        a.append(e)
    lvl = OxmlElement('w:lvl')
    lvl.set(qn('w:ilvl'), '0')
    for tag, val in (('w:start', '1'), ('w:numFmt', 'bullet'), ('w:lvlText', '•'), ('w:lvlJc', 'left')):
        e = OxmlElement(tag)
        e.set(qn('w:val'), val)
        lvl.append(e)
    ppr = OxmlElement('w:pPr')
    tabs = OxmlElement('w:tabs')
    tab = OxmlElement('w:tab')
    tab.set(qn('w:val'), 'num')
    tab.set(qn('w:pos'), str(BULLET_LEFT))
    tabs.append(tab)
    ppr.append(tabs)
    ind = OxmlElement('w:ind')
    ind.set(qn('w:left'), str(BULLET_LEFT))
    ind.set(qn('w:hanging'), str(BULLET_HANG))
    ppr.append(ind)
    lvl.append(ppr)
    rpr = OxmlElement('w:rPr')
    rf = OxmlElement('w:rFonts')
    for k in ('ascii', 'hAnsi', 'cs'):
        rf.set(qn('w:' + k), FONT)
    rf.set(qn('w:hint'), 'default')
    rpr.append(rf)
    lvl.append(rpr)
    a.append(lvl)
    first_num = numbering.find(qn('w:num'))
    if first_num is not None:
        first_num.addprevious(a)  # every abstractNum must come before the first num
    else:
        numbering.append(a)
    n = OxmlElement('w:num')
    n.set(qn('w:numId'), str(NUM_ID))
    ref = OxmlElement('w:abstractNumId')
    ref.set(qn('w:val'), str(NUM_ID))
    n.append(ref)
    numbering.append(n)


def _styles(d):
    normal = d.styles['Normal']
    normal.font.name, normal.font.size = FONT, Pt(10)
    _fonts(normal.element)
    nf = normal.paragraph_format
    nf.space_before, nf.space_after, nf.line_spacing, nf.widow_control = Pt(0), Pt(0), LINE, True
    for name, (size, bold, colour, before, after, keep, caps) in STYLES.items():
        st = d.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        st.base_style, st.quick_style = normal, True
        st.font.name, st.font.size, st.font.bold = FONT, Pt(size), bold
        _fonts(st.element)
        if colour:
            st.font.color.rgb = RGBColor.from_string(colour)
        if caps:
            st.font.all_caps = True
            sp = OxmlElement('w:spacing')
            sp.set(qn('w:val'), '12')  # 0.6 pt letter spacing
            rpr = st.element.get_or_add_rPr()
            anchor = next((rpr.find(qn('w:' + t)) for t in ('w', 'kern', 'position', 'sz', 'szCs')
                           if rpr.find(qn('w:' + t)) is not None), None)
            anchor.addprevious(sp) if anchor is not None else rpr.append(sp)  # schema order: spacing before sz
        pf = st.paragraph_format
        pf.space_before, pf.space_after, pf.line_spacing, pf.keep_with_next = Pt(before), Pt(after), LINE, keep
        ppr = st.element.get_or_add_pPr()
        if name == 'CV Section':
            bdr, bottom = OxmlElement('w:pBdr'), OxmlElement('w:bottom')
            for k, v in (('val', 'single'), ('sz', str(BORDER_SZ)), ('space', str(BORDER_SPACE)), ('color', RULE)):
                bottom.set(qn('w:' + k), v)
            bdr.append(bottom)
            ppr.append(bdr)
            lvl = OxmlElement('w:outlineLvl')
            lvl.set(qn('w:val'), '0')  # a real heading for navigation and screen readers
            ppr.append(lvl)
        if name == 'CV Role':
            tabs, tab = OxmlElement('w:tabs'), OxmlElement('w:tab')
            tab.set(qn('w:val'), 'right')
            tab.set(qn('w:pos'), str(PAGE_W - 2 * MARGIN_X))
            tabs.append(tab)
            ppr.append(tabs)
        if name == 'CV Bullet':
            num = OxmlElement('w:numPr')
            nid = OxmlElement('w:numId')
            nid.set(qn('w:val'), str(NUM_ID))
            num.append(nid)
            ppr.insert(0, num)
            pf.left_indent, pf.first_line_indent = Twips(BULLET_LEFT), Twips(-BULLET_HANG)
    # pPr children must follow the schema order Word expects.
    order = ['pStyle', 'keepNext', 'keepLines', 'pageBreakBefore', 'widowControl', 'numPr', 'pBdr', 'shd', 'tabs',
             'spacing', 'ind', 'jc', 'outlineLvl']
    for name in STYLES:
        ppr = d.styles[name].element.pPr
        kids = sorted(list(ppr), key=lambda e: order.index(e.tag.split('}')[1]) if e.tag.split('}')[1] in order else 99)
        for k in kids:
            ppr.remove(k)
            ppr.append(k)


def new_document():
    d = docx.Document()
    s = d.sections[0]
    s.page_width, s.page_height = Twips(PAGE_W), Twips(PAGE_H)
    s.left_margin = s.right_margin = Twips(MARGIN_X)
    s.top_margin = s.bottom_margin = Twips(MARGIN_Y)
    s.header_distance = s.footer_distance = Twips(MARGIN_Y // 2)
    _bullets(d)
    _styles(d)
    neutral_properties(d)
    return d


def build(head, sections, path):
    """Write the CV as a .docx at path (a path or a stream)."""
    d = new_document()
    for style, payload in blocks(head, sections):
        brk = style.endswith(BREAK)
        style = style.removesuffix(BREAK)
        p = d.add_paragraph(style=style)
        if brk:  # a whole section starts page 2, so page 2 is never a near-empty tail
            p.paragraph_format.page_break_before = True
        if style == 'CV Role':
            title, company, date = payload
            p.add_run(title)
            if company:
                p.add_run(SEP + company).bold = False
            if date:
                r = p.add_run('\t' + date)
                r.bold, r.font.size = False, Pt(DATE_SIZE)
                r.font.color.rgb = RGBColor.from_string(MUTED)
            continue
        for text, bold in payload:
            p.add_run(text).bold = True if bold else None
    d.save(path)


def md_to_docx(md, path):
    head, sections = parse(md)
    build(head, sections, path)


# ---------------------------------------------------------------- fit estimate (no Word needed)

_FONT_FILES = {False: Path(r'C:\Windows\Fonts\arial.ttf'), True: Path(r'C:\Windows\Fonts\arialbd.ttf')}
_fonts_cache = {}


def width(text, size, bold=False):
    if bold not in _fonts_cache:
        from PIL import ImageFont
        _fonts_cache[bold] = ImageFont.truetype(str(_FONT_FILES[bold]), 200)
    return _fonts_cache[bold].getlength(text) * size / 200


def wrap(pieces, avail):
    """Lines needed for [(text, size, bold)] pieces in avail points, wrapping at spaces like Word."""
    words = []  # (width, space width before it)
    glue = 0.0
    for text, size, bold in pieces:
        for i, tok in enumerate(re.split(r'( +)', text)):
            if not tok:
                continue
            if tok.startswith(' '):
                glue += width(tok, size, bold)
            elif words and not glue and i == 0:
                words[-1] = (words[-1][0] + width(tok, size, bold), words[-1][1])  # a run boundary inside a word
            else:
                words.append((width(tok, size, bold), glue))
                glue = 0.0
    lines, cur = 1, 0.0
    for w, space in words:
        if cur and cur + space + w > avail:
            lines, cur = lines + 1, w
        else:
            cur += (space if cur else 0) + w
    return lines


def metrics(style, payload):
    """(space before, line count, line height, space after, keep with next) for one paragraph, in points."""
    size, bold, _, before, after, keep, caps = STYLES[style]
    avail = TEXT_W - (BULLET_LEFT / 20 if style == 'CV Bullet' else 0)
    if style == 'CV Role':
        title, company, date = payload
        pieces = [(title, size, True)] + ([(SEP + company, size, False)] if company else [])
        n = wrap(pieces, avail)
        if date:  # the right-aligned dates need their own room on the last line, plus a gap
            total = sum(width(t, s, b) for t, s, b in pieces)
            used = total % avail if n > 1 else total
            if used + 12 + width(date, DATE_SIZE) > avail:
                n += 1
    else:
        text_bold = [(t.upper() if caps else t, size, bold or b) for t, b in payload]
        n = wrap(text_bold, avail)
        if caps:
            n = wrap([(t, s * 1.04, b) for t, s, b in text_bold], avail)  # letter spacing
    extra = (BORDER_SZ / 8 + BORDER_SPACE) if style == 'CV Section' else 0
    return before, n, size * ARIAL_LINE * LINE, after + extra, keep


def paginate(plan, page=TEXT_H * SAFETY):
    """Lines per page for a paragraph plan, following Word's rules closely enough: keep-with-next chains move
    together, widow and orphan control keep at least two lines of a split paragraph together."""
    items = [metrics(s.removesuffix(BREAK), p) for s, p in plan]
    breaks = {k for k, (s, _) in enumerate(plan) if s.endswith(BREAK)}
    placed = []  # (paragraph index, page, lines, bottom of its text in points from the page top)
    page_no, start, y, i = 0, 0, 0.0, 0
    while i < len(items):
        before, n, lh, after, keep = items[i]
        if i in breaks and i != start:
            page_no, start, y = page_no + 1, i, 0.0
            continue
        top = before if y else 0
        if y + top + n * lh <= page or i == start:  # fits, or is alone at the top of a page anyway
            placed.append((i, page_no, n, y + top + n * lh))
            y += top + n * lh + after
            i += 1
            continue
        room = int((page - y - top) // lh)
        if not keep and n >= 4 and room >= 2 and n - room >= 2:  # split, two lines at least on each side
            placed += [(i, page_no, room, y + top + room * lh), (i, page_no + 1, n - room, (n - room) * lh)]
            page_no, start, y, i = page_no + 1, i, (n - room) * lh + after, i + 1
            continue
        j = i  # move this paragraph and the keep-with-next paragraphs just above it
        while j - 1 > start and items[j - 1][4]:
            j -= 1
        placed = [x for x in placed if x[0] < j]
        page_no, start, y, i = page_no + 1, j, 0.0, j
    pages, heights = [0] * (page_no + 1), [0.0] * (page_no + 1)
    for _, pg, n, bottom in placed:
        pages[pg] += n
        heights[pg] = max(heights[pg], bottom)
    return pages, heights


def page_fill(head, sections):
    """(pages, lines on the last page, fraction of the last page used) by the estimate."""
    pages, heights = paginate(blocks(head, sections))
    return len(pages), pages[-1], heights[-1] / TEXT_H


def first_page_fill(head, sections):
    """Fraction of page 1 holding text, by the estimate (a gap at its foot shows as a lower number)."""
    return paginate(blocks(head, sections))[1][0] / TEXT_H
