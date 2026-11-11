"""Background/startup launcher without PowerShell policy changes."""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys

state=Path(os.environ['LOCALAPPDATA'])/'JobSearchHQ'
settings=json.loads((state/'server-settings.json').read_text(encoding='utf-8-sig'))
if 'JOB_SEARCH_HQ_DATA' not in os.environ and settings.get('data_root'):
    os.environ['JOB_SEARCH_HQ_DATA'] = settings['data_root']
_settings_spec = importlib.util.spec_from_file_location('startup_owner_settings', Path(__file__).with_name('hq_settings.py'))
hq_settings = importlib.util.module_from_spec(_settings_spec)
_settings_spec.loader.exec_module(hq_settings)
os.chdir(hq_settings.app_root())
codex=Path(os.environ['LOCALAPPDATA'])/'Programs/OpenAI/Codex/bin'
os.environ['PATH']=str(codex)+os.pathsep+os.environ.get('PATH','')
log=state/'server.log'
if log.exists() and log.stat().st_size>2*1024*1024:
    log.replace(state/'server.log.previous')
with log.open('a',encoding='utf-8',buffering=1) as stream:
    sys.stdout=sys.stderr=stream
    sys.argv=[str(Path(__file__).with_name('server.py')),'--origin',settings['origin'],'--state',str(state)]
    runpy.run_path(sys.argv[0],run_name='__main__')
