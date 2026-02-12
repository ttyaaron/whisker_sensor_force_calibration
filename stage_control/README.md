# Stage Control 模块

三轴线性平台控制系统

## 硬件配置

- **X轴**: LSM100A (0-101.6 mm)
- **Y轴**: LSM100A (0-101.6 mm)  
- **Z轴**: LSM50A (0-50.8 mm)
- **通信**: COM11, 9600 baud
- **步进精度**: 0.000047625 mm/step

## 文件说明

### stage_module.py
核心控制库，提供底层串口通信和stage控制功能。

**主要类**:
- `StageModuleControl(ser, mid, step_size, total_steps)`: stage控制器

**主要方法**:
- `home()`: 归零
- `go_pos_mm(pos, wait=True)`: 移动到指定位置(mm)
- `get_pos()`: 读取当前位置(mm)
- `set_speed()`: 设置速度

### control_and_monitor_stages.py
**交互式控制面板** - 带GUI的完整控制界面

**功能**:
- ✓ 实时显示X/Y/Z三轴位置
- ✓ ±5mm增量移动按钮
- ✓ 归零按钮
- ✓ 直接输入目标位置
- ✓ 多线程实时监控

**使用方法**:
```bash
python control_and_monitor_stages.py
```

### monitor_stage_positions.py
**位置监控工具** - 仅监控，不控制

**功能**:
- ✓ 实时显示当前位置
- ✓ 绘制位置变化曲线
- ✓ 无控制功能，纯监控

**使用方法**:
```bash
python monitor_stage_positions.py
```

## 快速开始

1. 确保stages连接到COM11端口
2. 运行控制面板:
   ```bash
   python control_and_monitor_stages.py
   ```

3. 使用界面进行控制:
   - 点击 `-5mm` / `+5mm` 按钮移动
   - 点击 `Home` 按钮归零
   - 在文本框输入位置后按Enter直接移动

## 注意事项

⚠️ **移动范围限制**:
- X/Y轴: 0-101.6 mm
- Z轴: 0-50.8 mm

⚠️ **串口占用**: 同一时间只能有一个程序访问COM11

⚠️ **初始化**: 每次使用前建议先执行归零操作
