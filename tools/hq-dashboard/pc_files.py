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
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile
import threading

HERE=Path(__file__).resolve().parent
ROOT=hq_settings.data_root()
MIME='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MD_MIME='text/markdown; charset=utf-8'
PREFIX={kind: SETTINGS['document_prefix']+suffix for kind,suffix in (('cv','CV_'),('cl','CL_'))}
DOC_LIMIT,REPORT_LIMIT=5*1024*1024,1024*1024
_converter=None
_store=None
_export_lock=threading.RLock()


def document_store(path):
    global _store
    if _store is None:
        spec=importlib.util.spec_from_file_location('hq_document_store',HERE.parent/'tracker'/'store.py')
        _store=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_store)
    return _store.Store(path)


def document_records(store_path):
    if store_path is None or not Path(store_path).is_file():return []
    storage=document_store(store_path)
    try:return storage.documents()
    finally:storage.close()


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


def documents(work=ROOT/'work',store_path=None):
    """CVs and cover letters. `pair` is the shared suffix, so CV_X.docx and CL_X.docx link."""
    work=Path(work)
    out=[]
    records={r['file_name']:r for r in document_records(store_path)}
    for kind,prefix in PREFIX.items():
        for path in sorted(work.glob(prefix+'*.docx')):
            if not path.name.startswith(prefix) or not _allowed(path,work,DOC_LIMIT):continue
            pair=path.stem.removeprefix(prefix)
            title=pair.replace('_',' ')
            record=records.get(path.name,{})
            out.append({'id':_key(path.name),'kind':kind,'pair':pair,'name':path.name,'title':title,'company':title.split(' ')[0],
                        'reviewed':False,'updated':str(path.stat().st_mtime_ns),'bytes':path.stat().st_size,'mime':MIME,
                        'made_by':record.get('made_by','outside'),'job_id':record.get('job_id'),
                        'created':record.get('created') or datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),
                        'clean':is_clean(path)})
    return out


def existing(work=ROOT/'work',store_path=None):
    # /api/cvs keeps its old meaning: CVs only.
    return [d for d in documents(work,store_path) if d['kind']=='cv']


def existing_file(key,work=ROOT/'work',kinds=('cv',),store_path=None):
    item=next((item for item in documents(work,store_path) if item['id']==key and item['kind'] in kinds),None)
    if not item:raise ValueError('Unknown document.')
    return Path(work)/item['name']


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
def export_draft(state,posting_id,kind,record,copy_dir=None,work=None,store_path=None,blocked_terms=None):
    with _export_lock:
        return _export_draft(state,posting_id,kind,record,copy_dir,work,store_path,blocked_terms)


def _export_draft(state,posting_id,kind,record,copy_dir,work,store_path,blocked_terms):
    if not re.fullmatch(r'[0-9a-f-]{36}',posting_id) or kind not in ('cv','letter'):
        raise ValueError('Invalid document request.')
    field='cv_markdown' if kind=='cv' else 'cover_letter_markdown'
    content=record.get('fit',{}).get(field)
    if not content:raise ValueError('Create this draft first.')
    problems=converter().readability_problems(content,SETTINGS['cv_blocked_terms'] if blocked_terms is None else blocked_terms)
    if problems:raise ValueError(' '.join(problems))
    convert=converter().md_to_docx if kind=='cv' else converter().letter_to_docx
    fingerprint='draft:'+hashlib.sha256(content.encode()).hexdigest()
    directory=Path(work) if work is not None else ROOT/'work'
    directory.mkdir(parents=True,exist_ok=True)
    def words(value):
        return '_'.join(w[:1].upper()+w[1:] for w in re.findall(r'[^\W_]+',str(value or '').replace("'",'').replace('\u2019','')))
    company,role=words(record.get('company')) or 'Posting',words(record.get('role'))
    name=f"{SETTINGS['document_prefix']}{'CV' if kind=='cv' else 'CL'}_{company}"+(('_'+role) if role else '')+'.docx'
    storage=document_store(store_path) if store_path is not None and Path(store_path).is_file() else None
    # Excel mode has no tracker records. Keep only an export cache in server state
    # so a repeated download still reuses its unchanged file after a restart.
    cache=None
    if storage is None:
        key=hashlib.sha256((str(directory.resolve())+'\n'+posting_id+'\n'+kind+'\n'+fingerprint).encode()).hexdigest()
        cache=Path(state)/'document-exports'/(key+'.json')
    path=None
    try:
        # Serialize record lookup and file creation for concurrent exports of one draft.
        if storage:storage.db.execute('BEGIN IMMEDIATE')
        for row in storage.documents() if storage else []:
            previous=directory/row['file_name']
            if (row['made_by']=='site' and row['posting_id']==posting_id and row['kind']==kind
                    and fingerprint in row['checks'] and _allowed(previous,directory,DOC_LIMIT)
                    and hashlib.sha256(previous.read_bytes()).hexdigest()==row['sha256']):
                path=previous
                break
        if cache and cache.is_file():
            try:
                saved=json.loads(cache.read_text(encoding='utf-8'))
                previous=directory/saved['file_name']
                if _allowed(previous,directory,DOC_LIMIT) and hashlib.sha256(previous.read_bytes()).hexdigest()==saved['sha256']:
                    path=previous
            except (OSError,ValueError,KeyError,TypeError):pass
        if path is None:
            # Convert before claiming a listed name; final names are created exclusively.
            with tempfile.TemporaryFile() as temp:
                convert(content,temp)
                temp.seek(0)
                payload=temp.read()
            path=_write_unique(directory,name,payload)
            if storage:
                storage.add_document(kind,path.name,hashlib.sha256(path.read_bytes()).hexdigest(),'site',
                                     datetime.now(timezone.utc).isoformat(),job_id=record.get('jobId') or None,
                                     posting_id=posting_id,evidence_ids=record.get('fit',{}).get('sources',[]),
                                     checks=['markers','readability',fingerprint])
            if cache:
                cache.parent.mkdir(parents=True,exist_ok=True)
                cache.write_text(json.dumps({'file_name':path.name,'sha256':hashlib.sha256(payload).hexdigest()}),encoding='utf-8')
        if storage:storage.db.commit()
    except Exception:
        if storage:storage.db.rollback()
        raise
    finally:
        if storage:storage.close()
    target=copy_dir if copy_dir is not None else (Path(SETTINGS['copy_folder']).expanduser() if SETTINGS['copy_folder'] else None)
    if target is not None and copy_dir is None:
        target = ROOT / target
    if target is not None and target.parent.is_dir():
        dest=target/SETTINGS['copy_subfolder']
        dest.mkdir(parents=True,exist_ok=True)
        _write_unique(dest,path.name,path.read_bytes(),reuse=True)
    return path


def _write_unique(directory,name,payload,reuse=False):
    stem=Path(name).stem
    index=1
    while True:
        path=directory/(name if index==1 else f'{stem}_{index}.docx')
        try:stream=path.open('xb')
        except FileExistsError:
            if reuse and _allowed(path,directory,DOC_LIMIT) and path.read_bytes()==payload:return path
            index+=1
            continue
        try:
            with stream:stream.write(payload)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path
