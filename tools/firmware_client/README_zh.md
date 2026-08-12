# OSRacer 独立固件客户端

## 1. 适用范围

`osracer-firmware-client` 是 Jetson Linux ARM64 上的独立 ESP32-S3 固件工具。
正式可执行文件已内置运行时、串口依赖、本地网页和两套经过固定校验的官方固件，
不依赖 ROS、ESP-IDF、Python 包或联网下载。

客户端只有一个。标准更新会读取当前设备信息并自动选择匹配的官方固件，无需
用户选择或识别资源文件。

本地网页支持中文和 English 切换。首次打开时按浏览器语言选择，之后只在当前
浏览器本地保存语言偏好。切换语言不会连接设备或改变更新状态。为避免现场支持
产生歧义，`UPDATE`、`FLASH CUSTOM`、`PREPARE B01/B02` 和
`ERASE AND FLASH B01/B02` 确认口令以及底层诊断错误保持英文原文。

该客户端只负责固件和 NVS 安全流程，不执行 `git pull`、ROS 编译或车辆启动。
固件完成后，客户可以独立更新并测试自己的 `osracer` 工作区。

## 2. 推荐入口

先停止占用 `/dev/osrbot_base` 的 ROS 节点，然后启动本地界面：

```bash
chmod +x osracer-firmware-client
./osracer-firmware-client ui
```

客户端只监听 `127.0.0.1`，启动后会打印带随机会话令牌的本地 URL。页面不加载
CDN，不提供局域网服务，同一时刻只允许一个固件操作。

SSH 环境可使用命令行：

```bash
./osracer-firmware-client inspect
./osracer-firmware-client bundles
./osracer-firmware-client official
./osracer-firmware-client custom /absolute/path/application.bin
./osracer-firmware-client erase --bundle B01
```

默认串口是 `/dev/osrbot_base`。如需修改，只使用全局参数：

```bash
./osracer-firmware-client --port /dev/ttyACM0 inspect
```

## 3. 三种操作

### 3.1 Official Firmware Update

客户端读取当前设备身份并只选择唯一匹配的 `B01` 或 `B02`。身份未知、车型
不匹配、升级会话未清理或逻辑参数无法备份时，不启动 App 写入。

标准流程：

1. 停止车辆并独占串口；
2. 识别固件、协议、Profile 和电压状态；
3. 备份当前固件能够导出的车辆参数；
4. 显示备份路径和 SHA256；
5. 输入 `UPDATE`；
6. 只写 App OTA 分区，不擦除 NVS；
7. 重连并核对官方目标；
8. 在目标支持时重新读取并比较车辆参数。

### 3.2 Custom Application Flash

该模式允许客户选择自己的 ESP32-S3 `application.bin`，不要求它遵循官方产品
命名。客户端仍会拒绝 bootloader、FullFlash、merged image、错误芯片、错误
校验和、缺少 validation hash 或超过 OTA slot 的文件。

开始前输入 `FLASH CUSTOM`。当前固件支持的参数会先备份，App OTA 不擦除 NVS。
客户 App 如果不再提供 OSR 查询或 OTA 命令，客户端只能报告传输结果，不能声称
客户功能已通过验证；后续恢复应使用高级模式。

### 3.3 Erase and Restore

这是隔离的高级恢复入口，只允许选择内置 `B01` 或 `B02`，不接受客户 FullFlash。

流程分两次确认：

1. 输入 `PREPARE B01` 或 `PREPARE B02`；
2. 逻辑参数备份属于尽力读取；原始 NVS 分区备份属于强制门槛；
3. 客户端显示原始 NVS 路径、offset、size 和 SHA256；
4. 操作者确认非 NVS 持久数据会丢失；
5. 输入 `ERASE AND FLASH B01` 或 `ERASE AND FLASH B02`；
6. 客户端再次核对同一设备、安全状态和未变化的 NVS；
7. 整片擦除、写入官方恢复镜像、将原始 NVS 写回 `0x9000`；
8. 回读 `0x6000` bytes 并逐字节比较，然后重启核对官方固件。

在第二次确认前不会执行擦除。secure boot、secure download、flash encryption、
设备身份、16 MiB flash、分区、资源 SHA256、NVS 文件或回读任一检查失败，流程
都会停止。整片擦除会删除 `storage`、OTA 历史和备用 App；第一版不备份
`storage`。

## 4. 备份和审计位置

默认状态目录：

```text
${XDG_STATE_HOME:-~/.local/state}/osracer/firmware-client/
├── audit/
├── backups/
├── nvs-raw/
└── uploads/
```

目录权限为 `0700`，备份文件权限为 `0600`。写入采用临时文件、`fsync`、原子
替换、重新读取、大小和 SHA256 复核。审计只记录状态、字段名、路径和 hash，
不记录车辆参数原值；参数原值只存在于私有备份文件。

如果结果显示 `Do not reflash`，不要再次运行 App 更新，应先按结果卡检查当前
设备状态。如果高级擦除已经开始且失败，必须保留已显示的 raw NVS 文件和
SHA256，按物理恢复流程处理。

## 5. 构建和验证

正式客户包必须在 Linux ARM64 上构建：

```bash
tools/firmware_client/build.sh
```

输出：

```text
tools/firmware_client/dist/firmware-client/osracer-firmware-client
tools/firmware_client/dist/firmware-client/osracer-firmware-client.sha256
```

构建脚本使用固定依赖版本，并检查命令行入口、来源信息、许可证和内置固件资源。
使用下载的可执行文件前，应通过随附的 SHA256 文件核对其完整性。

版本 0.1.2 面向 Linux ARM64 发布并内置受支持的官方固件资源。Official Update
仅在设备身份、协议、Profile、电压和升级状态全部通过检查后选择固件。Custom
App 和整片擦除恢复属于高级操作，只能使用经过授权的镜像，并应完整保留备份。

## 6. 支持入口

新部署统一使用 `osracer-firmware-client`。旧的
`osracer_firmware_update.py` 命令不作为受支持的客户入口发布。

## 7. 许可证边界

仓库自有源码继续遵循根目录 MIT 许可证。自包含可执行文件还打包了
GPL-2.0-or-later 的 `esptool`，因此该可执行文件按 GPL-2.0-or-later 分发；精确
版本、源码链接和其他依赖许可证见
`osracer_firmware_client/THIRD_PARTY_NOTICES.txt`。
