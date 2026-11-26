"""Read exported job-alert messages from a local folder or mailbox."""
from email import policy
from email.parser import BytesParser
from html import unescape
import mailbox
from pathlib import Path
import re

LABEL, PACE, CAP, TIMEOUT = 'LinkedIn job alerts', 0, 100, 5
LINK = re.compile(r'https://(?:www\.)?linkedin\.com/jobs/view/(?:([a-z0-9-]+)-)?(\d+)', re.I)
DETAIL = re.compile(r'(?im)^\s*(?:Job title|Title)\s*:\s*([^\r\n]+)|^\s*Company\s*:\s*([^\r\n]+)')


def enabled(settings):
    return bool(settings.get('path'))


def messages(path):
    if path.is_dir():
        for file in sorted(path.glob('*.eml'), reverse=True)[:CAP]:
            if file.stat().st_size <= 1024 * 1024:
                yield BytesParser(policy=policy.default).parsebytes(file.read_bytes())
    elif path.suffix.lower() == '.eml':
        if path.stat().st_size <= 1024 * 1024:
            yield BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    elif path.is_file():
        box = mailbox.mbox(path, factory=BytesParser(policy=policy.default).parse, create=False)
        try:
            for key in list(box.keys())[-CAP:]:
                yield box[key]
        finally:
            box.close()
    else:
        raise OSError('The alert export is unavailable.')


def read(fetch, queries, settings):
    hits = {}
    for message in messages(Path(settings['path'])):
        subject = str(message.get('Subject', ''))
        if 'job alert' not in subject.lower():
            continue
        for part in message.walk():
            if part.get_content_type() in ('text/plain', 'text/html') and not part.is_attachment():
                content = unescape(part.get_content()[:1024 * 1024])
                matches = list(LINK.finditer(content))[:CAP]
                for index, match in enumerate(matches):
                    url = 'https://www.linkedin.com/jobs/view/' + match[2]
                    before = matches[index - 1].end() if index else 0
                    context = content[max(before, match.start() - 500):match.start()]
                    context = re.sub(r'<[^>]+>', '\n', context)
                    details = DETAIL.findall(context)
                    title = next((value for value, _ in reversed(details) if value), '')
                    company = next((value for _, value in reversed(details) if value), '')
                    title = title or (match[1] or '').replace('-', ' ')
                    if url not in hits or company and title:
                        hits[url] = dict(url=url, title=title.strip(), company=company.strip())
    return list(hits.values())[:CAP]
