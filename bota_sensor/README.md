# Bota力传感器工具集

用于Bota力/力矩传感器 (BFT-ROKS-ECAT-M8) 的实时数据可视化和校准工具。

## 📁 文件结构

```
bota_sensor/
├── visualize_realtime.py    # 实时可视化 - 综合视图 (推荐) ⭐
├── visualize_detailed.py    # 实时可视化 - 详细视图 (6个独立子图)
└── README.md                 # 本文件
```

## 🚀 快速开始

### 运行实时可视化

**推荐 - 综合视图**（显示力/力矩的合成大小和分量）：
```bash
python bota_sensor/visualize_realtime.py
```

**详细视图**（显示所有6个独立分量）：
```bash
python bota_sensor/visualize_detailed.py
```

## 📊 可视化说明

### visualize_realtime.py (推荐)
- **左上**: 合成力大小 (总力)
- **右上**: 合成力矩大小 (总力矩)
- **左下**: 力的三个分量 (Fx, Fy, Fz)
- **右下**: 力矩的三个分量 (Tx, Ty, Tz)
- 显示实时统计 (均值、最大值、采样率、温度)

### visualize_detailed.py
- 6个独立子图分别显示 Fx, Fy, Fz, Tx, Ty, Tz
- 适合需要单独观察每个分量的场景

## ⚙️ 配置

### 传感器信息
- **型号**: BFT-ROKS-ECAT-M8
- **序列号**: SN000856
- **连接方式**: EtherCAT (Gen0)
- **采样率**: ~1000 Hz (可调整)

### 调整采样率

编辑配置文件 `bota_driver_py_example/bota_driver_config/ethercat_gen0.json`:

```json
"sensor_operation_params": {
    "sinc_length": 50  // 调整此值
}
```

**采样率参考**:
- `sinc_length = 32`: ~1500-2000 Hz (最高速度)
- `sinc_length = 50`: ~1000 Hz (当前设置) ✓
- `sinc_length = 100`: ~512 Hz (标准)
- `sinc_length = 256`: ~200 Hz (最平滑)

**权衡**: 采样率越高 → 响应越快但噪声越大

## 📋 系统要求

### 必需软件
- **Python 3.8+**
- **Npcap** (Windows EtherCAT支持)
  - 下载: https://npcap.com/
  - 安装时勾选 "WinPcap API-compatible Mode"

### Python依赖
```bash
pip install bota-driver matplotlib numpy
```

### 网络配置
- 电脑以太网IP: `10.20.0.100`
- 子网掩码: `255.255.255.0`
- 传感器通过EtherCAT协议通信 (不使用TCP/IP)

## 🎮 操作提示

1. **启动程序**
   - 必须以**管理员身份**运行PowerShell
   - 确保传感器已通电并连接网线

2. **归零操作**
   - 程序启动时自动归零 (tare)
   - 归零时请勿触摸传感器

3. **测试传感器**
   - 用手指按压传感器 → 观察力的变化
   - 轻轻扭转 → 观察力矩的变化
   - 不同方向施力 → 观察各分量响应

4. **退出程序**
   - 按 `Ctrl+C`
   - 或直接关闭可视化窗口

## 🎨 颜色编码

- 🔴 **红色**: X轴分量
- 🟢 **绿色**: Y轴分量
- 🔵 **蓝色**: Z轴分量

## 🔧 故障排查

### 传感器找不到
```bash
# 检查网络配置
ipconfig

# 应该看到以太网适配器 IP: 10.20.0.100
```

### 连接失败
- 确保以管理员身份运行
- 检查Npcap是否已安装
- 重启传感器电源
- 检查网线连接

### 采样率不符预期
- 编辑 `ethercat_gen0.json` 调整 `sinc_length`
- 重启程序使配置生效

## 📝 开发信息

- **传感器驱动**: bota-driver v1.1.6
- **开发日期**: 2026年2月
- **EtherCAT协议**: CANopen over EtherCAT Gen0

## 📧 支持

遇到问题？检查：
1. Bota Systems官方文档: https://code.botasys.com/
2. 传感器固件版本: V1.3.8
3. 网络接口GUID: {0E6C1C31-6272-4102-85B3-CEDA92379035}
