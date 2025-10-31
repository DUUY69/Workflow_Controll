import os
import json
import urllib.request
import urllib.error
import time
import uuid
from typing import Any, Dict, List
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
import uvicorn


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


def _http_post_json(url: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any] | None:
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode('utf-8')
            return json.loads(text)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def dispatch_step(env: Dict[str, str], wf_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
    system = str(step.get('system', '')).strip().lower()
    request: Dict[str, Any] = dict(step.get('request') or {})
    timeout_s = float(step.get('timeout', env.get('DEFAULT_STEP_TIMEOUT', '10')))
    step_id = str(step.get('id') or str(uuid.uuid4()))

    # correlate id
    if 'id' not in request:
        request['id'] = f"{wf_id}:{step_id}"

    use_http = str(env.get('USE_HTTP', '0')).strip() in ('1', 'true', 'yes')
    if system == 'arm':
        inbox = os.path.abspath(env['ARM_INBOX'])
        outbox = os.path.abspath(env['ARM_OUTBOX'])
        http_url = env.get('ARM_HTTP')
    elif system == 'iot':
        inbox = os.path.abspath(env['IOT_INBOX'])
        outbox = os.path.abspath(env['IOT_OUTBOX'])
        http_url = env.get('IOT_HTTP')
    else:
        return { 'ok': False, 'message': f'unknown_system:{system}' }

    # Log step begin
    try:
        print(f"[WF] Step start wf={wf_id} step={step_id} system={system} timeout={timeout_s}s request={json.dumps(request, ensure_ascii=False)}")
    except Exception:
        print(f"[WF] Step start wf={wf_id} step={step_id} system={system} timeout={timeout_s}s (request log failed)")

    # Prefer HTTP if enabled and URL present
    if use_http and http_url:
        # Choose endpoint per system
        if system == 'arm':
            url = http_url.rstrip('/') + '/robot/command'
        else:
            url = http_url.rstrip('/') + '/command_json'
        print(f"[WF] Step {step_id} via HTTP: POST {url}")
        resp = _http_post_json(url, request, timeout_s)
        if resp is None:
            # fallback to file
            print(f"[WF] Step {step_id} HTTP failed/timeout -> fallback file pipeline")
        else:
            try:
                print(f"[WF] Step {step_id} HTTP response: {json.dumps(resp, ensure_ascii=False)}")
            except Exception:
                print(f"[WF] Step {step_id} HTTP response (log failed)")
            return resp

    base_name = f"wf_{wf_id}__{step_id}"
    in_path = os.path.join(inbox, base_name + '.json')
    write_json(in_path, request)
    print(f"[WF] Step {step_id} file dispatch -> {in_path}")
    resp = wait_for_response(outbox, base_name, timeout_s)
    if not resp:
        print(f"[WF] Step {step_id} file response: timeout after {timeout_s}s")
        return { 'ok': False, 'message': 'timeout' }
    try:
        print(f"[WF] Step {step_id} file response: {json.dumps(resp, ensure_ascii=False)}")
    except Exception:
        print(f"[WF] Step {step_id} file response (log failed)")
    return resp


def run_workflow(env: Dict[str, str], wf: Dict[str, Any]) -> Dict[str, Any]:
    wf_id = str(wf.get('id') or str(uuid.uuid4()))
    name = str(wf.get('name') or wf_id)
    steps: List[Dict[str, Any]] = list(wf.get('steps') or [])
    results: List[Dict[str, Any]] = []

    print(f"[WF] Run workflow start id={wf_id} name={name} steps={len(steps)}")

    for idx, step in enumerate(steps, start=1):
        step_name = str(step.get('id') or idx)
        resp = dispatch_step(env, wf_id, step)
        results.append({ 'step': step_name, 'response': resp })
        if not resp.get('ok'):
            print(f"[WF] Workflow failed at step={step_name} (index={idx}) resp={resp}")
            return { 'id': wf_id, 'name': name, 'ok': False, 'failed_at': idx, 'results': results }
    print(f"[WF] Workflow success id={wf_id} name={name}")
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
                print(f"[WF] Received workflow file: {full}")
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


app = FastAPI(title="WorkflowController")

_WF_ENV: Dict[str, str] | None = None
_WF_IN_DIR: str | None = None
_WF_OUT_DIR: str | None = None
_WF_TEMPLATES_DIR: str | None = None

def _startup_setup() -> None:
    global _WF_ENV, _WF_IN_DIR, _WF_OUT_DIR, _WF_TEMPLATES_DIR
    base = os.path.dirname(__file__)
    env_path = os.path.join(base, '.env_workflow_config')
    env = load_env(env_path)
    env.setdefault('INPUT_DIR', './inbox')
    env.setdefault('OUTPUT_DIR', './outbox')
    env.setdefault('DEFAULT_STEP_TIMEOUT', '12')
    repo_root = os.path.abspath(os.path.join(base, '..'))
    env.setdefault('ARM_INBOX', os.path.join(repo_root, 'ArmController', 'inbox'))
    env.setdefault('ARM_OUTBOX', os.path.join(repo_root, 'ArmController', 'outbox'))
    env.setdefault('IOT_INBOX', os.path.join(repo_root, 'IotController', 'inbox'))
    env.setdefault('IOT_OUTBOX', os.path.join(repo_root, 'IotController', 'outbox'))
    in_dir = os.path.abspath(os.path.join(base, env.get('INPUT_DIR', './inbox')))
    out_dir = os.path.abspath(os.path.join(base, env.get('OUTPUT_DIR', './outbox')))
    templates_dir = os.path.join(base, 'workflows')
    for p in [in_dir, out_dir, env['ARM_INBOX'], env['ARM_OUTBOX'], env['IOT_INBOX'], env['IOT_OUTBOX'], templates_dir]:
        ensure_dir(p)
    _WF_ENV = env
    _WF_IN_DIR = in_dir
    _WF_OUT_DIR = out_dir
    _WF_TEMPLATES_DIR = templates_dir

def _worker_loop():
    assert _WF_ENV and _WF_IN_DIR and _WF_OUT_DIR
    base = os.path.dirname(__file__)
    while True:
        for name in sorted(os.listdir(_WF_IN_DIR)):
            if not name.lower().endswith('.json'):
                continue
            full = os.path.join(_WF_IN_DIR, name)
            try:
                wf = read_json(full)
                print(f"[WF] Received workflow file: {full}")
                try:
                    os.remove(full)
                except Exception:
                    pass
            except Exception:
                resp = { 'ok': False, 'message': 'invalid_json' }
            else:
                resp = run_workflow(_WF_ENV, wf)
            out_name = os.path.splitext(name)[0] + '.response.json'
            write_json(os.path.join(_WF_OUT_DIR, out_name), resp)
        time.sleep(0.2)

@app.on_event('startup')
def _on_startup():
    import threading
    _startup_setup()
    threading.Thread(target=_worker_loop, daemon=True).start()
    print('workflow_ready_http')

@app.get('/health')
def health():
    return { 'status': 'ok' }

@app.get('/api/devices')
def api_devices():
    # Parse IoT devices from IotController .env
    assert _WF_ENV is not None
    iot_env_path = os.path.join(os.path.dirname(__file__), '..', 'IotController', '.env_iot_config')
    devices: Dict[str, Dict[str, str]] = {}
    if os.path.exists(iot_env_path):
        with open(iot_env_path, 'r', encoding='utf-8') as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                if ',' in value:
                    com, baud = value.split(',', 1)
                    devices[key.strip()] = { 'com': com.strip(), 'baud': baud.strip() }
    return { 'devices': devices }

@app.get('/api/arm/lua_list')
def api_arm_lua_list():
    assert _WF_ENV is not None
    arm_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'ArmController'))
    lua_dir = os.path.join(arm_root, 'lua_scripts')
    # Đảm bảo thư mục tồn tại để người dùng có thể đặt file vào
    ensure_dir(lua_dir)
    files: list[str] = []
    # Quét trong lua_scripts/
    try:
        for n in sorted(os.listdir(lua_dir)):
            if n.lower().endswith('.lua'):
                files.append(n)
    except Exception:
        pass
    # Fallback: nếu trống, quét trực tiếp dưới ArmController/
    if not files:
        try:
            for n in sorted(os.listdir(arm_root)):
                if n.lower().endswith('.lua'):
                    files.append(n)
        except Exception:
            pass
    try:
        print(f"[WF] lua_list dir={os.path.abspath(lua_dir)} count={len(files)} files={files}")
    except Exception:
        print(f"[WF] lua_list dir={os.path.abspath(lua_dir)} count={len(files)}")
    return { 'lua_files': files, 'dir': os.path.abspath(lua_dir) }

@app.get('/api/workflows')
def api_list_workflows():
    assert _WF_TEMPLATES_DIR is not None
    items = []
    for n in sorted(os.listdir(_WF_TEMPLATES_DIR)):
        if n.lower().endswith('.json'):
            items.append(n)
    return { 'workflows': items }

@app.get('/api/workflows/{name}')
def api_get_workflow(name: str):
    assert _WF_TEMPLATES_DIR is not None
    p = os.path.join(_WF_TEMPLATES_DIR, name)
    if not os.path.exists(p):
        raise HTTPException(status_code=404, detail='not_found')
    return read_json(p)

@app.post('/api/workflows')
async def api_create_workflow(req: Request):
    assert _WF_TEMPLATES_DIR is not None
    body = await req.json()
    name = body.get('name') or (body.get('id') or str(uuid.uuid4()))
    fname = f"{name}.json" if not str(name).lower().endswith('.json') else str(name)
    p = os.path.join(_WF_TEMPLATES_DIR, fname)
    write_json(p, body)
    return { 'ok': True, 'name': fname }

@app.put('/api/workflows/{name}')
async def api_update_workflow(name: str, req: Request):
    assert _WF_TEMPLATES_DIR is not None
    body = await req.json()
    p = os.path.join(_WF_TEMPLATES_DIR, name)
    if not os.path.exists(p):
        raise HTTPException(status_code=404, detail='not_found')
    write_json(p, body)
    return { 'ok': True }

@app.delete('/api/workflows/{name}')
def api_delete_workflow(name: str):
    assert _WF_TEMPLATES_DIR is not None
    p = os.path.join(_WF_TEMPLATES_DIR, name)
    if not os.path.exists(p):
        raise HTTPException(status_code=404, detail='not_found')
    try:
        os.remove(p)
    except Exception:
        pass
    return { 'ok': True }

@app.post('/api/run')
async def api_run_workflow(req: Request):
    assert _WF_ENV is not None and _WF_IN_DIR is not None
    wf = await req.json()
    wf_id = str(wf.get('id') or str(uuid.uuid4()))
    base_name = f"wf_req_{wf_id}"
    in_path = os.path.join(_WF_IN_DIR, base_name + '.json')
    write_json(in_path, wf)
    return { 'ok': True, 'queued': base_name }

@app.get('/api/outbox')
def api_list_outbox():
    assert _WF_OUT_DIR is not None
    items = []
    for n in sorted(os.listdir(_WF_OUT_DIR)):
        if n.lower().endswith('.response.json'):
            items.append(n)
    return { 'responses': items }

@app.get('/ui')
def ui_root():
    # Minimal UI, suggest opening index.html under /ui/
    html = """
<!DOCTYPE html>
<html lang=\"vi\">\n<head>\n<meta charset=\"utf-8\" />\n<title>COFFEE KIOSK CONTROLLER - Workflow UI</title>\n<style>\n:root{--bg:#0f172a;--panel:#111827;--muted:#1f2937;--card:#0b1220;--txt:#e5e7eb;--accent:#06b6d4;--good:#22c55e;--bad:#ef4444;--warn:#f59e0b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font-family:Segoe UI,Roboto,Arial,sans-serif}header{display:flex;align-items:center;gap:12px;padding:14px 18px;border-bottom:1px solid #1f2937;background:linear-gradient(180deg,#0b1220,#0f172a)}header h1{font-size:18px;margin:0}main{display:grid;grid-template-columns:320px 1fr;gap:12px;padding:12px}section,aside{background:var(--panel);border:1px solid #283246;border-radius:10px;overflow:hidden}h2{font-size:14px;margin:0;padding:10px 12px;border-bottom:1px solid #263041;background:#0b1220;color:#cbd5e1} .box{padding:12px} .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}button{background:#1f2937;border:1px solid #334155;color:var(--txt);padding:8px 12px;border-radius:8px;cursor:pointer}button:hover{border-color:#475569}button.primary{background:var(--accent);border-color:#0891b2;color:#031b26;font-weight:600}button.good{background:var(--good);border-color:#16a34a;color:#06240f}button.warn{background:var(--warn);border-color:#b45309;color:#261503}button.bad{background:var(--bad);border-color:#b91c1c;color:#2a0909}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}select,input,textarea{width:100%;background:#0b1220;color:var(--txt);border:1px solid #314055;border-radius:8px;padding:8px}textarea{min-height:140px}.list{max-height:200px;overflow:auto;border:1px solid #233146;border-radius:8px}.list-item{padding:8px 10px;border-bottom:1px solid #1f2736;display:flex;justify-content:space-between;gap:8px} .pill{padding:2px 8px;border:1px solid #2b374a;border-radius:999px;background:#0b1220;color:#93c5fd;font-size:12px}.steps{max-height:380px;overflow:auto;border:1px dashed #2b374a;border-radius:10px}.step{padding:10px;border-bottom:1px dashed #2b374a;display:flex;justify-content:space-between;gap:8px}.muted{color:#9ca3af}.mono{font-family:ui-monospace,Consolas,monospace}pre{background:#0b1220;border:1px solid #2b374a;border-radius:8px;padding:8px;overflow:auto}footer{padding:10px 12px;border-top:1px solid #263041;background:#0b1220;text-align:right}\n</style>\n</head>\n<body>\n<header>\n  <svg width=\"22\" height=\"22\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\"><path d=\"M12 2L15 8L22 9L17 14L18 21L12 18L6 21L7 14L2 9L9 8L12 2Z\" fill=\"#06b6d4\"/></svg>\n  <h1>COFFEE KIOSK CONTROLLER – Workflow Builder</h1>\n</header>\n<main>\n  <aside>\n    <h2>Kho dữ liệu</h2>\n    <div class=\"box\">\n      <div class=\"row\">\n        <button onclick=\"reloadAll()\">Tải lại</button>\n      </div>\n      <div class=\"grid2\" style=\"margin-top:10px\">\n        <div>\n          <div class=\"row\" style=\"justify-content:space-between\"><strong>Thiết bị IoT</strong> <span class=\"pill\" id=\"devCnt\">0</span></div>\n          <div class=\"list\" id=\"devList\"></div>\n        </div>\n        <div>\n          <div class=\"row\" style=\"justify-content:space-between\"><strong>Lua scripts</strong> <span class=\"pill\" id=\"luaCnt\">0</span></div>\n          <div class=\"list\" id=\"luaList\"></div>\n        </div>\n      </div>\n      <div style=\"margin-top:12px\">\n        <div class=\"row\" style=\"justify-content:space-between\"><strong>Workflows</strong> <span class=\"pill\" id=\"wfCnt\">0</span></div>\n        <div class=\"list\" id=\"wfList\"></div>\n        <div class=\"row\" style=\"margin-top:8px\">\n          <input id=\"wfName\" placeholder=\"Tên workflow...\" />\n          <button class=\"primary\" onclick=\"saveWF()\">Lưu</button>\n          <button onclick=\"loadSelectedWF()\">Tải</button>\n          <button class=\"bad\" onclick=\"deleteSelectedWF()\">Xóa</button>\n        </div>\n      </div>\n    </div>\n  </aside>\n  <section>\n    <h2>Thiết kế Workflow</h2>\n    <div class=\"box\">\n      <div class=\"row\" style=\"gap:12px\">\n        <input id=\"wfId\" placeholder=\"ID\" style=\"max-width:220px\"/>\n        <input id=\"wfTitle\" placeholder=\"Tên hiển thị\" style=\"flex:1\"/>\n        <button class=\"good\" onclick=\"runWF()\">Chạy Workflow</button>\n      </div>\n      <div class=\"grid2\" style=\"margin-top:12px\">\n        <div>\n          <strong>Thêm bước</strong>\n          <div class=\"row\" style=\"margin-top:6px\">\n            <select id=\"sysSel\"><option value=\"arm\">arm</option><option value=\"iot\">iot</option></select>\n            <input id=\"stepId\" placeholder=\"step id (tùy chọn)\" style=\"flex:1\"/>\n            <input id=\"timeout\" type=\"number\" min=\"1\" value=\"20\" style=\"width:120px\"/>\n          </div>\n          <div id=\"armForm\" style=\"margin-top:8px\">\n            <div class=\"row\">\n              <select id=\"luaSel\"></select>\n              <span class=\"pill\">type = run_lua</span>\n            </div>\n          </div>\n          <div id=\"iotForm\" style=\"margin-top:8px; display:none\">\n            <div class=\"row\">\n              <select id=\"devSel\"></select>\n              <input id=\"hexData\" class=\"mono\" placeholder=\"HEX (vd: 04 07 AA 02 05 BC FF)\"/>\n            </div>\n            <div class=\"row\">\n              <label class=\"row\"><input type=\"checkbox\" id=\"flush\"/> Flush</label>
              <input id=\"readLen\" type=\"number\" min=\"0\" placeholder=\"read_len\" style=\"max-width:140px\"/>
            </div>\n          </div>\n          <div class=\"row\" style=\"margin-top:8px\">\n            <button class=\"primary\" onclick=\"addStep()\">Thêm bước</button>\n          </div>\n        </div>\n        <div>\n          <strong>Các bước</strong>\n          <div class=\"steps\" id=\"steps\"></div>\n        </div>\n      </div>\n      <div style=\"margin-top:12px\">\n        <strong>JSON</strong>
        <textarea id=\"wfjson\"></textarea>
      </div>\n      <div class=\"row\" style=\"margin-top:8px\">\n        <button onclick=\"syncFromJson()\">Nạp từ JSON</button>\n        <button onclick=\"syncToJson()\">Cập nhật JSON</button>\n      </div>\n      <div style=\"margin-top:12px\">\n        <strong>Kết quả chạy gần đây</strong>
        <pre id=\"runresp\"></pre>
      </div>\n    </div>\n  </section>\n</main>\n<footer>© Workflow UI</footer>
<script>\nlet DEV={}, LUA=[], WFLS=[], STEPS=[]; let selWF=null;\nfunction el(id){return document.getElementById(id)}\nfunction renderLists(){\n  const devList=el('devList'); devList.innerHTML=''; const keys=Object.keys(DEV||{}); el('devCnt').textContent=keys.length;\n  keys.forEach(k=>{ const d=document.createElement('div'); d.className='list-item'; d.innerHTML='<span>'+k+'</span><span class=\\'muted\\'>'+DEV[k].com+' @ '+DEV[k].baud+'</span>'; devList.appendChild(d); });\n  const luaList=el('luaList'); luaList.innerHTML=''; el('luaCnt').textContent=(LUA||[]).length;\n  LUA.forEach(n=>{ const d=document.createElement('div'); d.className='list-item'; d.textContent=n; luaList.appendChild(d); });\n  const luaSel=el('luaSel'); luaSel.innerHTML=''; LUA.forEach(n=>{ const o=document.createElement('option'); o.value=n;o.textContent=n; luaSel.appendChild(o); });\n  const devSel=el('devSel'); devSel.innerHTML=''; keys.forEach(k=>{ const o=document.createElement('option'); o.value=k;o.textContent=k+' ('+DEV[k].com+')'; devSel.appendChild(o); });\n  const wfList=el('wfList'); wfList.innerHTML=''; el('wfCnt').textContent=(WFLS||[]).length;\n  WFLS.forEach(n=>{ const d=document.createElement('div'); d.className='list-item'; d.innerHTML='<span>'+n+'</span><span><button onclick=\\'selectWF(\''+n+'\')\\'>Chọn</button></span>'; wfList.appendChild(d); });\n}\nasync function reloadAll(){ await Promise.all([loadDevices(), loadLua(), listWorkflows()]); renderLists(); }\nasync function loadDevices(){ const r=await fetch('/api/devices'); const j=await r.json(); DEV=j.devices||{} }\nasync function loadLua(){ const r=await fetch('/api/arm/lua_list'); const j=await r.json(); LUA=j.lua_files||[] }\nasync function listWorkflows(){ const r=await fetch('/api/workflows'); const j=await r.json(); WFLS=j.workflows||[] }\nfunction selectWF(name){ selWF=name; el('wfName').value=name; fetch('/api/workflows/'+encodeURIComponent(name)).then(r=>r.json()).then(j=>{ loadWF(j); }); }\nfunction loadSelectedWF(){ const n=el('wfName').value.trim(); if(!n) return; selectWF(n); }\nfunction deleteSelectedWF(){ const n=el('wfName').value.trim(); if(!n) return; fetch('/api/workflows/'+encodeURIComponent(n), {method:'DELETE'}).then(()=>{ listWorkflows().then(renderLists); }); }\nfunction loadWF(j){ el('wfId').value=j.id||''; el('wfTitle').value=j.name||''; STEPS=[...(j.steps||[])]; renderSteps(); syncToJson(); }\nfunction renderSteps(){ const box=el('steps'); box.innerHTML=''; STEPS.forEach((s,idx)=>{ const div=document.createElement('div'); div.className='step'; div.innerHTML='<div><div><strong>'+(s.id||('Bước '+(idx+1)))+'</strong> <span class=\\'pill\\'>'+s.system+'</span></div><div class=\\'muted\\'>timeout='+s.timeout+'s</div><div class=\\'mono\\'>'+JSON.stringify(s.request)+'</div></div><div class=\\'row\\'><button onclick=\\'moveStep('+idx+',-1)\\'>Lên</button><button onclick=\\'moveStep('+idx+',1)\\'>Xuống</button><button class=\\'bad\\' onclick=\\'delStep('+idx+')\\'>Xóa</button></div>'; box.appendChild(div); }); }\nfunction moveStep(i,dir){ const j=i+dir; if(j<0||j>=STEPS.length) return; const t=STEPS[i]; STEPS[i]=STEPS[j]; STEPS[j]=t; renderSteps(); syncToJson(); }\nfunction delStep(i){ STEPS.splice(i,1); renderSteps(); syncToJson(); }\nfunction addStep(){ const sys=el('sysSel').value; const id=el('stepId').value.trim(); const timeout=parseFloat(el('timeout').value||'20'); let step={id:id||undefined, system:sys, timeout:timeout, request:{}}; if(sys==='arm'){ step.request={ type:'run_lua', file: el('luaSel').value || '' }; } else { step.request={ command:'send_hex', device: el('devSel').value||'', hex: el('hexData').value||'', flush: el('flush').checked, read_len: parseInt(el('readLen').value||'0')||undefined }; } STEPS.push(step); renderSteps(); syncToJson(); }\nfunction syncToJson(){ const wf={ id: el('wfId').value||undefined, name: el('wfTitle').value||undefined, steps: STEPS }; el('wfjson').value=JSON.stringify(wf,null,2); }\nfunction syncFromJson(){ try{ const wf=JSON.parse(el('wfjson').value); loadWF(wf); }catch(e){ alert('JSON không hợp lệ'); } }\nfunction buildWF(){ try{ return JSON.parse(el('wfjson').value); }catch(e){ return { id: el('wfId').value||undefined, name: el('wfTitle').value||undefined, steps: STEPS }; } }\nfunction saveWF(){ const wf=buildWF(); let name=el('wfName').value.trim() || wf.name || wf.id || ('wf_'+Date.now()); if(!String(name).toLowerCase().endsWith('.json')) name=name+'.json'; fetch('/api/workflows/'+encodeURIComponent(name), {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(wf)}).then(r=>{ if(r.status===404){ return fetch('/api/workflows',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(wf)}); } }).then(()=>{ listWorkflows().then(renderLists); }); }\nfunction runWF(){ const wf=buildWF(); fetch('/api/run',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(wf)}).then(r=>r.json()).then(j=>{ el('runresp').textContent=JSON.stringify(j,null,2); }); }\nel('sysSel').addEventListener('change',()=>{ const v=el('sysSel').value; el('armForm').style.display=(v==='arm')?'block':'none'; el('iotForm').style.display=(v==='iot')?'block':'none'; });\nwindow.onload=()=>{ reloadAll(); syncToJson(); };\n</script>\n</body>\n</html>\n    """
    return HTMLResponse(content=html)

if __name__ == '__main__':
    uvicorn.run("workflow_service:app", host="0.0.0.0", port=8003, reload=False)


