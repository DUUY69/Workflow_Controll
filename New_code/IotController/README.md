IoT Controller (NEW)

Overview
- File-based service that mirrors OLD IoT behavior with config.env, serial connect, and simple HEX send.
- Watches inbox for JSON commands; writes responses to outbox.

Config
- Prefer .env_iot_config (new). Falls back to config.env if missing.
- Example .env_iot_config:
  Pump=COM7,115200
  Scale=COM5,9600
  DEVICES=Pump:COM7;Scale:COM5
  DEFAULT_BAUDRATE=115200
  DEFAULT_TIMEOUT=1.0
  INPUT_DIR=./inbox
  OUTPUT_DIR=./outbox
- Legacy config.env is supported with same keys.
  DEVICES=Pump:COM7;Scale:COM5
  DEFAULT_BAUDRATE=115200
  DEFAULT_TIMEOUT=1.0
  Pump=COM7,115200
  Scale=COM5,9600

Run
- From project root:
  python New_code/IotController/iot_service.py
- Wait for: iot_ready

Commands (JSON in inbox)
- Connect by device name:
  {"command":"connect","device":"Pump"}
- Connect by port/baud:
  {"command":"connect","port":"COM7","baud":115200}
- Send HEX:
  {"command":"send_hex","hex":"AA 01 00 FF"}
- Disconnect:
  {"command":"disconnect"}

Responses
- Written to outbox as <name>.response.json with fields { id, ok, message }.


