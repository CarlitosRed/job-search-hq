"""One fictional ChatGPT CLI connectivity check.
optional redacted readiness result for scheduled checks.
Uses a small amount of the existing ChatGPT plan; never sends real career data.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
from datetime import datetime, timezone

HERE=Path(__file__).resolve().parent
def check():
    # Match the startup launcher's CLI discovery, including Task Scheduler's shorter PATH.
    if not shutil.which('codex') and os.name == 'nt':
        install=Path(os.environ.get('LOCALAPPDATA',''))/'Programs/OpenAI/Codex/bin'
        if (install/'codex.exe').is_file():
            os.environ['PATH']=str(install)+os.pathsep+os.environ.get('PATH','')
    spec=importlib.util.spec_from_file_location('hq_worker_smoke',HERE/'ai_tasks.py')
    worker=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    with tempfile.TemporaryDirectory(prefix='hq-cli-smoke-') as directory:
        prompt='''Connectivity check only. Do not call tools or read files. No career claims.
Return the required analysis JSON: is_job_posting false, readable true,
company and role empty, link and posting_text empty, score 1, verdict SKIP,
summary Fictional connectivity check, location_ok unclear, matches gaps flags empty arrays.'''
        result=worker.run_codex(prompt,'analyze',Path(directory),[])
        if result.get('is_job_posting') is not False or not result.get('summary'):
            raise RuntimeError('Unexpected fictional connectivity result.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result',type=Path,help='Optional redacted JSON result file (outside Git).')
    args=parser.parse_args()
    outcome={'checkedAt':datetime.now(timezone.utc).isoformat(),'passed':False,'fictionalOnly':True}
    try:
        check()
        outcome['passed']=True
        outcome['message']='ChatGPT CLI connectivity passed; no real posting, evidence or document was sent.'
    except Exception:
        # CLI/provider errors can include local paths or input. Keep them out of readiness reports.
        outcome['message']='Connectivity failed. Check the task identity, CLI login, plan usage and permissions locally.'
    if args.result:
        args.result.parent.mkdir(parents=True,exist_ok=True)
        temporary=args.result.with_name(args.result.name+'.new')
        temporary.write_text(json.dumps(outcome,indent=2)+'\n',encoding='utf-8')
        temporary.replace(args.result)
    print(outcome['message'])
    return 0 if outcome['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
