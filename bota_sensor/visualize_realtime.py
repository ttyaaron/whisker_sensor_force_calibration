"""
Bota力传感器实时可视化 - 综合视图 (推荐)
显示力/力矩的合成大小和分量
"""
import os
import time
import signal
import bota_driver
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque
import numpy as np

# 配置文件路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "..", "bota_driver_py_example", "bota_driver_config", "ethercat_gen0.json")

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
    bota_ft_sensor_driver = bota_driver.BotaDriver(CONFIG_PATH)
    
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
    finally:
        cleanup_sensor()
        print("程序已退出")
