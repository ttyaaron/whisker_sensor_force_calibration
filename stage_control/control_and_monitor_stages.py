"""
线性平台控制 + 实时位置监控
交互式控制界面，实时显示位置
"""
import serial
import serial.tools.list_ports
import time
import threading
import matplotlib.pyplot as plt
from matplotlib.widgets import Button, TextBox
from stage_module import StageModuleControl

# 全局变量
ser = None
sx = None
sy = None
sz = None
current_pos = {'x': 0.0, 'y': 0.0, 'z': 0.0}
update_lock = threading.Lock()
running = True

def find_serial_port():
    """查找串口"""
    ports = serial.tools.list_ports.comports()
    for port in ports:
        if "USB Serial" in port.description or "COM11" in port.device:
            return port.device
    return ports[0].device if ports else None

def init_stages(port):
    """初始化stages"""
    global ser, sx, sy, sz
    
    print(f"连接到 {port}...")
    ser = serial.Serial(port, 9600, timeout=2)
    time.sleep(0.3)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    time.sleep(0.2)
    
    sx = StageModuleControl(ser, 1, step_size=0.000047625, total_steps=2133333)
    sy = StageModuleControl(ser, 2, step_size=0.000047625, total_steps=2133333)
    sz = StageModuleControl(ser, 3, step_size=0.000047625, total_steps=1066666)
    
    # 初始位置
    update_positions()
    print(f"✓ 初始化完成")
    print(f"  X={current_pos['x']:.2f}, Y={current_pos['y']:.2f}, Z={current_pos['z']:.2f} mm")

def update_positions():
    """更新位置读数"""
    global current_pos
    try:
        with update_lock:
            current_pos['x'] = sx.get_pos()
            time.sleep(0.05)
            current_pos['y'] = sy.get_pos()
            time.sleep(0.05)
            current_pos['z'] = sz.get_pos()
    except Exception as e:
        print(f"位置读取错误: {e}")

def position_monitor_thread():
    """后台线程持续读取位置"""
    global running
    while running:
        update_positions()
        time.sleep(0.2)  # 每200ms更新一次

def move_axis(axis, distance):
    """移动指定轴"""
    try:
        with update_lock:
            if axis == 'x':
                target = max(0, min(101.6, current_pos['x'] + distance))
                sx.go_pos_mm(target, wait=True)
            elif axis == 'y':
                target = max(0, min(101.6, current_pos['y'] + distance))
                sy.go_pos_mm(target, wait=True)
            elif axis == 'z':
                target = max(0, min(50.8, current_pos['z'] + distance))
                sz.go_pos_mm(target, wait=True)
        time.sleep(0.1)
        update_positions()
        return True
    except Exception as e:
        print(f"移动失败: {e}")
        return False

def goto_position(axis, position):
    """移动到指定位置"""
    try:
        with update_lock:
            if axis == 'x':
                sx.go_pos_mm(max(0, min(101.6, position)), wait=True)
            elif axis == 'y':
                sy.go_pos_mm(max(0, min(101.6, position)), wait=True)
            elif axis == 'z':
                sz.go_pos_mm(max(0, min(50.8, position)), wait=True)
        time.sleep(0.1)
        update_positions()
        return True
    except Exception as e:
        print(f"移动失败: {e}")
        return False

def update_display():
    """更新显示"""
    # 清除图表
    for ax in [ax_x, ax_y, ax_z]:
        ax.clear()
    
    pos_x = current_pos['x']
    pos_y = current_pos['y']
    pos_z = current_pos['z']
    
    # X轴显示
    ax_x.text(0.5, 0.7, f'{pos_x:.2f} mm', ha='center', va='center',
             fontsize=50, fontweight='bold', color='red')
    ax_x.text(0.5, 0.5, 'X-Axis', ha='center', fontsize=16, color='red')
    x_progress = pos_x / 101.6
    ax_x.barh(0.3, x_progress, height=0.15, color='red', alpha=0.5)
    ax_x.barh(0.3, 1.0, height=0.15, fill=False, edgecolor='gray', linewidth=2)
    ax_x.text(0.5, 0.1, '0 ← → 101.6 mm', ha='center', fontsize=11, color='gray')
    ax_x.set_xlim(0, 1)
    ax_x.set_ylim(0, 1)
    ax_x.axis('off')
    
    # Y轴显示
    ax_y.text(0.5, 0.7, f'{pos_y:.2f} mm', ha='center', va='center',
             fontsize=50, fontweight='bold', color='green')
    ax_y.text(0.5, 0.5, 'Y-Axis', ha='center', fontsize=16, color='green')
    y_progress = pos_y / 101.6
    ax_y.barh(0.3, y_progress, height=0.15, color='green', alpha=0.5)
    ax_y.barh(0.3, 1.0, height=0.15, fill=False, edgecolor='gray', linewidth=2)
    ax_y.text(0.5, 0.1, '0 ← → 101.6 mm', ha='center', fontsize=11, color='gray')
    ax_y.set_xlim(0, 1)
    ax_y.set_ylim(0, 1)
    ax_y.axis('off')
    
    # Z轴显示
    ax_z.text(0.5, 0.7, f'{pos_z:.2f} mm', ha='center', va='center',
             fontsize=50, fontweight='bold', color='blue')
    ax_z.text(0.5, 0.5, 'Z-Axis', ha='center', fontsize=16, color='blue')
    z_progress = pos_z / 50.8
    ax_z.barh(0.3, z_progress, height=0.15, color='blue', alpha=0.5)
    ax_z.barh(0.3, 1.0, height=0.15, fill=False, edgecolor='gray', linewidth=2)
    ax_z.text(0.5, 0.1, '0 ← → 50.8 mm', ha='center', fontsize=11, color='gray')
    ax_z.set_xlim(0, 1)
    ax_z.set_ylim(0, 1)
    ax_z.axis('off')
    
    plt.draw()

# 按钮回调函数
def on_xplus(event):
    move_axis('x', 5.0)
    update_display()

def on_xminus(event):
    move_axis('x', -5.0)
    update_display()

def on_yplus(event):
    move_axis('y', 5.0)
    update_display()

def on_yminus(event):
    move_axis('y', -5.0)
    update_display()

def on_zplus(event):
    move_axis('z', 5.0)
    update_display()

def on_zminus(event):
    move_axis('z', -5.0)
    update_display()

def on_home_x(event):
    print("Homing X...")
    with update_lock:
        sx.home()
    update_positions()
    update_display()
    print("✓ X homed")

def on_home_y(event):
    print("Homing Y...")
    with update_lock:
        sy.home()
    update_positions()
    update_display()
    print("✓ Y homed")

def on_home_z(event):
    print("Homing Z...")
    with update_lock:
        sz.home()
    update_positions()
    update_display()
    print("✓ Z homed")

def main():
    global fig, ax_x, ax_y, ax_z, running
    
    print("\n" + "="*60)
    print("  STAGE CONTROL & POSITION MONITOR")
    print("="*60)
    
    # 查找串口
    port = find_serial_port()
    port_input = input(f"\n使用串口 {port} ? (回车确认): ").strip()
    if port_input:
        port = port_input
    
    try:
        # 初始化
        init_stages(port)
        
        # 启动位置监控线程
        monitor_thread = threading.Thread(target=position_monitor_thread, daemon=True)
        monitor_thread.start()
        
        # 创建GUI
        fig = plt.figure(figsize=(14, 8))
        fig.canvas.manager.set_window_title('Stage Control Panel')
        
        # 位置显示区域
        ax_x = plt.subplot(2, 3, 1)
        ax_y = plt.subplot(2, 3, 2)
        ax_z = plt.subplot(2, 3, 3)
        
        # 控制按钮区域
        # X轴控制
        ax_xplus = plt.axes([0.15, 0.35, 0.08, 0.05])
        ax_xminus = plt.axes([0.05, 0.35, 0.08, 0.05])
        ax_xhome = plt.axes([0.10, 0.28, 0.08, 0.05])
        
        btn_xplus = Button(ax_xplus, 'X +5mm', color='lightcoral')
        btn_xminus = Button(ax_xminus, 'X -5mm', color='lightcoral')
        btn_xhome = Button(ax_xhome, 'Home X', color='gray')
        
        btn_xplus.on_clicked(on_xplus)
        btn_xminus.on_clicked(on_xminus)
        btn_xhome.on_clicked(on_home_x)
        
        # Y轴控制
        ax_yplus = plt.axes([0.48, 0.35, 0.08, 0.05])
        ax_yminus = plt.axes([0.38, 0.35, 0.08, 0.05])
        ax_yhome = plt.axes([0.43, 0.28, 0.08, 0.05])
        
        btn_yplus = Button(ax_yplus, 'Y +5mm', color='lightgreen')
        btn_yminus = Button(ax_yminus, 'Y -5mm', color='lightgreen')
        btn_yhome = Button(ax_yhome, 'Home Y', color='gray')
        
        btn_yplus.on_clicked(on_yplus)
        btn_yminus.on_clicked(on_yminus)
        btn_yhome.on_clicked(on_home_y)
        
        # Z轴控制
        ax_zplus = plt.axes([0.81, 0.35, 0.08, 0.05])
        ax_zminus = plt.axes([0.71, 0.35, 0.08, 0.05])
        ax_zhome = plt.axes([0.76, 0.28, 0.08, 0.05])
        
        btn_zplus = Button(ax_zplus, 'Z +5mm', color='lightblue')
        btn_zminus = Button(ax_zminus, 'Z -5mm', color='lightblue')
        btn_zhome = Button(ax_zhome, 'Home Z', color='gray')
        
        btn_zplus.on_clicked(on_zplus)
        btn_zminus.on_clicked(on_zminus)
        btn_zhome.on_clicked(on_home_z)
        
        # 初始显示
        update_display()
        
        print("\n" + "="*60)
        print("  控制面板已启动!")
        print("="*60)
        print("使用界面上的按钮控制stage")
        print("位置每200ms自动更新")
        print("关闭窗口退出")
        print("="*60 + "\n")
        
        # 定时更新显示
        timer = fig.canvas.new_timer(interval=500)  # 每500ms刷新显示
        timer.add_callback(update_display)
        timer.start()
        
        plt.show()
        
    except KeyboardInterrupt:
        print("\n收到中断信号...")
    except Exception as e:
        print(f"\n✗ 错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        running = False
        time.sleep(0.5)
        if ser and ser.is_open:
            ser.close()
        print("程序已退出")

if __name__ == "__main__":
    main()
