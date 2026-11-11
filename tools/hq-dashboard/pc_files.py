"""Allowlisted existing CVs, cover letters, reports and draft exports.
Updated by Claude · HQ Builder (Opus), 2026-11-05: cover letters (CL_*.docx) and reports
(report-*.md) join the allowlist; a CV and its letter share a `pair` key; letters export as plain paragraphs.
"""
# Resolve settings beside this module under isolated execution.
import importlib.util as _settings_util
from pathlib import Path as _SettingsPath
_settings_spec = _settings_util.spec_from_file_location('hq_owner_settings', _SettingsPath(__file__).with_name('hq_settings.py'))
hq_settings = _settings_util.module_from_spec(_settings_spec)
_settings_spec.loader.exec_module(hq_settings)
SETTINGS = hq_settings.load()

import hashlib
import importlib.util
import io
from pathlib import Path
import re

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
MIME='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MD_MIME='text/markdown; charset=utf-8'
PREFIX={kind: SETTINGS['document_prefix']+suffix for kind,suffix in (('cv','CV_'),('cl','CL_'))}
DOC_LIMIT,REPORT_LIMIT=5*1024*1024,1024*1024
_converter=None


def converter():
    global _converter
    if _converter is None:
        spec=importlib.util.spec_from_file_location('hq_docx_converter',HERE/'docx_export.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _converter=module
    return _converter


def _allowed(path,work,limit):
    # Same rules for every listed file: a regular file directly inside work/, never a link, never too large.
    try:
        if path.is_symlink() or not path.is_file():return False
        if path.resolve().parent!=work.resolve():return False
        return path.stat().st_size<=limit
    except OSError:
        return False


def _key(name):
    return hashlib.sha256(name.encode()).hexdigest()[:24]


_clean_cache={}


def is_clean(path):
    """False when a .docx holds an exact marker line (body, header or footer). Read-only; cached by mtime and size.
    None when the file cannot be read as Word. Files on disk are never changed."""
    st=path.stat()
    key=(path.name,st.st_mtime_ns,st.st_size)
    if key not in _clean_cache:
        try:
            import docx
            d=docx.Document(path)
            texts=[p.text for p in d.paragraphs]+[c.text for t in d.tables for r in t.rows for c in r.cells]
            # Any header or footer text counts as notes: the owner wants no header block in a CV or letter file.
            edge=[p.text for s in d.sections for part in (s.header,s.footer) for p in part.paragraphs]
            edge+=[c.text for s in d.sections for part in (s.header,s.footer) for t in part.tables for r in t.rows for c in r.cells]
            mark,tag=converter().marker_line,converter().has_known_tag
            _clean_cache[key]=not any(x.strip() for x in edge) and not any(mark(line) or tag(line) for t in texts for line in t.split('\n'))
        except Exception:
            _clean_cache[key]=None
        if len(_clean_cache)>500:_clean_cache.clear()
    return _clean_cache[key]


def documents(work=ROOT/'work'):
    """CVs and cover letters. `pair` is the shared suffix, so CV_X.docx and CL_X.docx link."""
    out=[]
    for kind,prefix in PREFIX.items():
        for path in sorted(work.glob(prefix+'*.docx')):
            if not path.name.startswith(prefix) or not _allowed(path,work,DOC_LIMIT):continue
            pair=path.stem.removeprefix(prefix)
            title=pair.replace('_',' ')
            out.append({'id':_key(path.name),'kind':kind,'pair':pair,'name':path.name,'title':title,'company':title.split(' ')[0],
                        'reviewed':False,'updated':str(path.stat().st_mtime_ns),'bytes':path.stat().st_size,'mime':MIME,
                        'clean':is_clean(path)})
    return out


def existing(work=ROOT/'work'):
    # /api/cvs keeps its old meaning: CVs only.
    return [d for d in documents(work) if d['kind']=='cv']


def existing_file(key,work=ROOT/'work',kinds=('cv',)):
    item=next((item for item in documents(work) if item['id']==key and item['kind'] in kinds),None)
    if not item:raise ValueError('Unknown document.')
    return work/item['name']


def reports(work=ROOT/'work'):
    out=[]
    for path in sorted(work.glob('report-*.md'),reverse=True):
        if not path.name.startswith('report-') or not _allowed(path,work,REPORT_LIMIT):continue
        title=re.sub(r'(\d{4}) (\d{2}) (\d{2})',r'\1-\2-\3',path.stem.removeprefix('report-').replace('-',' ').replace('_',' ')).strip()
        out.append({'id':_key(path.name),'kind':'report','name':path.name,'title':title[:1].upper()+title[1:],
                    'updated':str(path.stat().st_mtime_ns),'bytes':path.stat().st_size})
    return out


def report_file(key,work=ROOT/'work'):
    item=next((item for item in reports(work) if item['id']==key),None)
    if not item:raise ValueError('Unknown document.')
    return work/item['name'],item


def report_view(key,work=ROOT/'work'):
    path,item=report_file(key,work)
    return {**item,'blocks':converter().report_blocks(path.read_text(encoding='utf-8',errors='replace'))}


def report_docx(key,work=ROOT/'work'):
    path,item=report_file(key,work)
    stream=io.BytesIO()
    converter().report_to_docx(path.read_text(encoding='utf-8',errors='replace'),stream)
    return path.stem+'.docx',stream.getvalue()


# Callers use the explicit copy_dir option.
def export_draft(state,posting_id,kind,record,copy_dir=None):
    if not re.fullmatch(r'[0-9a-f-]{36}',posting_id) or kind not in ('cv','letter'):
        raise ValueError('Invalid document request.')
    field='cv_markdown' if kind=='cv' else 'cover_letter_markdown'
    content=record.get('fit',{}).get(field)
    if not content:raise ValueError('Create this draft first.')
    convert=converter().md_to_docx if kind=='cv' else converter().letter_to_docx
    digest=hashlib.sha256((posting_id+'\n'+content).encode()).hexdigest()[:12]
    company='_'.join(re.findall(r'[A-Za-z0-9]+',str(record.get('company') or '')))[:40] or 'Posting'
    # No "DRAFT" in the file name: the file may be sent as is once the owner has checked it.
    name=f"{SETTINGS['document_prefix']}{'CV' if kind=='cv' else 'Cover_Letter'}_{company}_{digest}.docx"
    directory=state/'generated-documents'
    directory.mkdir(exist_ok=True)
    path=directory/name
    if not path.exists():
        temp=directory/(name+'.new.docx')
        convert(content,temp)
        temp.replace(path)
    # Content-addressed copies never overwrite a file edited in the synced folder.
    target=copy_dir if copy_dir is not None else (Path(SETTINGS['copy_folder']).expanduser() if SETTINGS['copy_folder'] else None)
    if target is not None and target.parent.is_dir():
        dest=target/SETTINGS['copy_subfolder']
        dest.mkdir(parents=True,exist_ok=True)
        copy=dest/name
        if not copy.exists():copy.write_bytes(path.read_bytes())
    return path
