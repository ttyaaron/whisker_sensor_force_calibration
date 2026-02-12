"""
实时显示线性平台位置
可视化X, Y, Z轴的当前位置和移动轨迹
"""
import serial
import serial.tools.list_ports
import time
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque
import numpy as np
from stage_module import StageModuleControl

# 配置
UPDATE_INTERVAL = 100  # 更新间隔(ms)
MAX_HISTORY = 200  # 保存最近200个数据点

# 数据存储
time_data = deque(maxlen=MAX_HISTORY)
pos_x_data = deque(maxlen=MAX_HISTORY)
pos_y_data = deque(maxlen=MAX_HISTORY)
pos_z_data = deque(maxlen=MAX_HISTORY)

# 全局变量
ser = None
sx = None
sy = None
sz = None
start_time = None
stop_flag = False

def find_serial_port():
    """查找USB Serial Port"""
    ports = serial.tools.list_ports.comports()
    for port in ports:
        if "USB Serial" in port.description or "COM11" in port.device:
            return port.device
    # 如果没找到，返回第一个端口
    return ports[0].device if ports else None

def init_stages(port):
    """初始化stage连接"""
    global ser, sx, sy, sz, start_time
    
    print("="*60)
    print(f"连接到串口: {port}")
    ser = serial.Serial(port, 9600, timeout=2)
    time.sleep(0.3)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    time.sleep(0.2)
    
    # 创建stage对象
    sx = StageModuleControl(ser, 1, step_size=0.000047625, total_steps=2133333)
    sy = StageModuleControl(ser, 2, step_size=0.000047625, total_steps=2133333)
    sz = StageModuleControl(ser, 3, step_size=0.000047625, total_steps=1066666)
    
    # 读取初始位置
    print("读取初始位置...")
    try:
        pos_x = sx.get_pos()
        time.sleep(0.1)
        pos_y = sy.get_pos()
        time.sleep(0.1)
        pos_z = sz.get_pos()
        print(f"  X: {pos_x:.2f} mm")
        print(f"  Y: {pos_y:.2f} mm")
        print(f"  Z: {pos_z:.2f} mm")
    except Exception as e:
        print(f"初始读取失败: {e}")
        print("将重试...")
    
    start_time = time.time()
    print("✓ 初始化完成")
    print("="*60)

def cleanup():
    """清理连接"""
    global ser
    if ser and ser.is_open:
        print("\n关闭串口连接...")
        ser.close()
        print("✓ 已关闭")

def read_positions():
    """读取所有轴位置"""
    try:
        pos_x = sx.get_pos()
        pos_y = sy.get_pos()
        pos_z = sz.get_pos()
        return pos_x, pos_y, pos_z, None
    except Exception as e:
        return None, None, None, str(e)

def update_plot(frame):
    """更新图表"""
    global stop_flag
    
    if stop_flag:
        cleanup()
        plt.close('all')
        return
    
    # 读取位置
    pos_x, pos_y, pos_z, error = read_positions()
    
    if error:
        # 读取失败，跳过此次更新
        return
    
    # 存储数据
    current_time = time.time() - start_time
    time_data.append(current_time)
    pos_x_data.append(pos_x)
    pos_y_data.append(pos_y)
    pos_z_data.append(pos_z)
    
    # 清除所有子图
    for ax in [ax1, ax2, ax3, ax4]:
        ax.clear()
    
    # === 1. 大数字显示当前位置 ===
    ax1.text(0.5, 0.8, f'{pos_x:.2f}', ha='center', va='center', 
             fontsize=60, fontweight='bold', color='red')
    ax1.text(0.5, 0.5, 'X-axis (mm)', ha='center', va='center', 
             fontsize=20, color='red')
    # 进度条
    x_progress = pos_x / 101.6
    ax1.barh(0.2, x_progress, height=0.1, color='red', alpha=0.5)
    ax1.barh(0.2, 1.0, height=0.1, fill=False, edgecolor='gray', linewidth=2)
    ax1.text(0.5, 0.08, f'Range: 0 - 101.6 mm', ha='center', fontsize=12, color='gray')
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1)
    ax1.axis('off')
    
    # === 2. Y轴显示 ===
    ax2.text(0.5, 0.8, f'{pos_y:.2f}', ha='center', va='center', 
             fontsize=60, fontweight='bold', color='green')
    ax2.text(0.5, 0.5, 'Y-axis (mm)', ha='center', va='center', 
             fontsize=20, color='green')
    # 进度条
    y_progress = pos_y / 101.6
    ax2.barh(0.2, y_progress, height=0.1, color='green', alpha=0.5)
    ax2.barh(0.2, 1.0, height=0.1, fill=False, edgecolor='gray', linewidth=2)
    ax2.text(0.5, 0.08, f'Range: 0 - 101.6 mm', ha='center', fontsize=12, color='gray')
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    ax2.axis('off')
    
    # === 3. Z轴显示 ===
    ax3.text(0.5, 0.8, f'{pos_z:.2f}', ha='center', va='center', 
             fontsize=60, fontweight='bold', color='blue')
    ax3.text(0.5, 0.5, 'Z-axis (mm)', ha='center', va='center', 
             fontsize=20, color='blue')
    # 进度条
    z_progress = pos_z / 50.8
    ax3.barh(0.2, z_progress, height=0.1, color='blue', alpha=0.5)
    ax3.barh(0.2, 1.0, height=0.1, fill=False, edgecolor='gray', linewidth=2)
    ax3.text(0.5, 0.08, f'Range: 0 - 50.8 mm', ha='center', fontsize=12, color='gray')
    ax3.set_xlim(0, 1)
    ax3.set_ylim(0, 1)
    ax3.axis('off')
    
    # === 4. 时间序列图 ===
    if len(time_data) > 1:
        time_array = np.array(time_data)
        ax4.plot(time_array, pos_x_data, 'r-', linewidth=2, label='X-axis', alpha=0.8)
        ax4.plot(time_array, pos_y_data, 'g-', linewidth=2, label='Y-axis', alpha=0.8)
        ax4.plot(time_array, pos_z_data, 'b-', linewidth=2, label='Z-axis', alpha=0.8)
        ax4.set_xlabel('Time (s)', fontsize=12, fontweight='bold')
        ax4.set_ylabel('Position (mm)', fontsize=12, fontweight='bold')
        ax4.set_title('Position History', fontsize=14, fontweight='bold')
        ax4.legend(loc='upper left', fontsize=10)
        ax4.grid(True, alpha=0.3)
    
    # 总标题
    fig.suptitle(f'Linear Stage Position Monitor | Update Rate: ~{1000/UPDATE_INTERVAL:.0f} Hz | Time: {current_time:.1f}s',
                 fontsize=13, fontweight='bold')
    
    plt.tight_layout()

def main():
    global fig, ax1, ax2, ax3, ax4, stop_flag
    
    print("\n" + "="*60)
    print("  LINEAR STAGE POSITION MONITOR")
    print("="*60)
    
    # 查找串口
    port = find_serial_port()
    if not port:
        print("✗ 未找到串口!")
        return
    
    port_input = input(f"\n使用串口 {port} ? (回车确认, 或输入其他端口): ").strip()
    if port_input:
        port = port_input
    
    try:
        # 初始化
        init_stages(port)
        
        # 创建图表 (2x2布局)
        fig = plt.figure(figsize=(14, 10))
        ax1 = plt.subplot(2, 2, 1)  # X轴
        ax2 = plt.subplot(2, 2, 2)  # Y轴
        ax3 = plt.subplot(2, 2, 3)  # Z轴
        ax4 = plt.subplot(2, 2, 4)  # 时间序列
        
        fig.canvas.manager.set_window_title('Stage Position Monitor')
        
        # 创建动画
        ani = animation.FuncAnimation(
            fig,
            update_plot,
            interval=UPDATE_INTERVAL,
            cache_frame_data=False
        )
        
        print("\n" + "="*60)
        print("  实时监控已启动!")
        print("="*60)
        print("提示:")
        print("  • 使用其他程序/脚本移动stage")
        print("  • 此窗口会实时显示位置")
        print("  • 按 Ctrl+C 或关闭窗口退出")
        print("="*60 + "\n")
        
        plt.show()
        
    except KeyboardInterrupt:
        print("\n收到中断信号...")
        stop_flag = True
    except Exception as e:
        print(f"\n✗ 错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cleanup()
        print("程序已退出")

if __name__ == "__main__":
    main()
