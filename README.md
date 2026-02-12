# Whisker Sensor Force Calibration

胡须传感器力校准项目 - 包含Bota力传感器数据采集和stage控制模块

## 📁 项目结构

```
whisker_sensor_force_calibration/
│
├── stage_control/                    # 线性平台控制 ⭐
│   ├── stage_module.py              # 核心控制库
│   ├── control_and_monitor_stages.py # 交互式控制面板 (推荐)
│   ├── monitor_stage_positions.py   # 位置监控工具
│   └── README.md                     # 使用说明
│
├── bota_sensor/                      # Bota力传感器工具 ⭐
│   ├── visualize_realtime.py        # 实时可视化 - 综合视图 (推荐)
│   ├── visualize_detailed.py        # 实时可视化 - 详细视图
│   └── README.md                     # Bota传感器使用说明
│
├── bota_driver_py_example/           # Bota官方驱动和示例
│   ├── bota_driver_config/          # 传感器配置文件
│   │   └── ethercat_gen0.json       # EtherCAT Gen0配置 (当前使用)
│   └── examples/                     # 官方示例代码
│
└── QUICKSTART.md                     # 快速入门指南 (遗留)
```

## 🚀 快速开始

### 1. 线性平台控制

**最常用** - 控制X/Y/Z三轴线性平台：

```bash
cd "E:\02 2024\04 Research\whisker_sensor_force_calibration\stage_control"

# 启动控制面板 (推荐)
python control_and_monitor_stages.py

# 或仅监控位置
python monitor_stage_positions.py
```

详细说明见：[stage_control/README.md](stage_control/README.md)

### 2. Bota传感器实时可视化

**查看力/力矩实时数据**：

```bash
# 以管理员身份运行PowerShell
cd "E:\02 2024\04 Research\whisker_sensor_force_calibration"

# 启动实时可视化
python bota_sensor/visualize_realtime.py
```

详细说明见：[bota_sensor/README.md](bota_sensor/README.md)

## ⚙️ 系统配置

### Bota传感器 (BFT-ROKS-ECAT-M8)
- **连接方式**: EtherCAT
- **采样率**: ~1000 Hz
- **网络IP**: 10.20.0.100 (电脑以太网)
- **需要**: Npcap库 + 管理员权限

### Python环境
```bash
# 创建虚拟环境
conda create -n whisker python=3.10
conda activate whisker

# 安装依赖
pip install bota-driver matplotlib numpy pyserial
```

## 📊 主要功能

### 线性平台控制
- ✅ 三轴独立控制 (X/Y/Z)
- ✅ 实时位置显示
- ✅ 交互式GUI面板
- ✅ 增量移动 (±5mm)
- ✅ 直接输入目标位置
- ✅ 归零功能
- ✅ 自动范围限制保护

**硬件配置**:
- X/Y轴: LSM100A (0-101.6 mm)
- Z轴: LSM50A (0-50.8 mm)
- 通信: COM11, 9600 baud
- 精度: 0.000047625 mm/step

### Bota传感器
- ✅ 实时数据可视化 (力/力矩)
- ✅ 多种显示模式 (综合/详细)
- ✅ 自动归零 (tare)
- ✅ 温度监控
- ✅ 采样率可调 (200-2000 Hz)

**传感器配置**:
- 型号: BFT-ROKS-ECAT-M8 (SN000856)
- 连接: EtherCAT Gen0
- 采样率: ~1000 Hz

## 🔧 配置说明

### 调整传感器采样率

编辑 `bota_driver_py_example/bota_driver_config/ethercat_gen0.json`:

```json
"sensor_operation_params": {
    "sinc_length": 50  // 50=1000Hz, 100=512Hz, 32=2000Hz
}
```

### 网络配置

**Windows网络设置**:
1. 控制面板 → 网络和共享中心 → 以太网 → 属性
2. IPv4属性 → 手动设置：
   - IP地址: `10.20.0.100`
   - 子网掩码: `255.255.255.0`
   - 网关: 留空

## 📝 使用注意事项

### 运行Bota传感器程序前
1. ✅ 确保Npcap已安装
2. ✅ 以**管理员身份**运行PowerShell
3. ✅ 传感器已通电
4. ✅ 网线已连接
5. ✅ 网络IP已配置为 10.20.0.100

### 数据采集建议
- 启动前让传感器预热1-2分钟
- 归零时保持传感器无外力
- 避免撞击或过载传感器

## 🛠️ 故障排查

### 传感器连接失败
```bash
# 检查管理员权限
whoami /priv | findstr SeDebugPrivilege

# 检查网络配置
ipconfig | findstr "10.20.0"
```

### Npcap未安装
下载地址: https://npcap.com/
安装时勾选 "WinPcap API-compatible Mode"

### 可视化无数据
- 检查传感器电源指示灯
- 重启传感器
- 重新运行程序

## 📚 相关文档

- [Bota传感器详细说明](bota_sensor/README.md)
- [快速入门指南](QUICKSTART.md)
- [Bota官方文档](https://code.botasys.com/)

## 🔍 项目信息

- **开发日期**: 2024-2026
- **Python版本**: 3.8+
- **操作系统**: Windows 10/11
- **传感器**: Bota Systems BFT-ROKS-ECAT-M8 (SN000856)
