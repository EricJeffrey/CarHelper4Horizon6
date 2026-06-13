[English Version](README_EN.md).

# 极限竞速地平线6 车辆信息助手

**游戏内一键查看车辆详细介绍:**

![Result Example](resources/imgs/result.png)

[Demo Video](resources/demo.mp4)

## 功能特性

- **一键触发** — 支持 Xbox 手柄或键盘，游戏中直接使用，无需切屏
- **自动识别** — 自动截屏，通过黄色高亮边框定位当前选中的车辆卡片，OCR 读取车名
- **智能查询** — 优先匹配本地数据库（精确/关键词/模糊三级匹配），查不到时调用 LLM API 兜底

## 技术栈

| 技术 | 用途 |
|------|------|
| Python 3 | 核心逻辑 |
| PyQt5 | 悬浮窗界面 |
| PaddleOCR PP-OCRv5 | 从截图中识别车名 |
| OpenCV + PIL | 图像处理与选区检测 |
| win32gui / win32api | Windows 全屏截图 |
| pygame | 手柄输入监听 |
| rapidfuzz | 模糊字符串匹配 |

## 安装

### 1.下载项目文件

```bash
git clone https://github.com/EricJeffrey/CarHelper4Horizon6.git # 或者直接下载压缩包
cd CarHelper4Horizon6
pip install -r src/requirements.txt
```

> 注意: paddlepaddle 与 paddleocr 的版本建议按照文件里指定的版本，否则可能会导致运行时错误。参考 [Unimplemented ConvertPirAttribute2RuntimeAttribute error](https://github.com/PaddlePaddle/PaddleOCR/issues/17539#issuecomment-3842363440)

### 2.安装 PaddleOCR 模型
文字识别使用 PP-OCRv5_mobile 模型，可从 https://www.paddleocr.ai/main/version3.x/pipeline_usage/OCR.html 下载，将文件放入项目根目录的 `models/` 文件夹并修改 `src/config.json` 中的 `det_model_dir/rec_model_dir` 路径。`models/` 文件夹目录结构如下：
```
PP-OCRv5_mobile_det_infer:
- inference.json
- inference.pdiparams
- inference.yml
PP-OCRv5_mobile_rec_infer:
- inference.json
- inference.pdiparams
- inference.yml
```

### 3.车辆信息数据库
默认使用本地文件的形式作为数据库，位于 `resources/cars_info.jsonl`（每行一个 JSON 对象），由 LLM 批量生成，可能存在不准确之处，欢迎自行修正。
添加车辆信息的格式如下：

```json
{"m": "BMW", "m_cn": "宝马", "c": "M3 Competition", "i": "宝马 M3 Competition 是一款高性能运动轿车..."}
```

有大模型的也可以修改 config 中的 API 配置，调用自己的模型。参考 [自定义配置](#自定义配置)。

## 使用方法

### 启动工具

> 注意：如果你使用手柄玩游戏，请先连接手柄再启动工具。

```bash
cd src
python controller.py
```

在进入 FH6 前或在游戏过程中运行即可，程序会在后台待命。

### 游戏中触发

1. 在 FH6 的车辆列表中，选中一辆车使其出现**黄色高亮边框**
2. 按下手柄 **右摇杆**（或键盘 **`i`** 键），等待悬浮窗弹出
3. 再次按下关闭悬浮窗

### 自定义配置

所有设置都在 `src/config.json` 中：

- **api** — LLM API 相关设置
- **capture** — 截图与车辆检测参数
- **input** — 键盘和手柄快捷键设置
- **ocr** — PaddleOCR 文字识别模型设置
- **match** — 本地数据库匹配参数
- **debug** — 是否启用调试信息
- **overlay** — 悬浮窗大小、透明度等外观设置
```json
{
  "api": {                                  // LLM API 相关设置
    "enable": false,                        // 是否启用 LLM API
    "provider": "local",                    // LLM API 提供商
    "base_url": "http://localhost:8080",    // LLM API 基础 URL
    "api_key": "",                          // LLM API 密钥
    "model": "local-model",                 // LLM 模型名称
    "timeout": 30,                          // LLM API 超时时间（秒）
    "prompt_template": "..."                // LLM API 提示词模板
  },
  "capture": {                              // 截图与车辆检测参数
    "highlight_color_range": {              // 黄色高亮边框颜色范围
      "yellow": {
        "r_min": 140,
        "r_max": 220,
        "g_min": 180,
        "g_max": 255,
        "b_min": 0,
        "b_max": 60
      }
    },
    "min_box_width": 200,                   // 最小检测框宽度
    "min_box_height": 150,                  // 最小检测框高度
    "crop_top_ratio": 0.5,                  // 裁剪顶部比例（0.5）
  },
  "input": {                                // 键盘和手柄快捷键设置
    "trigger_button": "RIGHTSTICK",         // 触发按钮（右摇杆）
    "trigger_key": "i",                     // 触发键（i）
    "poll_interval": 0.016,                 // 手柄输入轮询间隔（0.016）
    "process_stop_timeout": 2.0             // 进程停止超时时间（2.0秒）
  },
  "ocr": {                                  // PaddleOCR 文字识别模型设置
    "det_model_dir": "../models/PP-OCRv5_mobile_det_infer", // 检测模型目录
    "rec_model_dir": "../models/PP-OCRv5_mobile_rec_infer" // 识别模型目录
  },
  "match": {                                // 本地数据库匹配参数
    "data_path": "../resources/cars_info.jsonl", // 数据库文件路径
    "match_threshold": 85,                      // 匹配阈值（0-100）
    "ambiguity_margin": 1,                      // 模糊匹配top1与top2的匹配度最小差值
    "model_only_safe_threshold": 95             // 仅型号匹配，车辆品牌不匹配时的匹配度阈值（0-100）
  },
  "debug": {                                // 是否启用调试信息
    "enabled": false
  },
  "overlay": {                              // 悬浮窗大小、透明度等外观设置
    "width": 960,
    "max_height": 720,
    "opacity": 0.6,
    "font_size": 20,
    "scroll_speed": 1,                      // 每毫秒滚动的像素数
    "scroll_interval": 30,                  // 滚动间隔（毫秒）
    "scroll_delay": 2000,                   // 滚动延迟（毫秒）
    "bg_color": "rgba(0, 0, 0, 0.6)",
    "text_color": "#EEEEEE",
    "err_win_pos": "right-center"
  }
}
```

## 项目结构

```
automaker-helper/
├── src/
│   ├── controller.py       # 主控模块，串联各功能
│   ├── input_module.py     # Xbox 手柄 + 键盘监听
│   ├── capture_module.py   # 截图 & 黄色边框卡片定位
│   ├── ocr_module.py       # PaddleOCR 文字识别
│   ├── match_module.py     # 本地数据库匹配
│   ├── api_module.py       # LLM API 调用
│   ├── overlay_module.py   # 悬浮窗界面
│   ├── gui_bridge.py       # Qt 信号桥接
│   ├── utils.py            # 日志工具
│   ├── config.json         # 全部配置项
│   └── requirements.txt    # Python 依赖
├── resources/
│   └── cars_info.jsonl     # 车辆数据库（JSONL 格式）
└── models/                 # 离线 OCR 模型
```

## 工作原理

1. **截取画面** — 全屏截图 → 通过颜色轮廓分析定位黄色高亮的车辆卡片 → 裁剪车名区域
2. **文字识别** — 将裁剪后的图片送入 PaddleOCR，提取车名字符串
3. **匹配查询** — 查询本地 `cars_info.jsonl`，匹配失败时调用 LLM API 获取介绍
4. **展示结果** — 在无边框置顶悬浮窗中渲染结果（Windows 毛玻璃效果）
