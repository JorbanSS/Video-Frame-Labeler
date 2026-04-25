# coding:utf-8
import sys
from enum import Enum

from PyQt5.QtCore import QLocale
from qfluentwidgets import (qconfig, QConfig, ConfigItem, OptionsConfigItem, BoolValidator,
                            OptionsValidator, RangeConfigItem, RangeValidator,
                            FolderListValidator, Theme, FolderValidator, ConfigSerializer, __version__)


class Language(Enum):
    """ Language enumeration """

    CHINESE_SIMPLIFIED = QLocale(QLocale.Chinese, QLocale.China)
    CHINESE_TRADITIONAL = QLocale(QLocale.Chinese, QLocale.HongKong)
    ENGLISH = QLocale(QLocale.English)
    AUTO = QLocale()


class LanguageSerializer(ConfigSerializer):
    """ Language serializer """

    def serialize(self, language):
        return language.value.name() if language != Language.AUTO else "Auto"

    def deserialize(self, value: str):
        return Language(QLocale(value)) if value != "Auto" else Language.AUTO


def isWin11():
    return sys.platform == 'win32' and sys.getwindowsversion().build >= 22000


class Config(QConfig):
    """ Config of application """

    # folders
    downloadFolder = ConfigItem(
        "Folders", "Download", "app/download", FolderValidator())

    # main window
    micaEnabled = ConfigItem("MainWindow", "MicaEnabled", isWin11(), BoolValidator())
    dpiScale = OptionsConfigItem(
        "MainWindow", "DpiScale", "Auto", OptionsValidator([1, 1.25, 1.5, 1.75, 2, "Auto"]), restart=True)
    language = OptionsConfigItem(
        "MainWindow", "Language", Language.AUTO, OptionsValidator(Language), LanguageSerializer(), restart=True)

    # Material
    blurRadius  = RangeConfigItem("Material", "AcrylicBlurRadius", 15, RangeValidator(0, 40))

    # software update
    checkUpdateAtStartUp = ConfigItem("Update", "CheckUpdateAtStartUp", True, BoolValidator())

    # FFmpeg settings
    ffmpegPath = ConfigItem("FFmpeg", "Path", "ffmpeg")
    ffmpegThreads = RangeConfigItem("FFmpeg", "Threads", 4, RangeValidator(1, 16))
    ffmpegHardwareAccel = OptionsConfigItem("FFmpeg", "HardwareAcceleration", "none", 
                                          OptionsValidator(["none", "cuda", "opencl", "dxva2", "qsv"]))

    # Video frame extraction settings
    extractionMode = OptionsConfigItem("VideoFrameExtraction", "Mode", "fps", 
                                     OptionsValidator(["fps", "interval", "frame_count"]))
    extractionFps = RangeConfigItem("VideoFrameExtraction", "Fps", 20, RangeValidator(1, 600))
    extractionInterval = RangeConfigItem("VideoFrameExtraction", "Interval", 1, RangeValidator(1, 3600))
    extractionFrameCount = RangeConfigItem("VideoFrameExtraction", "FrameCount", 100, RangeValidator(1, 10000))
    outputFormat = OptionsConfigItem("VideoFrameExtraction", "OutputFormat", "png", 
                                   OptionsValidator(["png", "jpg", "bmp", "tiff"]))
    outputDirectory = ConfigItem("VideoFrameExtraction", "OutputDirectory", "")
    outputPrefix = ConfigItem("VideoFrameExtraction", "OutputPrefix", "frame")
    numberingDigits = RangeConfigItem("VideoFrameExtraction", "NumberingDigits", 6, RangeValidator(1, 10))
    resizeEnabled = ConfigItem("VideoFrameExtraction", "ResizeEnabled", False, BoolValidator())
    resizeWidth = RangeConfigItem("VideoFrameExtraction", "ResizeWidth", 1920, RangeValidator(1, 7680))
    resizeHeight = RangeConfigItem("VideoFrameExtraction", "ResizeHeight", 1080, RangeValidator(1, 4320))


YEAR = 2026
AUTHOR = "JorbanS"
VERSION = __version__
HELP_URL = "https://qfluentwidgets.com"
REPO_URL = "https://github.com/JorbanSS/Video-Frame-Labeler"
EXAMPLE_URL = "https://github.com/zhiyiYo/PyQt-Fluent-Widgets/tree/master/examples"
FEEDBACK_URL = "https://github.com/zhiyiYo/PyQt-Fluent-Widgets/issues"
RELEASE_URL = "https://github.com/zhiyiYo/PyQt-Fluent-Widgets/releases/latest"
ZH_SUPPORT_URL = "https://qfluentwidgets.com/zh/price/"
EN_SUPPORT_URL = "https://qfluentwidgets.com/price/"


cfg = Config()
cfg.themeMode.value = Theme.AUTO
qconfig.load('app/config/config.json', cfg)
