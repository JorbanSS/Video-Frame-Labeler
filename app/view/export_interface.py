# coding:utf-8
import ctypes
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QFont
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFileDialog,
    QSizePolicy,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)
from qfluentwidgets import (
    PushButton,
    ComboBox,
    BodyLabel,
    LineEdit,
    InfoBar,
    MessageBox,
    TableWidget,
    FluentIcon as FIF,
    ProgressBar,
)

from .gallery_interface import GalleryInterface
from ..common.config import cfg
from ..common.signal_bus import signalBus


EXPORT_HISTORY_PATH = Path(__file__).resolve().parent.parent / "config" / "export_history.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff"}


def _force_dialog_focus(widget):
    widget.raise_()
    widget.activateWindow()
    try:
        hwnd = int(widget.winId())
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def sanitize_folder_name(name):
    safe_name = str(name or "").strip()
    replace_map = str.maketrans({
        "/": "_",
        "\\": "_",
        ":": "_",
        "*": "_",
        "?": "_",
        '"': "_",
        "<": "_",
        ">": "_",
        "|": "_",
    })
    safe_name = safe_name.translate(replace_map)
    safe_name = re.sub(r"[\x00-\x1f]", "", safe_name).rstrip(" .")
    return safe_name or "unclassified"


def get_project_file_prefix(project_name):
    first_word = str(project_name or "").strip().split()[0] if str(project_name or "").strip() else "project"
    return sanitize_folder_name(first_word)


def unique_target_path(target_dir, file_name, project_name):
    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    prefix = get_project_file_prefix(project_name)
    target = target_dir / f"{prefix}_{stem}{suffix}"
    if not target.exists():
        return target

    index = 2
    while True:
        candidate = target_dir / f"{prefix}_{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def clear_directory(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for item in directory.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


class ExportHistoryStore:
    def __init__(self, history_path=None):
        self.history_path = Path(history_path or EXPORT_HISTORY_PATH)
        self.records = []
        self.load()

    def load(self):
        if not self.history_path.exists():
            self.records = []
            return

        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            self.records = []
            return

        self.records = data.get("records", []) if isinstance(data, dict) else []

    def save(self):
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "records": self.records}
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add(self, record):
        self.records.insert(0, record)
        self.records = self.records[:100]
        self.save()

    def delete(self, index):
        if index < 0 or index >= len(self.records):
            return False
        del self.records[index]
        self.save()
        return True


class ExportWorker(QThread):
    progress = pyqtSignal(int, int, str)
    exportDone = pyqtSignal(bool, str, dict)

    def __init__(self, selected_projects, output_dir, work_dir):
        super().__init__()
        self.selected_projects = selected_projects
        self.output_dir = str(output_dir)
        self.work_dir = str(work_dir or "")

    def run(self):
        try:
            output_path = Path(self.output_dir)
            total_files = 0
            for project in self.selected_projects:
                labeled_dir = Path(project["path"]) / "labeled_pic"
                if not labeled_dir.exists():
                    continue
                for image_file in labeled_dir.rglob("*"):
                    if image_file.is_file() and image_file.suffix.lower() in IMAGE_EXTENSIONS:
                        total_files += 1

            clear_directory(output_path)

            copied_count = 0
            category_result = {}
            for project in self.selected_projects:
                project_name = project["name"]
                labeled_dir = Path(project["path"]) / "labeled_pic"
                if not labeled_dir.exists():
                    continue

                with os.scandir(labeled_dir) as category_entries:
                    for category_entry in category_entries:
                        if not category_entry.is_dir():
                            continue

                        category_name = category_entry.name
                        target_category_dir = output_path / sanitize_folder_name(category_name)
                        target_category_dir.mkdir(parents=True, exist_ok=True)

                        for image_file in Path(category_entry.path).iterdir():
                            if not image_file.is_file() or image_file.suffix.lower() not in IMAGE_EXTENSIONS:
                                continue

                            target_path = unique_target_path(target_category_dir, image_file.name, project_name)
                            shutil.copy2(image_file, target_path)
                            copied_count += 1
                            category_result[category_name] = category_result.get(category_name, 0) + 1
                            self.progress.emit(copied_count, total_files, image_file.name)

            record = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "work_dir": self.work_dir,
                "output_dir": str(output_path),
                "total_files": copied_count,
                "categories": category_result,
                "projects": len(self.selected_projects),
                "project_names": [project["name"] for project in self.selected_projects],
            }
            self.exportDone.emit(True, f"已导出 {copied_count} 张图片", record)
        except Exception as e:
            self.exportDone.emit(False, str(e), {})


class ExportInterface(GalleryInterface):
    def __init__(self, parent=None):
        super().__init__(
            title="导出",
            subtitle="合并工作目录下所有项目的已分类图片",
            parent=parent,
        )

        self.setObjectName("exportInterface")
        self.historyStore = ExportHistoryStore()
        self.projects = []
        self.categoryCounts = {}
        self.categoryNames = []
        self.totalFiles = 0
        self.totalOriginFiles = 0
        self.selectedProjectPaths = set()
        self.knownProjectPaths = set()
        self.projectSelectionInitialized = False
        self.currentWorkDir = ""
        self.exportWorker = None

        self.initUI()
        self.refreshClassification()
        self.refreshHistory()
        signalBus.workDirectoryChanged.connect(lambda _: self.refreshClassification())

    def initUI(self):
        self.addExampleCard(
            title="导出设置",
            widget=self.createOverviewWidget(),
            sourcePath="",
            stretch=1,
        )
        self.addExampleCard(
            title="项目明细",
            widget=self.createProjectDetailWidget(),
            sourcePath="",
            stretch=1,
        )
        self.addExampleCard(
            title="导出历史",
            widget=self.createHistoryWidget(),
            sourcePath="",
            stretch=1,
        )

    def createOverviewWidget(self):
        widget = QWidget()
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        buttonLayout = QHBoxLayout()
        buttonLayout.setSpacing(10)

        self.workDirLabel = BodyLabel("", self)
        self.workDirLabel.setWordWrap(True)
        self.workDirLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        buttonLayout.addWidget(self.workDirLabel, 1)

        self.refreshButton = PushButton("刷新", self, FIF.ROTATE)
        self.refreshButton.clicked.connect(self.refreshClassification)
        buttonLayout.addWidget(self.refreshButton)

        self.exportButton = PushButton("导出到目录", self, FIF.SAVE)
        self.exportButton.clicked.connect(self.exportMergedCategories)
        buttonLayout.addWidget(self.exportButton)
        layout.addLayout(buttonLayout)

        exportDirLayout = QHBoxLayout()
        exportDirLayout.setSpacing(10)
        exportDirLayout.addWidget(BodyLabel("导出目录:", self))
        self.exportDirEdit = LineEdit(self)
        self.exportDirEdit.setText(cfg.get(cfg.exportDirectory))
        self.exportDirEdit.setReadOnly(True)
        self.exportDirEdit.setClearButtonEnabled(False)
        exportDirLayout.addWidget(self.exportDirEdit, 1)

        self.selectExportDirButton = PushButton("选择目录", self, FIF.FOLDER)
        self.selectExportDirButton.clicked.connect(self.selectExportDirectory)
        exportDirLayout.addWidget(self.selectExportDirButton)

        self.openExportDirButton = PushButton("打开导出目录", self, FIF.FOLDER)
        self.openExportDirButton.clicked.connect(self.openExportDirectory)
        exportDirLayout.addWidget(self.openExportDirButton)
        layout.addLayout(exportDirLayout)

        self.statsLabel = BodyLabel("项目数: 0 | 可导出图片: 0", self)
        layout.addWidget(self.statsLabel)

        self.exportProgressBar = ProgressBar(self)
        self.exportProgressBar.setValue(0)
        self.exportProgressBar.setVisible(False)
        layout.addWidget(self.exportProgressBar)

        self.exportProgressLabel = BodyLabel("", self)
        self.exportProgressLabel.setWordWrap(True)
        self.exportProgressLabel.setVisible(False)
        layout.addWidget(self.exportProgressLabel)

        return widget

    def createProjectDetailWidget(self):
        widget = QWidget()
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(widget)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        self.projectTable = TableWidget(self)
        self.projectTable.setContentsMargins(0, 0, 0, 0)
        self.projectTable.verticalHeader().hide()
        self.projectTable.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.projectTable.setSelectionMode(QAbstractItemView.NoSelection)
        self.projectTable.setFocusPolicy(Qt.NoFocus)
        self.projectTable.setMinimumHeight(0)
        self.projectTable.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.projectTable.itemChanged.connect(self.onProjectSelectionChanged)
        layout.addWidget(self.projectTable)

        return widget

    def createHistoryWidget(self):
        widget = QWidget()
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        buttonLayout = QHBoxLayout()
        buttonLayout.setSpacing(10)

        self.historyComboBox = ComboBox(self)
        self.historyComboBox.setMinimumWidth(420)
        self.historyComboBox.currentIndexChanged.connect(self.showSelectedHistory)
        buttonLayout.addWidget(self.historyComboBox, 1)

        self.openHistoryButton = PushButton("打开目录", self, FIF.FOLDER)
        self.openHistoryButton.clicked.connect(self.openSelectedHistoryOutput)
        buttonLayout.addWidget(self.openHistoryButton)

        self.deleteHistoryButton = PushButton("删除记录", self, FIF.DELETE)
        self.deleteHistoryButton.clicked.connect(self.deleteSelectedHistory)
        buttonLayout.addWidget(self.deleteHistoryButton)
        layout.addLayout(buttonLayout)

        layout.addWidget(BodyLabel("导出结果", self))

        self.historySummaryLabel = BodyLabel("暂无导出历史。", self)
        self.historySummaryLabel.setWordWrap(True)
        self.historySummaryLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.historySummaryLabel)

        self.historyOutputLabel = BodyLabel("", self)
        self.historyOutputLabel.setWordWrap(True)
        self.historyOutputLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.historyOutputLabel)

        self.historyCategoryTable = TableWidget(self)
        self.historyCategoryTable.setColumnCount(2)
        self.historyCategoryTable.setHorizontalHeaderLabels(["分类", "图片数"])
        self.historyCategoryTable.verticalHeader().hide()
        self.historyCategoryTable.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.historyCategoryTable.setSelectionMode(QAbstractItemView.NoSelection)
        self.historyCategoryTable.setFocusPolicy(Qt.NoFocus)
        self.historyCategoryTable.setMinimumHeight(0)
        self.historyCategoryTable.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.historyCategoryTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.historyCategoryTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(self.historyCategoryTable)

        return widget

    def scanWorkDirectory(self):
        workDir = cfg.get(cfg.workDirectory)
        projects = []
        categoryCounts = {}
        totalFiles = 0
        totalOriginFiles = 0

        if not workDir or not os.path.isdir(workDir):
            return workDir, projects, categoryCounts, totalFiles, totalOriginFiles

        with os.scandir(workDir) as entries:
            projectDirs = [
                Path(entry.path)
                for entry in entries
                if entry.is_dir() and not entry.name.startswith(".")
            ]

        for projectDir in sorted(projectDirs, key=self.getPathModifiedTime, reverse=True):
            originDir = projectDir / "origin_pic"
            labeledDir = projectDir / "labeled_pic"
            originCount = 0
            if originDir.exists():
                originCount = sum(
                    1
                    for item in originDir.iterdir()
                    if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS
                )
                totalOriginFiles += originCount

            projectCategories = {}
            if labeledDir.exists():
                with os.scandir(labeledDir) as categoryEntries:
                    for categoryEntry in categoryEntries:
                        if not categoryEntry.is_dir():
                            continue
                        categoryName = categoryEntry.name
                        count = sum(
                            1
                            for item in Path(categoryEntry.path).iterdir()
                            if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS
                        )
                        projectCategories[categoryName] = count
                        categoryCounts[categoryName] = categoryCounts.get(categoryName, 0) + count
                        totalFiles += count

            projects.append({
                "name": projectDir.name,
                "path": str(projectDir),
                "origin_count": originCount,
                "categories": projectCategories,
            })

        return workDir, projects, categoryCounts, totalFiles, totalOriginFiles

    def getPathModifiedTime(self, path):
        try:
            return Path(path).stat().st_mtime
        except OSError:
            return 0

    def refreshClassification(self):
        workDir, self.projects, self.categoryCounts, self.totalFiles, self.totalOriginFiles = self.scanWorkDirectory()
        self.currentWorkDir = workDir
        self.categoryNames = sorted(self.categoryCounts.keys(), key=lambda name: name.lower())
        self.syncProjectSelection()

        self.workDirLabel.setText(f"工作目录: {workDir or '未设置'}")

        if not workDir or not os.path.isdir(workDir):
            self.statsLabel.setText("请先在设置中配置工作目录。")
            self.projectTable.setRowCount(0)
            self.adjustProjectTableHeight()
            self.updateExportButtonState()
            return

        self.updateSelectionStats()
        self.refreshProjectTable()

    def selectExportDirectory(self):
        startDir = cfg.get(cfg.exportDirectory) or str(Path.home())
        outputDir = QFileDialog.getExistingDirectory(self, "选择导出目录", startDir)
        if not outputDir:
            return

        cfg.set(cfg.exportDirectory, outputDir)
        self.exportDirEdit.setText(outputDir)
        self.refreshClassification()

    def openExportDirectory(self):
        outputDir = cfg.get(cfg.exportDirectory)
        if not outputDir:
            InfoBar.warning("警告", "请先选择导出目录", duration=2000, parent=self)
            return

        Path(outputDir).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(outputDir))

    def syncProjectSelection(self):
        currentPaths = {project["path"] for project in self.projects}
        if not self.projectSelectionInitialized:
            self.selectedProjectPaths = set(currentPaths)
            self.projectSelectionInitialized = True
        else:
            newPaths = currentPaths - self.knownProjectPaths
            self.selectedProjectPaths = (self.selectedProjectPaths & currentPaths) | newPaths

        self.knownProjectPaths = currentPaths

    def getSelectedProjects(self):
        return [
            project
            for project in self.projects
            if project["path"] in self.selectedProjectPaths
        ]

    def getSelectedProjectStats(self):
        selectedProjects = self.getSelectedProjects()
        selectedCategoryCounts = {}
        selectedFiles = 0
        selectedOriginFiles = 0

        for project in selectedProjects:
            selectedOriginFiles += project.get("origin_count", 0)
            for categoryName, count in project.get("categories", {}).items():
                selectedCategoryCounts[categoryName] = selectedCategoryCounts.get(categoryName, 0) + count
                selectedFiles += count

        return selectedProjects, selectedCategoryCounts, selectedFiles, selectedOriginFiles

    def updateExportButtonState(self):
        _, _, selectedFiles, _ = self.getSelectedProjectStats()
        hasWorkDir = bool(self.currentWorkDir and os.path.isdir(self.currentWorkDir))
        hasExportDir = bool(cfg.get(cfg.exportDirectory))
        self.exportButton.setEnabled(bool(hasWorkDir and selectedFiles > 0 and hasExportDir))

    def updateSelectionStats(self):
        selectedProjects, _, selectedFiles, selectedOriginFiles = self.getSelectedProjectStats()
        self.statsLabel.setText(
            f"项目数: {len(self.projects)} | 已选项目: {len(selectedProjects)} | "
            f"原图总数: {self.totalOriginFiles} | 已选原图: {selectedOriginFiles} | "
            f"可导出图片: {self.totalFiles} | 已选可导出: {selectedFiles}"
        )
        self.updateExportButtonState()

    def onProjectSelectionChanged(self, item):
        if item.column() != 0 or item.row() >= len(self.projects):
            return

        projectPath = item.data(Qt.UserRole)
        if not projectPath:
            return

        if item.checkState() == Qt.Checked:
            self.selectedProjectPaths.add(projectPath)
        else:
            self.selectedProjectPaths.discard(projectPath)

        self.updateSelectionStats()
        self.updateProjectSummaryRow()

    def refreshProjectTable(self):
        headers = ["导出", "项目", "原图总数"] + self.categoryNames + ["合计"]
        signalsBlocked = self.projectTable.signalsBlocked()
        self.projectTable.blockSignals(True)
        try:
            self.projectTable.setColumnCount(len(headers))
            self.projectTable.setHorizontalHeaderLabels(headers)
            self.projectTable.setRowCount(len(self.projects) + 1)

            for row, project in enumerate(self.projects):
                checkItem = QTableWidgetItem()
                checkItem.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                checkItem.setCheckState(Qt.Checked if project["path"] in self.selectedProjectPaths else Qt.Unchecked)
                checkItem.setTextAlignment(Qt.AlignCenter)
                checkItem.setData(Qt.UserRole, project["path"])
                self.projectTable.setItem(row, 0, checkItem)
                self.projectTable.setItem(row, 1, QTableWidgetItem(project["name"]))
                self.projectTable.setItem(row, 2, QTableWidgetItem(str(project.get("origin_count", 0))))
                rowTotal = 0
                for column, categoryName in enumerate(self.categoryNames, start=3):
                    count = project["categories"].get(categoryName, 0)
                    rowTotal += count
                    self.projectTable.setItem(row, column, QTableWidgetItem(str(count)))
                self.projectTable.setItem(row, len(headers) - 1, QTableWidgetItem(str(rowTotal)))

            self.updateProjectSummaryRow()
        finally:
            self.projectTable.blockSignals(signalsBlocked)

        if headers:
            self.projectTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
            self.projectTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            for column in range(2, len(headers)):
                self.projectTable.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.projectTable.resizeRowsToContents()
        self.adjustProjectTableHeight()

    def updateProjectSummaryRow(self):
        if not hasattr(self, "projectTable") or self.projectTable.rowCount() == 0:
            return

        summaryRow = len(self.projects)
        if summaryRow >= self.projectTable.rowCount():
            return

        _, selectedCategoryCounts, selectedFiles, selectedOriginFiles = self.getSelectedProjectStats()
        values = ["", "已选汇总", str(selectedOriginFiles)]
        values.extend(str(selectedCategoryCounts.get(categoryName, 0)) for categoryName in self.categoryNames)
        values.append(str(selectedFiles))

        boldFont = QFont()
        boldFont.setBold(True)
        signalsBlocked = self.projectTable.signalsBlocked()
        self.projectTable.blockSignals(True)
        try:
            for column, value in enumerate(values):
                item = self.projectTable.item(summaryRow, column)
                if not item:
                    item = QTableWidgetItem()
                    self.projectTable.setItem(summaryRow, column, item)
                item.setText(value)
                item.setFlags(Qt.ItemIsEnabled)
                item.setFont(boldFont)
        finally:
            self.projectTable.blockSignals(signalsBlocked)

    def adjustProjectTableHeight(self):
        headerHeight = self.projectTable.horizontalHeader().height()
        rowsHeight = sum(
            self.projectTable.rowHeight(row)
            for row in range(self.projectTable.rowCount())
        )
        frameHeight = self.projectTable.frameWidth() * 2
        targetHeight = headerHeight + rowsHeight + frameHeight + 2
        self.projectTable.setFixedHeight(max(64, min(targetHeight, 520)))

    def exportMergedCategories(self):
        selectedProjects, _, selectedFiles, _ = self.getSelectedProjectStats()
        if not selectedProjects:
            InfoBar.warning("警告", "请先选择要导出的项目", duration=2000, parent=self)
            return

        if not selectedFiles:
            InfoBar.warning("警告", "已选项目没有可导出的已分类图片", duration=2000, parent=self)
            return

        outputDir = cfg.get(cfg.exportDirectory)
        if not outputDir:
            InfoBar.warning("警告", "请先选择导出目录", duration=2000, parent=self)
            return

        outputPath = Path(outputDir)
        workDir = cfg.get(cfg.workDirectory)
        if workDir:
            try:
                if outputPath.resolve() == Path(workDir).resolve():
                    InfoBar.error("错误", "导出目录不能和工作目录相同", duration=3000, parent=self)
                    return
            except Exception:
                pass

        confirm = MessageBox(
            "确认导出",
            f"将导出已选 {len(selectedProjects)} 个项目中的 {selectedFiles} 张图片。\n"
            f"导出前会清空目录中的所有内容：\n{outputPath}\n\n是否继续？",
            self.window(),
        )
        QTimer.singleShot(0, lambda: _force_dialog_focus(confirm))
        if not confirm.exec():
            return

        self.setExportRunning(True)
        self.exportProgressBar.setValue(0)
        self.exportProgressLabel.setText("准备导出...")

        self.exportWorker = ExportWorker(selectedProjects, outputPath, cfg.get(cfg.workDirectory))
        self.exportWorker.progress.connect(self.onExportProgress)
        self.exportWorker.exportDone.connect(self.onExportFinished)
        self.exportWorker.start()

    def setExportRunning(self, running):
        self.exportButton.setEnabled(not running)
        self.refreshButton.setEnabled(not running)
        self.selectExportDirButton.setEnabled(not running)
        self.exportProgressBar.setVisible(running)
        self.exportProgressLabel.setVisible(running)
        if not running:
            self.updateExportButtonState()

    def onExportProgress(self, copied, total, file_name):
        percent = int(copied * 100 / total) if total else 100
        self.exportProgressBar.setValue(percent)
        self.exportProgressLabel.setText(f"正在导出: {copied} / {total} | {file_name}")

    def onExportFinished(self, success, message, record):
        self.setExportRunning(False)
        self.exportWorker = None
        if success:
            self.exportProgressBar.setValue(100)
            self.exportProgressLabel.setVisible(True)
            self.exportProgressLabel.setText(message)
            self.historyStore.add(record)
            self.refreshHistory()
            InfoBar.success("成功", message, duration=3000, parent=self)
        else:
            self.exportProgressLabel.setVisible(True)
            self.exportProgressLabel.setText(f"导出失败: {message}")
            InfoBar.error("错误", f"导出失败: {message}", duration=5000, parent=self)

    def refreshHistory(self):
        if not hasattr(self, "historyComboBox"):
            return

        self.historyComboBox.blockSignals(True)
        self.historyComboBox.clear()
        for record in self.historyStore.records:
            self.historyComboBox.addItem(
                f"{record.get('time', '')} | {record.get('total_files', 0)} 张 | {record.get('output_dir', '')}"
            )
        self.historyComboBox.blockSignals(False)

        hasRecords = bool(self.historyStore.records)
        self.openHistoryButton.setEnabled(hasRecords)
        self.deleteHistoryButton.setEnabled(hasRecords)
        if hasRecords:
            self.historyComboBox.setCurrentIndex(0)
            self.showSelectedHistory()
        else:
            self.historySummaryLabel.setText("暂无导出历史。")
            self.historyOutputLabel.setText("")
            self.historyCategoryTable.setRowCount(0)
            self.adjustHistoryCategoryTableHeight()

    def showSelectedHistory(self, *args):
        index = self.historyComboBox.currentIndex()
        if index < 0 or index >= len(self.historyStore.records):
            return

        record = self.historyStore.records[index]
        self.historySummaryLabel.setText(
            f"导出时间: {record.get('time', '')}    "
            f"项目数: {record.get('projects', 0)}    "
            f"图片数: {record.get('total_files', 0)}"
        )
        self.historyOutputLabel.setText(
            f"工作目录: {record.get('work_dir', '')}\n"
            f"导出目录: {record.get('output_dir', '')}"
        )

        categories = record.get("categories", {})
        sortedCategories = sorted(categories.items(), key=lambda item: item[0].lower())
        self.historyCategoryTable.setRowCount(len(sortedCategories))
        if categories:
            for row, (categoryName, count) in enumerate(sortedCategories):
                self.historyCategoryTable.setItem(row, 0, QTableWidgetItem(categoryName))
                self.historyCategoryTable.setItem(row, 1, QTableWidgetItem(str(count)))
        self.historyCategoryTable.resizeRowsToContents()
        self.adjustHistoryCategoryTableHeight()

    def adjustHistoryCategoryTableHeight(self):
        headerHeight = self.historyCategoryTable.horizontalHeader().height()
        rowsHeight = sum(
            self.historyCategoryTable.rowHeight(row)
            for row in range(self.historyCategoryTable.rowCount())
        )
        frameHeight = self.historyCategoryTable.frameWidth() * 2
        targetHeight = headerHeight + rowsHeight + frameHeight + 2
        self.historyCategoryTable.setFixedHeight(max(64, min(targetHeight, 320)))

    def openSelectedHistoryOutput(self):
        index = self.historyComboBox.currentIndex()
        if index < 0 or index >= len(self.historyStore.records):
            return

        outputDir = self.historyStore.records[index].get("output_dir", "")
        if outputDir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(outputDir))

    def deleteSelectedHistory(self):
        index = self.historyComboBox.currentIndex()
        if self.historyStore.delete(index):
            self.refreshHistory()
            InfoBar.success("成功", "已删除导出记录", duration=1500, parent=self)
