# coding:utf-8
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QUrl
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
)

from .gallery_interface import GalleryInterface
from ..common.config import cfg
from ..common.signal_bus import signalBus


EXPORT_HISTORY_PATH = Path(__file__).resolve().parent.parent / "config" / "export_history.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff"}


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
        self.categoryNames = sorted(self.categoryCounts.keys(), key=lambda name: name.lower())

        self.workDirLabel.setText(f"工作目录: {workDir or '未设置'}")
        hasExportDir = bool(cfg.get(cfg.exportDirectory))
        self.exportButton.setEnabled(bool(workDir and os.path.isdir(workDir) and self.totalFiles > 0 and hasExportDir))

        if not workDir or not os.path.isdir(workDir):
            self.statsLabel.setText("请先在设置中配置工作目录。")
            self.projectTable.setRowCount(0)
            self.adjustProjectTableHeight()
            return

        self.statsLabel.setText(
            f"项目数: {len(self.projects)} | 原图总数: {self.totalOriginFiles} | 可导出图片: {self.totalFiles}"
        )
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

    def refreshProjectTable(self):
        headers = ["项目", "原图总数"] + self.categoryNames + ["合计"]
        self.projectTable.setColumnCount(len(headers))
        self.projectTable.setHorizontalHeaderLabels(headers)
        self.projectTable.setRowCount(len(self.projects) + 1)

        for row, project in enumerate(self.projects):
            self.projectTable.setItem(row, 0, QTableWidgetItem(project["name"]))
            self.projectTable.setItem(row, 1, QTableWidgetItem(str(project.get("origin_count", 0))))
            rowTotal = 0
            for column, categoryName in enumerate(self.categoryNames, start=2):
                count = project["categories"].get(categoryName, 0)
                rowTotal += count
                self.projectTable.setItem(row, column, QTableWidgetItem(str(count)))
            self.projectTable.setItem(row, len(headers) - 1, QTableWidgetItem(str(rowTotal)))

        summaryRow = len(self.projects)
        self.projectTable.setItem(summaryRow, 0, QTableWidgetItem("汇总"))
        self.projectTable.setItem(summaryRow, 1, QTableWidgetItem(str(self.totalOriginFiles)))
        for column, categoryName in enumerate(self.categoryNames, start=2):
            self.projectTable.setItem(
                summaryRow,
                column,
                QTableWidgetItem(str(self.categoryCounts.get(categoryName, 0))),
            )
        self.projectTable.setItem(summaryRow, len(headers) - 1, QTableWidgetItem(str(self.totalFiles)))
        boldFont = QFont()
        boldFont.setBold(True)
        for column in range(len(headers)):
            item = self.projectTable.item(summaryRow, column)
            if item:
                item.setFont(boldFont)

        if headers:
            self.projectTable.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            for column in range(1, len(headers)):
                self.projectTable.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.projectTable.resizeRowsToContents()
        self.adjustProjectTableHeight()

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
        if not self.totalFiles:
            InfoBar.warning("警告", "没有可导出的已分类图片", duration=2000, parent=self)
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
            f"导出前会清空目录中的所有内容：\n{outputPath}\n\n是否继续？",
            self.window(),
        )
        if not confirm.exec():
            return

        copiedCount = 0
        categoryResult = {}
        clear_directory(outputPath)

        for project in self.projects:
            projectName = project["name"]
            labeledDir = Path(project["path"]) / "labeled_pic"
            if not labeledDir.exists():
                continue

            with os.scandir(labeledDir) as categoryEntries:
                for categoryEntry in categoryEntries:
                    if not categoryEntry.is_dir():
                        continue

                    categoryName = categoryEntry.name
                    targetCategoryDir = outputPath / sanitize_folder_name(categoryName)
                    targetCategoryDir.mkdir(parents=True, exist_ok=True)

                    for imageFile in Path(categoryEntry.path).iterdir():
                        if not imageFile.is_file() or imageFile.suffix.lower() not in IMAGE_EXTENSIONS:
                            continue

                        targetPath = unique_target_path(targetCategoryDir, imageFile.name, projectName)
                        shutil.copy2(imageFile, targetPath)
                        copiedCount += 1
                        categoryResult[categoryName] = categoryResult.get(categoryName, 0) + 1

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record = {
            "time": now,
            "work_dir": cfg.get(cfg.workDirectory),
            "output_dir": str(outputPath),
            "total_files": copiedCount,
            "categories": categoryResult,
            "projects": len(self.projects),
        }
        self.historyStore.add(record)
        self.refreshHistory()
        InfoBar.success("成功", f"已导出 {copiedCount} 张图片", duration=3000, parent=self)

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
