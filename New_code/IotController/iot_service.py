import os
import sys
import json
import time
from typing import Any, Dict
import binascii

from serial.tools import list_ports  # type: ignore
import serial  # type: ignore


def load_env_file(path: str) -> Dict[str, str]:
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


class IoTSerial:
    def __init__(self) -> None:
        self.ser: serial.Serial | None = None

    def open(self, port: str, baudrate: int = 115200, timeout: float = 1.0) -> bool:
        try:
            if self.ser and self.ser.is_open:
                try:
                    self.ser.close()
                except Exception:
                    pass
            self.ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout,
                                     bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE)
            return bool(self.ser and self.ser.is_open)
        except Exception:
            return False

    def is_open(self) -> bool:
        return bool(self.ser and self.ser.is_open)

    def close(self) -> None:
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass

    def send_hex(self, hex_string: str) -> bool:
        if not self.is_open():
            return False
        cleaned = hex_string.replace(' ', '').replace('0x', '').replace('-', '').replace('_', '')
        if len(cleaned) % 2 == 1:
            cleaned = '0' + cleaned
        try:
            data = bytes.fromhex(cleaned)
            self.ser.write(data)  # type: ignore[union-attr]
            self.ser.flush()  # type: ignore[union-attr]
            return True
        except Exception:
            return False

    def read_bytes(self, num_bytes: int = 1) -> bytes:
        if not self.is_open():
            return b""
        try:
            return self.ser.read(num_bytes)  # type: ignore[union-attr]
        except Exception:
            return b""

    def read_until_hex(self, hex_pattern: str, max_bytes: int = 4096) -> bytes:
        if not self.is_open():
            return b""
        pattern_clean = hex_pattern.replace(' ', '').replace('0x', '').replace('-', '').replace('_', '')
        try:
            pattern = bytes.fromhex(pattern_clean) if pattern_clean else b""
        except binascii.Error:
            pattern = b""
        if not pattern:
            return b""
        buf = bytearray()
        try:
            while len(buf) < max_bytes:
                b = self.ser.read(1)  # type: ignore[union-attr]
                if not b:
                    break
                buf += b
                if buf.endswith(pattern):
                    break
        except Exception:
            pass
        return bytes(buf)


def parse_devices_from_config_env(config_path: str) -> Dict[str, Dict[str, str]]:
    devices: Dict[str, Dict[str, str]] = {}
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                if ',' in value:
                    com, baud = value.split(',', 1)
                    devices[key.strip()] = { 'com': com.strip(), 'baud': baud.strip() }
    return devices


def _bytes_to_hex(data: bytes) -> str:
    if not data:
        return ""
    return ' '.join(f"{b:02X}" for b in data)


def _resolve_port_and_baud(cmd: Dict[str, Any], devices: Dict[str, Dict[str, str]], defaults: Dict[str, Any]) -> tuple[str | None, int]:
    """Resolve target serial port and baudrate from command and devices map."""
    port = cmd.get('port')
    baud = cmd.get('baud')
    device = cmd.get('device')
    if device and not port:
        info = devices.get(str(device), {})
        port = info.get('com')
        baud = baud or info.get('baud')
    if not port:
        return None, int(defaults.get('DEFAULT_BAUDRATE', 115200))
    baudrate = int(baud or defaults.get('DEFAULT_BAUDRATE', 115200))
    return str(port), baudrate


def process_command(iot_pool: Dict[str, IoTSerial], devices: Dict[str, Dict[str, str]], defaults: Dict[str, Any], cmd: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = { 'id': cmd.get('id'), 'ok': False }
    type_raw = cmd.get('type') if cmd.get('type') is not None else cmd.get('command', '')
    type_ = str(type_raw).strip().lower()

    if type_ == 'connect':
        # Supports multi-device: { command: 'connect', device: 'IceMake' } or { port: 'COM7', baud: 115200 }
        port, baudrate = _resolve_port_and_baud(cmd, devices, defaults)
        if not port:
            result['message'] = 'Missing port/device'
            return result
        timeout = float(defaults.get('DEFAULT_TIMEOUT', 1.0))
        key = str(cmd.get('device') or port)
        if key not in iot_pool:
            iot_pool[key] = IoTSerial()
        ok = iot_pool[key].open(port=str(port), baudrate=baudrate, timeout=timeout)
        result['ok'] = bool(ok)
        result['message'] = 'connected' if ok else 'failed'
        return result

    if type_ == 'disconnect':
        # Target a specific device/port; if none provided, disconnect all
        target_key = str(cmd.get('device') or cmd.get('port') or '')
        if target_key:
            i = iot_pool.get(target_key)
            if i:
                i.close()
                del iot_pool[target_key]
            result['ok'] = True
            result['message'] = 'disconnected'
        else:
            for key, i in list(iot_pool.items()):
                try:
                    i.close()
                finally:
                    del iot_pool[key]
            result['ok'] = True
            result['message'] = 'disconnected_all'
        return result

    if type_ == 'send_hex':
        hex_string = cmd.get('hex') or cmd.get('data')
        if not hex_string:
            result['message'] = 'Missing hex'
            return result
        # Route to specific device/port; auto-connect if not present but resolvable
        target_key = str(cmd.get('device') or cmd.get('port') or '')
        if not target_key:
            result['message'] = 'Missing device/port'
            return result
        iot = iot_pool.get(target_key)
        if not iot or not iot.is_open():
            port, baudrate = _resolve_port_and_baud(cmd, devices, defaults)
            if not port:
                result['message'] = 'Missing port/device'
                return result
            iot = iot or IoTSerial()
            ok_open = iot.open(port=port, baudrate=baudrate, timeout=float(defaults.get('DEFAULT_TIMEOUT', 1.0)))
            if not ok_open:
                result['message'] = 'failed'
                return result
            iot_pool[target_key] = iot
        # Optional: flush any pending input before sending
        if cmd.get('flush', False) and iot.is_open():
            try:
                iot.ser.reset_input_buffer()  # type: ignore[union-attr]
            except Exception:
                pass
        ok = iot.send_hex(str(hex_string))
        result['ok'] = bool(ok)
        result['message'] = 'sent' if ok else 'failed'
        # Optional read back
        if ok and iot.is_open():
            read_len = cmd.get('read_len')
            read_until = cmd.get('read_until')
            received: bytes = b""
            if isinstance(read_len, int) and read_len > 0:
                received = iot.read_bytes(read_len)
            elif isinstance(read_until, str) and read_until:
                max_bytes = int(cmd.get('max_bytes', 4096))
                received = iot.read_until_hex(read_until, max_bytes=max_bytes)
            if received:
                result['received'] = _bytes_to_hex(received)
        return result

    result['message'] = 'Unknown command'
    return result


def ensure_dirs(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def main() -> None:
    base = os.path.dirname(__file__)
    # Prefer dedicated IoT env, fallback to config.env
    preferred_env = os.path.join(base, '.env_iot_config')
    fallback_env = os.path.join(base, 'config.env')
    env_path = preferred_env if os.path.exists(preferred_env) else fallback_env
    env = load_env_file(env_path)
    # Defaults
    env.setdefault('INPUT_DIR', './inbox')
    env.setdefault('OUTPUT_DIR', './outbox')
    env.setdefault('DEFAULT_BAUDRATE', '115200')
    env.setdefault('DEFAULT_TIMEOUT', '1.0')

    in_dir = os.path.abspath(os.path.join(base, env.get('INPUT_DIR', './inbox')))
    out_dir = os.path.abspath(os.path.join(base, env.get('OUTPUT_DIR', './outbox')))
    ensure_dirs(in_dir)
    ensure_dirs(out_dir)

    devices = parse_devices_from_config_env(env_path)
    # Multi-device connection pool: key by device name or port string
    iot_pool: Dict[str, IoTSerial] = {}
    print('iot_ready')

    while True:
        for name in sorted(os.listdir(in_dir)):
            if not name.lower().endswith('.json'):
                continue
            full = os.path.join(in_dir, name)
            try:
                with open(full, 'r', encoding='utf-8') as f:
                    cmd = json.load(f)
                # remove file immediately after read
                try:
                    os.remove(full)
                except Exception:
                    pass
            except Exception:
                resp = { 'ok': False, 'message': 'invalid_json' }
            else:
                resp = process_command(iot_pool, devices, env, cmd)
            out_name = os.path.splitext(name)[0] + '.response.json'
            with open(os.path.join(out_dir, out_name), 'w', encoding='utf-8') as f:
                json.dump(resp, f, ensure_ascii=False)
        time.sleep(0.2)


if __name__ == '__main__':
    main()


