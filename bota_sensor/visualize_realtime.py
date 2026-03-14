"""
Bota力传感器实时可视化 - 综合视图 (推荐)
显示力/力矩的合成大小和分量
"""
import os
import time
import signal
import json
import tempfile
import socket
import array
import sys
import bota_driver
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque
import numpy as np

# 配置文件路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "..", "bota_driver_config", "ethercat_gen0.json")
CONFIG_PATH = os.environ.get("BOTA_CONFIG_PATH", DEFAULT_CONFIG_PATH)


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


def get_ipv4_addresses(interface_name):
    """读取指定网卡上的IPv4地址（仅Linux）。"""
    try:
        import fcntl
        import struct
    except Exception:
        return []

    ip_addresses = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    max_interfaces = 64
    bytes_buffer = max_interfaces * 40
    names = array.array('B', b'\0' * bytes_buffer)

    try:
        request = struct.pack('iL', bytes_buffer, names.buffer_info()[0])
        result = fcntl.ioctl(sock.fileno(), 0x8912, request)  # SIOCGIFCONF
        out_bytes = struct.unpack('iL', result)[0]
        namestr = names.tobytes()
        for index in range(0, out_bytes, 40):
            name = namestr[index:index + 16].split(b'\0', 1)[0].decode('utf-8', errors='ignore')
            if name != interface_name:
                continue
            ip_bytes = namestr[index + 20:index + 24]
            ip_addresses.append(socket.inet_ntoa(ip_bytes))
    except Exception:
        return []
    finally:
        sock.close()

    return ip_addresses


def linux_preflight_notice(network_interface):
    """Linux环境启动前检查提示。"""
    if os.name == "nt":
        return

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("[提示] Linux下EtherCAT通常需要root权限。建议使用: sudo -E ./run_bota_realtime.sh")

    ip_addresses = get_ipv4_addresses(network_interface)
    if ip_addresses and not any(ip.startswith("10.20.0.") for ip in ip_addresses):
        print(
            f"[提示] 网卡 {network_interface} 当前IP: {', '.join(ip_addresses)}，"
            "与传感器常用网段10.20.0.x不一致。"
        )
        print("[提示] 可先设置: sudo ip addr flush dev <网卡>; sudo ip addr add 10.20.0.100/24 dev <网卡>")


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
        linux_preflight_notice(override_iface)
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
        linux_preflight_notice(auto_iface)
        temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        with temp_file as handle:
            json.dump(config_data, handle, ensure_ascii=False, indent=4)
        print(f"检测到Linux网卡，自动使用: {auto_iface}")
        return temp_file.name

    return config_path

# 数据存储
MAX_POINTS = 500
time_data = deque(maxlen=MAX_POINTS)
force_mag = deque(maxlen=MAX_POINTS)
torque_mag = deque(maxlen=MAX_POINTS)
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
    global stop_flag
    stop_flag = True

signal.signal(signal.SIGINT, signal_handler)

def init_sensor():
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
    global bota_ft_sensor_driver
    if bota_ft_sensor_driver is not None:
        print("\n关闭传感器连接...")
        bota_ft_sensor_driver.deactivate()
        bota_ft_sensor_driver.shutdown()
        print("✓ 传感器已关闭")

def update_plot(frame):
    global stop_flag
    
    if stop_flag:
        cleanup_sensor()
        plt.close('all')
        return
    
    try:
        # 读取传感器数据
        bota_frame = bota_ft_sensor_driver.read_frame()
        
        current_time = time.perf_counter() - start_time
        force = bota_frame.force
        torque = bota_frame.torque
        temp = bota_frame.temperature
        
        # 计算合成大小
        f_mag = np.sqrt(force[0]**2 + force[1]**2 + force[2]**2)
        t_mag = np.sqrt(torque[0]**2 + torque[1]**2 + torque[2]**2)
        
        # 存储数据
        time_data.append(current_time)
        force_mag.append(f_mag)
        torque_mag.append(t_mag)
        force_x.append(force[0])
        force_y.append(force[1])
        force_z.append(force[2])
        torque_x.append(torque[0])
        torque_y.append(torque[1])
        torque_z.append(torque[2])
        temperature_data.append(temp)
        
        # 清除所有子图
        for ax in [ax1, ax2, ax3, ax4]:
            ax.clear()
        
        time_array = np.array(time_data)
        
        # 1. 合成力大小
        ax1.plot(time_array, force_mag, 'r-', linewidth=2, label='Force Magnitude')
        ax1.fill_between(time_array, force_mag, alpha=0.3, color='red')
        ax1.set_ylabel('Force (N)', fontsize=11, fontweight='bold')
        ax1.set_title('Total Force Magnitude', fontsize=12, fontweight='bold')
        ax1.grid(True, alpha=0.3)
        ax1.legend(loc='upper right')
        
        if len(force_mag) > 10:
            mean_f = np.mean(list(force_mag)[-100:])
            max_f = np.max(list(force_mag)[-100:])
            stats = f'Mean: {mean_f:.2f}N | Max: {max_f:.2f}N'
            ax1.text(0.02, 0.98, stats, transform=ax1.transAxes,
                    verticalalignment='top', fontsize=9,
                    bbox=dict(boxstyle='round', facecolor='yellow', alpha=0.7))
        
        # 2. 合成力矩大小
        ax2.plot(time_array, torque_mag, 'b-', linewidth=2, label='Torque Magnitude')
        ax2.fill_between(time_array, torque_mag, alpha=0.3, color='blue')
        ax2.set_ylabel('Torque (Nm)', fontsize=11, fontweight='bold')
        ax2.set_title('Total Torque Magnitude', fontsize=12, fontweight='bold')
        ax2.grid(True, alpha=0.3)
        ax2.legend(loc='upper right')
        
        if len(torque_mag) > 10:
            mean_t = np.mean(list(torque_mag)[-100:])
            max_t = np.max(list(torque_mag)[-100:])
            stats = f'Mean: {mean_t:.3f}Nm | Max: {max_t:.3f}Nm'
            ax2.text(0.02, 0.98, stats, transform=ax2.transAxes,
                    verticalalignment='top', fontsize=9,
                    bbox=dict(boxstyle='round', facecolor='yellow', alpha=0.7))
        
        # 3. 力的三个分量
        ax3.plot(time_array, force_x, 'r-', linewidth=1.5, label='Fx', alpha=0.8)
        ax3.plot(time_array, force_y, 'g-', linewidth=1.5, label='Fy', alpha=0.8)
        ax3.plot(time_array, force_z, 'b-', linewidth=1.5, label='Fz', alpha=0.8)
        ax3.set_ylabel('Force Components (N)', fontsize=11, fontweight='bold')
        ax3.set_xlabel('Time (s)', fontsize=11)
        ax3.set_title('Force Components (X, Y, Z)', fontsize=12, fontweight='bold')
        ax3.grid(True, alpha=0.3)
        ax3.legend(loc='upper right')
        
        # 4. 力矩的三个分量
        ax4.plot(time_array, torque_x, 'r-', linewidth=1.5, label='Tx', alpha=0.8)
        ax4.plot(time_array, torque_y, 'g-', linewidth=1.5, label='Ty', alpha=0.8)
        ax4.plot(time_array, torque_z, 'b-', linewidth=1.5, label='Tz', alpha=0.8)
        ax4.set_ylabel('Torque Components (Nm)', fontsize=11, fontweight='bold')
        ax4.set_xlabel('Time (s)', fontsize=11)
        ax4.set_title('Torque Components (X, Y, Z)', fontsize=12, fontweight='bold')
        ax4.grid(True, alpha=0.3)
        ax4.legend(loc='upper right')
        
        # 总标题
        rate = len(force_x) / max(current_time, 0.1)
        fig.suptitle(f'Bota力传感器实时监控 | 温度: {temp:.1f}°C | 采样率: {rate:.1f} Hz | 数据点: {len(time_data)}',
                     fontsize=13, fontweight='bold')
        
        plt.tight_layout()
        
    except Exception as e:
        print(f"数据读取错误: {e}")
        stop_flag = True

if __name__ == "__main__":
    exit_code = 0
    try:
        init_sensor()
        
        # 创建2x2网格布局
        fig = plt.figure(figsize=(16, 10))
        ax1 = plt.subplot(2, 2, 1)
        ax2 = plt.subplot(2, 2, 2)
        ax3 = plt.subplot(2, 2, 3)
        ax4 = plt.subplot(2, 2, 4)
        
        fig.canvas.manager.set_window_title('Bota Sensor - Comprehensive View')
        
        # 创建动画
        ani = animation.FuncAnimation(
            fig,
            update_plot,
            interval=20,  # 20ms = 50Hz
            cache_frame_data=False
        )
        
        print("\n===========================================")
        print("  实时可视化已启动!")
        print("===========================================")
        print("操作提示:")
        print("  • 按 Ctrl+C 或关闭窗口退出")
        print("  • 对传感器施加力/力矩观察变化")
        print("  • 红色=X轴, 绿色=Y轴, 蓝色=Z轴")
        print("===========================================\n")
        
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
