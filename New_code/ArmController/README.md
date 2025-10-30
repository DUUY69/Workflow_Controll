# Arm Controller - Robot Control Service

## Tổng quan
Arm Controller là một service đơn giản để điều khiển robot arm Fairino thông qua file JSON commands. Service này được thiết kế để dễ bảo trì và cập nhật, không có GUI hay console output phức tạp.

## Cấu trúc thư mục
```
New_code/ArmController/
├── arm_controller.py          # Main service script
├── .env_arm_config           # Configuration file
├── requirements.txt          # Python dependencies
├── inbox/                    # Input directory for JSON commands
├── outbox/                   # Output directory for responses
├── lua_scripts/              # Lua script files
└── TechPoint_db/             # TechPoint database files
```

## Cài đặt và chạy

### 1. Cài đặt dependencies
```bash
pip install -r requirements.txt
```

### 2. Cấu hình robot
Chỉnh sửa file `.env_arm_config`:
```
ROBOT_IP=192.168.1.100
ROBOT_PORT=8080
TCP_PORT=20010
INBOX_DIR=inbox
OUTBOX_DIR=outbox
```

### 3. Chạy service
```bash
python arm_controller.py
```

Service sẽ hiển thị `ready` khi kết nối robot thành công.

## Các chức năng chính

### 1. Kết nối Robot
- **SDK Priority**: Ưu tiên sử dụng `fairino_sdk/` trước, sau đó `fairino/`
- **XML-RPC Fallback**: Nếu SDK không có, sử dụng XML-RPC trực tiếp
- **Auto-detection**: Tự động phát hiện và sử dụng phương thức kết nối phù hợp

### 2. Upload Lua Scripts
**Command**: `upload_lua`
```json
{
  "id": "upload_lua_1",
  "type": "upload_lua", 
  "path": "MoveToMotor.lua"
}
```

**Chức năng**:
- Upload file Lua lên robot qua XML-RPC + TCP stream
- Tự động resolve đường dẫn trong thư mục `lua_scripts/`
- Sử dụng `FileUpload` + TCP port 20010 + `LuaUpLoadUpdate`

### 3. Chạy Lua Scripts
**Command**: `run_lua`
```json
{
  "id": "run_lua_1",
  "type": "run_lua",
  "file": "MoveToMotor.lua"
}
```

**Chức năng**:
- Load và chạy Lua script trên robot
- Sử dụng `ProgramLoad` + `ProgramRun`
- Chờ hoàn thành với timeout 8 giây
- Kiểm tra completion qua nhiều API: `CheckCommandFinish`, `GetRobotMotionState`, `GetProgramState`

### 4. Upload TechPoint Database
**Command**: `upload_tech_point`
```json
{
  "id": "upload_db_1", 
  "type": "upload_tech_point",
  "path": "web_point_2.db",
  "activate": true
}
```

**Chức năng**:
- Upload TechPoint database file
- Tự động resolve đường dẫn trong thư mục `TechPoint_db/`
- Sử dụng SDK methods: `PointTableUpLoad`, `PointTableUpload`, `PointTableUpdateLua`
- Optional activation với `PointTableSwitch`

## Cách sử dụng

### 1. Chuẩn bị files
- **Lua scripts**: Đặt trong thư mục `lua_scripts/`
- **TechPoint DB**: Đặt trong thư mục `TechPoint_db/`

### 2. Tạo JSON commands
Tạo file JSON trong thư mục `inbox/` với format:
```json
{
  "id": "unique_command_id",
  "type": "command_type",
  "path": "file_path",
  "activate": true/false
}
```

### 3. Chạy service
```bash
python arm_controller.py
```

### 4. Kiểm tra kết quả
- Service sẽ xử lý file JSON trong `inbox/`
- Ghi response vào `outbox/` với tên `filename.response.json`
- Xóa file input sau khi xử lý xong

## Ví dụ workflow hoàn chỉnh

### 1. Upload và chạy Lua script
```json
// inbox/step1_upload_lua.json
{
  "id": "upload_move_script",
  "type": "upload_lua",
  "path": "MoveToMotor.lua"
}

// inbox/step2_run_lua.json  
{
  "id": "run_move_script",
  "type": "run_lua", 
  "file": "MoveToMotor.lua"
}
```

### 2. Upload và activate TechPoint DB
```json
// inbox/step3_upload_db.json
{
  "id": "upload_points_db",
  "type": "upload_tech_point",
  "path": "web_point_2.db", 
  "activate": true
}
```

## Response format
Tất cả responses đều có format:
```json
{
  "id": "command_id",
  "ok": true/false,
  "message": "status_message"
}
```

**Status messages**:
- `"uploaded"` - Upload thành công
- `"completed"` - Chạy script hoàn thành
- `"failed"` - Thất bại
- `"invalid_json"` - JSON không hợp lệ

## Debug và troubleshooting

### Debug output
Service hiển thị debug messages với prefix `[DEBUG]`:
- Kết nối robot
- Upload progress
- Completion checking
- File processing

### Common issues
1. **Robot connection failed**: Kiểm tra IP và port trong `.env_arm_config`
2. **File not found**: Đảm bảo file tồn tại trong thư mục đúng
3. **Upload failed**: Kiểm tra robot có sẵn sàng và network connection

## Technical details

### Robot connection methods
1. **SDK fairino_sdk**: `from fairino_sdk import Robot`
2. **SDK fairino**: `from fairino import Robot` 
3. **XML-RPC**: `xmlrpc.client.ServerProxy`

### File upload protocols
- **Lua files**: XML-RPC `FileUpload` + TCP stream + `LuaUpLoadUpdate`
- **TechPoint DB**: SDK `PointTableUpLoad` + `PointTableUpload` + `PointTableUpdateLua`

### Completion detection
Service thử các method sau để detect completion:
1. `CheckCommandFinish()` - Method từ old code
2. `GetRobotMotionState()` - Motion state API
3. `GetProgramState()` - Program state API
4. Timeout fallback - Coi như hoàn thành sau 8 giây

## Maintenance và updates

### Thêm command mới
1. Thêm case trong `process_command()` function
2. Implement method trong `RobotClient` class
3. Update documentation

### Thay đổi cấu hình
- Chỉnh sửa `.env_arm_config` file
- Restart service

### Debug issues
- Kiểm tra debug output với prefix `[DEBUG]`
- Xem response files trong `outbox/`
- Kiểm tra robot connection status

---

**Version**: 1.0  
**Last updated**: 2025-10-29  
**Compatible with**: Fairino robot arms với SDK hoặc XML-RPC interface