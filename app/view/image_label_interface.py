# coding:utf-8
import os
import sys
import json
import shutil
from pathlib import Path

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QKeySequence, QPixmap
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, 
                             QFileDialog, QLabel, QPushButton, 
                             QComboBox, QShortcut, QFrame, QSizePolicy)
from qfluentwidgets import (PushButton, ComboBox, StrongBodyLabel, 
                           BodyLabel, LineEdit, InfoBar,
                           FluentIcon as FIF, PrimaryPushButton, CardWidget,
                           FlowLayout, TextEdit, ToolTipFilter, isDarkTheme)
from .gallery_interface import GalleryInterface


class Category:
    def __init__(self, name, color="#3498db", shortcut_key=""):
        self.name = name
        self.color = color
        self.shortcut_key = shortcut_key
        self.count = 0
    
    def to_dict(self):
        return {"name": self.name, "color": self.color, "shortcut_key": self.shortcut_key}
    
    @classmethod
    def from_dict(cls, data):
        return cls(data.get("name", ""), data.get("color", "#3498db"), data.get("shortcut_key", ""))


class LabelProject:
    def __init__(self, project_dir):
        self.project_dir = Path(project_dir)
        self.categories = []
        self.current_image_index = 0
        self.labeled_images = {}
        self.load_config()
    
    def get_config_path(self):
        return self.project_dir / "label_config.json"
    
    def get_output_base_dir(self):
        return self.project_dir / "output"
    
    def load_config(self):
        config_path = self.get_config_path()
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.categories = [Category.from_dict(c) for c in data.get("categories", [])]
                    self.current_image_index = data.get("current_image_index", 0)
                    
                    labeled_images_raw = data.get("labeled_images", {})
                    self.labeled_images = {}
                    for image_path, category_name in labeled_images_raw.items():
                        abs_path = Path(image_path)
                        if abs_path.is_absolute():
                            try:
                                rel_path = str(abs_path.relative_to(self.project_dir))
                                self.labeled_images[rel_path] = category_name
                            except ValueError:
                                self.labeled_images[image_path] = category_name
                        else:
                            self.labeled_images[image_path] = category_name
            except Exception as e:
                print(f"加载配置失败: {e}")
                self.categories = []
        else:
            self.categories = [Category("无效类", "#95a5a6", "")]
    
    def save_config(self):
        config_path = self.get_config_path()
        data = {
            "categories": [c.to_dict() for c in self.categories],
            "current_image_index": self.current_image_index,
            "labeled_images": self.labeled_images
        }
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置失败: {e}")
    
    def get_image_files(self):
        image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff'}
        images = []
        for f in self.project_dir.iterdir():
            if f.suffix.lower() in image_extensions and f.name != "label_config.json":
                images.append(f)
        images.sort(key=lambda x: x.name)
        return images
    
    def add_category(self, name, color="#3498db", shortcut_key=""):
        if any(c.name == name for c in self.categories):
            return False
        self.categories.append(Category(name, color, shortcut_key))
        self.save_config()
        return True
    
    def delete_category(self, name):
        self.categories = [c for c in self.categories if c.name != name]
        self.save_config()
    
    def update_category(self, old_name, new_name, color, shortcut_key=""):
        for c in self.categories:
            if c.name == old_name:
                c.name = new_name
                c.color = color
                c.shortcut_key = shortcut_key
                break
        self.save_config()
    
    def get_category(self, name):
        for c in self.categories:
            if c.name == name:
                return c
        return None
    
    def label_image(self, image_path, category_name):
        rel_path = str(Path(image_path).relative_to(self.project_dir))
        self.labeled_images[rel_path] = category_name
        self.save_config()
    
    def get_image_label(self, image_path):
        rel_path = str(Path(image_path).relative_to(self.project_dir))
        return self.labeled_images.get(rel_path)
    
    def create_output_folders(self):
        output_dir = self.get_output_base_dir()
        output_dir.mkdir(exist_ok=True)
        for category in self.categories:
            category_dir = output_dir / category.name
            category_dir.mkdir(exist_ok=True)
        return output_dir
    
    def export_labeled_images(self):
        output_dir = self.create_output_folders()
        
        for rel_path, category_name in self.labeled_images.items():
            image_file = self.project_dir / rel_path
            if image_file.exists():
                category = self.get_category(category_name)
                if category:
                    target_dir = output_dir / category_name
                    target_path = target_dir / image_file.name
                    try:
                        shutil.copy2(image_file, target_path)
                    except Exception as e:
                        print(f"复制文件失败: {e}")
        
        return output_dir


class ImageDisplayWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.initUI()
    
    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.imageLabel = QLabel("请选择文件夹开始标记", self)
        self.imageLabel.setAlignment(Qt.AlignCenter)
        self.imageLabel.setStyleSheet("""
            QLabel {
                background-color: #2d2d2d;
                border: 2px dashed #555;
                border-radius: 8px;
                min-height: 400px;
                color: #888;
                font-size: 16px;
            }
        """)
        self.imageLabel.setScaledContents(False)
        self.imageLabel.setWordWrap(True)
        
        layout.addWidget(self.imageLabel)
    
    def load_image(self, image_path, max_size=None):
        if not image_path or not Path(image_path).exists():
            self.imageLabel.setText("图片不存在")
            self.image_path = None
            return
        
        try:
            pixmap = QPixmap(str(image_path))
            if pixmap.isNull():
                self.imageLabel.setText("无法加载图片")
                self.image_path = None
                return
            
            if max_size:
                pixmap = pixmap.scaled(max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            
            self.imageLabel.setPixmap(pixmap)
            self.imageLabel.setStyleSheet("""
                QLabel {
                    background-color: #1a1a1a;
                    border: 1px solid #444;
                    border-radius: 4px;
                }
            """)
            self.image_path = image_path
        except Exception as e:
            self.imageLabel.setText(f"加载失败: {str(e)}")
            self.image_path = None


class CategoryCard(CardWidget):
    def __init__(self, category, parent=None, count=0):
        super().__init__(parent)
        self.category = category
        self.parent_widget = parent
        self.setFixedSize(160, 90)  # 增大卡片尺寸以便更好地显示
        self.setCursor(Qt.PointingHandCursor)
        
        self.initUI(count)
    
    def initUI(self, count):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        
        headerLayout = QHBoxLayout()
        headerLayout.setSpacing(8)
        
        self.colorIndicator = QFrame(self)
        self.colorIndicator.setFixedSize(16, 16)
        self.colorIndicator.setAutoFillBackground(True)
        self.colorIndicator.setStyleSheet(f"""
            QFrame {{
                background-color: {self.category.color};
                border-radius: 8px;
                border: 1px solid rgba(0,0,0,0.1);
            }}
        """)
        headerLayout.addWidget(self.colorIndicator)
        
        self.nameLabel = BodyLabel(self.category.name, self)
        self.updateLabelColors()
        headerLayout.addWidget(self.nameLabel, 1)
        
        layout.addLayout(headerLayout)
        
        self.countLabel = BodyLabel(f"数量: {count}", self)
        self.updateCountColor()
        layout.addWidget(self.countLabel, 0, Qt.AlignLeft)
        
        self.installEventFilter(ToolTipFilter(self))
        self.setToolTip("点击选择分类进行标记")
    
    def updateCount(self, count):
        self.countLabel.setText(f"数量: {count}")
    
    def updateLabelColors(self):
        """更新标签颜色以适应深色模式"""
        text_color = "#ffffff" if isDarkTheme() else "#333333"
        self.nameLabel.setStyleSheet(f"""
            QLabel {{
                font-weight: bold;
                font-size: 13px;
                color: {text_color};
            }}
        """)
    
    def updateCountColor(self):
        """更新数量标签颜色以适应深色模式"""
        count_color = "#cccccc" if isDarkTheme() else "#666666"
        self.countLabel.setStyleSheet(f"""
            QLabel {{
                font-size: 12px;
                color: {count_color};
            }}
        """)
    
    def updateColorIndicator(self):
        """更新颜色指示器"""
        self.colorIndicator.setStyleSheet(f"""
            QFrame {{
                background-color: {self.category.color};
                border-radius: 8px;
                border: 1px solid rgba(0,0,0,0.1);
            }}
        """)
    
    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if self.parent_widget:
            self.parent_widget.labelCurrentImage(self.category.name)


class ImageLabelInterface(GalleryInterface):
    def __init__(self, parent=None):
        super().__init__(
            title="图片标记",
            subtitle="对已提取的图片进行分类标记",
            parent=parent
        )
        
        self.setObjectName("imageLabelInterface")
        self.project = None
        self.image_files = []
        self.current_index = -1
        self.shortcutLayout = None
        self.shortcutKeys = {}
        
        self.initUI()
    
    def initUI(self):
        self.addExampleCard(
            title="项目选择",
            widget=self.createProjectSelectionWidget(),
            sourcePath=""
        )
        
        self.addExampleCard(
            title="分类管理",
            widget=self.createCategoryManagementWidget(),
            sourcePath=""
        )
        
        self.addExampleCard(
            title="图片标记",
            widget=self.createImageLabelingWidget(),
            sourcePath="",
            stretch=1
        )
        
        self.addExampleCard(
            title="导出结果",
            widget=self.createExportWidget(),
            sourcePath=""
        )
    
    def createProjectSelectionWidget(self):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setSpacing(15)
        
        self.selectFolderButton = PushButton("选择图片文件夹", self, FIF.FOLDER)
        self.selectFolderButton.clicked.connect(self.selectProjectFolder)
        layout.addWidget(self.selectFolderButton)
        
        self.pasteFolderButton = PushButton("粘贴", self, FIF.PASTE)
        self.pasteFolderButton.clicked.connect(self.pasteFolderFromClipboard)
        layout.addWidget(self.pasteFolderButton)
        
        self.folderPathLabel = BodyLabel("未选择", self)
        layout.addWidget(self.folderPathLabel, 1)
        
        return widget
    
    def createCategoryManagementWidget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)
        
        inputLayout = QHBoxLayout()
        inputLayout.setSpacing(10)
        
        self.newCategoryName = LineEdit(self)
        self.newCategoryName.setPlaceholderText("输入分类名称")
        self.newCategoryName.setFixedWidth(150)
        inputLayout.addWidget(self.newCategoryName)
        
        self.newCategoryColor = ComboBox(self)
        self.newCategoryColor.setFixedWidth(120)
        colors = [
            ("🔵 蓝色", "#3498db"),
            ("🟢 绿色", "#2ecc71"),
            ("🔴 红色", "#e74c3c"),
            ("🟡 黄色", "#f1c40f"),
            ("🟣 紫色", "#9b59b6"),
            ("🟠 橙色", "#e67e22"),
            ("⚪ 灰色", "#95a5a6"),
        ]
        for name, code in colors:
            self.newCategoryColor.addItem(name, code)
        inputLayout.addWidget(self.newCategoryColor)
        
        self.newCategoryShortcut = LineEdit(self)
        self.newCategoryShortcut.setPlaceholderText("快捷键")
        self.newCategoryShortcut.setFixedWidth(80)
        inputLayout.addWidget(self.newCategoryShortcut)
        
        self.addCategoryButton = PushButton("添加", self, FIF.ADD)
        self.addCategoryButton.clicked.connect(self.addCategory)
        inputLayout.addWidget(self.addCategoryButton)
        
        layout.addLayout(inputLayout)
        
        self.categoryContainer = QWidget()
        self.categoryContainer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.categoryFlowLayout = FlowLayout(self.categoryContainer, isTight=False)
        self.categoryFlowLayout.setHorizontalSpacing(15)
        self.categoryFlowLayout.setVerticalSpacing(15)
        self.categoryFlowLayout.setContentsMargins(10, 10, 10, 10)
        self.categoryCards = {}
        layout.addWidget(self.categoryContainer)
        
        return widget
    
    def createImageLabelingWidget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.imageDisplay = ImageDisplayWidget(self)
        layout.addWidget(self.imageDisplay, 1)
        
        controlLayout = QHBoxLayout()
        controlLayout.setSpacing(15)
        
        thumbnailLabel = BodyLabel("图片列表:", self)
        controlLayout.addWidget(thumbnailLabel)
        
        self.imageListCombo = ComboBox(self)
        self.imageListCombo.setFixedWidth(200)
        self.imageListCombo.currentIndexChanged.connect(self.onImageSelected)
        controlLayout.addWidget(self.imageListCombo)
        
        self.prevButton = PushButton("上一张", self, FIF.LEFT_ARROW)
        self.prevButton.clicked.connect(self.prevImage)
        
        self.nextButton = PushButton("下一张", self, FIF.RIGHT_ARROW)
        self.nextButton.clicked.connect(self.nextImage)
        
        self.imageIndexLabel = BodyLabel("0 / 0", self)
        
        controlLayout.addWidget(self.prevButton)
        controlLayout.addWidget(self.imageIndexLabel)
        controlLayout.addWidget(self.nextButton)
        controlLayout.addStretch()
        
        layout.addLayout(controlLayout)
        
        self.shortcutLayout = QHBoxLayout()
        self.shortcutLayout.setSpacing(10)
        self.shortcutButtons = {}
        layout.addLayout(self.shortcutLayout)
        
        self.currentLabelLabel = BodyLabel("当前标记: 无", self)
        self.currentLabelLabel.setStyleSheet("""
            QLabel {
                color: #888;
                font-size: 14px;
                padding: 8px;
                background-color: #2d2d2d;
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.currentLabelLabel)
        
        return widget
    
    def createExportWidget(self):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setSpacing(15)
        
        self.exportInfoLabel = BodyLabel("已标记: 0 / 0 张图片", self)
        layout.addWidget(self.exportInfoLabel)
        
        self.exportButton = PushButton("导出已标记图片", self, FIF.SAVE)
        self.exportButton.clicked.connect(self.exportImages)
        
        self.openOutputButton = PushButton("打开输出文件夹", self, FIF.FOLDER)
        self.openOutputButton.clicked.connect(self.openOutputFolder)
        
        self.progressLabel = BodyLabel("", self)
        
        layout.addWidget(self.exportButton)
        layout.addWidget(self.openOutputButton)
        layout.addWidget(self.progressLabel)
        layout.addStretch()
        
        return widget
    
    def lighten_color(self, color, amount=0.2):
        import colorsys
        color = color.lstrip('#')
        r, g, b = tuple(int(color[i:i+2], 16) for i in (0, 2, 4))
        h, l, s = colorsys.rgb_to_hls(r/255, g/255, b/255)
        l = min(1, l + amount)
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return '#{:02x}{:02x}{:02x}'.format(int(r*255), int(g*255), int(b*255))
    
    def selectProjectFolder(self):
        folderPath = QFileDialog.getExistingDirectory(
            self,
            "选择图片文件夹",
            ""
        )
        
        if folderPath:
            self.loadProjectFolder(folderPath)
    
    def pasteFolderFromClipboard(self):
        """ 从剪贴板粘贴文件夹路径 """
        from PyQt5.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboardText = clipboard.text().strip()
        
        if not clipboardText:
            InfoBar.warning(
                "警告",
                "剪贴板为空",
                duration=2000,
                parent=self
            )
            return
        
        path = Path(clipboardText)
        
        if not path.exists():
            InfoBar.error(
                "错误",
                f"路径不存在: {clipboardText}",
                duration=3000,
                parent=self
            )
            return
        
        if not path.is_dir():
            InfoBar.error(
                "错误",
                f"路径不是文件夹: {clipboardText}",
                duration=3000,
                parent=self
            )
            return
        
        self.loadProjectFolder(str(path))
    
    def loadProjectFolder(self, folderPath):
        """ 加载项目文件夹 """
        self.project = LabelProject(folderPath)
        self.image_files = self.project.get_image_files()
        
        self.folderPathLabel.setText(folderPath)
        self.updateCategoryList()
        self.updateImageList()
        
        if self.image_files:
            self.current_index = 0
            self.loadCurrentImage()
            InfoBar.success(
                "成功",
                f"已加载 {len(self.image_files)} 张图片",
                duration=2000,
                parent=self
            )
        else:
            InfoBar.warning(
                "警告",
                "文件夹中没有找到图片文件",
                duration=2000,
                parent=self
            )
    
    def updateCategoryList(self):
        widgets_to_delete = []
        i = 0
        while i < self.categoryFlowLayout.count():
            item = self.categoryFlowLayout.takeAt(i)
            if item is not None:
                widget = getattr(item, 'widget', lambda: None)()
                if widget is not None:
                    widgets_to_delete.append(widget)
                else:
                    i += 1
            else:
                i += 1
        
        for widget in widgets_to_delete:
            if widget:
                widget.deleteLater()
        
        self.categoryCards = {}
        
        category_counts = {}
        for image_path, category_name in self.project.labeled_images.items():
            if category_name not in category_counts:
                category_counts[category_name] = 0
            category_counts[category_name] += 1
        
        for category in self.project.categories:
            count = category_counts.get(category.name, 0)
            card = CategoryCard(category, self, count)
            self.categoryFlowLayout.addWidget(card)
            self.categoryCards[category.name] = card
        
        self.updateShortcutButtons()
        self.registerShortcuts()
        self.updateAllCategoryCardColors()
    
    def updateShortcutButtons(self):
        if not self.shortcutLayout:
            return
        
        while self.shortcutLayout.count():
            item = self.shortcutLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self.shortcutButtons = {}
        
        for category in self.project.categories:
            btn = PrimaryPushButton(category.name, self)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {category.color};
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 8px 16px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {self.lighten_color(category.color)};
                }}
            """)
            btn.clicked.connect(lambda checked, c=category: self.labelCurrentImage(c.name))
            self.shortcutButtons[category.name] = btn
            self.shortcutLayout.addWidget(btn)
        
        self.shortcutLayout.addStretch()
        self.registerShortcuts()
    
    def registerShortcuts(self):
        for key, shortcut in self.shortcutKeys.items():
            if shortcut:
                shortcut.deleteLater()
        
        self.shortcutKeys = {}
        
        if not self.project:
            return
        
        for category in self.project.categories:
            if category.shortcut_key:
                key_sequence = QKeySequence(category.shortcut_key)
                shortcut = QShortcut(key_sequence, self)
                shortcut.activated.connect(lambda c=category: self.labelCurrentImage(c.name))
                self.shortcutKeys[category.name] = shortcut
    
    def updateAllCategoryCardColors(self):
        """更新所有分类卡片的颜色主题"""
        for card in self.categoryCards.values():
            card.updateLabelColors()
            card.updateCountColor()
            card.updateColorIndicator()
    
    def onThemeChanged(self, theme):
        """主题变化时的处理"""
        self.updateAllCategoryCardColors()
    
    def updateImageList(self):
        self.imageListCombo.clear()
        for i, img_file in enumerate(self.image_files):
            self.imageListCombo.addItem(f"{i+1}. {img_file.name}", str(img_file))
    
    def onImageSelected(self, index):
        if index >= 0 and index < len(self.image_files):
            self.current_index = index
            self.loadCurrentImage()
    
    def loadCurrentImage(self):
        if self.current_index < 0 or self.current_index >= len(self.image_files):
            return
        
        img_file = self.image_files[self.current_index]
        self.imageDisplay.load_image(str(img_file), QSize(800, 500))
        
        self.imageIndexLabel.setText(f"{self.current_index + 1} / {len(self.image_files)}")
        self.imageListCombo.setCurrentIndex(self.current_index)
        
        label = self.project.get_image_label(img_file)
        if label:
            self.currentLabelLabel.setText(f"当前标记: {label}")
            self.currentLabelLabel.setStyleSheet("""
                QLabel {
                    color: #4ecdc4;
                    font-size: 14px;
                    padding: 8px;
                    background-color: #2d2d2d;
                    border-radius: 4px;
                    border: 1px solid #4ecdc4;
                }
            """)
        else:
            self.currentLabelLabel.setText("当前标记: 未标记")
            self.currentLabelLabel.setStyleSheet("""
                QLabel {
                    color: #888;
                    font-size: 14px;
                    padding: 8px;
                    background-color: #2d2d2d;
                    border-radius: 4px;
                }
            """)
        
        labeled_count = len(self.project.labeled_images)
        self.exportInfoLabel.setText(f"已标记: {labeled_count} / {len(self.image_files)} 张图片")
    
    def prevImage(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.loadCurrentImage()
    
    def nextImage(self):
        if self.current_index < len(self.image_files) - 1:
            self.current_index += 1
            self.loadCurrentImage()
    
    def labelCurrentImage(self, category_name):
        if self.current_index < 0 or self.current_index >= len(self.image_files):
            return
        
        img_file = self.image_files[self.current_index]
        self.project.label_image(img_file, category_name)
        self.loadCurrentImage()
        self.updateCategoryList()  # 更新分类列表以刷新数量显示
        
        InfoBar.success(
            "成功",
            f"已标记为: {category_name}",
            duration=1000,
            parent=self
        )
    
    def addCategory(self):
        if not self.project:
            InfoBar.warning(
                "警告",
                "请先选择图片文件夹",
                duration=2000,
                parent=self
            )
            return
        
        name = self.newCategoryName.text().strip()
        color = self.newCategoryColor.currentData()
        shortcut = self.newCategoryShortcut.text().strip()
        
        if not name:
            InfoBar.warning(
                "警告",
                "分类名称不能为空",
                duration=2000,
                parent=self
            )
            return
        
        if self.project.add_category(name, color, shortcut):
            self.newCategoryName.clear()
            self.newCategoryShortcut.clear()
            self.updateCategoryList()
            self.updateShortcutButtons()  # 确保快捷键按钮也更新
            InfoBar.success(
                "成功",
                f"已添加分类: {name}",
                duration=2000,
                parent=self
            )
        else:
            InfoBar.warning(
                "警告",
                "分类名称已存在",
                duration=2000,
                parent=self
            )
    
    def editCategory(self, category):
        if not self.project:
            return
        
        self.newCategoryName.setText(category.name)
        
        for i in range(self.newCategoryColor.count()):
            if self.newCategoryColor.itemData(i) == category.color:
                self.newCategoryColor.setCurrentIndex(i)
                break
        
        self.newCategoryShortcut.setText(category.shortcut_key)
        
        self.addCategoryButton.setText("保存")
        self.addCategoryButton.clicked.connect(lambda: self.saveCategoryEdit(category.name))
        self.newCategoryName.setFocus()
        
        InfoBar.info(
            "编辑模式",
            f"正在编辑分类: {category.name}",
            duration=3000,
            parent=self
        )
    
    def saveCategoryEdit(self, old_name):
        if not self.project:
            return
        
        name = self.newCategoryName.text().strip()
        color = self.newCategoryColor.currentData()
        shortcut = self.newCategoryShortcut.text().strip()
        
        if not name:
            InfoBar.warning(
                "警告",
                "分类名称不能为空",
                duration=2000,
                parent=self
            )
            return
        
        for c in self.project.categories:
            if c.name != old_name and c.name == name:
                InfoBar.warning(
                    "警告",
                    "分类名称已存在",
                    duration=2000,
                    parent=self
                )
                return
        
        self.project.update_category(old_name, name, color, shortcut)
        self.newCategoryName.clear()
        self.newCategoryShortcut.clear()
        self.addCategoryButton.setText("添加")
        self.addCategoryButton.clicked.connect(self.addCategory)
        self.updateCategoryList()
        
        InfoBar.success(
            "成功",
            "分类已更新",
            duration=2000,
            parent=self
        )
    
    def deleteCategory(self, category):
        if not self.project:
            return
        
        self.project.delete_category(category.name)
        self.updateCategoryList()
        
        InfoBar.success(
            "成功",
            f"已删除分类: {category.name}",
            duration=2000,
            parent=self
        )
    
    def exportImages(self):
        if not self.project:
            InfoBar.warning(
                "警告",
                "请先选择图片文件夹",
                duration=2000,
                parent=self
            )
            return
        
        if not self.project.labeled_images:
            InfoBar.warning(
                "警告",
                "没有已标记的图片",
                duration=2000,
                parent=self
            )
            return
        
        try:
            output_dir = self.project.export_labeled_images()
            self.progressLabel.setText(f"已导出到: {output_dir}")
            
            InfoBar.success(
                "成功",
                f"已导出 {len(self.project.labeled_images)} 张图片到 {output_dir}",
                duration=3000,
                parent=self
            )
        except Exception as e:
            InfoBar.error(
                "错误",
                f"导出失败: {str(e)}",
                duration=3000,
                parent=self
            )
    
    def openOutputFolder(self):
        if not self.project:
            InfoBar.warning(
                "警告",
                "请先选择图片文件夹",
                duration=2000,
                parent=self
            )
            return
        
        output_dir = self.project.get_output_base_dir()
        if not output_dir.exists():
            output_dir = self.project.project_dir
        
        try:
            if sys.platform == "win32":
                os.startfile(str(output_dir))
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(["open", str(output_dir)])
            else:
                import subprocess
                subprocess.run(["xdg-open", str(output_dir)])
        except Exception as e:
            InfoBar.error(
                "错误",
                f"无法打开文件夹: {str(e)}",
                duration=3000,
                parent=self
            )