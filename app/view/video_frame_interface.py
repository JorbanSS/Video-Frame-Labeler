# coding:utf-8
import os
import subprocess
import json
from pathlib import Path
from datetime import timedelta

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, 
                             QFileDialog, QLabel, QSpinBox, QComboBox, 
                             QPushButton, QProgressBar, QTextEdit, QGroupBox,
                             QRadioButton, QButtonGroup, QMessageBox, QLineEdit)
from qfluentwidgets import (CardWidget, PushButton, SpinBox, ComboBox, 
                           StrongBodyLabel, BodyLabel, TitleLabel, 
                           StateToolTip, InfoBar, InfoBarPosition,
                           FluentIcon as FIF, TextEdit, ProgressBar, LineEdit,
                           RadioButton)

from .gallery_interface import GalleryInterface
from ..common.config import cfg
from ..common.signal_bus import signalBus


class VideoInfoWidget(CardWidget):
    """ 视频信息显示组件 """
    
    def __init__(self, parent=None):
        super().__init__(parent)
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
        
        # 文件路径
        self.filePathLabel = BodyLabel("文件路径: ", self)
        infoGrid.addWidget(self.filePathLabel, 0, 0)
        self.filePathValue = BodyLabel("-", self)
        infoGrid.addWidget(self.filePathValue, 0, 1)
        
        # 分辨率
        self.resolutionLabel = BodyLabel("分辨率: ", self)
        infoGrid.addWidget(self.resolutionLabel, 1, 0)
        self.resolutionValue = BodyLabel("-", self)
        infoGrid.addWidget(self.resolutionValue, 1, 1)
        
        # 帧率
        self.fpsLabel = BodyLabel("帧率: ", self)
        infoGrid.addWidget(self.fpsLabel, 2, 0)
        self.fpsValue = BodyLabel("-", self)
        infoGrid.addWidget(self.fpsValue, 2, 1)
        
        # 时长
        self.durationLabel = BodyLabel("时长: ", self)
        infoGrid.addWidget(self.durationLabel, 3, 0)
        self.durationValue = BodyLabel("-", self)
        infoGrid.addWidget(self.durationValue, 3, 1)
        
        # 总帧数
        self.totalFramesLabel = BodyLabel("总帧数: ", self)
        infoGrid.addWidget(self.totalFramesLabel, 4, 0)
        self.totalFramesValue = BodyLabel("-", self)
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
        self.isRunning = True
    
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
                encoding='utf-8'
            )
            
            # 读取输出并计算进度
            totalFrames = self.settings.get('total_frames', 100)
            currentFrame = 0
            
            for line in process.stdout:
                if not self.isRunning:
                    process.terminate()
                    break
                
                self.log.emit(line.strip())
                
                # 解析进度信息
                if 'frame=' in line:
                    try:
                        frameNum = int(line.split('frame=')[1].split()[0])
                        currentFrame = frameNum
                        progress = int((currentFrame / totalFrames) * 100)
                        self.progress.emit(progress)
                    except:
                        pass
            
            process.wait()
            
            if process.returncode == 0:
                self.finished.emit(True, "帧提取完成！")
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
        outputPattern = os.path.join(self.outputDir, f'frame_%06d.{outputFormat}')
        
        # 帧率设置
        if self.settings.get('extraction_mode') == 'fps':
            fps = self.settings.get('fps', 10) / 10.0  # Convert back from integer
            cmd.extend(['-vf', f'fps={fps}'])
        else:
            interval = self.settings.get('interval', 1)
            cmd.extend(['-vf', f'fps=1/{interval}'])
        
        # 图片尺寸设置
        if self.settings.get('resize_enabled'):
            width = self.settings.get('resize_width', 1920)
            height = self.settings.get('resize_height', 1080)
            # 更新vf参数
            vf_index = cmd.index('-vf')
            vf_value = cmd[vf_index + 1]
            cmd[vf_index + 1] = f"{vf_value},scale={width}:{height}"
        
        # 输出文件
        cmd.append(outputPattern)
        
        return cmd
    
    def stop(self):
        self.isRunning = False


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
        self.controlGroup = None  # 保存控制组的引用
        
        self.initUI()
        
    def initUI(self):
        # 视频选择和信息显示区域
        videoGroup = self.addExampleCard(
            title="视频选择",
            widget=self.createVideoSelectionWidget(),
            sourcePath=""
        )
        
        # 提取设置区域
        settingsGroup = self.addExampleCard(
            title="提取设置",
            widget=self.createExtractionSettingsWidget(),
            sourcePath=""
        )
        
        # 输出设置区域
        outputGroup = self.addExampleCard(
            title="输出设置",
            widget=self.createOutputSettingsWidget(),
            sourcePath=""
        )
        
        # 控制按钮和进度区域（包含预计输出图片数量）
        self.controlGroup = self.addExampleCard(
            title="提取控制 - 预计输出图片数量: " + str(self.estimatedImages) + " 张",
            widget=self.createControlWidget(),
            sourcePath=""
        )
    
    def createVideoSelectionWidget(self):
        """ 创建视频选择组件 """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)
        
        # 选择文件按钮
        self.selectFileButton = PushButton("选择视频文件", self, FIF.VIDEO)
        self.selectFileButton.clicked.connect(self.selectVideoFile)
        layout.addWidget(self.selectFileButton)
        
        # 视频信息显示
        self.videoInfoWidget = VideoInfoWidget(self)
        layout.addWidget(self.videoInfoWidget)
        
        return widget
    
    def createExtractionSettingsWidget(self):
        """ 创建提取设置组件 """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)
        
        # 提取模式选择
        modeLayout = QHBoxLayout()
        modeLabel = BodyLabel("提取模式:", self)
        modeLayout.addWidget(modeLabel)
        
        self.modeGroup = QButtonGroup(self)
        self.fpsModeRadio = RadioButton("按帧率提取", self)
        self.intervalModeRadio = RadioButton("按时间间隔提取", self)
        self.fpsModeRadio.setChecked(True)
        
        self.modeGroup.addButton(self.fpsModeRadio)
        self.modeGroup.addButton(self.intervalModeRadio)
        modeLayout.addWidget(self.fpsModeRadio)
        modeLayout.addWidget(self.intervalModeRadio)
        modeLayout.addStretch()
        
        layout.addLayout(modeLayout)
        
        # FPS设置
        self.fpsLayout = QHBoxLayout()
        self.fpsLabel = BodyLabel("每秒提取帧数:", self)
        self.fpsSpinBox = SpinBox(self)
        self.fpsSpinBox.setRange(1, 600)  # 0.1-60 fps, multiply by 10 for integer
        self.fpsSpinBox.setValue(10)  # 1.0 fps
        self.fpsSpinBox.setSingleStep(1)
        
        self.fpsLayout.addWidget(self.fpsLabel)
        self.fpsLayout.addWidget(self.fpsSpinBox)
        self.fpsLayout.addStretch()
        layout.addLayout(self.fpsLayout)
        
        # 间隔设置（默认隐藏）
        self.intervalLayout = QHBoxLayout()
        self.intervalLabel = BodyLabel("每多少秒提取一帧:", self)
        self.intervalSpinBox = SpinBox(self)
        self.intervalSpinBox.setRange(1, 3600)
        self.intervalSpinBox.setValue(1)
        self.intervalSpinBox.setEnabled(False)
        
        self.intervalLayout.addWidget(self.intervalLabel)
        self.intervalLayout.addWidget(self.intervalSpinBox)
        self.intervalLayout.addStretch()
        layout.addLayout(self.intervalLayout)
        
        # 预计图片数量（已移到标题栏）
        self.estimatedCountLabel = BodyLabel("预计输出图片数量: 0 张", self)
        self.estimatedCountLabel.setVisible(False)  # 隐藏，因为已经移到标题栏
        
        # 连接信号
        self.fpsModeRadio.toggled.connect(self.onModeChanged)
        self.fpsSpinBox.valueChanged.connect(self.updateEstimatedCount)
        self.intervalSpinBox.valueChanged.connect(self.updateEstimatedCount)
        
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
        self.formatComboBox.setCurrentText('png')
        
        formatLayout.addWidget(formatLabel)
        formatLayout.addWidget(self.formatComboBox)
        formatLayout.addStretch()
        layout.addLayout(formatLayout)
        
        # 输出目录
        outputDirLayout = QHBoxLayout()
        outputDirLabel = BodyLabel("输出目录:", self)
        self.outputDirEdit = LineEdit(self)
        self.outputDirEdit.setText(str(Path.home() / "VideoFrames"))
        
        self.selectOutputDirButton = PushButton("浏览", self, FIF.FOLDER)
        self.selectOutputDirButton.clicked.connect(self.selectOutputDirectory)
        
        outputDirLayout.addWidget(outputDirLabel)
        outputDirLayout.addWidget(self.outputDirEdit)
        outputDirLayout.addWidget(self.selectOutputDirButton)
        layout.addLayout(outputDirLayout)
        
        # 图片尺寸设置
        resizeLayout = QHBoxLayout()
        self.resizeCheckBox = RadioButton("调整输出图片尺寸", self)
        self.resizeCheckBox.setChecked(False)
        resizeLayout.addWidget(self.resizeCheckBox)
        
        # 尺寸输入
        self.widthSpinBox = SpinBox(self)
        self.widthSpinBox.setRange(1, 7680)
        self.widthSpinBox.setValue(1920)
        self.widthSpinBox.setEnabled(False)
        
        self.heightSpinBox = SpinBox(self)
        self.heightSpinBox.setRange(1, 4320)
        self.heightSpinBox.setValue(1080)
        self.heightSpinBox.setEnabled(False)
        
        resizeLayout.addWidget(BodyLabel("宽度:", self))
        resizeLayout.addWidget(self.widthSpinBox)
        resizeLayout.addWidget(BodyLabel("高度:", self))
        resizeLayout.addWidget(self.heightSpinBox)
        resizeLayout.addStretch()
        
        layout.addLayout(resizeLayout)
        
        # 连接信号
        self.resizeCheckBox.toggled.connect(self.onResizeToggled)
        
        return widget
    
    def createControlWidget(self):
        """ 创建控制组件 """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)
        
        # 控制按钮
        buttonLayout = QHBoxLayout()
        self.startButton = PushButton("开始提取", self, FIF.PLAY)
        self.startButton.clicked.connect(self.startExtraction)
        self.startButton.setEnabled(False)
        
        self.stopButton = PushButton("停止", self, FIF.CLOSE)
        self.stopButton.clicked.connect(self.stopExtraction)
        self.stopButton.setEnabled(False)
        
        buttonLayout.addWidget(self.startButton)
        buttonLayout.addWidget(self.stopButton)
        buttonLayout.addStretch()
        
        layout.addLayout(buttonLayout)
        
        # 进度条
        self.progressBar = ProgressBar(self)
        self.progressBar.setVisible(False)
        layout.addWidget(self.progressBar)
        
        # 日志输出
        logLabel = BodyLabel("提取日志:", self)
        layout.addWidget(logLabel)
        
        self.logTextEdit = TextEdit(self)
        self.logTextEdit.setMaximumHeight(200)
        self.logTextEdit.setReadOnly(True)
        layout.addWidget(self.logTextEdit)
        
        return widget
    
    def onModeChanged(self, checked):
        """ 模式切换处理 """
        isFpsMode = self.fpsModeRadio.isChecked()
        self.fpsSpinBox.setEnabled(isFpsMode)
        self.intervalSpinBox.setEnabled(not isFpsMode)
        self.updateEstimatedCount()
    
    def onResizeToggled(self, checked):
        """ 尺寸调整切换处理 """
        self.widthSpinBox.setEnabled(checked)
        self.heightSpinBox.setEnabled(checked)
    
    def updateEstimatedCount(self):
        """ 更新预计图片数量 """
        if not self.videoInfo:
            self.estimatedImages = 0
            if self.controlGroup:
                self.controlGroup.titleLabel.setText("提取控制 - 预计输出图片数量: 0 张")
            return
            
        total_frames = self.videoInfo.get('total_frames', 0)
        duration = self.videoInfo.get('duration', 0)
        
        if self.fpsModeRadio.isChecked():
            # 按帧率提取
            fps = self.fpsSpinBox.value() / 10.0
            self.estimatedImages = int(duration * fps)
        else:
            # 按时间间隔提取
            interval = self.intervalSpinBox.value()
            self.estimatedImages = int(duration / interval)
            
        if self.controlGroup:
            self.controlGroup.titleLabel.setText(f"提取控制 - 预计输出图片数量: {self.estimatedImages} 张")
    
    def selectVideoFile(self):
        """ 选择视频文件 """
        filePath, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.webm);;所有文件 (*.*)"
        )
        
        if filePath:
            self.videoPath = filePath
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
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
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
            'extraction_mode': 'fps' if self.fpsModeRadio.isChecked() else 'interval',
            'fps': self.fpsSpinBox.value(),  # This is multiplied by 10
            'interval': self.intervalSpinBox.value(),
            'format': self.formatComboBox.currentText(),
            'resize_enabled': self.resizeCheckBox.isChecked(),
            'resize_width': self.widthSpinBox.value(),
            'resize_height': self.heightSpinBox.value(),
            'total_frames': self.videoInfo.get('total_frames', 100)
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
    
    def stopExtraction(self):
        """ 停止提取 """
        if self.extractionWorker and self.extractionWorker.isRunning():
            self.extractionWorker.stop()
            self.extractionWorker.wait()
            
            self.extractionFinished(False, "提取被用户停止")
    
    def updateProgress(self, value):
        """ 更新进度 """
        self.progressBar.setValue(value)
    
    def extractionFinished(self, success, message):
        """ 提取完成处理 """
        self.startButton.setEnabled(True)
        self.stopButton.setEnabled(False)
        self.progressBar.setVisible(False)
        
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