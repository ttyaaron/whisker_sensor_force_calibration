# Stage Control 模块

三轴线性平台控制与状态监控模块（X/Y/Z）。

## 当前状态

- 支持三轴控制: X=ID1, Y=ID2, Z=ID3（可在脚本里改模块ID）。
- 串口自动检测优先使用 `/dev/serial/by-id/...`。
- `control_and_monitor_stages.py` 现在支持两种模式:
   - GUI 模式（Matplotlib 交互后端可用时）
   - 终端模式（`--terminal-only` 或 GUI 后端不可用时自动回退）
- 已修复: 过去在 Agg 后端下 `plt.show()` 无法显示窗口的问题。

## 硬件配置

- X轴: LSM100A (0-101.6 mm)
- Y轴: LSM100A (0-101.6 mm)
- Z轴: LSM50A (0-50.8 mm)
- 通信: 串口 9600 baud
- 步进精度: 0.000047625 mm/step

## 文件说明

### stage_module.py

底层串口控制库，封装 stage 指令通信。

主要类:
- `StageModuleControl(ser, mid, step_size, total_steps)`

常用方法:
- `home()`
- `go_pos_mm(pos, wait=True)`
- `get_pos()`
- `set_speed()`

### control_and_monitor_stages.py

三轴控制主入口（GUI + 终端双模式）。

功能:
- 实时读取 X/Y/Z 位置
- 轴向移动与归零
- 后台线程周期刷新位置
- GUI 不可用时自动切到终端命令模式

运行方式:

```bash
# 推荐: 使用项目 venv
/home/bdml/Desktop/whisker_sensor_force_calibration/.venv/bin/python ./stage_control/control_and_monitor_stages.py
```

强制终端模式:

```bash
/home/bdml/Desktop/whisker_sensor_force_calibration/.venv/bin/python ./stage_control/control_and_monitor_stages.py --terminal-only
```

终端模式命令:

- `status`
- `x+ <mm>`, `x- <mm>`
- `y+ <mm>`, `y- <mm>`
- `z+ <mm>`, `z- <mm>`
- `x=<mm>`, `y=<mm>`, `z=<mm>`
- `home x|y|z`
- `help`
- `quit` / `exit`

### monitor_stage_positions.py

仅监控位置，不执行控制动作。

## 依赖

最小依赖:

- `pyserial`

GUI 模式额外依赖（任选其一）:

- Qt 后端: `pyqt5`
- Tk 后端: 系统包 `python3-tk`

安装示例:

```bash
/home/bdml/Desktop/whisker_sensor_force_calibration/.venv/bin/pip install pyserial pyqt5
```

## 快速排查

### 1) 报错 `FigureCanvasAgg is non-interactive`

说明当前是非交互后端。

处理方式:
- 直接使用 `--terminal-only`
- 或安装 GUI 后端依赖（`pyqt5` / `python3-tk`）

### 2) 报错缺少 `pyserial`

说明当前 Python 环境不对，或未装依赖。

处理方式:
- 使用项目 `.venv` 解释器运行
- 在同一环境安装 `pyserial`

### 3) 串口连接失败

检查:
- 串口设备是否为 `/dev/serial/by-id/...`
- 是否被其他程序占用（同一时间仅一个进程可访问）
- Linux 用户是否有串口权限（如 `dialout` 组）

### 4) Y 轴 home 丢步/起步扭矩不足

Y 轴为垂直安装，重力负载下默认速度扭矩不足会导致失步。
程序默认在每次 Y 轴移动和 home 前自动设置速度为 10000 microsteps/s
（通过 CMD 42 maxspeed + CMD 41 limit.approach.maxspeed）。

如需调整，可在运行前设置环境变量:

```bash
export STAGE_Y_SPEED=10000   # 默认值，可降低至 5000/2000 增加扭矩
```

或在终端模式中实时调整:

```
stage> speed y 10000
```

终端模式还支持以下 Y 轴专用命令:

- `zero y` — 将当前 Y 位置标记为 0（开环漂移恢复）
- `resync` — 重新读取所有轴位置
- `stress y [n]` — 反复 y+20/y-20 测试可靠性

## 安全与使用建议

- 每次实验前先 `home x/y/z`。
- 严格遵守行程限制:
   - X/Y: 0-101.6 mm
   - Z: 0-50.8 mm
- 先用终端模式确认通信和模块ID，再接入上层实验面板。
