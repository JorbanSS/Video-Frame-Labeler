# Video Frame Labeler

基于 PyQt5 + QFluentWidgets 的视频帧标注与动作识别工具，适合作为毕业设计展示与日常项目说明。

## 项目简介

本项目面向视频帧级标注、动作分析和结果管理场景，支持从工作目录中加载视频、导入动作识别模型、按采样策略进行逐帧分析，并将结果保存到对应视频项目中。界面采用 QFluentWidgets 风格，整体偏向桌面端工具应用，适合演示视频分析、标签管理和结果可视化流程。

## 主要功能

- 视频加载与基础播放预览
- 工作目录视频扫描与切换
- PyTorch 动作识别模型导入与管理
- 按采样频率进行视频帧分析
- 基于滑动窗口的动作置信度计算
- 低置信度自动判为无效类
- 分析结果显示与统计汇总
- 动作 JSON 配置保存与读取
- 视频帧提取与导出

## 目录结构

```text
Video-Frame-Labeler/
├─ app/
│  ├─ common/              # 通用配置、工具和信号
│  ├─ components/          # 复用界面组件
│  ├─ config/              # 应用全局配置文件
│  ├─ download/            # 默认下载/输出目录
│  ├─ resource/            # Qt 资源文件
│  └─ view/                # 各功能界面
├─ data/                   # 示例数据或运行生成的数据
├─ doc/                    # 截图与文档素材
├─ model/                  # 导入的模型与模型注册信息
├─ main.py                 # 程序入口
└─ README.md
```

## 安装

```bash
python3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python main.py
```

## 启动

```bash
.\.venv\Scripts\python.exe .\main.py
```

## 使用流程

1. 设置工作目录。
2. 导入或选择动作识别模型。
3. 加载视频。
4. 配置采样频率与窗口大小。
5. 开始分析并查看结果。
6. 需要时导出帧或打开对应 JSON。

## 动作分析思路

动作识别模块不会只看单帧结果，而是对采样帧附近的 $N$ 帧组成滑动窗口，对每个类别的概率取平均：

$$
P_i(c_k) = \frac{1}{|W_i|} \sum_{j \in W_i} p_j(c_k)
$$

其中：

- $W_i$ 为采样帧 $i$ 对应的窗口帧集合
- $c_k$ 为第 $k$ 个类别
- $p_j(c_k)$ 为第 $j$ 帧的类别概率

最高候选类别为：

$$
c_{\mathrm{top}} = \arg\max_{c_k \in C} P_i(c_k)
$$

若最高综合置信度低于阈值 $\tau = 0.5$，则最终标记为无效类：

$$
\mathrm{label}_i =
\begin{cases}
c_{\mathrm{top}}, & P_i(c_{\mathrm{top}}) \ge \tau \\
\mathrm{invalid}, & P_i(c_{\mathrm{top}}) < \tau
\end{cases}
$$

## 配置文件

### 全局配置 `app/config/config.json`

该文件由程序启动时自动加载，主要保存界面、工作目录、FFmpeg、导出和逐帧抽取相关设置。

常见字段：

- `Folders.WorkDirectory`：工作视频目录
- `Export.OutputDirectory`：导出目录
- `VideoFrameExtraction.Mode`：抽帧模式
- `VideoFrameExtraction.Fps`：每秒抽取帧数
- `VideoFrameExtraction.Interval`：抽帧间隔
- `VideoFrameExtraction.FrameCount`：抽取帧总数
- `VideoFrameExtraction.OutputDirectory`：抽帧输出目录
- `FFmpeg.Path`：FFmpeg 可执行文件路径
- `FFmpeg.Threads`：FFmpeg 线程数

### 动作分析配置 `action_analysis_config.json`

该文件与单个视频项目绑定，用于保存分析状态和预测结果。

```json
{
  "version": 3,
  "video_path": "E:/.../video.mp4",
  "selected_model_id": "model_xxx",
  "window_size": 5,
  "sample_rate": 1,
  "total_frames": 1234,
  "fps": 29.97,
  "duration_ms": 41123,
  "model_predictions": {
    "model_xxx": {
      "42": {
        "class_name": "closeup_celebration",
        "confidence": 0.94982,
        "probabilities": {
          "closeup_celebration": 0.94982,
          "dinking": 0.009957,
          "drive_smash": 0.020288,
          "idle_walking": 0.009006,
          "serve": 0.010929
        },
        "top_class_name": "closeup_celebration",
        "window_size": 5
      }
    }
  },
  "labeled_frames": {
    "42": "closeup_celebration"
  }
}
```

字段说明：

- `video_path`：原视频路径
- `selected_model_id`：当前模型 ID
- `window_size`：滑动窗口大小
- `sample_rate`：每秒采样次数
- `total_frames`：视频总帧数
- `fps`：视频帧率
- `duration_ms`：视频时长，单位毫秒
- `model_predictions`：各模型逐帧预测结果
- `labeled_frames`：最终标签结果

每条预测中：

- `class_name`：最终类别
- `confidence`：综合置信度
- `probabilities`：窗口平均后的各类别概率
- `top_class_name`：最高候选类别
- `window_size`：对应窗口大小

## 截图

![图片标记](./doc/img/%E5%9B%BE%E7%89%87%E6%A0%87%E8%AE%B0.png)

![视频帧提取](./doc/img/%E8%A7%86%E9%A2%91%E5%B8%A7%E6%8F%90%E5%8F%96.png)
