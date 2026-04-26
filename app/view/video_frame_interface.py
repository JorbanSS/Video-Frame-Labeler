# coding:utf-8
import os
import sys
import subprocess
import json
import re
import shutil
from pathlib import Path
from datetime import timedelta

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QStandardPaths
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, 
                             QFileDialog, QLabel, QSpinBox, QComboBox, 
                             QPushButton, QProgressBar, QTextEdit, QGroupBox,
                             QRadioButton, QButtonGroup, QMessageBox, QLineEdit, QSizePolicy)
from qfluentwidgets import (CardWidget, PushButton, SpinBox, ComboBox, 
                           StrongBodyLabel, BodyLabel, TitleLabel, 
                           StateToolTip, InfoBar, InfoBarPosition,
                           FluentIcon as FIF, TextEdit, ProgressBar, LineEdit,
                           RadioButton)

from .gallery_interface import GalleryInterface
from ..common.config import cfg
from ..common.signal_bus import signalBus
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}


class VideoInfoWidget(CardWidget):
    """ 视频信息显示组件 """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)  # 设置大小策略为可扩展
        self.initUI()
    
    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # 标题
        titleLabel = StrongBodyLabel("视频信息", self)
        layout.addWidget(titleLabel)
        
        # 视频信息网格
        infoGrid = QGridLayout()
        infoGrid.setSpacing(10)
        infoGrid.setColumnStretch(1, 1)  # 让第二列（值列）占满剩余空间
        
        # 文件路径
        self.filePathLabel = BodyLabel("文件路径: ", self)
        infoGrid.addWidget(self.filePathLabel, 0, 0)
        self.filePathValue = BodyLabel("-", self)
        self.filePathValue.setWordWrap(True)
        self.filePathValue.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        infoGrid.addWidget(self.filePathValue, 0, 1)
        
        # 分辨率
        self.resolutionLabel = BodyLabel("分辨率: ", self)
        infoGrid.addWidget(self.resolutionLabel, 1, 0)
        self.resolutionValue = BodyLabel("-", self)
        self.resolutionValue.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        infoGrid.addWidget(self.resolutionValue, 1, 1)
        
        # 帧率
        self.fpsLabel = BodyLabel("帧率: ", self)
        infoGrid.addWidget(self.fpsLabel, 2, 0)
        self.fpsValue = BodyLabel("-", self)
        self.fpsValue.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        infoGrid.addWidget(self.fpsValue, 2, 1)
        
        # 时长
        self.durationLabel = BodyLabel("时长: ", self)
        infoGrid.addWidget(self.durationLabel, 3, 0)
        self.durationValue = BodyLabel("-", self)
        self.durationValue.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        infoGrid.addWidget(self.durationValue, 3, 1)
        
        # 总帧数
        self.totalFramesLabel = BodyLabel("总帧数: ", self)
        infoGrid.addWidget(self.totalFramesLabel, 4, 0)
        self.totalFramesValue = BodyLabel("-", self)
        self.totalFramesValue.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        infoGrid.addWidget(self.totalFramesValue, 4, 1)
        
        layout.addLayout(infoGrid)
        layout.addStretch()
    
    def updateInfo(self, videoInfo):
        """ 更新视频信息 """
        self.filePathValue.setText(videoInfo.get('file_path', '-'))
        self.resolutionValue.setText(f"{videoInfo.get('width', 0)}×{videoInfo.get('height', 0)}")
        self.fpsValue.setText(f"{videoInfo.get('fps', 0):.2f} fps")
        
        # 格式化时长显示
        duration = videoInfo.get('duration', 0)
        if duration > 0:
            td = timedelta(seconds=int(duration))
            hours, remainder = divmod(td.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                duration_str = f"{minutes:02d}:{seconds:02d}"
            self.durationValue.setText(duration_str)
        else:
            self.durationValue.setText("-")
            
        self.totalFramesValue.setText(str(videoInfo.get('total_frames', 0)))


class FrameExtractionWorker(QThread):
    """ 帧提取工作线程 """
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)
    log = pyqtSignal(str)
    
    def __init__(self, videoPath, outputDir, settings):
        super().__init__()
        self.videoPath = videoPath
        self.outputDir = outputDir
        self.settings = settings
        self._running = True
    
    def run(self):
        try:
            # 构建FFmpeg命令
            cmd = self.buildFFmpegCommand()
            
            # 执行FFmpeg命令
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding='utf-8',
                errors='replace'
            )
            
            # 读取输出并计算进度
            # 使用实际估计的输出帧数来计算进度
            targetFrames = getattr(self, 'estimatedOutputFrames', self.settings.get('total_frames', 100))
            currentFrame = 0
            
            # 编译正则表达式以匹配 frame= 格式
            frame_pattern = re.compile(r'frame\s*=\s*(\d+)')
            
            for line in process.stdout:
                if not self._running:
                    process.terminate()
                    break
                
                self.log.emit(line.strip())
                
                # 解析进度信息 - 使用正则表达式匹配 frame= 格式
                frame_match = frame_pattern.search(line)
                if frame_match:
                    try:
                        frameNum = int(frame_match.group(1))
                        currentFrame = frameNum
                        # 防止除以0，并限制最大值
                        if targetFrames > 0:
                            progress = min(int((currentFrame / targetFrames) * 100), 100)
                            self.progress.emit(progress)
                    except (ValueError, IndexError):
                        pass
            
            process.wait()
            
            if process.returncode == 0:
                self.finished.emit(True, f"帧提取完成！共提取 {currentFrame} 帧")
            else:
                self.finished.emit(False, "FFmpeg处理失败！")
                
        except Exception as e:
            self.finished.emit(False, f"错误: {str(e)}")
    
    def buildFFmpegCommand(self):
        """ 构建FFmpeg命令 """
        ffmpegPath = cfg.get(cfg.ffmpegPath) if hasattr(cfg, 'ffmpegPath') else 'ffmpeg'
        
        # 基础命令
        cmd = [ffmpegPath, '-i', self.videoPath]
        
        # 输出设置
        outputFormat = self.settings.get('format', 'png')
        outputPrefix = self.settings.get('prefix', 'frame')
        digits = self.settings.get('digits', 6)
        digitsStr = f'%0{digits}d'
        outputPattern = os.path.join(self.outputDir, f'{outputPrefix}_{digitsStr}.{outputFormat}')
        
        # 计算实际会提取的帧数（用于进度计算）
        self.estimatedOutputFrames = self._calculateEstimatedOutputFrames()
        
        # 帧率设置
        extraction_mode = self.settings.get('extraction_mode', 'fps')
        if extraction_mode == 'fps':
            fps = self.settings.get('fps', 20) / 10.0  # Convert back from integer
            cmd.extend(['-vf', f'fps={fps}'])
        elif extraction_mode == 'interval':
            interval = self.settings.get('interval', 1)
            cmd.extend(['-vf', f'fps=1/{interval}'])
        else:  # frame_count mode
            total_frames = self.settings.get('total_frames', 100)
            target_frames = self.settings.get('frame_count', 100)
            # 计算帧间隔
            frame_interval = max(1, int(total_frames / target_frames))
            cmd.extend(['-vf', f'select=not(mod(n\\,{frame_interval}))', '-vsync', 'vfr'])
        
        # 图片尺寸设置
        # 输出文件
        cmd.append(outputPattern)
        
        return cmd
    
    def _calculateEstimatedOutputFrames(self):
        """ 计算预计输出的帧数 """
        video_duration = self.settings.get('duration', 0)
        total_frames = self.settings.get('total_frames', 100)
        extraction_mode = self.settings.get('extraction_mode', 'fps')
        
        if extraction_mode == 'fps':
            # 按帧率提取
            fps = self.settings.get('fps', 20) / 10.0
            return max(1, int(video_duration * fps))
        elif extraction_mode == 'interval':
            # 按时间间隔提取
            interval = self.settings.get('interval', 1)
            return max(1, int(video_duration / interval))
        else:  # frame_count mode
            # 指定输出帧数
            return self.settings.get('frame_count', 100)
    
    def stop(self):
        self._running = False


class VideoFrameInterface(GalleryInterface):
    """ 视频帧提取界面 """
    
    def __init__(self, parent=None):
        super().__init__(
            title="视频帧提取",
            subtitle="使用FFmpeg从视频中提取帧",
            parent=parent
        )
        
        self.setObjectName("videoFrameInterface")
        self.videoPath = None
        self.videoInfo = {}
        self.extractionWorker = None
        self.estimatedImages = 0
        self.settingsGroup = None
        self.outputGroup = None
        self.controlGroup = None  # 保存控制组的引用
        
        self.loadSettings()
        self.initUI()
        self.connectSignals()
        signalBus.workDirectoryChanged.connect(lambda _: self.refreshWorkDirectoryVideos())
        
    def initUI(self):
        # 视频选择和信息显示区域
        videoGroup = self.addExampleCard(
            title="视频选择",
            widget=self.createVideoSelectionWidget(),
            sourcePath="",
            stretch=1  # 让视频选择区域占满可用宽度
        )
        
        # 提取设置区域
        self.settingsGroup = self.addExampleCard(
            title="提取设置",
            widget=self.createExtractionSettingsWidget(),
            sourcePath="",
            stretch=1  # 让提取设置区域占满可用宽度
        )
        
        # 输出设置区域
        self.outputGroup = self.addExampleCard(
            title="输出设置",
            widget=self.createOutputSettingsWidget(),
            sourcePath="",
            stretch=1  # 让输出设置区域占满可用宽度
        )
        
        # 控制按钮和进度区域（包含预计输出图片数量）
        self.controlGroup = self.addExampleCard(
            title="提取控制",
            widget=self.createControlWidget(),
            sourcePath="",
            stretch=1  # 让组件占满可用宽度
        )
    
        self.setExtractionSectionsVisible(False)

    def createVideoSelectionWidget(self):
        """ 创建视频选择组件 """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)
        layout.setContentsMargins(0, 0, 0, 0)  # 移除边距，让内容占满空间
        
        # 选择文件按钮
        self.selectFileButton = PushButton("选择视频文件", self, FIF.VIDEO)
        self.selectFileButton.clicked.connect(self.selectVideoFile)
        layout.addWidget(self.selectFileButton)

        projectButtonLayout = QHBoxLayout()
        projectButtonLayout.setSpacing(15)

        self.selectWorkDirectoryButton = PushButton("选择工作目录", self, FIF.FOLDER)
        self.selectWorkDirectoryButton.clicked.connect(self.selectVideoWorkDirectory)
        projectButtonLayout.addWidget(self.selectWorkDirectoryButton)

        self.openWorkDirectoryButton = PushButton("打开工作目录", self, FIF.FOLDER)
        self.openWorkDirectoryButton.clicked.connect(self.openVideoWorkDirectory)
        projectButtonLayout.addWidget(self.openWorkDirectoryButton)

        self.pasteWorkDirectoryButton = PushButton("粘贴", self, FIF.PASTE)
        self.pasteWorkDirectoryButton.clicked.connect(self.pasteVideoWorkDirectoryFromClipboard)
        projectButtonLayout.addWidget(self.pasteWorkDirectoryButton)
        projectButtonLayout.addStretch()
        layout.addLayout(projectButtonLayout)

        quickLayout = QHBoxLayout()
        quickLayout.setSpacing(10)
        quickLayout.addWidget(BodyLabel("工作目录视频:", self))
        self.quickVideoComboBox = ComboBox(self)
        self.quickVideoComboBox.setMinimumWidth(360)
        quickLayout.addWidget(self.quickVideoComboBox, 1)

        self.loadQuickVideoButton = PushButton("选择", self, FIF.PLAY)
        self.loadQuickVideoButton.clicked.connect(self.selectQuickVideo)
        quickLayout.addWidget(self.loadQuickVideoButton)

        self.refreshQuickVideoButton = PushButton("刷新", self, FIF.ROTATE)
        self.refreshQuickVideoButton.clicked.connect(self.refreshWorkDirectoryVideos)
        quickLayout.addWidget(self.refreshQuickVideoButton)
        layout.addLayout(quickLayout)

        self.quickVideoPaths = {}
        self.refreshWorkDirectoryVideos()
        
        # 视频信息显示
        self.videoInfoWidget = VideoInfoWidget(self)
        self.videoInfoWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.videoInfoWidget, 1)  # 添加拉伸因子，让视频信息组件占满剩余空间
        
        return widget
    
    def createExtractionSettingsWidget(self):
        """ 创建提取设置组件 """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)

        # 提取模式1: 按帧率提取
        fpsModeLayout = QHBoxLayout()
        self.fpsModeRadio = RadioButton("每秒提取", self)
        fpsModeLayout.addWidget(self.fpsModeRadio)
        
        self.fpsSpinBox = SpinBox(self)
        self.fpsSpinBox.setRange(1, 600)  # 0.1-60 fps, multiply by 10 for integer
        self.fpsSpinBox.setValue(cfg.extractionFps.value)  # 从配置加载
        self.fpsSpinBox.setSingleStep(1)
        fpsModeLayout.addWidget(self.fpsSpinBox)
        
        fpsModeLayout.addWidget(BodyLabel("帧", self))
        fpsModeLayout.addStretch()
        
        layout.addLayout(fpsModeLayout)

        # 提取模式2: 按时间间隔提取
        intervalModeLayout = QHBoxLayout()
        self.intervalModeRadio = RadioButton("每帧间隔", self)
        intervalModeLayout.addWidget(self.intervalModeRadio)
        
        self.intervalSpinBox = SpinBox(self)
        self.intervalSpinBox.setRange(1, 3600)
        self.intervalSpinBox.setValue(cfg.extractionInterval.value)  # 从配置加载
        intervalModeLayout.addWidget(self.intervalSpinBox)
        
        intervalModeLayout.addWidget(BodyLabel("秒", self))
        intervalModeLayout.addStretch()
        
        layout.addLayout(intervalModeLayout)

        # 提取模式3: 指定输出帧数
        frameCountLayout = QHBoxLayout()
        self.frameCountModeRadio = RadioButton("自定义输出共", self)
        frameCountLayout.addWidget(self.frameCountModeRadio)
        
        self.frameCountSpinBox = SpinBox(self)
        self.frameCountSpinBox.setRange(1, 10000)
        self.frameCountSpinBox.setValue(cfg.extractionFrameCount.value)  # 从配置加载
        frameCountLayout.addWidget(self.frameCountSpinBox)
        
        frameCountLayout.addWidget(BodyLabel("帧", self))
        frameCountLayout.addStretch()
        
        layout.addLayout(frameCountLayout)

        # 从配置加载模式
        self.modeGroup = QButtonGroup(self)
        self.modeGroup.addButton(self.fpsModeRadio)
        self.modeGroup.addButton(self.intervalModeRadio)
        self.modeGroup.addButton(self.frameCountModeRadio)
        
        if cfg.extractionMode.value == "fps":
            self.fpsModeRadio.setChecked(True)
        elif cfg.extractionMode.value == "interval":
            self.intervalModeRadio.setChecked(True)
        else:
            self.frameCountModeRadio.setChecked(True)

        # 预计图片数量显示
        self.estimatedCountLabel = BodyLabel("预计输出图片数量: 0 张", self)
        layout.addWidget(self.estimatedCountLabel)

        # 连接信号
        self.fpsModeRadio.toggled.connect(self.onModeChanged)
        self.intervalModeRadio.toggled.connect(self.onModeChanged)
        self.frameCountModeRadio.toggled.connect(self.onModeChanged)
        self.fpsSpinBox.valueChanged.connect(self.updateEstimatedCount)
        self.intervalSpinBox.valueChanged.connect(self.updateEstimatedCount)
        self.frameCountSpinBox.valueChanged.connect(self.updateEstimatedCount)
        self.updateModeInputState()

        return widget
    
    def createOutputSettingsWidget(self):
        """ 创建输出设置组件 """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)
        
        # 输出格式
        formatLayout = QHBoxLayout()
        formatLabel = BodyLabel("输出格式:", self)
        self.formatComboBox = ComboBox(self)
        self.formatComboBox.addItems(['png', 'jpg', 'bmp', 'tiff'])
        self.formatComboBox.setCurrentText(cfg.outputFormat.value)  # 从配置加载
        
        formatLayout.addWidget(formatLabel)
        formatLayout.addWidget(self.formatComboBox)
        formatLayout.addStretch()
        layout.addLayout(formatLayout)
        
        # 图片前缀
        prefixLayout = QHBoxLayout()
        prefixLabel = BodyLabel("图片前缀:", self)
        self.prefixEdit = LineEdit(self)
        self.prefixEdit.setText(cfg.outputPrefix.value)
        self.prefixEdit.setPlaceholderText("默认为 frame")
        
        prefixLayout.addWidget(prefixLabel)
        prefixLayout.addWidget(self.prefixEdit)
        prefixLayout.addStretch()
        layout.addLayout(prefixLayout)
        
        # 编号位数
        digitsLayout = QHBoxLayout()
        digitsLabel = BodyLabel("编号位数:", self)
        self.digitsSpinBox = SpinBox(self)
        self.digitsSpinBox.setRange(1, 10)
        self.digitsSpinBox.setValue(cfg.numberingDigits.value)
        
        digitsLayout.addWidget(digitsLabel)
        digitsLayout.addWidget(self.digitsSpinBox)
        digitsLayout.addStretch()
        layout.addLayout(digitsLayout)
        
        # 输出目录
        outputDirLayout = QHBoxLayout()
        outputDirLabel = BodyLabel("输出目录:", self)
        self.outputDirEdit = LineEdit(self)
        outputDir = cfg.outputDirectory.value
        if not outputDir:
            outputDir = str(Path.home() / "VideoFrames")
        self.outputDirEdit.setText(outputDir)
        self.outputDirEdit.setReadOnly(True)
        self.outputDirEdit.setClearButtonEnabled(False)
        
        self.copyOutputDirButton = PushButton("复制", self, FIF.COPY)
        self.copyOutputDirButton.clicked.connect(self.copyOutputDirectory)
        
        outputDirLayout.addWidget(outputDirLabel)
        outputDirLayout.addWidget(self.outputDirEdit)
        outputDirLayout.addWidget(self.copyOutputDirButton)
        layout.addLayout(outputDirLayout)
        
        return widget
    
    def createControlWidget(self):
        """ 创建控制组件 """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)
        layout.setContentsMargins(0, 0, 0, 0)  # 移除边距
        
        # 控制按钮
        buttonLayout = QHBoxLayout()
        self.startButton = PushButton("开始提取", self, FIF.PLAY)
        self.startButton.clicked.connect(self.startExtraction)
        self.startButton.setEnabled(False)
        
        self.stopButton = PushButton("停止", self, FIF.CLOSE)
        self.stopButton.clicked.connect(self.stopExtraction)
        self.stopButton.setEnabled(False)
        
        self.openOutputButton = PushButton("打开输出文件夹", self, FIF.FOLDER)
        self.openOutputButton.clicked.connect(self.openOutputDirectory)
        self.openOutputButton.setEnabled(True)
        
        buttonLayout.addWidget(self.startButton)
        buttonLayout.addWidget(self.stopButton)
        buttonLayout.addWidget(self.openOutputButton)
        buttonLayout.addStretch()
        
        layout.addLayout(buttonLayout)
        
        # 进度信息显示
        progressInfoLayout = QHBoxLayout()
        self.progressLabel = BodyLabel("预计输出图片数量: 0 张", self)
        self.progressInfoLabel = BodyLabel("", self)
        progressInfoLayout.addWidget(self.progressLabel)
        progressInfoLayout.addWidget(self.progressInfoLabel)
        progressInfoLayout.addStretch()
        layout.addLayout(progressInfoLayout)
        
        # 进度条
        self.progressBar = ProgressBar(self)
        self.progressBar.setFixedHeight(8)
        self.progressBar.setMinimumWidth(200)
        self.progressBar.setVisible(False)
        layout.addWidget(self.progressBar)
        
        # 日志输出
        logLabel = BodyLabel("提取日志:", self)
        layout.addWidget(logLabel)
        
        # 创建日志组件，设置占满宽度
        self.logTextEdit = TextEdit(self)
        self.logTextEdit.setMaximumHeight(200)
        self.logTextEdit.setReadOnly(True)
        self.logTextEdit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.logTextEdit, 1)  # 添加拉伸因子占满可用空间
        
        return widget
    
    def onModeChanged(self, checked):
        """ 模式切换处理 """
        self.updateModeInputState()
        self.updateEstimatedCount()

    def updateModeInputState(self):
        """仅允许编辑当前选中的提取模式参数。"""
        self.fpsSpinBox.setEnabled(self.fpsModeRadio.isChecked())
        self.intervalSpinBox.setEnabled(self.intervalModeRadio.isChecked())
        self.frameCountSpinBox.setEnabled(self.frameCountModeRadio.isChecked())
    
    def updateEstimatedCount(self):
        """ 更新预计图片数量 """
        if not self.videoInfo:
            self.estimatedImages = 0
            self.estimatedCountLabel.setText("预计输出图片数量: 0 张")
            self.progressLabel.setText("预计输出图片数量: 0 张")
            return
            
        total_frames = self.videoInfo.get('total_frames', 0)
        duration = self.videoInfo.get('duration', 0)
        fps = self.videoInfo.get('fps', 30)
        
        if self.fpsModeRadio.isChecked():
            # 按帧率提取
            extract_fps = self.fpsSpinBox.value() / 10.0
            self.estimatedImages = int(duration * extract_fps)
            self.estimatedCountLabel.setText(f"预计一共输出图片: {self.estimatedImages} 张")
            self.progressLabel.setText(f"预计一共输出图片: {self.estimatedImages} 张")
            
        elif self.intervalModeRadio.isChecked():
            # 按时间间隔提取
            interval = self.intervalSpinBox.value()
            self.estimatedImages = int(duration / interval)
            self.estimatedCountLabel.setText(f"预计一共输出图片: {self.estimatedImages} 张")
            self.progressLabel.setText(f"预计一共输出图片: {self.estimatedImages} 张")
            
        else:
            # 指定输出帧数
            frame_count = self.frameCountSpinBox.value()
            frame_interval = total_frames / frame_count if frame_count > 0 else 0
            self.estimatedCountLabel.setText(f"预计每 {frame_interval:.2f} 帧选一张")
            self.progressLabel.setText(f"预计每 {frame_interval:.2f} 帧选一张")
    
    def selectVideoFile(self):
        """ 选择视频文件 """
        startDir = self.resolveDialogDirectory(
            self.videoPath,
            cfg.get(cfg.workDirectory),
            self.outputDirEdit.text()
        )
        filePath, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            startDir,
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.webm);;所有文件 (*.*)"
        )
        
        if filePath:
            self.loadVideoFile(filePath)

    def selectVideoWorkDirectory(self):
        startDir = self.resolveDialogDirectory(
            cfg.get(cfg.workDirectory),
            self.outputDirEdit.text(),
            Path(self.videoPath).parent if self.videoPath else None,
        )
        folderPath = QFileDialog.getExistingDirectory(self, "选择工作目录", startDir)
        if folderPath:
            self.setVideoWorkDirectory(folderPath)

    def pasteVideoWorkDirectoryFromClipboard(self):
        from PyQt5.QtWidgets import QApplication

        clipboardText = QApplication.clipboard().text().strip()
        if not clipboardText:
            InfoBar.warning("警告", "剪贴板为空", duration=2000, parent=self)
            return

        path = Path(clipboardText)
        if not path.exists():
            InfoBar.error("错误", f"路径不存在: {clipboardText}", duration=3000, parent=self)
            return

        if not path.is_dir():
            InfoBar.error("错误", f"路径不是工作目录: {clipboardText}", duration=3000, parent=self)
            return

        self.setVideoWorkDirectory(str(path))

    def setVideoWorkDirectory(self, folderPath):
        folderPath = str(Path(folderPath))
        if cfg.get(cfg.workDirectory) == folderPath:
            self.refreshWorkDirectoryVideos()
            return

        cfg.set(cfg.workDirectory, folderPath)
        signalBus.workDirectoryChanged.emit(folderPath)
        self.refreshWorkDirectoryVideos()
        InfoBar.success("成功", f"工作目录已设置为: {folderPath}", duration=2000, parent=self)

    def openPathInFileManager(self, path):
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)])
            else:
                subprocess.run(["xdg-open", str(path)])
        except Exception as e:
            InfoBar.error("错误", f"无法打开: {str(e)}", duration=3000, parent=self)

    def openVideoWorkDirectory(self):
        workDir = cfg.get(cfg.workDirectory)
        if not workDir:
            InfoBar.warning("警告", "请先选择工作目录", duration=2000, parent=self)
            return

        Path(workDir).mkdir(parents=True, exist_ok=True)
        self.openPathInFileManager(workDir)

    def refreshWorkDirectoryVideos(self):
        """Refresh quick video list from configured work directory."""
        if not hasattr(self, "quickVideoComboBox"):
            return

        self.quickVideoComboBox.clear()
        self.quickVideoPaths = {}

        workDir = cfg.get(cfg.workDirectory)
        if not workDir or not os.path.isdir(workDir):
            self.quickVideoComboBox.addItem("未设置工作目录")
            self.loadQuickVideoButton.setEnabled(False)
            return

        videos = []
        for path in Path(workDir).iterdir():
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
                videos.append(path)

        videos.sort(key=self.getPathModifiedTime, reverse=True)
        for path in videos:
            display = str(path.relative_to(workDir))
            self.quickVideoComboBox.addItem(display)
            self.quickVideoPaths[display] = str(path)

        if not videos:
            self.quickVideoComboBox.addItem("工作目录下未找到视频")

        self.loadQuickVideoButton.setEnabled(bool(videos))

    def getPathModifiedTime(self, path):
        try:
            return Path(path).stat().st_mtime
        except OSError:
            return 0

    def selectQuickVideo(self):
        display = self.quickVideoComboBox.currentText()
        filePath = self.quickVideoPaths.get(display)
        if filePath:
            self.loadVideoFile(filePath)

    def loadVideoFile(self, filePath):
        self.videoPath = filePath
        self.setExtractionSectionsVisible(True)
        projectPaths = self.buildProjectPaths(filePath)
        self.outputDirEdit.setText(str(projectPaths['origin_dir']))
        self.loadVideoInfo(filePath)
        self.startButton.setEnabled(True)
        self.updateEstimatedCount()
    
    def selectOutputDirectory(self):
        """ 选择输出目录 """
        dirPath = QFileDialog.getExistingDirectory(
            self,
            "选择输出目录",
            str(Path.home())
        )
        
        if dirPath:
            self.outputDirEdit.setText(dirPath)
    
    def copyOutputDirectory(self):
        """ 复制输出目录到剪贴板 """
        from PyQt5.QtWidgets import QApplication
        try:
            paths = self.prepareProjectStructure(self.videoPath)
        except Exception as e:
            InfoBar.error(
                "错误",
                f"准备项目目录失败: {str(e)}",
                duration=3000,
                parent=self
            )
            return

        self.videoPath = str(paths['video_path'])
        outputDir = str(paths['origin_dir'])
        self.outputDirEdit.setText(outputDir)
        if self.videoInfo:
            self.videoInfo['file_path'] = self.videoPath
            self.videoInfoWidget.updateInfo(self.videoInfo)
        if outputDir:
            clipboard = QApplication.clipboard()
            clipboard.setText(outputDir)
            InfoBar.success(
                "成功",
                "输出目录已复制到剪贴板！",
                duration=1500,
                parent=self
            )
        else:
            InfoBar.warning(
                "警告",
                "输出目录为空",
                duration=1500,
                parent=self
            )
    
    def resolveDialogDirectory(self, *candidates):
        """Select a valid directory for file dialogs."""
        for path in candidates:
            if not path:
                continue

            candidate = str(path).strip()
            if not candidate:
                continue

            if os.path.isfile(candidate):
                candidate = os.path.dirname(candidate)

            if os.path.isdir(candidate):
                return candidate

        for location in (
            QStandardPaths.MoviesLocation,
            QStandardPaths.DocumentsLocation,
            QStandardPaths.HomeLocation
        ):
            candidate = QStandardPaths.writableLocation(location)
            if candidate and os.path.isdir(candidate):
                return candidate

        home = str(Path.home())
        if os.path.isdir(home):
            return home

        return os.getcwd()

    def setExtractionSectionsVisible(self, visible: bool):
        """Show/hide all sections except video selection."""
        for card in (self.settingsGroup, self.outputGroup, self.controlGroup):
            if card:
                card.setVisible(visible)

    def buildProjectPaths(self, videoPath):
        """Build the standardized project folder structure paths."""
        videoPath = Path(videoPath)
        parentDir = videoPath.parent

        # 已在同名项目目录中时，直接复用当前目录，避免再嵌套一层同名目录
        if parentDir.name.lower() == videoPath.stem.lower():
            projectDir = parentDir
            targetVideoPath = videoPath
        else:
            projectDir = parentDir / videoPath.stem
            targetVideoPath = projectDir / videoPath.name

        return {
            'project_dir': projectDir,
            'video_path': targetVideoPath,
            'origin_dir': projectDir / 'origin_pic',
            'labeled_dir': projectDir / 'labeled_pic'
        }

    def prepareProjectStructure(self, sourceVideoPath):
        """Create project folder, move video into it, and prepare output folders."""
        sourcePath = Path(sourceVideoPath)
        if not sourcePath.exists():
            raise FileNotFoundError(f"视频文件不存在: {sourcePath}")

        paths = self.buildProjectPaths(sourcePath)
        paths['project_dir'].mkdir(parents=True, exist_ok=True)
        paths['origin_dir'].mkdir(parents=True, exist_ok=True)
        paths['labeled_dir'].mkdir(parents=True, exist_ok=True)

        targetVideoPath = paths['video_path']
        if sourcePath.resolve() != targetVideoPath.resolve():
            if targetVideoPath.exists():
                suffix = sourcePath.suffix
                stem = sourcePath.stem
                index = 1
                while True:
                    candidate = paths['project_dir'] / f"{stem}_{index}{suffix}"
                    if not candidate.exists():
                        shutil.move(str(sourcePath), str(candidate))
                        sourcePath = candidate
                        break
                    index += 1
            else:
                shutil.move(str(sourcePath), str(targetVideoPath))
                sourcePath = targetVideoPath

        paths['video_path'] = sourcePath
        return paths

    def copyOutputDirectory(self):
        """复制输出目录到剪贴板（覆盖旧实现）。"""
        from PyQt5.QtWidgets import QApplication
        try:
            paths = self.prepareProjectStructure(self.videoPath)
        except Exception as e:
            InfoBar.error(
                "错误",
                f"准备项目目录失败: {str(e)}",
                duration=3000,
                parent=self
            )
            return

        self.videoPath = str(paths['video_path'])
        outputDir = str(paths['origin_dir'])
        self.outputDirEdit.setText(outputDir)
        if self.videoInfo:
            self.videoInfo['file_path'] = self.videoPath
            self.videoInfoWidget.updateInfo(self.videoInfo)
        if outputDir:
            clipboard = QApplication.clipboard()
            clipboard.setText(outputDir)
            InfoBar.success(
                "成功",
                "输出目录已复制到剪贴板！",
                duration=1500,
                parent=self
            )
        else:
            InfoBar.warning(
                "警告",
                "输出目录为空",
                duration=1500,
                parent=self
            )

    def loadVideoInfo(self, filePath):
        """ 加载视频信息 """
        try:
            # 使用FFprobe获取视频信息
            ffmpegPath = cfg.get(cfg.ffmpegPath) if hasattr(cfg, 'ffmpegPath') else 'ffmpeg'
            ffprobePath = os.path.join(os.path.dirname(ffmpegPath), 'ffprobe') if hasattr(cfg, 'ffmpegPath') else 'ffprobe'
            
            cmd = [
                ffprobePath,
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                filePath
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                
                # 提取视频流信息
                videoStream = None
                for stream in data.get('streams', []):
                    if stream.get('codec_type') == 'video':
                        videoStream = stream
                        break
                
                if videoStream:
                    self.videoInfo = {
                        'file_path': filePath,
                        'width': int(videoStream.get('width', 0)),
                        'height': int(videoStream.get('height', 0)),
                        'fps': self.parseFrameRate(videoStream.get('r_frame_rate', '0/1')),
                        'duration': float(data.get('format', {}).get('duration', 0)),
                        'total_frames': int(float(data.get('format', {}).get('duration', 0)) * 
                                          self.parseFrameRate(videoStream.get('r_frame_rate', '0/1')))
                    }
                    
                    self.videoInfoWidget.updateInfo(self.videoInfo)
                    
                    InfoBar.success(
                        "成功",
                        "视频信息加载成功！",
                        duration=2000,
                        parent=self
                    )
                else:
                    raise Exception("未找到视频流")
            else:
                raise Exception("FFprobe分析视频失败")
                
        except Exception as e:
            InfoBar.error(
                "错误",
                f"加载视频信息失败: {str(e)}",
                duration=5000,
                parent=self
            )
    
    def parseFrameRate(self, frameRateStr):
        """ 解析帧率字符串 """
        try:
            if '/' in frameRateStr:
                numerator, denominator = frameRateStr.split('/')
                return float(numerator) / float(denominator)
            else:
                return float(frameRateStr)
        except:
            return 30.0  # 默认值
    
    def startExtraction(self):
        """ 开始提取帧 """
        if not self.videoPath:
            InfoBar.warning(
                "警告",
                "请先选择视频文件！",
                duration=2000,
                parent=self
            )
            return
        
        outputDir = self.outputDirEdit.text()
        if not outputDir:
            InfoBar.warning(
                "警告",
                "请选择输出目录！",
                duration=2000,
                parent=self
            )
            return
        
        # 创建输出目录
        os.makedirs(outputDir, exist_ok=True)
        
        # 收集设置
        settings = {
            'extraction_mode': 'fps' if self.fpsModeRadio.isChecked() else ('interval' if self.intervalModeRadio.isChecked() else 'frame_count'),
            'fps': self.fpsSpinBox.value(),
            'interval': self.intervalSpinBox.value(),
            'frame_count': self.frameCountSpinBox.value(),
            'format': self.formatComboBox.currentText(),
            'prefix': self.prefixEdit.text() or 'frame',
            'digits': self.digitsSpinBox.value(),
            'total_frames': self.videoInfo.get('total_frames', 100),
            'duration': self.videoInfo.get('duration', 0)
        }
        
        # 禁用开始按钮，启用停止按钮
        self.startButton.setEnabled(False)
        self.stopButton.setEnabled(True)
        self.progressBar.setVisible(True)
        self.progressBar.setValue(0)
        self.logTextEdit.clear()
        
        # 创建工作线程
        self.extractionWorker = FrameExtractionWorker(self.videoPath, outputDir, settings)
        self.extractionWorker.progress.connect(self.updateProgress)
        self.extractionWorker.finished.connect(self.extractionFinished)
        self.extractionWorker.log.connect(self.appendLog)
        self.extractionWorker.start()
    
    def startExtraction(self):
        """开始提取帧（覆盖旧实现）。"""
        if not self.videoPath:
            InfoBar.warning(
                "警告",
                "请先选择视频文件！",
                duration=2000,
                parent=self
            )
            return

        try:
            paths = self.prepareProjectStructure(self.videoPath)
        except Exception as e:
            InfoBar.error(
                "错误",
                f"准备项目目录失败: {str(e)}",
                duration=3000,
                parent=self
            )
            return

        self.videoPath = str(paths['video_path'])
        outputDir = str(paths['origin_dir'])
        self.outputDirEdit.setText(outputDir)

        if self.videoInfo:
            self.videoInfo['file_path'] = self.videoPath
            self.videoInfoWidget.updateInfo(self.videoInfo)

        os.makedirs(outputDir, exist_ok=True)

        settings = {
            'extraction_mode': 'fps' if self.fpsModeRadio.isChecked() else ('interval' if self.intervalModeRadio.isChecked() else 'frame_count'),
            'fps': self.fpsSpinBox.value(),
            'interval': self.intervalSpinBox.value(),
            'frame_count': self.frameCountSpinBox.value(),
            'format': self.formatComboBox.currentText(),
            'prefix': self.prefixEdit.text() or 'frame',
            'digits': self.digitsSpinBox.value(),
            'total_frames': self.videoInfo.get('total_frames', 100),
            'duration': self.videoInfo.get('duration', 0)
        }

        self.startButton.setEnabled(False)
        self.stopButton.setEnabled(True)
        self.progressBar.setVisible(True)
        self.progressBar.setValue(0)
        self.logTextEdit.clear()

        self.extractionWorker = FrameExtractionWorker(self.videoPath, outputDir, settings)
        self.extractionWorker.progress.connect(self.updateProgress)
        self.extractionWorker.finished.connect(self.extractionFinished)
        self.extractionWorker.log.connect(self.appendLog)
        self.extractionWorker.start()

    def stopExtraction(self):
        """ 停止提取 """
        if not self.extractionWorker:
            return

        running_attr = getattr(self.extractionWorker, "isRunning", None)
        is_running = running_attr() if callable(running_attr) else bool(running_attr)
        if is_running:
            self.extractionWorker.stop()
            self.extractionWorker.wait()
            
            self.extractionFinished(False, "提取被用户停止")
    
    def updateProgress(self, value):
        """ 更新进度 """
        self.progressBar.setValue(value)
        # 更新进度信息标签
        if value > 0 and self.estimatedImages > 0:
            extracted_count = int(self.estimatedImages * value / 100)
            self.progressInfoLabel.setText(f"已提取: {extracted_count}/{self.estimatedImages} 张")
    
    def extractionFinished(self, success, message):
        """ 提取完成处理 """
        self.startButton.setEnabled(True)
        self.stopButton.setEnabled(False)
        self.progressBar.setVisible(False)
        # 清空进度信息
        self.progressInfoLabel.setText("")
        
        if success:
            InfoBar.success(
                "成功",
                message,
                duration=3000,
                parent=self
            )
        else:
            InfoBar.error(
                "错误",
                message,
                duration=5000,
                parent=self
            )
    
    def appendLog(self, message):
        """ 添加日志 """
        self.logTextEdit.append(message)
        # 自动滚动到底部
        self.logTextEdit.verticalScrollBar().setValue(
            self.logTextEdit.verticalScrollBar().maximum()
        )
    
    def openOutputDirectory(self):
        """ 打开输出文件夹 """
        outputDir = self.outputDirEdit.text()
        if not outputDir:
            InfoBar.warning(
                "警告",
                "请先设置输出目录！",
                duration=2000,
                parent=self
            )
            return
        
        # 确保目录存在
        os.makedirs(outputDir, exist_ok=True)
        
        # 使用系统默认文件管理器打开目录
        try:
            if sys.platform == "win32":
                os.startfile(outputDir)
            elif sys.platform == "darwin":  # macOS
                subprocess.run(["open", outputDir])
            else:  # Linux
                subprocess.run(["xdg-open", outputDir])
        except Exception as e:
            InfoBar.error(
                "错误",
                f"无法打开输出文件夹: {str(e)}",
                duration=3000,
                parent=self
            )
    
    def loadSettings(self):
        """ 加载设置 """
        # 设置已经通过默认值加载，这里可以添加额外的初始化逻辑
        pass
    
    def saveSettings(self):
        """ 保存设置 """
        try:
            # 提取模式
            if self.fpsModeRadio.isChecked():
                cfg.extractionMode.value = "fps"
            elif self.intervalModeRadio.isChecked():
                cfg.extractionMode.value = "interval"
            else:
                cfg.extractionMode.value = "frame_count"
            
            # 提取参数
            cfg.extractionFps.value = self.fpsSpinBox.value()
            cfg.extractionInterval.value = self.intervalSpinBox.value()
            cfg.extractionFrameCount.value = self.frameCountSpinBox.value()
            
            # 输出设置
            cfg.outputFormat.value = self.formatComboBox.currentText()
            cfg.outputDirectory.value = self.outputDirEdit.text()
            cfg.outputPrefix.value = self.prefixEdit.text()
            cfg.numberingDigits.value = self.digitsSpinBox.value()
            
            # 尺寸设置
            
            # 保存到配置文件
            cfg.save()
            
        except Exception as e:
            print(f"保存设置失败: {e}")
    
    def connectSignals(self):
        """ 连接信号，用于自动保存设置 """
        # 提取模式改变
        self.fpsModeRadio.toggled.connect(self.saveSettings)
        self.intervalModeRadio.toggled.connect(self.saveSettings)
        self.frameCountModeRadio.toggled.connect(self.saveSettings)
        
        # 提取参数改变
        self.fpsSpinBox.valueChanged.connect(self.saveSettings)
        self.intervalSpinBox.valueChanged.connect(self.saveSettings)
        self.frameCountSpinBox.valueChanged.connect(self.saveSettings)
        
        # 输出设置改变
        self.formatComboBox.currentTextChanged.connect(self.saveSettings)
        self.outputDirEdit.textChanged.connect(self.saveSettings)
        self.prefixEdit.textChanged.connect(self.saveSettings)
        self.digitsSpinBox.valueChanged.connect(self.saveSettings)
        
        # 尺寸设置改变

    def copyOutputDirectory(self):
        """复制输出目录到剪贴板。"""
        from PyQt5.QtWidgets import QApplication
        outputDir = self.outputDirEdit.text()
        if outputDir:
            clipboard = QApplication.clipboard()
            clipboard.setText(outputDir)
            InfoBar.success("成功", "输出目录已复制到剪贴板！", duration=1500, parent=self)
        else:
            InfoBar.warning("警告", "输出目录为空", duration=1500, parent=self)

    def startExtraction(self):
        """开始提取帧。"""
        if not self.videoPath:
            InfoBar.warning("警告", "请先选择视频文件！", duration=2000, parent=self)
            return

        try:
            paths = self.prepareProjectStructure(self.videoPath)
        except Exception as e:
            InfoBar.error("错误", f"准备项目目录失败: {str(e)}", duration=3000, parent=self)
            return

        self.videoPath = str(paths['video_path'])
        outputDir = str(paths['origin_dir'])
        self.outputDirEdit.setText(outputDir)
        if self.videoInfo:
            self.videoInfo['file_path'] = self.videoPath
            self.videoInfoWidget.updateInfo(self.videoInfo)

        os.makedirs(outputDir, exist_ok=True)

        settings = {
            'extraction_mode': 'fps' if self.fpsModeRadio.isChecked() else ('interval' if self.intervalModeRadio.isChecked() else 'frame_count'),
            'fps': self.fpsSpinBox.value(),
            'interval': self.intervalSpinBox.value(),
            'frame_count': self.frameCountSpinBox.value(),
            'format': self.formatComboBox.currentText(),
            'prefix': self.prefixEdit.text() or 'frame',
            'digits': self.digitsSpinBox.value(),
            'total_frames': self.videoInfo.get('total_frames', 100),
            'duration': self.videoInfo.get('duration', 0)
        }

        self.startButton.setEnabled(False)
        self.stopButton.setEnabled(True)
        self.progressBar.setVisible(True)
        self.progressBar.setValue(0)
        self.logTextEdit.clear()

        self.extractionWorker = FrameExtractionWorker(self.videoPath, outputDir, settings)
        self.extractionWorker.progress.connect(self.updateProgress)
        self.extractionWorker.finished.connect(self.extractionFinished)
        self.extractionWorker.log.connect(self.appendLog)
        self.extractionWorker.start()
