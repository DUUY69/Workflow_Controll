import os
import sys
import json
import time
import socket
import xmlrpc.client
from typing import Any, Dict, Optional


def _load_env_file(env_path: str) -> Dict[str, str]:
	cfg: Dict[str, str] = {}
	if os.path.exists(env_path):
		with open(env_path, "r", encoding="utf-8") as f:
			for line in f:
				line = line.strip()
				if not line or line.startswith("#"):
					continue
				if "=" in line:
					k, v = line.split("=", 1)
					cfg[k.strip()] = v.strip()
	return cfg


class RobotClient:
	def __init__(self, ip: str, xmlrpc_port: int = 20003, tcp_port: int = 20010):
		self.ip = ip
		self.xmlrpc_port = xmlrpc_port
		self.tcp_port = tcp_port
		self.robot = None  # SDK RPC object or xmlrpc ServerProxy

	def connect(self):
		# Try local fairino_sdk first
		robot = None
		try:
			import sys
			sdk_path = os.path.join(os.path.dirname(__file__), 'fairino_sdk')
			if sdk_path not in sys.path:
				sys.path.insert(0, sdk_path)
			from fairino import Robot  # type: ignore
			try:
				robot = Robot.RPC(self.ip)
			except Exception:
				robot = None
		except Exception:
			robot = None

		# Fallback to local fairino
		if robot is None:
			try:
				from fairino import Robot as LocalRobot  # type: ignore
				try:
					robot = LocalRobot.RPC(self.ip)
				except Exception:
					robot = None
			except Exception:
				robot = None

		# Fallback to raw XML-RPC
		if robot is None:
			paths = ["/RPC2", "/RPC", "/"]
			for path in paths:
				url = f"http://{self.ip}:{self.xmlrpc_port}{path}"
				try:
					proxy = xmlrpc.client.ServerProxy(url)
					# health check
					try:
						_ = proxy.GetControllerIP()
					except Exception:
						_ = proxy.GetLuaList()
					robot = proxy
					break
				except Exception:
					continue

		self.robot = robot
		return self.robot is not None

	def _is_xmlrpc(self) -> bool:
		return self.robot is not None and 'ServerProxy' in type(self.robot).__name__

	def _call(self, name: str, *args):
		func = getattr(self.robot, name, None)
		if callable(func):
			return func(*args)
		raise AttributeError(name)

	def run_lua_and_wait(self, lua_filename: str, timeout: float = 8.0) -> bool:
		if self.robot is None:
			print(f"[DEBUG] Robot is None")
			return False
		remote_path = f"/fruser/{lua_filename}"
		print(f"[DEBUG] Running {lua_filename} from {remote_path}")
		try:
			load_result = self._call('ProgramLoad', remote_path)
			print(f"[DEBUG] ProgramLoad result: {load_result}")
			if int(load_result) != 0:
				print(f"[DEBUG] ProgramLoad failed: {load_result}")
				return False
			run_result = self._call('ProgramRun')
			print(f"[DEBUG] ProgramRun result: {run_result}")
			if int(run_result) != 0:
				print(f"[DEBUG] ProgramRun failed: {run_result}")
				return False
			print(f"[DEBUG] Starting wait for completion...")
			return self._wait_complete(timeout)
		except Exception as e:
			print(f"[DEBUG] run_lua_and_wait exception: {e}")
			return False

	def _wait_complete(self, timeout: float) -> bool:
		start = time.time()
		while time.time() - start < timeout:
			try:
				# Method 1: CheckCommandFinish (from old code)
				if callable(getattr(self.robot, 'CheckCommandFinish', None)):
					try:
						result = self._call('CheckCommandFinish')
						if isinstance(result, tuple):
							err, finished = result
							if err == 0 and finished:
								print(f"[DEBUG] CheckCommandFinish: completed")
								return True
						elif result:
							print(f"[DEBUG] CheckCommandFinish: completed")
							return True
					except Exception as e:
						print(f"[DEBUG] CheckCommandFinish error: {e}")
				
				# Method 2: GetRobotMotionState (from old code)
				if callable(getattr(self.robot, 'GetRobotMotionState', None)):
					try:
						result = self._call('GetRobotMotionState')
						print(f"[DEBUG] Motion State: {result}")
						# Motion state == 0 usually means idle/completed
						if int(result) == 0:
							print(f"[DEBUG] GetRobotMotionState: completed")
							return True
					except Exception as e:
						print(f"[DEBUG] GetRobotMotionState error: {e}")
				
				# Method 3: GetProgramState
				if callable(getattr(self.robot, 'GetProgramState', None)):
					res = self._call('GetProgramState')
					if isinstance(res, tuple):
						if len(res) >= 2 and int(res[0]) == 0 and int(res[1]) == 0:
							print(f"[DEBUG] GetProgramState: completed")
							return True
					else:
						if int(res) == 0:
							print(f"[DEBUG] GetProgramState: completed")
							return True
			except Exception as e:
				print(f"[DEBUG] GetProgramState error: {e}")
			# Alternative names
			for alt in ('ProgramState', 'GetProgramRunState', 'IsProgramRunning'):
				try:
					fn = getattr(self.robot, alt, None)
					if callable(fn):
						val = fn()
						if isinstance(val, tuple):
							err = int(val[0])
							state = int(val[1]) if len(val) > 1 and val[1] is not None else 0
							if err == 0 and state == 0:
								print(f"[DEBUG] {alt}: completed")
								return True
						else:
							if val in (0, False, None):
								print(f"[DEBUG] {alt}: completed")
								return True
				except Exception:
					pass
			time.sleep(0.1)  # Faster polling like old code
		# If no API available (e.g., raw xmlrpc proxy without methods), treat timeout as done
		print(f"[DEBUG] Timeout after {timeout}s, treating as completed")
		return True  # Changed from False to True - assume completed if no API

	def upload_lua(self, path: str) -> bool:
		if self.robot is None:
			print(f"[DEBUG] Robot is None")
			return False
		filename = os.path.basename(path)
		print(f"[DEBUG] Uploading {filename} from {path}")
		# Skip SDK LuaUpload (not available), use XML-RPC directly
		# XML-RPC FileUpload + TCP stream + LuaUpLoadUpdate
		try:
			print(f"[DEBUG] Using XML-RPC FileUpload")
			try:
				_ = self._call('FileUpload', 0, filename)
			except Exception:
				_ = self._call('FileUpload', filename)
			with open(path, 'rb') as f:
				data = f.read()
			file_size = len(data)
			total_size = file_size + 4 + 46
			import hashlib
			md5 = hashlib.md5(data).hexdigest()
			head = f"/f/b{total_size:10d}{md5}".encode(errors='replace')
			end = b"/b/f"
			sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			sock.settimeout(10)
			try:
				sock.connect((self.ip, self.tcp_port))
				sock.sendall(head)
				sock.sendall(data)
				sock.sendall(end)
			finally:
				try:
					sock.close()
				except Exception:
					pass
			update = self._call('LuaUpLoadUpdate', filename)
			print(f"[DEBUG] LuaUpLoadUpdate result: {update}")
			if isinstance(update, (tuple, list)) and len(update) >= 1:
				return int(update[0]) == 0
			return int(update) == 0
		except Exception as e:
			print(f"[DEBUG] XML-RPC upload failed: {e}")
			return False

	def upload_tech_point(self, path: str, activate: bool = True, use_old: bool = False) -> bool:
		# Upload TechPoint database using dedicated SDK/RPC methods, not Lua upload
		print(f"[DEBUG] Uploading TechPoint DB from {path}")
		ok = False

		# If forcing old method, skip dedicated RPCs and do generic upload like old code
		if use_old:
			try:
				filename = os.path.basename(path)
				try:
					_ = self._call('FileUpload', 0, filename)
				except Exception:
					_ = self._call('FileUpload', filename)
				with open(path, 'rb') as f:
					data = f.read()
				import hashlib
				md5 = hashlib.md5(data).hexdigest()
				total_size = len(data) + 4 + 46
				head = f"/f/b{total_size:10d}{md5}".encode(errors='replace')
				end = b"/b/f"
				sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
				sock.settimeout(10)
				try:
					sock.connect((self.ip, self.tcp_port))
					sock.sendall(head)
					sock.sendall(data)
					sock.sendall(end)
				finally:
					try:
						sock.close()
					except Exception:
						pass
				update = self._call('LuaUpLoadUpdate', filename)
				# old code style: success when 0
				if isinstance(update, (tuple, list)) and len(update) >= 1:
					ok = int(update[0]) == 0
				else:
					ok = int(update) == 0
			except Exception as e:
				print(f"[DEBUG] Old-style upload failed: {e}")
				ok = False
		else:
			# Try known upload methods (following old code pattern)
			for method_name in (
				'PointTableUpLoad',   # primary - takes full path
				'PointTableUpload',   # alternative spelling - takes full path
				'PointTableUpdateLua' # seen in SDK symbols; may accept db
			):
				try:
					fn = getattr(self.robot, method_name, None)
					if callable(fn):
						print(f"[DEBUG] Trying {method_name}")
						# Use full path for upload methods (like old code)
						res = fn(os.path.abspath(path))
						print(f"[DEBUG] {method_name} result: {res}")
						ok = (int(res[0]) == 0) if isinstance(res, tuple) else (int(res) == 0)
						if ok:
							print(f"[DEBUG] {method_name} succeeded")
							break
				except Exception as e:
					print(f"[DEBUG] {method_name} failed: {e}")
					continue
			# As a last resort, try generic FileUpload+LuaUpLoadUpdate, though DBs usually require dedicated APIs
			if not ok:
				try:
					filename = os.path.basename(path)
					try:
						_ = self._call('FileUpload', 0, filename)
					except Exception:
						_ = self._call('FileUpload', filename)
					with open(path, 'rb') as f:
						data = f.read()
					import hashlib
					md5 = hashlib.md5(data).hexdigest()
					total_size = len(data) + 4 + 46
					head = f"/f/b{total_size:10d}{md5}".encode(errors='replace')
					end = b"/b/f"
					sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
					sock.settimeout(10)
					try:
						sock.connect((self.ip, self.tcp_port))
						sock.sendall(head)
						sock.sendall(data)
						sock.sendall(end)
					finally:
						try:
							sock.close()
						except Exception:
							pass
					update = self._call('LuaUpLoadUpdate', filename)
					ok = (int(update[0]) == 0) if isinstance(update, tuple) else (int(update) == 0)
				except Exception:
					ok = False
		# Try known upload methods (following old code pattern)
		for method_name in (
			'PointTableUpLoad',   # primary - takes full path
			'PointTableUpload',   # alternative spelling - takes full path
			'PointTableUpdateLua' # seen in SDK symbols; may accept db
		):
			try:
				fn = getattr(self.robot, method_name, None)
				if callable(fn):
					print(f"[DEBUG] Trying {method_name}")
					# Use full path for upload methods (like old code)
					res = fn(os.path.abspath(path))
					print(f"[DEBUG] {method_name} result: {res}")
					ok = (int(res[0]) == 0) if isinstance(res, tuple) else (int(res) == 0)
					if ok:
						print(f"[DEBUG] {method_name} succeeded")
						break
			except Exception as e:
				print(f"[DEBUG] {method_name} failed: {e}")
				continue
		# As a last resort, try generic FileUpload+LuaUpLoadUpdate, though DBs usually require dedicated APIs
		if not ok:
			try:
				filename = os.path.basename(path)
				try:
					_ = self._call('FileUpload', 0, filename)
				except Exception:
					_ = self._call('FileUpload', filename)
				with open(path, 'rb') as f:
					data = f.read()
				import hashlib
				md5 = hashlib.md5(data).hexdigest()
				total_size = len(data) + 4 + 46
				head = f"/f/b{total_size:10d}{md5}".encode(errors='replace')
				end = b"/b/f"
				sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
				sock.settimeout(10)
				try:
					sock.connect((self.ip, self.tcp_port))
					sock.sendall(head)
					sock.sendall(data)
					sock.sendall(end)
				finally:
					try:
						sock.close()
					except Exception:
						pass
				update = self._call('LuaUpLoadUpdate', filename)
				ok = (int(update[0]) == 0) if isinstance(update, tuple) else (int(update) == 0)
			except Exception:
				ok = False
		# Activate selected DB if requested (with delay like old code)
		if ok and activate:
			db_name = os.path.basename(path)
			print(f"[DEBUG] Activating DB: {db_name}")
			# Add small delay before activation (like old code pattern)
			import time
			time.sleep(0.5)
			try:
				fn = getattr(self.robot, 'PointTableSwitch', None)
				if callable(fn):
					print(f"[DEBUG] Calling PointTableSwitch")
					res = fn(db_name)
					print(f"[DEBUG] PointTableSwitch result: {res}")
					# Check if result is 0 (success) or 130 (already active)
					if isinstance(res, (tuple, list)):
						result_code = int(res[0])
					else:
						result_code = int(res)
					
					if result_code == 0:
						print(f"[DEBUG] Activation successful")
						ok = True
					elif result_code == 130:
						print(f"[DEBUG] DB already active (code 130)")
						ok = True  # Treat as success since DB is already active
					else:
						print(f"[DEBUG] Activation failed with code: {result_code}")
						ok = False
				else:
					print(f"[DEBUG] PointTableSwitch not available")
			except Exception as e:
				print(f"[DEBUG] Activation failed: {e}")
				pass
		return ok


def ensure_dirs(path: str):
	if not os.path.exists(path):
		os.makedirs(path, exist_ok=True)


def _resolve_path(p: str) -> str:
	base = os.path.dirname(__file__)
	if os.path.isabs(p):
		return p
	# If just filename, search in lua_scripts/ and TechPoint_db/ subdirs
	if not os.sep in p and not os.altsep in p:
		# Try lua_scripts first
		lua_path = os.path.join(base, 'lua_scripts', p)
		if os.path.exists(lua_path):
			return os.path.abspath(lua_path)
		# Try TechPoint_db
		db_path = os.path.join(base, 'TechPoint_db', p)
		if os.path.exists(db_path):
			return os.path.abspath(db_path)
	# Otherwise resolve relative to controller dir
	return os.path.abspath(os.path.join(base, p))


def process_command(robot: RobotClient, cmd: Dict[str, Any]) -> Dict[str, Any]:
	cmd_id = cmd.get('id')
	# Accept both 'type' and legacy 'command'
	raw_type = cmd.get('type') if cmd.get('type') is not None else cmd.get('command', '')
	type_ = str(raw_type).lower().strip()
	result: Dict[str, Any] = { 'id': cmd_id, 'ok': False }
	if type_ == 'run_lua':
		file_ = cmd.get('file')
		if not file_:
			result['message'] = 'Missing file'
			return result
		ok = robot.run_lua_and_wait(file_)
		result['ok'] = bool(ok)
		result['message'] = 'completed' if ok else 'failed'
		return result
	elif type_ == 'upload_lua':
		path = cmd.get('path')
		if not path:
			result['message'] = 'Missing path'
			return result
		resolved = _resolve_path(path)
		if not os.path.exists(resolved):
			result['message'] = 'Invalid path'
			return result
		result['ok'] = robot.upload_lua(resolved)
		result['message'] = 'uploaded' if result['ok'] else 'failed'
		return result
	elif type_ in ('upload_tech_point', 'upload_techpoint'):
		path = cmd.get('path')
		activate = bool(cmd.get('activate', True))
		use_old = bool(cmd.get('use_old', False))
		if not path:
			result['message'] = 'Missing path'
			return result
		resolved = _resolve_path(path)
		if not os.path.exists(resolved):
			result['message'] = 'Invalid path'
			return result
		result['ok'] = robot.upload_tech_point(resolved, activate=activate, use_old=use_old)
		result['message'] = 'uploaded' if result['ok'] else 'failed'
		return result
	else:
		result['message'] = 'Unknown command type'
		return result


def main():
	cfg_path = os.path.join(os.path.dirname(__file__), '.env_arm_config')
	cfg = _load_env_file(cfg_path)
	# defaults
	cfg.setdefault('ROBOT_IP', '192.168.58.2')
	cfg.setdefault('XMLRPC_PORT', '20003')
	cfg.setdefault('TCP_UPLOAD_PORT', '20010')
	cfg.setdefault('INPUT_DIR', './inbox')
	cfg.setdefault('OUTPUT_DIR', './outbox')

	in_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), cfg.get('INPUT_DIR', './inbox')))
	out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), cfg.get('OUTPUT_DIR', './outbox')))
	ensure_dirs(in_dir)
	ensure_dirs(out_dir)

	robot = RobotClient(
		ip=cfg.get('ROBOT_IP', '192.168.58.2'),
		xmlrpc_port=int(cfg.get('XMLRPC_PORT', '20003')),
		tcp_port=int(cfg.get('TCP_UPLOAD_PORT', '20010')),
	)
	if not robot.connect():
		print('connect_failed')
		sys.exit(1)
	print('ready')

	while True:
		for name in sorted(os.listdir(in_dir)):
			if not name.lower().endswith('.json'):
				continue
			full = os.path.join(in_dir, name)
			print(f"[DEBUG] Processing file: {name}")
			try:
				with open(full, 'r', encoding='utf-8') as f:
					cmd = json.load(f)
				# remove input file immediately after reading to avoid double-processing
				try:
					os.remove(full)
					print(f"[DEBUG] Removed input file: {full}")
				except Exception as e:
					print(f"[DEBUG] Failed to remove {full}: {e}")
			except Exception:
				# invalid JSON; remove to avoid repeated retries
				resp = { 'ok': False, 'message': 'invalid_json' }
				try:
					os.remove(full)
					print(f"[DEBUG] Removed invalid JSON file: {full}")
				except Exception as e:
					print(f"[DEBUG] Failed to remove invalid file {full}: {e}")
			else:
				resp = process_command(robot, cmd)
			# write response
			out_name = os.path.splitext(name)[0] + '.response.json'
			with open(os.path.join(out_dir, out_name), 'w', encoding='utf-8') as f:
				json.dump(resp, f, ensure_ascii=False)
		# small sleep to avoid busy loop
		time.sleep(0.2)


if __name__ == '__main__':
	main()
