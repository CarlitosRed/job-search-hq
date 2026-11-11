"""Trim a tailored CV by DELETING lines only, then build the .docx in the shared CV style (cv_layout.py).
 Updated by Claude · CV Layout Builder (Opus), 2026-11-05: new scannable layout, design
caps on bullets and summary, one page with 7% slack, posting-related bullets kept first, marker lines dropped.

Nothing is reworded: every kept line or summary sentence is copied verbatim from the source CV, so no new claim
can appear. The check below fails loudly if that ever stops being true. Page fit is an estimate from real Arial
metrics (no Word needed); always glance at the file once on the phone.

Order of deletions (each only while still needed, except the design caps which always apply). Inside a role or
project the bullet least related to the posting title (words in the output file name) goes first:
  1. design caps: 3 bullets for the two newest roles, 2 for older roles and for each project; summary at most
     3 sentences and about 70 words (later sentences go first).
  2. to fit one page: projects down to 1 bullet; roles oldest first down to 2/2/1/1/1; Projects down to its
     single strongest entry; summary down to 2 sentences; then the two newest roles down to 1 bullet. Skills,
     Education, Languages and role title lines are never touched.
  3. last resort (not reached by typical CVs): oldest roles keep only their title; then a whole section starts
     page 2 if that leaves MIN_LAST lines there.

Usage: python -I cv_onepage.py <source.md> <out.md> <out.docx>
"""
# Resolve settings beside this module under isolated execution.
import importlib.util as _settings_util
from pathlib import Path as _SettingsPath
_settings_spec = _settings_util.spec_from_file_location('hq_owner_settings', _SettingsPath(__file__).with_name('hq_settings.py'))
hq_settings = _settings_util.module_from_spec(_settings_spec)
_settings_spec.loader.exec_module(hq_settings)
SETTINGS = hq_settings.load()

import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location('hq_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


layout = _load('cv_layout')
markers = _load('docx_export')

ROLE_CAP = [3, 3]          # newest roles; every older role and every project keeps at most OLDER_CAP
OLDER_CAP = 2
MIN_BULLETS = [2, 2, 1, 1, 1]  # newest role first; the limit for deletions made to fit one page
SUMMARY_SENTENCES, SUMMARY_WORDS = 3, 70
MIN_LAST = 12                  # a second page holds at least this many lines (a whole section moves there)


def parse(md):
    return layout.parse(md)


def entries(items):
    """Group a section's lines into [title, bullet, bullet...] entries; plain lines stay alone."""
    out = []
    for line in items:
        if line.startswith('**') or not out:
            out.append([line])
        else:
            out[-1].append(line)
    return out


def sentences(text):
    return re.findall(r'.+?(?:[.!?](?=\s|$)|$)', text)


def drop_markers(md):
    """Marker lines (Signed :, Sentiment:, DRAFT banners, [HQ] tags...) are deleted whole. A known tag inside a real
    line is refused rather than cut out, so a content line is never altered."""
    out = []
    for line in md.replace('\r\n', '\n').split('\n'):
        if markers.marker_line(line):
            continue
        if markers.has_known_tag(line):
            raise SystemExit('A tag like [DRAFT] sits inside a content line; fix the source CV first: ' + line[:80])
        out.append(line)
    return '\n'.join(out)


def fill(head, sections):
    return layout.page_fill(head, sections)


STOP = {'and', 'the', 'for', 'with', 'of', 'to', 'in', 'a', 'an', 'junior', 'senior', 'latam', 'dubai', 'remote'}


def focus_words(name):
    """Posting words from an output file name such as CV_Example_Revenue_Operations_Analyst.docx."""
    stem = re.sub('^' + re.escape(SETTINGS['document_prefix'] + 'CV_'), '', Path(name).stem) if name else ''
    return {w.lower() for w in re.split(r'[_\W]+', stem) if len(w) >= 2 and w.lower() not in STOP}


def relevance(text, focus):
    """How many of the line's words match a posting word (same first 5 letters, so operation/operations meet but
    Workday does not count for Workflow)."""
    score = 0
    for w in re.findall(r'[A-Za-z0-9]+', text.lower()):
        if any(w == f or (len(w) >= 5 and len(f) >= 5 and w[:5] == f[:5]) for f in focus):
            score += 1
    return score


# Summary support (best effort). A kept Summary sentence must still be shown by the page: its distinctive tokens
# (words of 4+ letters cut to 6 letters, so trained/training and replaced/replace meet, plus every number and $ amount,
# minus common CV words and 4-digit years) must be covered by the kept support lines (bullets, role and project
# titles, Education, Languages; never the Summary itself, the head or Skills): every number and $ amount, and at
# least SUPPORT_SHARE of the other tokens. A tenure sentence (one that says "years") is proven by the roles and
# their dates, so it is exempt. This is token overlap, not understanding: it catches a claim whose only bullet was
# cut, and a reviewer still reads the result.
SUPPORT_SHARE = 0.7
AI_WORDS = {'ai', 'agent', 'agents', 'workflow', 'automation', 'enablement'}  # posting words that make it AI-titled
AI_CUT_ORDER = SETTINGS['cv_cut_order']
HIGH = re.compile(r'\b(?:agents?|governance|review|tests?|claude|codex)\b', re.I)
KNOWLEDGE = re.compile(r'knowledge base', re.I)
GENERIC = set('''with and the for from into that this than more about over under after before then also plus total
years year work works working across while what when were which their they them have been some each other only
analyst analysts operations consulting consultant technical support professional business data process processes
team teams client clients customer customers through using used based level partner partners role roles experience
engineering engineer degree progress native english spanish directly now'''.split())


def claim_tokens(text):
    out = set()
    for t in re.findall(r"\$?[A-Za-z0-9][A-Za-z0-9.+/$-]*", text.lower()):
        t = t.strip('.-/')
        if not t or t in GENERIC or re.fullmatch(r'\d{4}', t):
            continue
        if len(t) >= 4 or re.search(r'\d', t):
            out.add(t[:6])
    return out


def support_lines(sections):
    return [l for s in sections if s['name'] not in ('Summary', 'Skills') for l in s['items']]


def supported(sentence, lines, have=None):
    if re.search(r'\byears?\b', sentence, re.I):
        return True
    need = claim_tokens(sentence)
    if not need:
        return True
    have = have if have is not None else set().union(set(), *(claim_tokens(l) for l in lines))
    numbers = {t for t in need if re.search(r'\d', t)}
    return numbers <= have and len(need & have) >= SUPPORT_SHARE * len(need)


def unsupported_summary(sections):
    """Kept Summary sentences that no kept line supports (see the heuristic above)."""
    summary = next((s for s in sections if s['name'] == 'Summary'), None)
    if not summary:
        return []
    lines = support_lines(sections)
    have = set().union(set(), *(claim_tokens(l) for l in lines))
    return [x.strip() for item in summary['items'] for x in sentences(item) if not supported(x, lines, have)]


def trim(md, focus=()):
    head, sections = parse(drop_markers(md))
    by = {s['name']: s for s in sections}
    exp = by.get('Experience')
    roles = entries(exp['items']) if exp else []
    proj = entries(by['Projects']['items']) if 'Projects' in by else []
    summary = by.get('Summary')
    parts = sentences(summary['items'][0]) if summary and summary['items'] else []
    focus = set(focus)
    ai = bool(focus & AI_WORDS)

    def value(entry, line):
        # Posting relevance; for an AI-titled posting, project bullets about agents, governance, review or tests
        # are the strongest evidence, and the knowledge-base bullet is the first to go.
        v = relevance(line, focus)
        if ai and any(entry is e for e in proj) and HIGH.search(line):
            v += 10
        if ai and KNOWLEDGE.search(line):
            v -= 5
        return v

    def apply():
        if exp:
            exp['items'] = [l for r in roles for l in r]
        if 'Projects' in by:
            by['Projects']['items'] = [l for r in proj for l in r]
        if summary and summary['items']:
            summary['items'][0] = ' '.join(p.strip() for p in parts)

    def state():
        apply()
        return fill(head, sections)

    def over():
        return state()[0] > 1

    def orphans():
        apply()
        return set(unsupported_summary(sections))

    def drop_weakest(entry, force=False):
        """Delete the bullet least related to the posting (on a tie the later one), but never the last support of a
        kept Summary sentence unless forced. Returns whether a bullet was deleted."""
        before = orphans()
        for k in sorted(range(1, len(entry)), key=lambda i: (value(entry, entry[i]), -i)):
            line = entry.pop(k)
            if orphans() <= before:
                return True
            entry.insert(k, line)
        if force and len(entry) > 1:
            del entry[min(range(1, len(entry)), key=lambda i: (value(entry, entry[i]), -i))]
            return True
        return False

    # 1: design caps, always: the most posting-related bullets stay, in their original order
    for idx, role in enumerate(roles):
        cap = ROLE_CAP[idx] if idx < len(ROLE_CAP) else OLDER_CAP
        while len(role) - 1 > cap:
            drop_weakest(role, force=True)
    for entry in proj:
        while len(entry) - 1 > OLDER_CAP:
            drop_weakest(entry, force=True)
    while len(parts) > SUMMARY_SENTENCES:
        parts.pop()
    while len(parts) > 2 and len(' '.join(parts).split()) > SUMMARY_WORDS:
        parts.pop()

    # 2: to fit one page (with the estimator's slack), lowest value first
    def oldest_first(floor_of, force=False):
        progress = True
        while over() and progress:
            progress = False
            for idx in range(len(roles) - 1, -1, -1):
                if len(roles[idx]) - 1 > floor_of(idx) and drop_weakest(roles[idx], force):
                    progress = True
                    if not over():
                        return

    def strongest_project(force=False):
        if not over() or len(proj) < 2:
            return
        before, old = orphans(), list(proj)
        for best in sorted(old, key=lambda e: (-sum(value(e, l) for l in e), old.index(e))):
            proj[:] = [best]
            if force or orphans() <= before:
                return
        proj[:] = old

    def bullets_and_projects(force=False):
        for entry in proj:  # projects down to their best bullet
            while over() and len(entry) > 2 and drop_weakest(entry, force):
                pass
        oldest_first(lambda idx: MIN_BULLETS[idx] if idx < len(MIN_BULLETS) else 1, force)  # roles to 2/2/1/1/1
        strongest_project(force)  # Projects keeps only its strongest entry

    def ai_order():
        # AI postings: older roles go to 1 bullet in the configured cut order, any other
        # older role first; the newest role is handled separately.
        def rank(idx):
            title = roles[idx][0].lower()
            hit = next((k for k, name in enumerate(AI_CUT_ORDER) if name in title), None)
            return (hit if hit is not None else -1, -idx)
        return sorted(range(1, len(roles)), key=rank)

    def ai_pass(force=False):
        for idx in ai_order():
            while over() and len(roles[idx]) > 2 and drop_weakest(roles[idx], force):
                pass
        for entry in proj:  # project bullets that are not high value
            while over() and len(entry) > 2 and min(value(entry, l) for l in entry[1:]) < 10 and drop_weakest(entry, force):
                pass
        strongest_project(force)

    step = ai_pass if ai else bullets_and_projects
    step()
    # A Summary sentence goes before the newest roles lose their second bullet; each cut may free bullets.
    while over() and len(parts) > 1:
        parts.pop()
        step()
    if ai:
        while roles and over() and len(roles[0]) > 2 and drop_weakest(roles[0]):
            pass
        for entry in proj:  # high-value project bullets come last
            while over() and len(entry) > 2 and drop_weakest(entry):
                pass
    oldest_first(lambda idx: 1)  # the two newest roles down to 1 bullet, oldest first
    # Never touched: Skills, Education, Languages and every role title line.

    # 3: last resort, never reached by typical CVs: forced cuts (an orphaned Summary sentence then goes), oldest
    # roles keep only their title line; then, if still two pages, a whole section of MIN_LAST+ lines starts page 2.
    bullets_and_projects(force=True)
    oldest_first(lambda idx: 1, force=True)
    oldest_first(lambda idx: 1 if idx < 2 else 0, force=True)
    for x in orphans():
        parts[:] = [p for p in parts if p.strip() != x]
    if summary and not parts:
        sections.remove(summary)
    if over():
        for sec in reversed(sections[1:]):
            sec['page_break'] = True
            pages, last, _ = state()
            if pages == 2 and last >= MIN_LAST:
                break
            del sec['page_break']
    state()
    return head, sections


def verify_summary_support(sections):
    gone = unsupported_summary(sections)
    if gone:
        raise SystemExit('A kept Summary sentence has no supporting line left, refusing to write: ' + gone[0][:80])


def render_md(head, sections):
    out = list(head) + ['']
    for s in sections:
        out += ['## ' + s['name']] + s['items'] + ['']
    return '\n'.join(out).rstrip() + '\n'


def contexts(text):
    """Yield (context, normalized line): context is the section plus the role/project title the line sits under."""
    section = title = ''
    for line in text.replace('\r\n', '\n').split('\n'):
        if not line.strip():
            continue
        if line.startswith('## '):
            section, title = line.strip(), ''
        elif line.startswith('**'):
            title = re.sub(r'\s+', ' ', line.strip())
        yield (section, title if not line.startswith('**') else ''), re.sub(r'\s+', ' ', line.strip())


def verify_subset(source, result):
    """Every kept line must be a whole source line under the same section and role; summary text must be whole
    source sentences in order. Nothing can be reworded, truncated, or moved under another role."""
    norm = lambda x: re.sub(r'\s+', ' ', x.strip())
    src_pairs = set(contexts(source))
    src_sentences = [norm(x) for l in source.replace('\r\n', '\n').split('\n') if l.strip() and not l.startswith(('#', '-', '**'))
                     for x in sentences(l)]
    pos = 0
    for pair in contexts(result):
        ctx, line = pair
        if pair in src_pairs:
            continue
        for x in (norm(y) for y in sentences(line)):
            if ctx[0] != '## Summary' or x not in src_sentences[pos:]:
                raise SystemExit('Not verbatim, refusing to write: ' + x[:80])
            pos = src_sentences.index(x, pos) + 1
    for line in result.split('\n'):
        if markers.marker_line(line) or markers.has_known_tag(line):
            raise SystemExit('A marker line would reach the CV, refusing to write: ' + line[:80])


def build_docx(head, sections, path):
    layout.build(head, sections, path)


def main():
    src, out_md, out_docx = (Path(a) for a in sys.argv[1:4])
    if src.resolve() in (out_md.resolve(), out_docx.resolve()) or out_md.resolve() == out_docx.resolve():
        raise SystemExit('Outputs must be two new files and must not overwrite the source CV.')
    source = src.read_text(encoding='utf-8')
    head, sections = trim(source, focus_words(out_docx.name))
    result = render_md(head, sections)
    verify_subset(source, result)
    verify_summary_support(sections)
    out_md.write_text(result, encoding='utf-8')
    build_docx(head, sections, out_docx)
    words = len(re.sub(r'[#*]', '', result).split())
    pages, last, used = fill(head, sections)
    where = f'{used:.0%} of a page' if pages == 1 else f'{pages} pages, {last} lines on the last ({used:.0%})'
    print(f'{src.stem}: {len(source.split())} -> {words} words, est {where}')


if __name__ == '__main__':
    main()
