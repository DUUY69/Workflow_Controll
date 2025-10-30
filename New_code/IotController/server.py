import os
from typing import Any, Dict
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

# Reuse logic from iot_service
from .iot_service import load_env_file, parse_devices_from_config_env, IoTSerial, process_command

app = FastAPI(title="IotController")


@app.on_event("startup")
async def startup_event():
    base = os.path.dirname(__file__)
    preferred_env = os.path.join(base, '.env_iot_config')
    fallback_env = os.path.join(base, 'config.env')
    env_path = preferred_env if os.path.exists(preferred_env) else fallback_env
    env: Dict[str, Any] = load_env_file(env_path)
    env.setdefault('DEFAULT_BAUDRATE', '115200')
    env.setdefault('DEFAULT_TIMEOUT', '1.0')
    devices = parse_devices_from_config_env(env_path)
    # global state
    app.state.env = env
    app.state.devices = devices
    app.state.iot_pool: Dict[str, IoTSerial] = {}


@app.post("/command_json")
async def command_json(req: Request):
    try:
        payload = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    resp = process_command(app.state.iot_pool, app.state.devices, app.state.env, payload)
    return JSONResponse(resp)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8002, reload=False)


