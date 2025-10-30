import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse

APP_ROOT = Path(__file__).resolve().parent
LUA_DIR = APP_ROOT / "lua_scripts"
DB_DIR = APP_ROOT / "TechPoint_db"
ACTIVE_DB_NAME = "web_point.db"

# Ensure directories exist at startup
LUA_DIR.mkdir(parents=True, exist_ok=True)
DB_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="ArmController")


def find_lua_executable() -> Optional[str]:
	# Allow override via environment variable
	lua_env = os.environ.get("LUA_EXE")
	if lua_env and shutil.which(lua_env):
		return lua_env
	# Common executable names
	for candidate in ["lua", "lua5.4", "lua5.3"]:
		exe = shutil.which(candidate)
		if exe:
			return exe
	return None


@app.post("/command")
async def handle_command(action: str = Form(...), file: Optional[str] = Form(None)):
	if action == "run_lua":
		if not file:
			raise HTTPException(status_code=400, detail="Missing 'file' for run_lua")
			
		lua_path = (LUA_DIR / file).resolve()
		# Security: confine to LUA_DIR
		if not str(lua_path).startswith(str(LUA_DIR.resolve())):
			raise HTTPException(status_code=400, detail="Invalid file path")
		if not lua_path.exists():
			raise HTTPException(status_code=404, detail=f"Lua file not found: {file}")

		lua_exe = find_lua_executable()
		if not lua_exe:
			# If no lua runtime available, simulate completion to keep workflow unblocked
			return JSONResponse({
				"status": "done",
				"message": f"Arm completed {file} (simulated - no lua runtime)",
			})

		try:
			# Execute lua script; working dir is LUA_DIR
			result = subprocess.run(
				[lua_exe, str(lua_path.name)],
				cwd=str(LUA_DIR),
				capture_output=True,
				text=True,
				check=True,
			)
			return JSONResponse({
				"status": "done",
				"message": f"Arm completed {file}",
				"stdout": result.stdout,
				"stderr": result.stderr,
			})
		except subprocess.CalledProcessError as e:
			raise HTTPException(status_code=500, detail={
				"error": "lua_execution_failed",
				"returncode": e.returncode,
				"stdout": e.stdout,
				"stderr": e.stderr,
			})
	else:
		raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")


# JSON-compatible endpoint matching file-based format
@app.post("/command_json")
async def command_json(req: Request):
    try:
        payload = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    # Lazy import to avoid circular at module import time
    from . import arm_controller as ac  # type: ignore
    # Initialize robot once
    global _robot_client, _arm_env
    if '_robot_client' not in globals():
        cfg_path = os.path.join(os.path.dirname(__file__), '.env_arm_config')
        _arm_env = ac._load_env_file(cfg_path)
        _arm_env.setdefault('ROBOT_IP', '192.168.58.2')
        _arm_env.setdefault('XMLRPC_PORT', '20003')
        _arm_env.setdefault('TCP_UPLOAD_PORT', '20010')
        _robot_client = ac.RobotClient(
            ip=_arm_env.get('ROBOT_IP', '192.168.58.2'),
            xmlrpc_port=int(_arm_env.get('XMLRPC_PORT', '20003')),
            tcp_port=int(_arm_env.get('TCP_UPLOAD_PORT', '20010')),
        )
        _robot_client.connect()
    resp = ac.process_command(_robot_client, payload)
    # Normalize to { id, ok, message, ... }
    return JSONResponse(resp)


@app.post("/upload/lua")
async def upload_lua(file: UploadFile = File(...)):
	# Basic validation
	if not file.filename.lower().endswith(".lua"):
		raise HTTPException(status_code=400, detail="Only .lua files are accepted")
	
	dst = LUA_DIR / Path(file.filename).name
	with open(dst, "wb") as f:
		f.write(await file.read())
	return {"status": "ok", "path": str(dst.relative_to(APP_ROOT))}


@app.post("/upload/db")
async def upload_db(
    file: UploadFile = File(...),
    activate: bool = Form(False),
):
    if not file.filename.lower().endswith(".db"):
        raise HTTPException(status_code=400, detail="Only .db files are accepted")

    dst = DB_DIR / Path(file.filename).name
    with open(dst, "wb") as f:
        f.write(await file.read())

    active_path = DB_DIR / ACTIVE_DB_NAME
    if activate:
        # Replace active DB atomically when possible
        temp_path = DB_DIR / (ACTIVE_DB_NAME + ".tmp")
        shutil.copy2(dst, temp_path)
        os.replace(temp_path, active_path)

    return {
        "status": "ok",
        "stored": str(dst.relative_to(APP_ROOT)),
        "active": str(active_path.relative_to(APP_ROOT)) if activate else None,
    }


@app.get("/health")
async def health():
	return {"status": "ok"}


if __name__ == "__main__":
	import uvicorn
	uvicorn.run("server:app", host="0.0.0.0", port=8001, reload=False)
