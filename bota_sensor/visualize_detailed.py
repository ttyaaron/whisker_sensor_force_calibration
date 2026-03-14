"""
Bota力传感器实时可视化 - 详细视图
显示力和力矩的所有6个分量
"""
import os
import time
import signal
import json
import tempfile
import sys
import bota_driver
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque
import numpy as np

# 配置文件路径 (相对于此脚本)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "..", "bota_driver_config", "ethercat_gen0.json")


def detect_linux_network_interface():
    """自动选择Linux网卡名（优先有线网卡）。"""
    if os.name == "nt":
        return ""

    net_dir = "/sys/class/net"
    if not os.path.isdir(net_dir):
        return ""

    interfaces = []
    for iface in sorted(os.listdir(net_dir)):
        if iface == "lo":
            continue
        operstate_path = os.path.join(net_dir, iface, "operstate")
        state = "unknown"
        if os.path.isfile(operstate_path):
            with open(operstate_path, "r", encoding="utf-8") as handle:
                state = handle.read().strip()
        interfaces.append((iface, state))

    up_eth = [iface for iface, state in interfaces if state == "up" and iface.startswith("en")]
    if up_eth:
        return up_eth[0]

    up_any = [iface for iface, state in interfaces if state == "up"]
    if up_any:
        return up_any[0]

    eth_any = [iface for iface, _ in interfaces if iface.startswith("en")]
    if eth_any:
        return eth_any[0]

    return interfaces[0][0] if interfaces else ""


def prepare_config_path(config_path):
    """根据操作系统和环境变量准备运行时配置文件路径。"""
    if not os.path.isfile(config_path):
        raise RuntimeError(f"配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as handle:
        config_data = json.load(handle)

    driver_config = config_data.get("driver_config", {})
    interface_name = driver_config.get("communication_interface_name", "")
    interface_params = driver_config.get("communication_interface_params", {})
    current_iface = interface_params.get("network_interface", "")
    override_iface = os.environ.get("BOTA_NETWORK_INTERFACE", "").strip()
    override_sensor_ip = os.environ.get("BOTA_SENSOR_IP", "").strip()

    if override_sensor_ip and "sensor_ip_address" in interface_params:
        interface_params["sensor_ip_address"] = override_sensor_ip
        temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        with temp_file as handle:
            json.dump(config_data, handle, ensure_ascii=False, indent=4)
        print(f"使用环境变量 BOTA_SENSOR_IP 覆盖传感器IP: {override_sensor_ip}")
        return temp_file.name

    is_ethercat = "EtherCAT" in interface_name

    if override_iface and is_ethercat:
        interface_params["network_interface"] = override_iface
        temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        with temp_file as handle:
            json.dump(config_data, handle, ensure_ascii=False, indent=4)
        print(f"使用环境变量 BOTA_NETWORK_INTERFACE 覆盖网卡: {override_iface}")
        return temp_file.name

    if os.name != "nt" and is_ethercat and isinstance(current_iface, str) and current_iface.startswith("\\\\Device\\NPF_"):
        auto_iface = detect_linux_network_interface()
        if not auto_iface:
            raise RuntimeError(
                "当前是Linux环境，但配置文件中的network_interface是Windows Npcap接口。\n"
                "请先设置Linux网卡名，例如:\n"
                "  export BOTA_NETWORK_INTERFACE=enp3s0\n"
                "可用网卡可用命令查看: ip -br link"
            )

        interface_params["network_interface"] = auto_iface
        temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        with temp_file as handle:
            json.dump(config_data, handle, ensure_ascii=False, indent=4)
        print(f"检测到Linux网卡，自动使用: {auto_iface}")
        return temp_file.name

    return config_path

# 数据存储
MAX_POINTS = 500  # 显示最近500个数据点
time_data = deque(maxlen=MAX_POINTS)
force_x = deque(maxlen=MAX_POINTS)
force_y = deque(maxlen=MAX_POINTS)
force_z = deque(maxlen=MAX_POINTS)
torque_x = deque(maxlen=MAX_POINTS)
torque_y = deque(maxlen=MAX_POINTS)
torque_z = deque(maxlen=MAX_POINTS)
temperature_data = deque(maxlen=MAX_POINTS)

# 全局变量
bota_ft_sensor_driver = None
start_time = None
stop_flag = False

def signal_handler(signum, frame):
    """处理Ctrl+C中断"""
    global stop_flag
    stop_flag = True

signal.signal(signal.SIGINT, signal_handler)

def init_sensor():
    """初始化传感器"""
    global bota_ft_sensor_driver, start_time
    
    print("=" * 60)
    print("初始化Bota力传感器...")
    print("=" * 60)
    
    runtime_config_path = prepare_config_path(CONFIG_PATH)
    bota_ft_sensor_driver = bota_driver.BotaDriver(runtime_config_path)
    
    if not bota_ft_sensor_driver.configure():
        raise RuntimeError("传感器配置失败")
    
    print("传感器归零中...")
    if not bota_ft_sensor_driver.tare():
        raise RuntimeError("传感器归零失败")
    
    if not bota_ft_sensor_driver.activate():
        raise RuntimeError("传感器激活失败")
    
    start_time = time.perf_counter()
    print("✓ 传感器初始化成功!")
    print("=" * 60)

def cleanup_sensor():
    """清理传感器连接"""
    global bota_ft_sensor_driver
    if bota_ft_sensor_driver is not None:
        print("\n关闭传感器连接...")
        bota_ft_sensor_driver.deactivate()
        bota_ft_sensor_driver.shutdown()
        print("✓ 传感器已关闭")

def update_plot(frame):
    """更新图表的回调函数"""
    global stop_flag
    
    if stop_flag:
        cleanup_sensor()
        plt.close('all')
        return
    
    try:
        # 读取传感器数据
        bota_frame = bota_ft_sensor_driver.read_frame()
        
        # 提取数据
        current_time = time.perf_counter() - start_time
        force = bota_frame.force
        torque = bota_frame.torque
        temp = bota_frame.temperature
        
        # 存储数据
        time_data.append(current_time)
        force_x.append(force[0])
        force_y.append(force[1])
        force_z.append(force[2])
        torque_x.append(torque[0])
        torque_y.append(torque[1])
        torque_z.append(torque[2])
        temperature_data.append(temp)
        
        # 清除并更新所有子图
        for ax in axs.flat:
            ax.clear()
        
        time_array = np.array(time_data)
        
        # 力的三个分量
        axs[0, 0].plot(time_array, force_x, 'r-', linewidth=1.5, label='Fx')
        axs[0, 0].set_ylabel('Force X (N)', fontsize=10)
        axs[0, 0].grid(True, alpha=0.3)
        axs[0, 0].legend(loc='upper right')
        
        axs[0, 1].plot(time_array, force_y, 'g-', linewidth=1.5, label='Fy')
        axs[0, 1].set_ylabel('Force Y (N)', fontsize=10)
        axs[0, 1].grid(True, alpha=0.3)
        axs[0, 1].legend(loc='upper right')
        
        axs[0, 2].plot(time_array, force_z, 'b-', linewidth=1.5, label='Fz')
        axs[0, 2].set_ylabel('Force Z (N)', fontsize=10)
        axs[0, 2].grid(True, alpha=0.3)
        axs[0, 2].legend(loc='upper right')
        
        # 力矩的三个分量
        axs[1, 0].plot(time_array, torque_x, 'r-', linewidth=1.5, label='Tx')
        axs[1, 0].set_ylabel('Torque X (Nm)', fontsize=10)
        axs[1, 0].set_xlabel('Time (s)', fontsize=10)
        axs[1, 0].grid(True, alpha=0.3)
        axs[1, 0].legend(loc='upper right')
        
        axs[1, 1].plot(time_array, torque_y, 'g-', linewidth=1.5, label='Ty')
        axs[1, 1].set_ylabel('Torque Y (Nm)', fontsize=10)
        axs[1, 1].set_xlabel('Time (s)', fontsize=10)
        axs[1, 1].grid(True, alpha=0.3)
        axs[1, 1].legend(loc='upper right')
        
        axs[1, 2].plot(time_array, torque_z, 'b-', linewidth=1.5, label='Tz')
        axs[1, 2].set_ylabel('Torque Z (Nm)', fontsize=10)
        axs[1, 2].set_xlabel('Time (s)', fontsize=10)
        axs[1, 2].grid(True, alpha=0.3)
        axs[1, 2].legend(loc='upper right')
        
        # 显示统计信息
        if len(force_x) > 0:
            rate = len(force_x) / max(current_time, 0.1)
            info_text = f'Points: {len(force_x)} | Rate: {rate:.1f} Hz'
            axs[0, 0].text(0.02, 0.98, info_text, transform=axs[0, 0].transAxes,
                          verticalalignment='top', fontsize=8, 
                          bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
        
        # 总标题
        fig.suptitle(f'Bota传感器实时数据 | 温度: {temp:.1f}°C', fontsize=12, fontweight='bold')
        
        plt.tight_layout()
        
    except Exception as e:
        print(f"数据读取错误: {e}")
        stop_flag = True

if __name__ == "__main__":
    exit_code = 0
    try:
        # 初始化传感器
        init_sensor()
        
        # 创建图表 (2x3布局)
        fig, axs = plt.subplots(2, 3, figsize=(15, 8))
        fig.canvas.manager.set_window_title('Bota Sensor - Detailed View')
        
        # 创建动画
        ani = animation.FuncAnimation(
            fig, 
            update_plot, 
            interval=20,  # 20ms更新间隔
            cache_frame_data=False
        )
        
        print("\n实时可视化已启动!")
        print("提示: 按 Ctrl+C 或关闭窗口退出\n")
        
        plt.show()
        
    except KeyboardInterrupt:
        print("\n收到中断信号...")
    except Exception as e:
        print(f"\n错误: {e}")
        exit_code = 1
    finally:
        cleanup_sensor()
        print("程序已退出")
        sys.exit(exit_code)
