# Hướng dẫn sử dụng IoT Controller (NEW)

Tối giản, chạy bằng file lệnh JSON. Dùng env riêng `.env_iot_config` để cấu hình COM/baud và thư mục I/O.

## 1) Cấu trúc thư mục
- New_code/IotController/iot_service.py: Service chính (file-based)
- New_code/IotController/.env_iot_config: File cấu hình env cho IoT
- New_code/IotController/inbox/: Nơi đặt lệnh JSON
- New_code/IotController/outbox/: Nơi nhận phản hồi JSON

## 2) Cấu hình `.env_iot_config`
Ví dụ:
```
# Thiết bị: NAME=COM,BAUD
Pump=COM7,115200
Scale=COM5,9600
IceMake=COM19,125200

# Danh sách (tuỳ chọn)
DEVICES=Pump:COM7;Scale:COM5;IceMake:COM19

# Mặc định
DEFAULT_BAUDRATE=115200
DEFAULT_TIMEOUT=1.0

# Thư mục I/O (tương đối so với thư mục này)
INPUT_DIR=./inbox
OUTPUT_DIR=./outbox
```

## 3) Chạy service
- Mở PowerShell ở thư mục gốc dự án: `D:\2025\Arm-29-10`
- Chạy: `python New_code/IotController/iot_service.py`
- Khi thấy: `iot_ready` là sẵn sàng nhận lệnh

## 4) Gửi lệnh (đặt file JSON vào `inbox/`)
- Kết nối theo tên thiết bị (đã khai báo trong `.env_iot_config`):
```
{"command":"connect","device":"IceMake"}
```
- Kết nối theo cổng/baud trực tiếp:
```
{"command":"connect","port":"COM19","baud":125200}
```
- Gửi chuỗi HEX (tối giản):
```
{"command":"send_hex","hex":"04 07 AA 01 05 BB FF"}
```
- Gửi và đọc phản hồi (đọc N byte):
```
{"command":"send_hex","hex":"04 07 AA 02 05 BC FF","read_len":16,"flush":true}
```
- Gửi và đọc đến khi gặp mẫu HEX (ví dụ kết thúc `FF`):
```
{"command":"send_hex","hex":"AA 55 01 02","read_until":"FF","flush":true}
```
- Ngắt kết nối:
```
{"command":"disconnect"}
```

## 5) Kết quả (outbox)
- Service tự tạo file `<ten_lenh>.response.json` trong `outbox/`, ví dụ:
```
{"id": null, "ok": true, "message": "sent", "received": "04 07 AA 02 01 B8 FF"}
```
- Trường `received` có thể có/không tùy tham số đọc (`read_len`/`read_until`).

## 6) Lỗi thường gặp
- "failed" khi gửi: kiểm tra cổng COM có đang bận bởi app khác, đúng `device`/`port`, đúng `baud`.
- Không có `received`: thiết bị không trả, hoặc cần tăng `read_len`/đổi `read_until`.
- Không thấy phản hồi file: đảm bảo service đang chạy và đường dẫn `INPUT_DIR`/`OUTPUT_DIR` đúng trong `.env_iot_config`.

## 7) Gợi ý quay video demo
1. Chạy service (`iot_ready`).
2. Đặt lệnh connect IceMake.
3. Đặt lệnh gửi HEX + `read_len` để thấy phản hồi trong outbox.
