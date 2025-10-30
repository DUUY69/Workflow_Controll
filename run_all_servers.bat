@echo off
REM Khoi dong ArmController tren port 8001
start "ArmController" cmd /k "cd /d D:\2025\Arm-28-10\New_code\New_code\ArmController && python arm_controller.py"
timeout /t 2

REM Khoi dong IotController mới (duy nhất, gộp hết vào iot_service.py)
start "IotController" cmd /k "cd /d D:\2025\Arm-28-10\New_code\New_code\IotController && python iot_service.py"
timeout /t 2

REM Khoi dong WorkFlowController
start "WorkFlowController" cmd /k "cd /d D:\2025\Arm-28-10\New_code\New_code\WorkFlowController && python workflow_service.py"

echo === Tat cua so nay la xong ===
pause
