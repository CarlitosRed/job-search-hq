"""One shared way to ask the AI for a checked JSON answer: complete(prompt, schema).

The default runner is the Codex CLI with ChatGPT auth, a read-only sandbox, no shell,
plugins, hooks or web, and no API credentials. Card 04c adds the Claude Code CLI as a second
engine, on the owner's own sign-in with no tools, MCP servers, skills or saved session.
Tests pass their own runner.
"""
# Card 04a: qualification judge.
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


class AiUnavailable(Exception):
    """The AI could not be reached or did not finish."""


class AiBadAnswer(Exception):
    """The AI answered, but not in the agreed shape."""


def _is(value, name):
    if name == 'object':
        return isinstance(value, dict)
    if name == 'array':
        return isinstance(value, list)
    if name == 'string':
        return isinstance(value, str)
    if name == 'boolean':
        return isinstance(value, bool)
    if name == 'null':
        return value is None
    if name == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if name == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
            and value not in (float('inf'), float('-inf'))
    raise AiBadAnswer(f'The schema uses an unknown type: {name}.')


def validate(answer, schema, where='answer'):
    """Return the answer when it fits the schema; raise AiBadAnswer otherwise."""
    kinds = schema.get('type')
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if not any(_is(answer, kind) for kind in kinds):
            raise AiBadAnswer(f'{where} must be {" or ".join(kinds)}.')
    if 'enum' in schema and answer not in schema['enum']:
        raise AiBadAnswer(f'{where} is not one of the allowed values.')
    if isinstance(answer, dict):
        properties = schema.get('properties', {})
        for key in schema.get('required', []):
            if key not in answer:
                raise AiBadAnswer(f'{where} is missing {key}.')
        if schema.get('additionalProperties') is False:
            for key in answer:
                if key not in properties:
                    raise AiBadAnswer(f'{where} has an unexpected field.')
        for key, value in answer.items():
            if key in properties:
                validate(value, properties[key], f'{where}.{key}')
    if isinstance(answer, list) and isinstance(schema.get('items'), dict):
        for index, item in enumerate(answer):
            validate(item, schema['items'], f'{where}[{index}]')
    return answer


def run_cli(prompt, schema, folder=None, images=(), timeout=600):
    """Run the Codex CLI once and return its JSON answer; a temporary folder is used when none is given."""
    if folder is None:
        with tempfile.TemporaryDirectory(prefix='hq-ai-') as temporary:
            return run_cli(prompt, schema, temporary, images, timeout)
    executable = shutil.which('codex')
    if not executable:
        raise RuntimeError('Codex CLI is not installed.')
    folder = Path(folder)
    output = folder / 'answer.json'
    schema_path = folder / 'schema.json'
    schema_path.write_text(json.dumps(schema), encoding='utf-8')
    args = [executable, 'exec', '--ignore-user-config', '--sandbox', 'read-only', '--disable', 'shell_tool',
            '--disable', 'plugins', '--disable', 'hooks', '--ephemeral', '--skip-git-repo-check', '-C', str(folder),
            '-c', 'approval_policy="never"', '-c', 'web_search="disabled"',
            '--output-schema', str(schema_path), '--output-last-message', str(output), '--color', 'never']
    for path in images:
        args.extend(['--image', str(path)])
    args.append('-')
    # Never inherit API-key routing from a parent terminal; auth remains CLI ChatGPT auth.
    env = {k: v for k, v in os.environ.items() if k not in ('OPENAI_API_KEY', 'CODEX_API_KEY')}
    completed = subprocess.run(args, input=prompt, text=True, encoding='utf-8', env=env, capture_output=True,
                               timeout=timeout,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if completed.returncode or not output.exists():
        raise RuntimeError('Codex could not finish. Check its login, usage limit or local permissions. '
                           'No permission bypass was used.')
    if output.stat().st_size > 262144:
        raise ValueError('The generated answer is too large.')
    return json.loads(output.read_text(encoding='utf-8'))


# Card 04c: Claude as a judge option.
ENGINES = {'codex': 'codex-cli', 'claude': 'claude-cli'}


def model_label(engine):
    """The model name saved with each answer, so a switch of engine is never mistaken for the same answer."""
    if engine not in ENGINES:
        raise ValueError('The AI must be codex or claude.')
    return ENGINES[engine]


def run_claude_cli(prompt, schema, timeout=600):
    """Run the Claude Code CLI once, with no tools, and return its structured JSON answer."""
    executable = shutil.which('claude')
    if not executable:
        raise RuntimeError('Claude CLI is not installed.')
    text = json.dumps(schema)
    # A .cmd shim passes its arguments through cmd.exe, where these characters would be read as commands.
    if executable.lower().endswith(('.cmd', '.bat')) and any(c in text for c in '&|<>^%!'):
        raise ValueError('The answer format cannot be passed to the Claude CLI safely.')
    # Safe mode turns off CLAUDE.md, plugins, hooks, MCP servers, skills and agents but keeps the sign-in.
    args = [executable, '-p', '--safe-mode', '--output-format', 'json', '--json-schema', text, '--tools', '',
            '--strict-mcp-config', '--disable-slash-commands', '--no-session-persistence']
    # Never inherit API-key routing from a parent terminal; auth remains the owner's Claude sign-in.
    env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}
    with tempfile.TemporaryDirectory(prefix='hq-ai-') as folder:
        completed = subprocess.run(args, input=prompt, text=True, encoding='utf-8', env=env, cwd=folder,
                                   capture_output=True, timeout=timeout,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if len(completed.stdout or '') > 1048576:
        raise ValueError('The generated answer is too large.')
    try:
        envelope = json.loads(completed.stdout or '')
    except ValueError:
        envelope = None
    if isinstance(envelope, dict) and envelope.get('subtype') == 'error_max_structured_output_retries':
        raise ValueError('Claude answered, but not in the agreed shape.')
    if completed.returncode or (isinstance(envelope, dict) and envelope.get('is_error')):
        raise RuntimeError('Claude could not finish. Check its sign-in (claude auth login) or usage limit.')
    if not isinstance(envelope, dict) or envelope.get('structured_output') is None:
        raise ValueError('Claude gave no structured answer.')
    return envelope['structured_output']


def complete(prompt, schema, *, runner=None, engine='codex', timeout=600):
    """Ask once and return an answer that fits the schema.

    `engine` picks the CLI (codex or claude) when no runner is given.
    Raises AiUnavailable when the runner is missing or fails, AiBadAnswer when the answer does not fit.
    """
    if runner is None:
        model_label(engine)  # An unknown engine is a setup error, not an outage.
    try:
        if runner is not None:
            answer = runner(prompt, schema)
        else:
            try:
                cli = run_claude_cli if engine == 'claude' else run_cli
                answer = cli(prompt, schema, timeout=timeout)
            except ValueError as error:
                # Unreadable or oversized output is an answer problem, not an outage.
                raise AiBadAnswer(str(error)[:200] or 'The answer could not be read.') from None
    except AiBadAnswer:
        raise
    except Exception as error:
        raise AiUnavailable(str(error)[:200] or 'The AI could not be reached.') from None
    return validate(answer, schema)
