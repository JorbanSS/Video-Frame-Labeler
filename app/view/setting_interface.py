# coding:utf-8
from qfluentwidgets import (SettingCardGroup, SwitchSettingCard, FolderListSettingCard,
                            OptionsSettingCard, PushSettingCard,
                            HyperlinkCard, PrimaryPushSettingCard, ScrollArea,
                            ComboBoxSettingCard, ExpandLayout, Theme, CustomColorSettingCard,
                            setTheme, setThemeColor, RangeSettingCard, isDarkTheme)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import InfoBar
from PyQt5.QtCore import Qt, pyqtSignal, QUrl, QStandardPaths
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QWidget, QLabel, QFileDialog

from ..common.config import cfg, REPO_URL, AUTHOR, YEAR, isWin11
from ..common.signal_bus import signalBus
from ..common.style_sheet import StyleSheet


class SettingInterface(ScrollArea):
    """ Setting interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        # setting label
        self.settingLabel = QLabel(self.tr("Settings"), self)

        # personalization
        self.personalGroup = SettingCardGroup(
            self.tr('Personalization'), self.scrollWidget)
        self.micaCard = SwitchSettingCard(
            FIF.TRANSPARENT,
            self.tr('Mica effect'),
            self.tr('Apply semi transparent to windows and surfaces'),
            cfg.micaEnabled,
            self.personalGroup
        )
        self.themeCard = OptionsSettingCard(
            cfg.themeMode,
            FIF.BRUSH,
            self.tr('Application theme'),
            self.tr("Change the appearance of your application"),
            texts=[
                self.tr('Light'), self.tr('Dark'),
                self.tr('Use system setting')
            ],
            parent=self.personalGroup
        )
        self.themeColorCard = CustomColorSettingCard(
            cfg.themeColor,
            FIF.PALETTE,
            self.tr('Theme color'),
            self.tr('Change the theme color of you application'),
            self.personalGroup
        )
        self.zoomCard = OptionsSettingCard(
            cfg.dpiScale,
            FIF.ZOOM,
            self.tr("Interface zoom"),
            self.tr("Change the size of widgets and fonts"),
            texts=[
                "100%", "125%", "150%", "175%", "200%",
                self.tr("Use system setting")
            ],
            parent=self.personalGroup
        )
        self.languageCard = ComboBoxSettingCard(
            cfg.language,
            FIF.LANGUAGE,
            self.tr('Language'),
            self.tr('Set your preferred language for UI'),
            texts=['简体中文', '繁體中文', 'English', self.tr('Use system setting')],
            parent=self.personalGroup
        )

        # workspace
        self.workspaceGroup = SettingCardGroup(
            self.tr('工作区'), self.scrollWidget)
        self.workDirectoryCard = PushSettingCard(
            self.tr('选择文件夹'),
            FIF.FOLDER,
            self.tr('工作目录'),
            cfg.get(cfg.workDirectory) or self.tr('未设置'),
            self.workspaceGroup
        )
        self.openWorkDirectoryCard = PushSettingCard(
            self.tr('打开'),
            FIF.FOLDER,
            self.tr('打开工作目录'),
            self.tr('使用文件管理器打开当前工作目录'),
            self.workspaceGroup
        )

        # material
        self.materialGroup = SettingCardGroup(
            self.tr('Material'), self.scrollWidget)
        self.blurRadiusCard = RangeSettingCard(
            cfg.blurRadius,
            FIF.ALBUM,
            self.tr('Acrylic blur radius'),
            self.tr('The greater the radius, the more blurred the image'),
            self.materialGroup
        )

        

        # FFmpeg settings
        self.ffmpegGroup = SettingCardGroup(
            self.tr("FFmpeg设置"), self.scrollWidget)
        self.ffmpegPathCard = PushSettingCard(
            self.tr('选择FFmpeg可执行文件'),
            FIF.FOLDER,
            self.tr('FFmpeg路径'),
            cfg.get(cfg.ffmpegPath),
            self.ffmpegGroup
        )
        self.ffmpegThreadsCard = RangeSettingCard(
            cfg.ffmpegThreads,
            FIF.TAG,
            self.tr('线程数'),
            self.tr('视频处理使用的线程数'),
            self.ffmpegGroup
        )
        self.ffmpegHardwareAccelCard = ComboBoxSettingCard(
            cfg.ffmpegHardwareAccel,
            FIF.DEVELOPER_TOOLS,
            self.tr('硬件加速'),
            self.tr('视频处理的硬件加速方法'),
            texts=['无', 'CUDA', 'OpenCL', 'DXVA2', 'Intel QSV'],
            parent=self.ffmpegGroup
        )

        # application
        self.aboutGroup = SettingCardGroup(self.tr('关于'), self.scrollWidget)
        self.aboutCard = PrimaryPushSettingCard(
            self.tr('访问GitHub页面'),
            FIF.GITHUB,
            self.tr('关于'),
            '© ' + self.tr('版权所有') + f" {YEAR}, {AUTHOR}.",
            self.aboutGroup
        )

        self.__initWidget()

    def __initWidget(self):
        self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 80, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setObjectName('settingInterface')

        # initialize style sheet
        self.scrollWidget.setObjectName('scrollWidget')
        self.settingLabel.setObjectName('settingLabel')
        StyleSheet.SETTING_INTERFACE.apply(self)

        self.micaCard.setEnabled(isWin11())

        # initialize layout
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        self.settingLabel.move(36, 30)

        # add cards to group
        self.personalGroup.addSettingCard(self.micaCard)
        self.personalGroup.addSettingCard(self.themeCard)
        self.personalGroup.addSettingCard(self.themeColorCard)
        self.personalGroup.addSettingCard(self.zoomCard)
        self.personalGroup.addSettingCard(self.languageCard)

        self.workspaceGroup.addSettingCard(self.workDirectoryCard)
        self.workspaceGroup.addSettingCard(self.openWorkDirectoryCard)

        self.materialGroup.addSettingCard(self.blurRadiusCard)

        self.ffmpegGroup.addSettingCard(self.ffmpegPathCard)
        self.ffmpegGroup.addSettingCard(self.ffmpegThreadsCard)
        self.ffmpegGroup.addSettingCard(self.ffmpegHardwareAccelCard)

        self.aboutGroup.addSettingCard(self.aboutCard)

        # add setting card group to layout
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)
        self.expandLayout.addWidget(self.personalGroup)
        self.expandLayout.addWidget(self.workspaceGroup)
        self.expandLayout.addWidget(self.materialGroup)
        self.expandLayout.addWidget(self.ffmpegGroup)
        self.expandLayout.addWidget(self.aboutGroup)

    def __showRestartTooltip(self):
        """ show restart tooltip """
        InfoBar.success(
            self.tr('Updated successfully'),
            self.tr('Configuration takes effect after restart'),
            duration=1500,
            parent=self
        )
    
    def __onFFmpegPathCardClicked(self):
        """ FFmpeg path card clicked slot """
        filePath, _ = QFileDialog.getOpenFileName(
            self, self.tr("选择FFmpeg可执行文件"), "./", 
            self.tr("可执行文件 (*.exe);;所有文件 (*.*)"))
        
        if not filePath or cfg.get(cfg.ffmpegPath) == filePath:
            return

        cfg.set(cfg.ffmpegPath, filePath)
        self.ffmpegPathCard.setContent(filePath)

    def __onWorkDirectoryCardClicked(self):
        """ Work directory card clicked slot """
        startDir = cfg.get(cfg.workDirectory) or QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        folderPath = QFileDialog.getExistingDirectory(
            self, self.tr("选择工作目录"), startDir)

        if not folderPath or cfg.get(cfg.workDirectory) == folderPath:
            return

        cfg.set(cfg.workDirectory, folderPath)
        self.workDirectoryCard.setContent(folderPath)
        signalBus.workDirectoryChanged.emit(folderPath)

    def __onOpenWorkDirectoryCardClicked(self):
        """ Open configured work directory. """
        folderPath = cfg.get(cfg.workDirectory)
        if not folderPath:
            InfoBar.warning(
                self.tr('未设置工作目录'),
                self.tr('请先选择一个工作目录'),
                duration=2000,
                parent=self
            )
            return

        QDesktopServices.openUrl(QUrl.fromLocalFile(folderPath))

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        cfg.appRestartSig.connect(self.__showRestartTooltip)

        # FFmpeg settings
        self.ffmpegPathCard.clicked.connect(self.__onFFmpegPathCardClicked)
        self.workDirectoryCard.clicked.connect(self.__onWorkDirectoryCardClicked)
        self.openWorkDirectoryCard.clicked.connect(self.__onOpenWorkDirectoryCardClicked)

        # personalization
        cfg.themeChanged.connect(setTheme)
        self.themeColorCard.colorChanged.connect(lambda c: setThemeColor(c))
        self.micaCard.checkedChanged.connect(signalBus.micaEnableChanged)

        # about
        self.aboutCard.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
