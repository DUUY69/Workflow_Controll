import xmlrpc.client
import os
import socket
import hashlib
import time
from datetime import datetime
import logging
from functools import wraps
from logging.handlers import RotatingFileHandler
from queue import Queue
import threading
import struct
import sys
import ctypes
from ctypes import *

# from Cython.Compiler.Options import error_on_unknown_names

is_init =False
class ROBOT_AUX_STATE(Structure):
    _pack_ = 1
    _fields_ = [
        ("servoId", c_uint8),         # 伺服驱动器ID号
        ("servoErrCode", c_int),     # 伺服驱动器故障码
        ("servoState", c_int),       # 伺服驱动器状态
        ("servoPos", c_double),      # 伺服当前位置
        ("servoVel", c_float),       # 伺服当前速度
        ("servoTorque", c_float),    # 伺服当前转矩
    ]

class EXT_AXIS_STATUS(Structure):
    _pack_ = 1
    _fields_ = [
        ("pos", c_double),        # 扩展轴位置
        ("vel", c_double),        # 扩展轴速度
        ("errorCode", c_int),     # 扩展轴故障码
        ("ready", c_uint8),        # 伺服准备好
        ("inPos", c_uint8),        # 伺服到位
        ("alarm", c_uint8),        # 伺服报警
        ("flerr", c_uint8),        # 跟随误差
        ("nlimit", c_uint8),       # 到负限位
        ("pLimit", c_uint8),       # 到正限位
        ("mdbsOffLine", c_uint8),  # 驱动器485总线掉线
        ("mdbsTimeout", c_uint8),  # 控制卡与控制箱485通信超时
        ("homingStatus", c_uint8), # 扩展轴回零状态
    ]

class WELDING_BREAKOFF_STATE(Structure):
    _pack_ = 1
    _fields_ = [
        ("breakOffState", ctypes.c_uint8),        # 焊接中断状态
        ("weldArcState", ctypes.c_uint8),        # 焊接电弧中断状态
    ]

"""   
@brief  机器人状态反馈数据包
"""
class RobotStatePkg(Structure):
    _pack_ = 1
    _fields_ = [
        ("frame_head", ctypes.c_uint16),      # 帧头 0x5A5A
        ("frame_cnt", ctypes.c_uint8),         # 帧计数
        ("data_len", ctypes.c_uint16),        # 数据长度
        ("program_state", ctypes.c_uint8),     # 程序运行状态，1-停止；2-运行；3-暂停
        ("robot_state", ctypes.c_uint8),       # 机器人运动状态，1-停止；2-运行；3-暂停；4-拖动
        ("main_code", ctypes.c_int),          # 主故障码
        ("sub_code", ctypes.c_int),           # 子故障码
        ("robot_mode", ctypes.c_uint8),        # 机器人模式，0-自动模式；1-手动模式
        ("jt_cur_pos", ctypes.c_double * 6),  # 机器人当前关节位置，假设有6个关节
        ("tl_cur_pos", ctypes.c_double * 6),  # 工具当前位姿
        ("flange_cur_pos", ctypes.c_double * 6),  # 末端法兰当前位姿
        ("actual_qd", ctypes.c_double * 6),  # 机器人当前关节速度
        ("actual_qdd", ctypes.c_double * 6),  # 机器人当前关节加速度
        ("target_TCP_CmpSpeed", ctypes.c_double * 2),  # 机器人TCP合成指令速度
        ("target_TCP_Speed", ctypes.c_double * 6),  # 机器人TCP指令速度
        ("actual_TCP_CmpSpeed", ctypes.c_double * 2),  # 机器人TCP合成实际速度
        ("actual_TCP_Speed", ctypes.c_double * 6),  # 机器人TCP实际速度
        ("jt_cur_tor", ctypes.c_double * 6),  # 当前扭矩
        ("tool", ctypes.c_int),  # 工具号
        ("user", ctypes.c_int),  # 工件号
        ("cl_dgt_output_h", ctypes.c_uint8),  # 数字输出15-8
        ("cl_dgt_output_l", ctypes.c_uint8),  # 数字输出7-0
        ("tl_dgt_output_l", ctypes.c_uint8),  # 工具数字输出7-0(仅bit0-bit1有效)
        ("cl_dgt_input_h", ctypes.c_uint8),  # 数字输入15-8
        ("cl_dgt_input_l", ctypes.c_uint8),  # 数字输入7-0
        ("tl_dgt_input_l", ctypes.c_uint8),  # 工具数字输入7-0(仅bit0-bit1有效)
        ("cl_analog_input", ctypes.c_uint16 * 2),  # 控制箱模拟量输入
        ("tl_anglog_input", ctypes.c_uint16),  # 工具模拟量输入
        ("ft_sensor_raw_data", ctypes.c_double * 6),  # 力/扭矩传感器原始数据
        ("ft_sensor_data", ctypes.c_double * 6),  # 力/扭矩传感器数据
        ("ft_sensor_active", ctypes.c_uint8),  # 力/扭矩传感器激活状态， 0-复位，1-激活
        ("EmergencyStop", ctypes.c_uint8),  # 急停标志
        ("motion_done", ctypes.c_int),  # 到位信号
        ("gripper_motiondone", ctypes.c_uint8),  # 夹爪运动完成信号
        ("mc_queue_len", ctypes.c_int),  # 运动队列长度
        ("collisionState", ctypes.c_uint8),  # 碰撞检测，1-碰撞；0-无碰撞
        ("trajectory_pnum", ctypes.c_int),  # 轨迹点编号
        ("safety_stop0_state", ctypes.c_uint8),  # 安全停止信号SI0
        ("safety_stop1_state", ctypes.c_uint8),  # 安全停止信号SI1
        ("gripper_fault_id", ctypes.c_uint8),  # 错误夹爪号
        ("gripper_fault", ctypes.c_uint16),  # 夹爪故障
        ("gripper_active", ctypes.c_uint16),  # 夹爪激活状态
        ("gripper_position", ctypes.c_uint8),  # 夹爪位置
        ("gripper_speed", ctypes.c_int8),  # 夹爪速度
        ("gripper_current", ctypes.c_int8),  # 夹爪电流
        ("gripper_tmp", ctypes.c_int),  # 夹爪温度
        ("gripper_voltage", ctypes.c_int),  # 夹爪电压
        ("auxState", ROBOT_AUX_STATE),  # 485扩展轴状态
        ("extAxisStatus", EXT_AXIS_STATUS*4),  # UDP扩展轴状态
        ("extDIState", ctypes.c_uint16*8),  # 扩展DI输入
        ("extDOState", ctypes.c_uint16*8),  # 扩展DO输出
        ("extAIState", ctypes.c_uint16*4),  # 扩展AI输入
        ("extAOState", ctypes.c_uint16*4),  # 扩展AO输出
        ("rbtEnableState", ctypes.c_int),  # 机器人使能状态
        ("jointDriverTorque", ctypes.c_double * 6),  # 关节驱动器当前扭矩
        ("jointDriverTemperature", ctypes.c_double * 6),  # 关节驱动器当前温度
        ("year", ctypes.c_uint16),  # 年
        ("mouth", ctypes.c_uint8),  # 月
        ("day", ctypes.c_uint8),  # 日
        ("hour", ctypes.c_uint8),  # 小时
        ("minute", ctypes.c_uint8),  # 分
        ("second", ctypes.c_uint8),  # 秒
        ("millisecond", ctypes.c_uint16),  # 毫秒
        ("softwareUpgradeState", ctypes.c_int),  # 机器人软件升级状态
        ("endLuaErrCode", ctypes.c_uint16),  # 末端LUA运行状态
        ("cl_analog_output", ctypes.c_uint16 * 2),  # 控制箱模拟量输出
        ("tl_analog_output", ctypes.c_uint16),  # 工具模拟量输出
        ("gripperRotNum", ctypes.c_float),  # 旋转夹爪当前旋转圈数
        ("gripperRotSpeed", ctypes.c_uint8),  # 旋转夹爪当前旋转速度百分比
        ("gripperRotTorque", ctypes.c_uint8),  # 旋转夹爪当前旋转力矩百分比
        ("weldingBreakOffState", WELDING_BREAKOFF_STATE), # 焊接中断状态
        ("jt_tgt_tor", ctypes.c_double * 6),  # 关节指令力矩
        ("smartToolState", ctypes.c_int),  # SmartTool手柄按钮状态
        ("wideVoltageCtrlBoxTemp", ctypes.c_float),  # 宽电压控制箱温度
        ("wideVoltageCtrlBoxFanCurrent", ctypes.c_uint16),  # 宽电压控制箱风扇电流(ma)
        ("toolCoord", ctypes.c_double * 6),  # 工具坐标系                                                                2025.09.17---3.8.6
        ("wobjCoord", ctypes.c_double * 6),  # 工件坐标系
        ("extoolCoord", ctypes.c_double * 6),  # 外部工具坐标系
        ("exAxisCoord", ctypes.c_double * 6),  # 扩展轴坐标系
        ("load", ctypes.c_double),  # 负载质量
        ("loadCog", ctypes.c_double * 3),  # 负载质心
        ("lastServoTarget", ctypes.c_double * 6),  # 队列中最后一个ServoJ目标位置                                          2025.10.15---3.8.7
        ("servoJCmdNum", ctypes.c_int),  # ServoJ指令计数
        ("check_sum", ctypes.c_uint16)]  # 校验和


class BufferedFileHandler(RotatingFileHandler):
    def __init__(self, filename, mode='a', maxBytes=0, backupCount=0, encoding=None, delay=False):
        super().__init__(filename, mode, maxBytes, backupCount, encoding, delay)
        self.buffer = []

    def emit(self, record):
        # log_entry = self.format(record)  # 格式化日志记录
        # print(log_entry)  # 打印日志条目
        if RPC.log_output_model == 2:
            RPC.queue.put(record)
        else:
            self.buffer.append(record)
            if len(self.buffer) >= 50:
                for r in self.buffer:
                    super().emit(r)
                self.buffer = []


class LogWriterThread(threading.Thread):
    def __init__(self, queue, log_handler):
        super().__init__()
        self.queue = queue
        self.log_handler = log_handler
        self.daemon = True

    def run(self):
        while True:
            record = self.queue.get()
            if record is None:
                break
            log_entry = self.log_handler.format(record)
            self.log_handler.stream.write(log_entry + self.log_handler.terminator)
            self.log_handler.flush()


def calculate_file_md5(file_path):
    if not os.path.exists(file_path):
        raise ValueError(f"{file_path} 不存在")
    md5 = hashlib.md5()
    with open(file_path, 'rb') as file:
        while chunk := file.read(8192):  # Read in 8KB chunks
            md5.update(chunk)
    return md5.hexdigest()


def xmlrpc_timeout(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if RPC.is_conect == False:
            return -4
        else:
            result = func(self, *args, **kwargs)
            return result

    return wrapper


class RobotError:
    ERR_SUCCESS = 0
    ERR_POINTTABLE_NOTFOUND = -7  # 上传文件不存在
    # ERR_SAVE_FILE_PATH_NOT_FOUND = -6  # 保存文件路径不存在
    ERR_NOT_FOUND_LUA_FILE = -5  # lua文件不存在
    ERR_RPC_ERROR = -4
    ERR_SOCKET_COM_FAILED = -2
    ERR_OTHER = -1
    ERROR_RECONN = -8
    ERR_SOCKET_RECV_FAILED=-16    #/* socket接收失败 */
    ERR_SOCKET_SEND_FAILED=-15    #/* socket发送失败 */
    ERR_FILE_OPEN_FAILED=-14    #/* 文件打开失败 */
    ERR_FILE_TOO_LARGE=-13    #/* 文件大小超限 */
    ERR_UPLOAD_FILE_ERROR=-12    #/* 上传文件异常 */
    ERR_FILE_NAME=-11    #/* 文件名称异常 */
    ERR_DOWN_LOAD_FILE_WRITE_FAILED=-10    #/* 下载文件写入失败 */
    ERR_DOWN_LOAD_FILE_CHECK_FAILED=-9     #/* 文件下载校验失败 */
    ERR_DOWN_LOAD_FILE_FAILED=-8     #/* 文件下载失败 */
    ERR_UPLOAD_FILE_NOT_FOUND=-7     #/* 上传文件存在 */
    ERR_SAVE_FILE_PATH_NOT_FOUND=-6     #/* 保存文件路径不存在 */


class RPC():
    ip_address = "192.168.58.2"

    logger = None
    log_output_model = -1
    queue = Queue(maxsize=10000 * 1024)
    logging_thread = None
    is_conect = True
    ROBOT_REALTIME_PORT = 20004
    # BUFFER_SIZE = 1024 * 2
    BUFFER_SIZE = 1024 * 1024
    thread=  threading.Thread()
    SDK_state=True

    sock_cli_state_state = False
    closeRPC_state = False
    reconnect_lock = False
    reconnect_flag = False
    g_sock_com_err = RobotError.ERROR_RECONN


    def __init__(self, ip="192.168.58.2"):
        self.lock = threading.Lock()  # 增加锁
        self.ip_address = ip
        link = 'http://' + self.ip_address + ":20003"
        self.robot = xmlrpc.client.ServerProxy(link)#xmlrpc连接机器人20003端口，用于发送机器人指令数据帧

        self.sock_cli_state = None
        self.robot_realstate_exit = False
        self.robot_state_pkg = RobotStatePkg#机器人状态数据

        self.stop_event = threading.Event()  # 停止事件
        self.connect_to_robot()
        thread= threading.Thread(target=self.robot_state_routine_thread)#创建线程循环接收机器人状态数据
        thread.daemon = True
        thread.start()
        time.sleep(1)
        print(self.robot)


        try:
            # 调用 XML-RPC 方法
            socket.setdefaulttimeout(1)
            self.robot.GetControllerIP()
        except socket.timeout:
            print("XML-RPC connection timed out.")
            RPC.is_conect = False

        except socket.error as e:
            print("可能是网络故障，请检查网络连接。")
            RPC.is_conect = False
        except Exception as e:
            print("An error occurred during XML-RPC call:", e)
            RPC.is_conect = False
        finally:
            # 恢复默认超时时间
            self.robot = None
            socket.setdefaulttimeout(None)
            self.robot = xmlrpc.client.ServerProxy(link)

    def connect_to_robot(self):
        """连接到机器人的实时端口"""
        # print("SDK连接机器人")
        self.sock_cli_state = socket.socket(socket.AF_INET, socket.SOCK_STREAM)#套接字连接机器人20004端口，用于实时更新机器人状态数据
        self.sock_cli_state.settimeout(0.3)  # 设置超时时间为 0.05 秒
        try:
            self.sock_cli_state.connect((self.ip_address, self.ROBOT_REALTIME_PORT))
            self.sock_cli_state_state = True
        except socket.timeout:
            # print("连接超时，请检查网络连接。")
            self.sock_cli_state_state = False
            return False
        except Exception as ex:
            self.sock_cli_state_state = False
            print("SDK连接机器人实时端口失败", ex)
            return False
        return True

    def reconnect(self):
        """自动重连"""
        max_retries = 1000
        retry_interval = 2  # 2秒
        # with self.lock:  # 加锁
        # RPC.is_conect = False
        # print("断联")
        self.reconnect_flag = True
        for attempt in range(max_retries):
            # print(f"尝试重新连接，第 {attempt + 1} 次")
            # print(f"尝试重新连接")
            # 确保 self.sock_cli_state 是新的 socket 对象
            if self.sock_cli_state:
                self.sock_cli_state.close()  # 关闭旧的 socket
                self.sock_cli_state = None  # 重置为 None
            # 重新初始化 XML-RPC 连接
            # self.robot = None
            # link = 'http://' + self.ip_address + ":20003"
            # self.robot = xmlrpc.client.ServerProxy(link)
            # 尝试连接

            if self.connect_to_robot():
                # print("重新连接成功")
                self.SDK_state = True
                self.reconnect_flag = False
                return True
                # 验证 XML-RPC 连接
                # try:
                #     time.sleep(1)
                #     self.Mode(0)  # 调用一个简单的 XML-RPC 方法
                #     time.sleep(1)
                #     self.Mode(1)  # 调用一个简单的 XML-RPC 方法
                #     time.sleep(1)
                #     # self.robot.Mode(0)  # 调用一个简单的 XML-RPC 方法
                #     # time.sleep(1)
                #     # self.robot.Mode(1)  # 调用一个简单的 XML-RPC 方法
                #     # time.sleep(1)
                #     # self.Mode(0)  # 调用一个简单的 XML-RPC 方法
                #     print("XML-RPC 连接验证成功")
                #     self.reconnect_flag = False
                #     # RPC.is_conect = True
                #     return True
                # except Exception as ex:
                #     print("XML-RPC 连接验证失败:", ex)
                #     self.SDK_state = False
            else:
                # print(f"重新连接失败，等待 {retry_interval} 秒后重试...")
                time.sleep(retry_interval)

        print("已达到最大重连次数，连接失败")
        self.SDK_state = False
        return False
        #
        # print("自动重连机制")
        # for i in range(1,6):
        #     print("---")
        #     time.sleep(2)
        #     try:
        #         self.sock_cli_state = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        #         self.sock_cli_state.connect((self.ip_address, self.ROBOT_REALTIME_PORT))
        #         self.sock_cli_state_state = True
        #     except Exception as ex:
        #         self.sock_cli_state_state = False
        #     if self.sock_cli_state_state:
        #         # self.sock_cli_state_state = True
        #         return

