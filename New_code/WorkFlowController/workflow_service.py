import os
import json
import time
import uuid
from typing import Any, Dict, List


def load_env(path: str) -> Dict[str, str]:
    cfg: Dict[str, str] = {}
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                cfg[k.strip()] = v.strip()
    return cfg


def ensure_dir(p: str) -> None:
    if not os.path.exists(p):
        os.makedirs(p, exist_ok=True)


def write_json(p: str, data: Dict[str, Any]) -> None:
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def read_json(p: str) -> Dict[str, Any]:
    with open(p, 'r', encoding='utf-8') as f:
        return json.load(f)


def wait_for_response(out_dir: str, base_name: str, timeout_s: float) -> Dict[str, Any] | None:
    """Wait until response file appears or timeout."""
    path = os.path.join(out_dir, base_name + '.response.json')
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if os.path.exists(path):
            try:
                return read_json(path)
            finally:
                try:
                    os.remove(path)
                except Exception:
                    pass
        time.sleep(0.1)
    return None


def dispatch_step(env: Dict[str, str], wf_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
    system = str(step.get('system', '')).strip().lower()
    request: Dict[str, Any] = dict(step.get('request') or {})
    timeout_s = float(step.get('timeout', env.get('DEFAULT_STEP_TIMEOUT', '10')))
    step_id = str(step.get('id') or str(uuid.uuid4()))

    # correlate id
    if 'id' not in request:
        request['id'] = f"{wf_id}:{step_id}"

    if system == 'arm':
        inbox = os.path.abspath(env['ARM_INBOX'])
        outbox = os.path.abspath(env['ARM_OUTBOX'])
    elif system == 'iot':
        inbox = os.path.abspath(env['IOT_INBOX'])
        outbox = os.path.abspath(env['IOT_OUTBOX'])
    else:
        return { 'ok': False, 'message': f'unknown_system:{system}' }

    base_name = f"wf_{wf_id}__{step_id}"
    in_path = os.path.join(inbox, base_name + '.json')
    write_json(in_path, request)
    resp = wait_for_response(outbox, base_name, timeout_s)
    if not resp:
        return { 'ok': False, 'message': 'timeout' }
    return resp


def run_workflow(env: Dict[str, str], wf: Dict[str, Any]) -> Dict[str, Any]:
    wf_id = str(wf.get('id') or str(uuid.uuid4()))
    name = str(wf.get('name') or wf_id)
    steps: List[Dict[str, Any]] = list(wf.get('steps') or [])
    results: List[Dict[str, Any]] = []

    for idx, step in enumerate(steps, start=1):
        resp = dispatch_step(env, wf_id, step)
        results.append({ 'step': step.get('id') or idx, 'response': resp })
        if not resp.get('ok'):
            return { 'id': wf_id, 'name': name, 'ok': False, 'failed_at': idx, 'results': results }
    return { 'id': wf_id, 'name': name, 'ok': True, 'results': results }


def main() -> None:
    base = os.path.dirname(__file__)
    env_path = os.path.join(base, '.env_workflow_config')
    env = load_env(env_path)

    # Defaults
    env.setdefault('INPUT_DIR', './inbox')
    env.setdefault('OUTPUT_DIR', './outbox')
    env.setdefault('DEFAULT_STEP_TIMEOUT', '12')

    # Resolve target controller paths (relative to repo root by default)
    repo_root = os.path.abspath(os.path.join(base, '..'))
    env.setdefault('ARM_INBOX', os.path.join(repo_root, 'ArmController', 'inbox'))
    env.setdefault('ARM_OUTBOX', os.path.join(repo_root, 'ArmController', 'outbox'))
    env.setdefault('IOT_INBOX', os.path.join(repo_root, 'IotController', 'inbox'))
    env.setdefault('IOT_OUTBOX', os.path.join(repo_root, 'IotController', 'outbox'))

    in_dir = os.path.abspath(os.path.join(base, env.get('INPUT_DIR', './inbox')))
    out_dir = os.path.abspath(os.path.join(base, env.get('OUTPUT_DIR', './outbox')))

    for p in [in_dir, out_dir, env['ARM_INBOX'], env['ARM_OUTBOX'], env['IOT_INBOX'], env['IOT_OUTBOX']]:
        ensure_dir(p)

    print('workflow_ready')
    while True:
        for name in sorted(os.listdir(in_dir)):
            if not name.lower().endswith('.json'):
                continue
            full = os.path.join(in_dir, name)
            try:
                wf = read_json(full)
                try:
                    os.remove(full)
                except Exception:
                    pass
            except Exception:
                resp = { 'ok': False, 'message': 'invalid_json' }
            else:
                resp = run_workflow(env, wf)
            out_name = os.path.splitext(name)[0] + '.response.json'
            write_json(os.path.join(out_dir, out_name), resp)
        time.sleep(0.2)


if __name__ == '__main__':
    main()


