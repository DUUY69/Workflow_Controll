import os
import sys
import json
import time
import threading
from typing import Any, Dict, Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
import socket
import xmlrpc.client
import hashlib
from pathlib import Path
from fastapi import UploadFile, File, Form
import subprocess
import shutil
from contextlib import asynccontextmanager


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
		# Mặc định gán chỉ số DO cho BUSY/DONE (có thể đổi qua cấu hình)
		self.DO_BUSY_INDEX = 0
		self.DO_DONE_INDEX = 1
		# Cache method RPC thiếu để không gọi lặp
		self._missing_rpc = set()
		# Trace state thread controls
		self._trace_thread = None
		self._trace_stop = threading.Event()
		self._trace_interval = 0.2

	def _log_state_once(self):
		"""Log one snapshot of state."""
		# Try realtime first
		ok, res = self._try_rpc('GetRobotRealTimeState')
		if ok and isinstance(res, (tuple, list)) and len(res) >= 2:
			try:
				err, pkg = int(res[0]), res[1]
				if err == 0 and pkg is not None:
					md_val = int(getattr(pkg, 'motion_done', 0))
					ql_val = int(getattr(pkg, 'mc_queue_len', -1))
					prog = int(getattr(pkg, 'program_state', 0))
					rbt = int(getattr(pkg, 'robot_state', 0))
					print(f"[TRACE] RT motion_done={md_val} queue={ql_val} program_state={prog} robot_state={rbt}")
					return
			except Exception:
				pass
		# Fallback motion done + queue length
		ok_md, res_md = self._try_rpc('GetRobotMotionDone')
		ok_ql, res_ql = self._try_rpc('GetMotionQueueLength')
		try:
			md = int(res_md[1]) if (ok_md and isinstance(res_md, (tuple, list)) and len(res_md) >= 2) else (int(res_md) if ok_md else -1)
			ql = int(res_ql[1]) if (ok_ql and isinstance(res_ql, (tuple, list)) and len(res_ql) >= 2) else (int(res_ql) if ok_ql else -1)
			print(f"[TRACE] MDQL motion_done={md} queue={ql}")
		except Exception as e:
			print(f"[TRACE] state error: {e}")

	def start_trace_state(self, interval_ms: int = 200):
		"""Start continuous state tracing logs."""
		self._trace_interval = max(10, int(interval_ms)) / 1000.0
		self.stop_trace_state()
		self._trace_stop.clear()
		def _runner():
			print(f"[TRACE] start state tracing every {self._trace_interval:.3f}s")
			while not self._trace_stop.is_set():
				self._log_state_once()
				time.sleep(self._trace_interval)
			print("[TRACE] stop state tracing")
		self._trace_thread = threading.Thread(target=_runner, daemon=True)
		self._trace_thread.start()

	def stop_trace_state(self):
		"""Stop state tracing if running."""
		try:
			self._trace_stop.set()
			if self._trace_thread and self._trace_thread.is_alive():
				self._trace_thread.join(timeout=0.1)
		except Exception:
			pass
		finally:
			self._trace_thread = None
		# Cache các RPC thiếu để không gọi lặp lại gây spam
		self._missing_rpc = set()

	def connect(self):
		print(f"[LOG] Bắt đầu kết nối tới robot tại IP {self.ip} (SDK/XMLRPC Port: {self.xmlrpc_port})")
		robot = None
		try:
			from fairino import Robot
			robot = Robot.RPC(self.ip)
			print(f"[LOG] Kết nối bằng fairino SDK (Robot.RPC) thành công.")
		except Exception as e:
			print(f"[LOG] Kết nối Robot.RPC thất bại: {e}. Fallback sang XML-RPC...")
			try:
				paths = ["/RPC2", "/RPC", "/"]
				for path in paths:
					url = f"http://{self.ip}:{self.xmlrpc_port}{path}"
					try:
						proxy = xmlrpc.client.ServerProxy(url)
						try:
							_ = proxy.GetControllerIP()
						except Exception:
							_ = proxy.GetLuaList()
						print(f"[LOG] Kết nối XML-RPC thành công tại URL {url}")
						robot = proxy
						break
					except Exception as e2:
						print(f"[LOG] Kết nối XML-RPC thất bại tại {url}: {e2}")
			except Exception as e3:
				print(f"[LOG] Kết nối XML-RPC thất bại hoàn toàn: {e3}")
		if robot is not None:
			print(f"[LOG] Kết nối tới robot thành công!")
		else:
			print(f"[LOG] Kết nối tới robot thất bại!")
		self.robot = robot
		return self.robot is not None

	def _is_xmlrpc(self) -> bool:
		return self.robot is not None and 'ServerProxy' in type(self.robot).__name__

	def _call(self, name: str, *args):
		func = getattr(self.robot, name, None)
		if callable(func):
			return func(*args)
		raise AttributeError(name)

	def _try_rpc(self, name: str, *args):
		"""Gọi RPC an toàn, nếu thiếu (-506) thì ghi nhớ để lần sau bỏ qua."""
		if name in self._missing_rpc:
			return False, None
		try:
			res = self._call(name, *args)
			return True, res
		except Exception as e:
			# Nếu lỗi -506 (method not defined), đánh dấu missing
			if "-506" in str(e) or "not defined" in str(e):
				self._missing_rpc.add(name)
			return False, e

	def set_do(self, index: int, value: int) -> bool:
		"""Set DO tại controller (không phải tool) nếu SDK hỗ trợ."""
		try:
			fn = getattr(self.robot, 'SetDO', None)
			if not callable(fn):
				return False
			res = fn(int(index), int(value), 0, 0)
			# nhiều API trả 0 khi thành công
			return (int(res[0]) == 0) if isinstance(res, (tuple, list)) else (int(res) == 0)
		except Exception:
			return False

	def get_do_state(self) -> dict:
		"""Đọc DO của controller, trả về dict gồm bitmask high/low và cờ busy/done."""
		result = {
			'ok': False,
			'do_state_h': None,
			'do_state_l': None,
			'busy': None,
			'done': None,
			'busy_index': self.DO_BUSY_INDEX,
			'done_index': self.DO_DONE_INDEX,
		}
		try:
			fn = getattr(self.robot, 'GetDO', None)
			if not callable(fn):
				return result
			res = fn()
			# theo SDK: (err, do_state_h, do_state_l)
			if isinstance(res, (tuple, list)) and len(res) >= 3:
				err = int(res[0])
				do_h = int(res[1])
				do_l = int(res[2])
				if err == 0:
					busy_bit = (do_l >> int(self.DO_BUSY_INDEX)) & 1 if self.DO_BUSY_INDEX < 8 else (do_h >> (self.DO_BUSY_INDEX - 8)) & 1
					done_bit = (do_l >> int(self.DO_DONE_INDEX)) & 1 if self.DO_DONE_INDEX < 8 else (do_h >> (self.DO_DONE_INDEX - 8)) & 1
					result.update({
						'ok': True,
						'do_state_h': do_h,
						'do_state_l': do_l,
						'busy': bool(busy_bit),
						'done': bool(done_bit),
					})
			return result
		except Exception:
			return result

	def wait_done_via_do(self, timeout_s: float = 30.0, poll_ms: int = 200, require_busy_first: bool = False) -> dict:
		"""Chờ đến khi DONE (done==1 và busy==0) dựa trên DO. Trả {ok, reason}."""
		# Nếu không đọc được DO ngay từ đầu -> báo lỗi nhanh để caller bỏ qua cơ chế này
		initial = self.get_do_state()
		if not initial.get('ok'):
			return { 'ok': False, 'reason': 'no_getdo', 'state': initial }
		start = time.time()
		seen_busy = False
		while True:
			state = self.get_do_state()
			if state.get('ok'):
				busy = bool(state.get('busy'))
				done = bool(state.get('done'))
				if require_busy_first:
					if busy:
						seen_busy = True
					# Chỉ kết thúc khi đã từng busy và hiện done && !busy
					if seen_busy and done and not busy:
						return { 'ok': True, 'reason': 'done_via_do', 'state': state }
				else:
					# Không yêu cầu bối cảnh busy
					if done and not busy:
						return { 'ok': True, 'reason': 'done_via_do', 'state': state }
			# timeout
			if timeout_s > 0 and (time.time() - start) > timeout_s:
				return { 'ok': False, 'reason': 'timeout', 'state': state }
			time.sleep(max(0.01, float(poll_ms) / 100.0 / 10.0))

	def run_lua_and_wait(self, lua_filename: str, timeout: float = 0) -> bool:
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
			# Pre-wait ngắn để tránh đọc nhầm frame idle ngay sau khi run
			try:
				self._prewait_enter_motion(max_wait_s=0.5)
			except Exception:
				pass
			print(f"[DEBUG] Starting wait for completion...")
			return self._wait_complete(timeout)
		except Exception as e:
			print(f"[DEBUG] run_lua_and_wait exception: {e}")
			return False

	def _wait_complete(self, timeout: float) -> bool:
		start = time.time()
		wait_forever = timeout <= 0
		last_missing_log_ts = 0.0
		seen_busy = False  # Chỉ cho phép hoàn thành sau khi đã từng thấy trạng thái bận

		# Pre-detect capability once (best-effort)
		try:
			get_rt = getattr(self.robot, 'GetRobotRealTimeState', None)
			get_md = getattr(self.robot, 'GetRobotMotionDone', None)
			get_ql = getattr(self.robot, 'GetMotionQueueLength', None)
			get_cf = getattr(self.robot, 'CheckCommandFinish', None)
			get_ms = getattr(self.robot, 'GetRobotMotionState', None)
			get_ps = getattr(self.robot, 'GetProgramState', None)
			detectors = [fn for fn in (get_rt, get_md and get_ql, get_cf, get_ms, get_ps) if callable(fn) or fn is not None]
			has_any_detector = len(detectors) > 0
		except Exception:
			has_any_detector = True  # be conservative

		# Nếu không có detector mà yêu cầu chờ vô hạn, vẫn chờ (không auto success)

		while True:
			# Timeout: nếu có timeout hữu hạn thì coi là không hoàn thành
			if not wait_forever and (time.time() - start > timeout):
				print(f"[DEBUG] Timeout after {timeout}s, not completed")
				return False

			try:
				# Method 0: Realtime state polling (motion_done && mc_queue_len == 0)
				# Prefer SDK realtime if available
				if callable(get_rt):
					ok, res = self._try_rpc('GetRobotRealTimeState')
					if ok and isinstance(res, (tuple, list)) and len(res) >= 2:
						err, pkg = res[0], res[1]
						if int(err) == 0 and pkg is not None:
							md_val = int(getattr(pkg, 'motion_done', 0))
							ql_val = int(getattr(pkg, 'mc_queue_len', 1))
							print(f"[DEBUG] RTState motion_done={md_val}, mc_queue_len={ql_val}")
							# Đánh dấu đã bận khi md==0 hoặc queue>0
							if (md_val == 0) or (ql_val > 0):
								seen_busy = True
							# Chỉ coi là hoàn thành khi đã từng bận và nay md==1 && queue==0
							if seen_busy and md_val == 1 and ql_val == 0:
								print(f"[DEBUG] Realtime state indicates completed")
								return True

				# Fallback: explicit motion done + queue length API
				if callable(get_md) and callable(get_ql):
					ok_md, res_md = self._try_rpc('GetRobotMotionDone')
					ok_ql, res_ql = self._try_rpc('GetMotionQueueLength')
					if ok_md and ok_ql:
						# expect (err, val)
						md_err, md_val = (int(res_md[0]), int(res_md[1])) if isinstance(res_md, (tuple, list)) and len(res_md) >= 2 else (0, int(res_md))
						ql_err, ql_val = (int(res_ql[0]), int(res_ql[1])) if isinstance(res_ql, (tuple, list)) and len(res_ql) >= 2 else (0, int(res_ql))
						md_ok = (int(md_err) == 0 and int(md_val) == 1) or (md_val in (1, True))
						ql_ok = (int(ql_err) == 0 and int(ql_val) == 0) or (ql_val in (0, False))
						print(f"[DEBUG] MotionDone={md_val} (err={md_err}), QueueLen={ql_val} (err={ql_err})")
						# Đánh dấu đã bận khi md==0 hoặc queue>0
						if (int(md_val) == 0) or (int(ql_val) > 0):
							seen_busy = True
						# Chỉ coi là hoàn thành khi đã từng bận và nay md==1 && queue==0
						if seen_busy and md_ok and ql_ok:
							print(f"[DEBUG] MotionDone+QueueLen: completed")
							return True

				# Bỏ các method phụ (ProgramState, CheckCommandFinish, ...), chỉ dựa vào RT/motion_done+queue
			except Exception as e:
				print(f"[DEBUG] Error in waiting loop: {e}")
			
			time.sleep(0.1)

	def _prewait_enter_motion(self, max_wait_s: float = 0.5) -> None:
		"""Đợi rất ngắn cho tới khi robot thật sự vào trạng thái bận để tránh snapshot idle.
		Điều kiện coi là đã bận: motion_done==0 hoặc mc_queue_len>0 hoặc program_state==2 hoặc robot_state==2.
		"""
		deadline = time.time() + max(0.0, float(max_wait_s))
		get_rt = getattr(self.robot, 'GetRobotRealTimeState', None)
		get_md = getattr(self.robot, 'GetRobotMotionDone', None)
		get_ql = getattr(self.robot, 'GetMotionQueueLength', None)
		get_ps = getattr(self.robot, 'GetProgramState', None)
		get_ms = getattr(self.robot, 'GetRobotMotionState', None)
		while time.time() < deadline:
			# Try realtime first
			try:
				if callable(get_rt):
					ok, res = self._try_rpc('GetRobotRealTimeState')
					if ok and isinstance(res, (tuple, list)) and len(res) >= 2:
						err, pkg = res[0], res[1]
						if int(err) == 0 and pkg is not None:
							md_val = int(getattr(pkg, 'motion_done', 1))
							ql_val = int(getattr(pkg, 'mc_queue_len', 0))
							prog = int(getattr(pkg, 'program_state', 0))
							rbt = int(getattr(pkg, 'robot_state', 0))
							if md_val == 0 or ql_val > 0 or prog == 2 or rbt == 2:
								return
				# Fallback MD/QL
				if callable(get_md) and callable(get_ql):
					ok_md, res_md = self._try_rpc('GetRobotMotionDone')
					ok_ql, res_ql = self._try_rpc('GetMotionQueueLength')
					if ok_md and ok_ql:
						md_val = int(res_md[1]) if (isinstance(res_md, (tuple, list)) and len(res_md) >= 2) else int(res_md)
						ql_val = int(res_ql[1]) if (isinstance(res_ql, (tuple, list)) and len(res_ql) >= 2) else int(res_ql)
						if md_val == 0 or ql_val > 0:
							return
				# Fallback Program/Motion state if available
				if callable(get_ps):
					ok_ps, res_ps = self._try_rpc('GetProgramState')
					if ok_ps:
						ps_val = int(res_ps[1]) if (isinstance(res_ps, (tuple, list)) and len(res_ps) >= 2) else int(res_ps)
						if ps_val == 2:
							return
				if callable(get_ms):
					ok_ms, res_ms = self._try_rpc('GetRobotMotionState')
					if ok_ms:
						ms_val = int(res_ms[1]) if (isinstance(res_ms, (tuple, list)) and len(res_ms) >= 2) else int(res_ms)
						if ms_val == 2:
							return
			except Exception:
				pass
			time.sleep(0.05)

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
		timeout = float(cmd.get('timeout', 0))  # Default to 0 (wait forever)
		# Luôn chờ đến khi dừng hẳn: bỏ các tuỳ chọn, cưỡng bức blocking
		if not file_:
			result['message'] = 'Missing file'
			return result
		# Luôn đợi tới khi dừng thật sự
		# 1) Đợi theo realtime/motion_done + queue (vô hạn)
		ok = robot.run_lua_and_wait(file_, 0)
		# 2) Dùng DO như xác nhận phụ (nếu khả dụng). Không bắt buộc DO.
		probe = robot.get_do_state()
		if ok and probe.get('ok'):
			do_wait = robot.wait_done_via_do(timeout_s=0, poll_ms=200, require_busy_first=True)
			ok = ok and bool(do_wait.get('ok'))
			result['do_wait'] = do_wait
		elif ok and not probe.get('ok'):
			# Ghi chú để biết DO không khả dụng, nhưng vẫn dựa realtime
			result['do_wait'] = { 'ok': False, 'reason': 'no_getdo', 'state': probe }
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


# ========== PATCH POOL ARM ==========
app = FastAPI(title="ArmController Combo")
_SHARED_POOL = None
_SHARED_CFG = None
_inbox_path = None
_outbox_path = None
_robot_client = None

# Khởi tạo toàn bộ config/pool ở STARTUP!!!
def on_startup_sync():
    global _SHARED_POOL, _SHARED_CFG, _robot_client, _inbox_path, _outbox_path
    base = os.path.dirname(__file__)
    cfg_path = os.path.join(base, '.env_arm_config')
    _SHARED_CFG = _load_env_file(cfg_path)
    _SHARED_CFG.setdefault('ROBOT_IP', '192.168.58.2')
    _SHARED_CFG.setdefault('XMLRPC_PORT', '20003')
    _SHARED_CFG.setdefault('TCP_UPLOAD_PORT', '20010')
    _SHARED_CFG.setdefault('INPUT_DIR', './inbox')
    _SHARED_CFG.setdefault('OUTPUT_DIR', './outbox')
    _inbox_path = os.path.abspath(os.path.join(base, _SHARED_CFG.get('INPUT_DIR', './inbox')))
    _outbox_path = os.path.abspath(os.path.join(base, _SHARED_CFG.get('OUTPUT_DIR', './outbox')))
    ensure_dirs(_inbox_path)
    ensure_dirs(_outbox_path)
    # Initialize robot client+pool duy nhất
    _robot_client = RobotClient(
        ip=_SHARED_CFG.get('ROBOT_IP', '192.168.58.2'),
        xmlrpc_port=int(_SHARED_CFG.get('XMLRPC_PORT', '20003')),
        tcp_port=int(_SHARED_CFG.get('TCP_UPLOAD_PORT', '20010')),
    )
    _robot_client.connect()
    print('arm_ready_combine')

@asynccontextmanager
async def lifespan(app):
    on_startup_sync()  # khởi tạo robot, pool, worker
    threading.Thread(target=arm_worker_file_loop, daemon=True).start()
    yield
app.router.lifespan_context = lifespan

# ========= BỔ SUNG/FIX: Đầy đủ endpoint từ server.py tích hợp luôn =========
APP_ROOT = Path(__file__).resolve().parent
LUA_DIR = APP_ROOT / "lua_scripts"
DB_DIR = APP_ROOT / "TechPoint_db"
ACTIVE_DB_NAME = "web_point.db"
LUA_DIR.mkdir(parents=True, exist_ok=True)
DB_DIR.mkdir(parents=True, exist_ok=True)

def find_lua_executable() -> Optional[str]:
    """Tìm kiếm file lua.exe trong các thư mục con của APP_ROOT."""
    for root, dirs, files in os.walk(APP_ROOT):
        for file in files:
            if file.lower() == "lua.exe":
                return os.path.join(root, file)
    return None

@app.post("/command")
async def handle_command(action: str = Form(...), file: Optional[str] = Form(None)):
    if action == "run_lua":
        if not file:
            raise HTTPException(status_code=400, detail="Missing 'file' for run_lua")
        lua_path = (LUA_DIR / file).resolve()
        if not str(lua_path).startswith(str(LUA_DIR.resolve())):
            raise HTTPException(status_code=400, detail="Invalid file path")
        if not lua_path.exists():
            raise HTTPException(status_code=404, detail=f"Lua file not found: {file}")
        lua_exe = find_lua_executable()
        if not lua_exe:
            return JSONResponse({
                "status": "done",
                "message": f"Arm completed {file} (simulated - no lua runtime)",
            })
        try:
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

@app.post("/robot/command")
async def handle_robot_command(request: Request):
    """
    Nhận lệnh JSON và gửi đến robot để thực thi.
    Đây là phiên bản HTTP của cơ chế file polling.
    """
    global _robot_client
    if not _robot_client or not _robot_client.robot:
        raise HTTPException(status_code=503, detail="Robot client not connected or initialized")
    
    try:
        command_payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Tái sử dụng logic xử lý lệnh hiện có
    result = process_command(_robot_client, command_payload)
    
    if result.get('ok'):
        return JSONResponse(content=result, status_code=200)
    else:
        # Nếu lỗi là do input không hợp lệ, trả về 400
        if result.get('message') in ['Missing file', 'Invalid path', 'Unknown command type', 'Missing path']:
            return JSONResponse(content=result, status_code=400)
        # Nếu lỗi là do robot thực thi thất bại, trả về 500
        else:
            return JSONResponse(content=result, status_code=500)

@app.get("/robot/do_state")
async def get_robot_do_state():
    """Trả trạng thái DO (bitmask high/low) và cờ busy/done theo chỉ số mặc định."""
    global _robot_client
    if not _robot_client or not _robot_client.robot:
        raise HTTPException(status_code=503, detail="Robot client not connected or initialized")
    state = _robot_client.get_do_state()
    if not state.get('ok'):
        return JSONResponse(content=state, status_code=500)
    return JSONResponse(content=state, status_code=200)

@app.post("/robot/wait_done")
async def wait_done_via_do(
    request: Request,
):
    """Block cho tới khi DONE qua DO (done==1 và busy==0), có timeout.
    JSON body (tuỳ chọn): { "timeout": 30.0, "poll_ms": 200, "require_busy_first": false }
    """
    global _robot_client
    if not _robot_client or not _robot_client.robot:
        raise HTTPException(status_code=503, detail="Robot client not connected or initialized")
    try:
        body = await request.json()
    except Exception:
        body = {}
    timeout = float(body.get('timeout', 30.0))
    poll_ms = int(body.get('poll_ms', 200))
    require_busy_first = bool(body.get('require_busy_first', False))
    result = _robot_client.wait_done_via_do(timeout_s=timeout, poll_ms=poll_ms, require_busy_first=require_busy_first)
    status = 200 if result.get('ok') else 408
    return JSONResponse(content=result, status_code=status)

@app.post("/robot/trace_state/start")
async def start_trace_state(request: Request):
    """Bật in log trạng thái liên tục (console). Body: { "interval_ms": 200 }"""
    global _robot_client
    if not _robot_client or not _robot_client.robot:
        raise HTTPException(status_code=503, detail="Robot client not connected or initialized")
    try:
        body = await request.json()
    except Exception:
        body = {}
    interval_ms = int(body.get('interval_ms', 200))
    _robot_client.start_trace_state(interval_ms=interval_ms)
    return {"ok": True, "interval_ms": interval_ms}

@app.post("/robot/trace_state/stop")
async def stop_trace_state():
    """Tắt log trạng thái liên tục."""
    global _robot_client
    if not _robot_client:
        return {"ok": True}
    _robot_client.stop_trace_state()
    return {"ok": True}

@app.post("/upload/lua")
async def upload_lua(file: UploadFile = File(...)):
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
        temp_path = DB_DIR / (ACTIVE_DB_NAME + ".tmp")
        shutil.copy2(dst, temp_path)
        os.replace(temp_path, active_path)
    return {
        "status": "ok",
        "stored": str(dst.relative_to(APP_ROOT)),
        "active": str(active_path.relative_to(APP_ROOT)) if activate else None,
    }

# Thêm lại health endpoint nếu cần
@app.get("/health")
async def health():
    return {"status": "ok"}

def arm_worker_file_loop():
    global _robot_client, _SHARED_CFG, _inbox_path, _outbox_path
    while True:
        if not (_robot_client and _SHARED_CFG and _inbox_path and _outbox_path):
            time.sleep(0.2)
            continue
        for name in sorted(os.listdir(_inbox_path)):
            if not name.lower().endswith('.json'):
                continue
            full = os.path.join(_inbox_path, name)
            try:
                with open(full, 'r', encoding='utf-8') as f:
                    cmd = json.load(f)
                # remove input file immediately after reading
                try:
                    os.remove(full)
                except Exception:
                    pass
            except Exception:
                resp = { 'ok': False, 'message': 'invalid_json' }
            else:
                resp = process_command(_robot_client, cmd)
            out_name = os.path.splitext(name)[0] + '.response.json'
            with open(os.path.join(_outbox_path, out_name), 'w', encoding='utf-8') as f:
                json.dump(resp, f, ensure_ascii=False)
        time.sleep(0.2)

if __name__ == "__main__":
    uvicorn.run("arm_controller:app", host="0.0.0.0", port=8001, reload=False)
