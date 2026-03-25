"""
线性平台控制 + 实时位置监控
交互式控制界面，实时显示位置
"""
from pathlib import Path
import os
import argparse
try:
    import serial
except ModuleNotFoundError as exc:
    raise SystemExit(
        "缺少依赖 pyserial。请安装后重试:\n"
        "  pip install pyserial\n"
        "或在 conda 环境中:\n"
        "  conda install pyserial"
    ) from exc
import time
import threading
try:
    import matplotlib
except ModuleNotFoundError:
    matplotlib = None


def _configure_matplotlib_backend() -> None:
    """Prefer an interactive backend so button widgets can be used."""
    if matplotlib is None:
        return

    forced = os.environ.get("MPLBACKEND", "").strip()
    if forced:
        return

    display_available = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if not display_available:
        return

    current = str(matplotlib.get_backend()).lower()
    if "agg" not in current:
        return

    for candidate in ("QtAgg", "Qt5Agg", "TkAgg"):
        try:
            matplotlib.use(candidate, force=True)
            return
        except Exception:
            continue


_configure_matplotlib_backend()

try:
    import matplotlib.pyplot as plt
    from matplotlib.widgets import Button, TextBox
except Exception:
    plt = None
    Button = None
    TextBox = None

from stage_module import StageModuleControl

try:
    import serial.tools.list_ports as serial_list_ports
except Exception:
    serial_list_ports = None

# 全局变量
ser = None
sx = None
sy = None
sz = None
current_pos = {'x': 0.0, 'y': 0.0, 'z': 0.0}
update_lock = threading.Lock()
running = True

# Y-axis is vertical, gravity-affected, open-loop (step accumulation only).
# Lower speed = more torque, which helps overcome gravity on the vertical axis.
# Speed is in microsteps/second. 0 = use device default (don't change).
# Start with ~2000 and decrease (1200, 800, 400) if still stalling.
Y_SPEED = int(os.environ.get("STAGE_Y_SPEED", "10000"))  # microsteps/s; lower=more torque
Y_MOVE_TIMEOUT_S = max(2.0, float(os.environ.get("STAGE_Y_MOVE_TIMEOUT_S", "15.0")))
Y_MOVE_RETRIES = max(1, int(os.environ.get("STAGE_Y_MOVE_RETRIES", "3")))
Y_HOME_POLL_TIMEOUT_S = max(20, float(os.environ.get("STAGE_Y_HOME_POLL_TIMEOUT_S", "90")))


def _ensure_serial_runtime():
    if not hasattr(serial, "Serial"):
        raise RuntimeError(
            "当前环境缺少可用的 pyserial。请先安装: pip install pyserial"
        )

def find_serial_port():
    """查找串口"""
    candidates = []

    if serial_list_ports is not None:
        ports = list(serial_list_ports.comports())
        for port in ports:
            desc = (port.description or "").lower()
            dev = port.device
            if not dev:
                continue
            if "usb serial" in desc or "ftdi" in desc or "ch340" in desc or "cp210" in desc:
                candidates.append(dev)
        for port in ports:
            if port.device:
                candidates.append(port.device)

    by_id_dir = Path("/dev/serial/by-id")
    if by_id_dir.is_dir():
        for path in sorted(by_id_dir.iterdir()):
            resolved = str(path.resolve()) if path.exists() else ""
            if resolved.startswith("/dev/ttyUSB") or resolved.startswith("/dev/ttyACM"):
                candidates.insert(0, str(path))

    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*"):
        for path in sorted(Path("/dev").glob(pattern.replace("/dev/", ""))):
            candidates.append(str(path))

    seen = set()
    deduped = []
    for dev in candidates:
        if dev in seen:
            continue
        seen.add(dev)
        deduped.append(dev)

    return deduped[0] if deduped else None

def init_stages(port):
    """初始化stages"""
    global ser, sx, sy, sz

    _ensure_serial_runtime()
    
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


def _clamp_axis_target(axis, target):
    if axis == 'x':
        return max(0.0, min(101.6, float(target)))
    if axis == 'y':
        return max(0.0, min(101.6, float(target)))
    if axis == 'z':
        return max(0.0, min(50.8, float(target)))
    raise ValueError(f"unknown axis: {axis}")


def _set_y_speed():
    """Set Y-axis speed if configured.  Call before Y moves."""
    if Y_SPEED > 0:
        try:
            sy.set_speed(Y_SPEED)
            time.sleep(0.05)
        except Exception as exc:
            print(f"  [Y] speed set failed ({exc}), continuing with default")


def _move_y_with_retry(target):
    """Move Y to *target* mm with retry on timeout.  Must hold update_lock.

    Uses a longer ACK timeout and retries to handle the Y-axis firmware's
    inconsistent CMD 20/10 responses under gravity load.  After each timeout
    we check whether the motor actually moved — if it did, the command
    succeeded despite the missing ACK.
    """
    target = _clamp_axis_target('y', target)
    _set_y_speed()

    for attempt in range(Y_MOVE_RETRIES):
        try:
            sy.go_pos_mm(target, wait=True, timeout_s=Y_MOVE_TIMEOUT_S)
            time.sleep(0.1)
            current_pos['y'] = sy.get_pos()
            return
        except TimeoutError as exc:
            # Timeout on ACK — check if the motor actually moved.
            time.sleep(0.2)
            try:
                ser.reset_input_buffer()
                pos = sy.get_pos()
                current_pos['y'] = pos
                if abs(pos - target) < 0.15:
                    print(f"  [Y] ACK timeout but move completed (pos={pos:.2f}mm)")
                    return
                print(
                    f"  [Y retry {attempt+1}/{Y_MOVE_RETRIES}] "
                    f"timeout at {pos:.2f}mm, target={target:.2f}mm: {exc}"
                )
            except Exception:
                print(f"  [Y retry {attempt+1}/{Y_MOVE_RETRIES}] timeout, position read also failed")
            time.sleep(0.3)

    # Final position check before declaring failure
    try:
        pos = sy.get_pos()
        current_pos['y'] = pos
        if abs(pos - target) < 0.15:
            return
    except Exception:
        pass
    raise RuntimeError(
        f"Y move to {target:.2f}mm failed after {Y_MOVE_RETRIES} attempts"
    )

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
                target = _clamp_axis_target('x', current_pos['x'] + distance)
                sx.go_pos_mm(target, wait=True)
            elif axis == 'y':
                target = _clamp_axis_target('y', current_pos['y'] + distance)
                _move_y_with_retry(target)
            elif axis == 'z':
                target = _clamp_axis_target('z', current_pos['z'] + distance)
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
                sx.go_pos_mm(_clamp_axis_target('x', position), wait=True)
            elif axis == 'y':
                _move_y_with_retry(_clamp_axis_target('y', position))
            elif axis == 'z':
                sz.go_pos_mm(_clamp_axis_target('z', position), wait=True)
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
    print("Homing Y (hardware home + position poll)...")
    try:
        with update_lock:
            _set_y_speed()
            sy.home(poll_completion=True, poll_timeout_s=Y_HOME_POLL_TIMEOUT_S)
        time.sleep(0.5)
        update_positions()
        update_display()
        print(f"✓ Y homed (pos={current_pos['y']:.2f}mm)")
    except Exception as e:
        update_positions()
        update_display()
        print(f"✗ Y home failed: {e}")

def on_home_z(event):
    print("Homing Z...")
    with update_lock:
        sz.home()
    update_positions()
    update_display()
    print("✓ Z homed")


def _print_terminal_help() -> None:
    print(
        "\n可用命令:\n"
        "  status                  查看当前位置\n"
        "  x+ <mm> / x- <mm>       X轴增量移动\n"
        "  y+ <mm> / y- <mm>       Y轴增量移动\n"
        "  z+ <mm> / z- <mm>       Z轴增量移动\n"
        "  x=<mm> / y=<mm> / z=<mm>  移动到绝对位置\n"
        "  home x|y|z              回零指定轴\n"
        "  zero y                  标记当前Y位置为0 (open-loop drift recovery)\n"
        "  resync                  重新读取所有轴位置\n"
        "  stress y [n]            反复 y+20/y-20 n次 (default 5) 用于测试可靠性\n"
        "  speed y <rate>          设置Y轴速度 (microsteps/s, lower=more torque, 0=default)\n"
        "  help                    显示帮助\n"
        "  quit / exit             退出程序\n"
        "\n"
        "注: Y轴为开环控制，位置来自步数累计。home y 使用硬件归零+位置轮询。\n"
        "    若Y轴因重力失步，用 speed y <rate> 降速增扭 (试: 2000, 1200, 800, 400)。\n"
    )


def run_terminal_control() -> None:
    print("\n" + "=" * 60)
    print("  TERMINAL-ONLY STAGE CONTROL")
    print("=" * 60)
    _print_terminal_help()

    while True:
        try:
            cmd = input("stage> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n退出终端控制")
            return

        if not cmd:
            continue
        if cmd in {"quit", "exit", "q"}:
            return
        if cmd in {"help", "h", "?"}:
            _print_terminal_help()
            continue
        if cmd in {"status", "s"}:
            update_positions()
            print(
                f"X={current_pos['x']:.3f} mm, "
                f"Y={current_pos['y']:.3f} mm, "
                f"Z={current_pos['z']:.3f} mm"
            )
            continue

        if cmd == "resync":
            print("  Re-reading all axis positions...")
            ser.reset_input_buffer()
            time.sleep(0.1)
            update_positions()
            print(
                f"  X={current_pos['x']:.3f} mm, "
                f"Y={current_pos['y']:.3f} mm, "
                f"Z={current_pos['z']:.3f} mm"
            )
            continue

        if cmd == "zero y":
            print("  Setting current Y position as 0...")
            try:
                with update_lock:
                    sy.set_zero()
                time.sleep(0.1)
                update_positions()
                print(f"  ✓ Y zeroed (now reads {current_pos['y']:.3f} mm)")
            except Exception as e:
                print(f"  ✗ zero failed: {e}")
            continue

        if cmd.startswith("stress y"):
            parts = cmd.split()
            n_cycles = 5
            if len(parts) >= 3:
                try:
                    n_cycles = int(parts[2])
                except ValueError:
                    pass
            print(f"  Y stress test: {n_cycles} cycles of y+20 / y-20")
            update_positions()
            start_pos = current_pos['y']
            successes = 0
            for i in range(n_cycles):
                print(f"  --- cycle {i+1}/{n_cycles} ---")
                ok_up = move_axis('y', 20.0)
                print(f"    y+20: {'OK' if ok_up else 'FAIL'}  pos={current_pos['y']:.2f}mm")
                ok_dn = move_axis('y', -20.0)
                print(f"    y-20: {'OK' if ok_dn else 'FAIL'}  pos={current_pos['y']:.2f}mm")
                if ok_up and ok_dn:
                    successes += 1
            update_positions()
            drift = abs(current_pos['y'] - start_pos)
            print(
                f"  Result: {successes}/{n_cycles} cycles OK, "
                f"drift={drift:.3f}mm (start={start_pos:.2f}, end={current_pos['y']:.2f})"
            )
            continue

        if cmd.startswith("speed y"):
            global Y_SPEED
            parts = cmd.split()
            if len(parts) < 3:
                print(f"  Current Y_SPEED={Y_SPEED}  (0=device default)")
                print("  Usage: speed y <rate>  (try: 2000, 1200, 800, 400, 0)")
                continue
            try:
                rate = int(parts[2])
                Y_SPEED = max(0, rate)
                if Y_SPEED > 0:
                    with update_lock:
                        sy.set_speed(Y_SPEED)
                    print(f"  ✓ Y speed set to {Y_SPEED} microsteps/s")
                else:
                    print("  ✓ Y speed reset to device default")
            except Exception as e:
                print(f"  ✗ speed set failed: {e}")
            continue

        if cmd.startswith("home "):
            axis = cmd.split(maxsplit=1)[1].strip()
            if axis not in {"x", "y", "z"}:
                print("无效轴，使用: home x|y|z")
                continue
            try:
                with update_lock:
                    if axis == "x":
                        sx.home()
                    elif axis == "y":
                        _set_y_speed()
                        sy.home(poll_completion=True, poll_timeout_s=Y_HOME_POLL_TIMEOUT_S)
                    else:
                        sz.home()
                update_positions()
                print(f"✓ {axis.upper()} homed (pos={current_pos[axis]:.2f}mm)")
            except Exception as e:
                print(f"✗ home失败: {e}")
            continue

        # Absolute move: x=12.3 / y=1.0 / z=5
        if len(cmd) >= 3 and cmd[1] == "=" and cmd[0] in {"x", "y", "z"}:
            axis = cmd[0]
            try:
                target = float(cmd[2:].strip())
            except ValueError:
                print("格式错误，示例: x=10.5")
                continue
            ok = goto_position(axis, target)
            if ok:
                print(f"✓ {axis.upper()} -> {target:.3f} mm")
            continue

        # Relative move: x+ 5 / y-2 / z+1.5
        if cmd[0] in {"x", "y", "z"} and len(cmd) >= 2 and cmd[1] in {"+", "-"}:
            axis = cmd[0]
            sign = 1.0 if cmd[1] == "+" else -1.0
            tail = cmd[2:].strip()
            try:
                delta = float(tail) if tail else 5.0
            except ValueError:
                print("格式错误，示例: x+ 5 或 y-2")
                continue
            ok = move_axis(axis, sign * abs(delta))
            if ok:
                print(f"✓ {axis.upper()} moved {sign * abs(delta):+.3f} mm")
            continue

        print("未知命令，输入 help 查看可用命令")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Stage control GUI with terminal-only fallback mode")
    p.add_argument(
        "--terminal-only",
        action="store_true",
        help="Force terminal command mode instead of Matplotlib GUI.",
    )
    return p.parse_args()


def main():
    global fig, ax_x, ax_y, ax_z, running
    args = parse_args()
    backend = str(matplotlib.get_backend()).lower() if matplotlib is not None else "none"
    gui_available = (plt is not None and Button is not None and "agg" not in backend)
    use_terminal_only = bool(args.terminal_only or not gui_available)
    
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
        
        if use_terminal_only:
            if args.terminal_only:
                print("已启用终端控制模式 (--terminal-only)")
            elif plt is None:
                print("未检测到可用 Matplotlib GUI，自动切换到终端控制模式")
            else:
                print(f"检测到非交互后端 '{backend}'，自动切换到终端控制模式")
            run_terminal_control()
            return
        
        # 仅在GUI模式下启动位置监控线程（终端模式用户手动输入status）
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
