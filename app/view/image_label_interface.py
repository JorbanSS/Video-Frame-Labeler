# coding:utf-8
import os
import sys
import json
import shutil
import re
from pathlib import Path

from PyQt5.QtCore import Qt, QSize, QPoint, QStandardPaths, QTimer
from PyQt5.QtGui import QKeySequence, QPixmap, QKeyEvent
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, 
                             QFileDialog, QLabel, QPushButton, 
                             QComboBox, QShortcut, QFrame, QSizePolicy, QScrollArea)
from qfluentwidgets import (PushButton, ComboBox, StrongBodyLabel, 
                           BodyLabel, LineEdit, InfoBar,
                           FluentIcon as FIF, PrimaryPushButton, CardWidget,
                           FlowLayout, TextEdit, ToolTipFilter, isDarkTheme,
                           Dialog, MessageBox, MessageBoxBase, RoundMenu, Action, ToolButton,
                           SubtitleLabel, InfoBadge)
from .gallery_interface import GalleryInterface


class Category:
    def __init__(self, name, color="#3498db", shortcut_key=""):
        self.name = name
        self.color = color
        self.shortcut_key = shortcut_key
    
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
        return self.get_output_base_dir() / "label_config.json"

    def get_legacy_config_path(self):
        return self.project_dir / "label_config.json"
    
    def get_output_base_dir(self):
        return self.project_dir.parent / "labeled_pic"
    
    def load_config(self):
        config_path = self.get_config_path()
        legacy_config_path = self.get_legacy_config_path()
        if (not config_path.exists()) and legacy_config_path.exists():
            config_path = legacy_config_path

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

                # 旧位置配置读取成功后，写回到新位置
                if config_path != self.get_config_path():
                    self.save_config()
            except Exception as e:
                print(f"加载配置失败: {e}")
                self.categories = []
        else:
            self.categories = []
            # 自动创建无效类
            invalid_category = Category("无效类", "#000000", "Space")
            self.categories.append(invalid_category)
            self.save_config()
    
    def save_config(self):
        config_path = self.get_config_path()
        data = {
            "categories": [c.to_dict() for c in self.categories],
            "current_image_index": self.current_image_index,
            "labeled_images": self.labeled_images
        }
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置失败: {e}")
    
    def get_image_files(self):
        image_extensions = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff')
        images = []
        with os.scandir(self.project_dir) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                if entry.name.lower().endswith(image_extensions):
                    images.append(Path(entry.path))
        images.sort(key=lambda x: x.name)
        return images
    
    def add_category(self, name, color="#3498db", shortcut_key=""):
        if any(c.name == name for c in self.categories):
            return False
        self.categories.append(Category(name, color, shortcut_key))
        self.save_config()
        return True
    
    def delete_category(self, name):
        # 删除类别时，同时删除该类别下的所有标记
        self.categories = [c for c in self.categories if c.name != name]
        self.labeled_images = {k: v for k, v in self.labeled_images.items() if v != name}
        self.save_config()
    
    def update_category(self, old_name, new_name, color, shortcut_key=""):
        # 如果名称改变，需要更新所有标记
        if old_name != new_name:
            self.labeled_images = {k: (new_name if v == old_name else v) 
                                  for k, v in self.labeled_images.items()}
        
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
    
    def get_category_count(self, category_name):
        """获取类别的已归类数量"""
        return sum(1 for v in self.labeled_images.values() if v == category_name)
    
    def label_image(self, image_path, category_name):
        rel_path = str(Path(image_path).relative_to(self.project_dir))
        self.labeled_images[rel_path] = category_name
        self.save_config()
    
    def get_image_label(self, image_path):
        rel_path = str(Path(image_path).relative_to(self.project_dir))
        return self.labeled_images.get(rel_path)
    
    def create_output_folders(self):
        output_dir = self.get_output_base_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        category_dirs = self.get_export_category_dirs(output_dir)
        for category_dir in category_dirs.values():
            category_dir.mkdir(parents=True, exist_ok=True)
        return output_dir, category_dirs

    def sanitize_export_folder_name(self, category_name):
        """Convert category name to a safe single-level folder name."""
        safe_name = str(category_name or "").strip()
        # Replace separators/invalid chars with readable full-width alternatives,
        # so folder names stay close to the original category text.
        full_width_map = str.maketrans({
            "/": "／",
            "\\": "＼",
            ":": "：",
            "*": "＊",
            "?": "？",
            "\"": "＂",
            "<": "＜",
            ">": "＞",
            "|": "｜",
        })
        safe_name = safe_name.translate(full_width_map)
        # Remove control characters.
        safe_name = re.sub(r'[\x00-\x1f]', "", safe_name)
        # Windows does not allow trailing spaces/dots in folder names.
        safe_name = safe_name.rstrip(" .")
        if not safe_name:
            safe_name = "未命名类别"

        # Avoid Windows reserved device names.
        reserved = {
            "CON", "PRN", "AUX", "NUL",
            "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
        }
        if safe_name.upper() in reserved:
            safe_name = f"_{safe_name}"

        return safe_name

    def get_export_category_dirs(self, output_dir):
        """Build unique export folder paths for each category name."""
        category_dirs = {}
        used_names = set()

        for category in self.categories:
            base_name = self.sanitize_export_folder_name(category.name)
            candidate_name = base_name
            index = 2
            while candidate_name.lower() in used_names:
                candidate_name = f"{base_name}_{index}"
                index += 1

            used_names.add(candidate_name.lower())
            category_dirs[category.name] = output_dir / candidate_name

        return category_dirs
    
    def export_labeled_images(self):
        output_dir, category_dirs = self.create_output_folders()
        
        for rel_path, category_name in self.labeled_images.items():
            image_file = self.project_dir / rel_path
            if image_file.exists():
                category = self.get_category(category_name)
                if category:
                    target_dir = category_dirs.get(category_name)
                    if not target_dir:
                        continue
                    target_path = target_dir / image_file.name
                    try:
                        shutil.copy2(image_file, target_path)
                    except Exception as e:
                        print(f"复制文件失败: {e}")
        
        return output_dir


class ShortcutLineEdit(LineEdit):
    """快捷键输入框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("点击后按下快捷键组合")
        self.setReadOnly(True)
        self.shortcut_text = ""
        # 设置焦点策略，允许点击后获得焦点
        self.setFocusPolicy(Qt.ClickFocus)
    
    def keyPressEvent(self, event: QKeyEvent):
        """捕获键盘事件"""
        modifiers = []
        if event.modifiers() & Qt.ControlModifier:
            modifiers.append("Ctrl")
        if event.modifiers() & Qt.AltModifier:
            modifiers.append("Alt")
        if event.modifiers() & Qt.ShiftModifier:
            modifiers.append("Shift")
        if event.modifiers() & Qt.MetaModifier:
            modifiers.append("Meta")
        
        key = event.key()
        if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta):
            return
        
        # 处理空格键
        if key == Qt.Key_Space:
            if modifiers:
                shortcut = "+".join(modifiers) + "+Space"
            else:
                shortcut = "Space"
            self.shortcut_text = shortcut
            self.setText(shortcut)
            event.accept()
            return
        
        key_name = QKeySequence(key).toString()
        if key_name:
            if modifiers:
                shortcut = "+".join(modifiers) + "+" + key_name
            else:
                shortcut = key_name
            self.shortcut_text = shortcut
            self.setText(shortcut)
        event.accept()
    
    def get_shortcut(self):
        return self.shortcut_text


class CategoryEditDialog(MessageBoxBase):
    """类别编辑对话框"""
    def __init__(self, category, parent=None):
        super().__init__(parent)
        self.category = category
        
        self.titleLabel = SubtitleLabel("编辑类别", self)
        self.nameLineEdit = LineEdit(self)
        self.nameLineEdit.setPlaceholderText("输入类别名称")
        self.nameLineEdit.setClearButtonEnabled(True)
        
        self.colorComboBox = ComboBox(self)
        colors = [
            ("🔵 蓝色", "#3498db"),
            ("🟢 绿色", "#2ecc71"),
            ("🔴 红色", "#e74c3c"),
            ("🟡 黄色", "#f1c40f"),
            ("🟣 紫色", "#9b59b6"),
            ("🟠 橙色", "#e67e22"),
            ("⚪ 灰色", "#95a5a6"),
        ]
        for i, (name, code) in enumerate(colors):
            self.colorComboBox.addItem(name)
            self.colorComboBox.setItemData(i, code)
        
        self.shortcutLineEdit = ShortcutLineEdit(self)
        
        # 填充现有数据
        self.nameLineEdit.setText(category.name)
        for i in range(self.colorComboBox.count()):
            if self.colorComboBox.itemData(i) == category.color:
                self.colorComboBox.setCurrentIndex(i)
                break
        self.shortcutLineEdit.setText(category.shortcut_key)
        self.shortcutLineEdit.shortcut_text = category.shortcut_key
        
        # 添加到视图
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(SubtitleLabel("类别名称", self))
        self.viewLayout.addWidget(self.nameLineEdit)
        self.viewLayout.addWidget(SubtitleLabel("颜色", self))
        self.viewLayout.addWidget(self.colorComboBox)
        self.viewLayout.addWidget(SubtitleLabel("快捷键", self))
        self.viewLayout.addWidget(self.shortcutLineEdit)
        
        self.widget.setMinimumWidth(400)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
    
    def get_data(self):
        """获取输入的数据"""
        name = self.nameLineEdit.text().strip()
        color = self.colorComboBox.currentData()
        shortcut = self.shortcutLineEdit.get_shortcut()
        return name, color, shortcut


class ImageDisplayWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_interface = parent
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
    
    def load_image(self, image_path, max_size=None, border_color=None):
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
                pixmap = pixmap.scaled(max_size, Qt.KeepAspectRatio, Qt.FastTransformation)
            
            self.imageLabel.setPixmap(pixmap)
            
            # 根据类别颜色设置边框
            if border_color:
                border_style = f"""
                    QLabel {{
                        background-color: #1a1a1a;
                        border: 3px solid {border_color};
                        border-radius: 4px;
                    }}
                """
            else:
                border_style = """
                    QLabel {
                        background-color: #1a1a1a;
                        border: 3px solid #3a3a3a;
                        border-radius: 4px;
                    }
                """
            self.imageLabel.setStyleSheet(border_style)
            self.image_path = image_path
        except Exception as e:
            self.imageLabel.setText(f"加载失败: {str(e)}")
            self.image_path = None
    
    def mousePressEvent(self, event):
        """处理鼠标点击事件"""
        if self.parent_interface and self.parent_interface.image_files:
            if event.button() == Qt.LeftButton:
                # 左键：下一张
                self.parent_interface.nextImage()
            elif event.button() == Qt.RightButton:
                # 右键：上一张
                self.parent_interface.prevImage()
        super().mousePressEvent(event)


class CategoryCard(CardWidget):
    def __init__(self, category, parent=None, count=0):
        super().__init__(parent)
        self.category = category
        self.parent_widget = parent
        # 不设置固定大小，让卡片适应宽度
        self.setMinimumHeight(64)
        self.setCursor(Qt.PointingHandCursor)
        
        self.initUI(count)
    
    def initUI(self, count):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        
        self.setFixedWidth(180)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        
        # 顶部：颜色指示器和名称
        headerLayout = QHBoxLayout()
        headerLayout.setSpacing(8)
        
        self.colorIndicator = QFrame(self)
        self.colorIndicator.setFixedSize(18, 18)
        self.colorIndicator.setAutoFillBackground(True)
        # 使用更可靠的方式设置颜色
        color = self.category.color if self.category.color else "#3498db"
        self.colorIndicator.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: 9px;
                border: 1px solid rgba(0,0,0,0.1);
            }}
        """)
        headerLayout.addWidget(self.colorIndicator)
        
        self.nameLabel = BodyLabel(self.category.name, self)
        self.updateLabelColors(display_mode=True)  # 显示模式为白色
        headerLayout.addWidget(self.nameLabel, 1)
        
        # 编辑和删除按钮
        buttonLayout = QHBoxLayout()
        buttonLayout.setSpacing(4)
        
        self.editButton = ToolButton(FIF.EDIT, self)
        self.editButton.setFixedSize(24, 24)
        self.editButton.setToolTip("编辑")
        self.editButton.clicked.connect(self.onEditClicked)
        
        self.deleteButton = ToolButton(FIF.DELETE, self)
        self.deleteButton.setFixedSize(24, 24)
        self.deleteButton.setToolTip("删除")
        self.deleteButton.clicked.connect(self.onDeleteClicked)
        
        buttonLayout.addWidget(self.editButton)
        buttonLayout.addWidget(self.deleteButton)
        buttonLayout.addStretch()
        
        headerLayout.addLayout(buttonLayout)
        layout.addLayout(headerLayout)
        
        # 使用文字显示快捷键和已归类数量，放在一行，前后对齐
        textLayout = QHBoxLayout()
        textLayout.setSpacing(6)
        
        # 已归类数量（前面）
        self.countLabel = BodyLabel(f"已标记: {count}", self)
        self.countLabel.setStyleSheet("""
            QLabel {
                color: #aaaaaa;
                font-size: 12px;
            }
        """)
        textLayout.addWidget(self.countLabel)
        
        textLayout.addStretch()
        
        # 快捷键（后面）
        shortcut_text = self.category.shortcut_key if self.category.shortcut_key else "无"
        self.shortcutLabel = BodyLabel(f"快捷键: {shortcut_text}", self)
        self.shortcutLabel.setStyleSheet("""
            QLabel {
                color: #888888;
                font-size: 11px;
            }
        """)
        textLayout.addWidget(self.shortcutLabel)
        
        layout.addLayout(textLayout)
        
        self.installEventFilter(ToolTipFilter(self))
        self.setToolTip("点击卡片选择分类进行标记")
    
    def updateCount(self, count):
        """更新数量文字"""
        if hasattr(self, 'countLabel'):
            self.countLabel.setText(f"已标记: {count}")
    
    def updateLabelColors(self, display_mode=True):
        """更新标签颜色
        display_mode=True: 显示模式（白色）
        display_mode=False: 编辑模式（灰色）
        """
        if display_mode:
            text_color = "#ffffff"
        else:
            text_color = "#999999"
        self.nameLabel.setStyleSheet(f"""
            QLabel {{
                font-weight: bold;
                font-size: 14px;
                color: {text_color};
            }}
        """)
    
    def updateColorIndicator(self):
        """更新颜色指示器"""
        color = self.category.color if self.category.color else "#3498db"
        self.colorIndicator.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: 9px;
                border: 1px solid rgba(0,0,0,0.1);
            }}
        """)
    
    def mouseReleaseEvent(self, e):
        """点击卡片选择分类"""
        super().mouseReleaseEvent(e)
        if self.parent_widget and e.button() == Qt.LeftButton:
            # 检查是否点击在按钮上
            edit_pos = self.editButton.mapFromGlobal(self.mapToGlobal(e.pos()))
            delete_pos = self.deleteButton.mapFromGlobal(self.mapToGlobal(e.pos()))
            if (self.editButton.rect().contains(edit_pos) or 
                self.deleteButton.rect().contains(delete_pos)):
                return
            self.parent_widget.labelCurrentImage(self.category.name)
    
    def onEditClicked(self):
        """编辑按钮点击"""
        if self.parent_widget:
            self.parent_widget.editCategory(self.category)
    
    def onDeleteClicked(self):
        """删除按钮点击"""
        if self.parent_widget:
            self.parent_widget.deleteCategory(self.category)


class ThumbnailListItem(QFrame):
    """Thumbnail item shown in full-screen left image list."""

    def __init__(self, index, image_path, parent_window=None):
        super().__init__(parent_window)
        self.index = index
        self.image_path = Path(image_path)
        self.parent_window = parent_window
        self._thumb_loaded = False
        self.setObjectName("fullscreenThumbItem")
        self.setCursor(Qt.PointingHandCursor)
        self.initUI()

    def initUI(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        self.thumbLabel = QLabel(self)
        self.thumbLabel.setFixedSize(88, 56)
        self.thumbLabel.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.thumbLabel)

        self.nameLabel = BodyLabel(f"{self.index + 1}. {self.image_path.name}", self)
        self.nameLabel.setWordWrap(True)
        layout.addWidget(self.nameLabel, 1)

        self.updateState(active=False, border_color=None, label_text=None)

    def ensureThumbnailLoaded(self, thumb_cache):
        if self._thumb_loaded:
            return

        cache_key = (str(self.image_path), self.thumbLabel.width(), self.thumbLabel.height())
        scaled = thumb_cache.get(cache_key)
        if scaled is None:
            pixmap = QPixmap(str(self.image_path))
            if pixmap.isNull():
                self.thumbLabel.setText("无图")
                self.thumbLabel.setStyleSheet("QLabel { color: #888888; }")
                self._thumb_loaded = True
                return

            scaled = pixmap.scaled(
                self.thumbLabel.size(),
                Qt.KeepAspectRatio,
                Qt.FastTransformation
            )
            thumb_cache[cache_key] = scaled

        self.thumbLabel.setPixmap(scaled)
        self._thumb_loaded = True

    def updateState(self, active=False, border_color=None, label_text=None):
        if isDarkTheme():
            base_bg = "#1f1f1f"
            active_bg = "#2a2a2a"
            text_color = "#e8e8e8"
            sub_text = "#b0b0b0"
            default_border = "#3a3a3a"
        else:
            base_bg = "#ffffff"
            active_bg = "#edf3ff"
            text_color = "#222222"
            sub_text = "#666666"
            default_border = "#d0d0d0"

        border = border_color if border_color else default_border
        border_width = 3 if active else 2
        background = active_bg if active else base_bg

        self.setStyleSheet(f"""
            QFrame#fullscreenThumbItem {{
                background-color: {background};
                border: {border_width}px solid {border};
                border-radius: 6px;
            }}
        """)
        if label_text:
            self.nameLabel.setText(f"{self.index + 1}. {self.image_path.name} [{label_text}]")
        else:
            self.nameLabel.setText(f"{self.index + 1}. {self.image_path.name}")
        self.nameLabel.setStyleSheet(f"QLabel {{ color: {text_color}; font-size: 12px; }}")
        self.thumbLabel.setStyleSheet(f"QLabel {{ background: transparent; color: {sub_text}; }}")

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self.parent_window:
            self.parent_window.selectImageIndex(self.index)


class FullscreenLabelWindow(QWidget):
    """Full-screen labeling window that shows categories and shortcuts only."""

    def __init__(self, label_interface):
        super().__init__(None)
        self.label_interface = label_interface
        self.shortcutKeys = {}
        self.categoryButtons = {}
        self.thumbnailItems = []
        self._thumbCache = {}
        self._sourcePixmapCache = {}
        self._sourcePixmapOrder = []
        self._sourcePixmapLimit = 8
        self._lastActiveThumbnailIndex = -1
        self._themeIsDark = None
        self._thumbnailThemeDirty = True
        self.setObjectName("fullscreenLabelWindow")
        self.setWindowTitle("图片标记")
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.initUI()

    def initUI(self):
        mainLayout = QVBoxLayout(self)
        mainLayout.setContentsMargins(16, 16, 16, 16)
        mainLayout.setSpacing(12)

        topLayout = QHBoxLayout()
        topLayout.setSpacing(10)
        self.titleLabel = StrongBodyLabel("图片标记", self)
        self.indexLabel = BodyLabel("0 / 0", self)
        self.prevButton = PushButton("上一张", self, FIF.LEFT_ARROW)
        self.prevButton.clicked.connect(self.onPrevClicked)
        self.nextButton = PushButton("下一张", self, FIF.RIGHT_ARROW)
        self.nextButton.clicked.connect(self.onNextClicked)
        topLayout.addWidget(self.titleLabel)
        topLayout.addStretch()
        topLayout.addWidget(self.indexLabel)
        topLayout.addWidget(self.prevButton)
        topLayout.addWidget(self.nextButton)
        mainLayout.addLayout(topLayout)

        contentLayout = QHBoxLayout()
        contentLayout.setSpacing(16)
        mainLayout.addLayout(contentLayout, 1)

        # Far left: image list (thumbnail + name + border color)
        listPanel = QWidget(self)
        listPanel.setObjectName("fullscreenListPanel")
        listPanel.setMinimumWidth(260)
        listPanel.setMaximumWidth(340)
        listPanel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        listPanelLayout = QVBoxLayout(listPanel)
        listPanelLayout.setContentsMargins(0, 0, 0, 0)
        listPanelLayout.setSpacing(0)

        self.thumbnailScroll = QScrollArea(self)
        self.thumbnailScroll.setObjectName("fullscreenThumbScroll")
        self.thumbnailScroll.setWidgetResizable(True)
        self.thumbnailScroll.setFrameShape(QFrame.NoFrame)
        self.thumbnailContainer = QWidget(self.thumbnailScroll)
        self.thumbnailContainer.setObjectName("fullscreenThumbContainer")
        self.thumbnailListLayout = QVBoxLayout(self.thumbnailContainer)
        self.thumbnailListLayout.setContentsMargins(0, 0, 0, 0)
        self.thumbnailListLayout.setSpacing(6)
        self.thumbnailListLayout.setAlignment(Qt.AlignTop)
        self.thumbnailListLayout.addStretch()
        self.thumbnailScroll.setWidget(self.thumbnailContainer)
        listPanelLayout.addWidget(self.thumbnailScroll, 1)
        contentLayout.addWidget(listPanel, 1)

        # Right: image on top, categories at bottom
        rightPanel = QWidget(self)
        rightPanel.setObjectName("fullscreenRightPanel")
        rightPanel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        rightPanelLayout = QVBoxLayout(rightPanel)
        rightPanelLayout.setContentsMargins(0, 0, 0, 0)
        rightPanelLayout.setSpacing(2)

        # Top area: image
        imagePanel = QWidget(self)
        imagePanelLayout = QVBoxLayout(imagePanel)
        imagePanelLayout.setContentsMargins(0, 0, 0, 0)
        imagePanelLayout.setSpacing(10)
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
        imagePanelLayout.addWidget(self.currentLabelLabel)

        self.imageDisplay = ImageDisplayWidget(self)
        # Reuse existing left/right click behavior from main interface.
        self.imageDisplay.parent_interface = self.label_interface
        imagePanelLayout.addWidget(self.imageDisplay, 1)
        rightPanelLayout.addWidget(imagePanel, 1)

        # Bottom: horizontal category + shortcut panel (no title)
        self.bottomCategoryPanel = QWidget(self)
        self.bottomCategoryPanel.setObjectName("fullscreenBottomCategoryPanel")
        bottomPanelLayout = QVBoxLayout(self.bottomCategoryPanel)
        bottomPanelLayout.setContentsMargins(0, 0, 0, 0)
        bottomPanelLayout.setSpacing(0)

        self.categoryScroll = QScrollArea(self)
        self.categoryScroll.setObjectName("fullscreenCategoryScroll")
        self.categoryScroll.setWidgetResizable(True)
        self.categoryScroll.setFrameShape(QFrame.NoFrame)
        self.categoryScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.categoryScroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.categoryScroll.setFixedHeight(96)
        self.categoryContainer = QWidget(self.categoryScroll)
        self.categoryContainer.setObjectName("fullscreenCategoryContainer")
        self.categoryListLayout = QHBoxLayout(self.categoryContainer)
        self.categoryListLayout.setContentsMargins(0, 0, 0, 0)
        self.categoryListLayout.setSpacing(8)
        self.categoryListLayout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.categoryListLayout.addStretch()
        self.categoryScroll.setWidget(self.categoryContainer)
        bottomPanelLayout.addWidget(self.categoryScroll)
        rightPanelLayout.addWidget(self.bottomCategoryPanel, 0)

        contentLayout.addWidget(rightPanel, 4)
        self.applyThemeStyles(force=True)

    def _buttonStyle(self, color, active=False):
        if isDarkTheme():
            active_border = "#ffffff"
            inactive_border = "rgba(255,255,255,0.25)"
        else:
            active_border = "#222222"
            inactive_border = "rgba(0,0,0,0.18)"
        border = f"2px solid {active_border}" if active else f"1px solid {inactive_border}"
        hover_color = self.label_interface.lighten_color(color)
        return f"""
            QPushButton {{
                background-color: {color};
                color: white;
                border: {border};
                border-radius: 6px;
                padding: 4px 6px;
                font-weight: bold;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {hover_color};
            }}
        """

    def applyThemeStyles(self, force=False):
        current_dark = isDarkTheme()
        if (not force) and self._themeIsDark is current_dark:
            return
        self._themeIsDark = current_dark
        self._thumbnailThemeDirty = True

        if isDarkTheme():
            window_bg = "#111111"
            text_color = "#f0f0f0"
            sub_text = "#a6a6a6"
            label_bg = "#2d2d2d"
            scroll_track = "#1b1b1b"
            scroll_handle = "#4f4f4f"
            scroll_handle_hover = "#6a6a6a"
        else:
            window_bg = "#f7f7f7"
            text_color = "#202020"
            sub_text = "#666666"
            label_bg = "#f1f2f4"
            scroll_track = "#eceff2"
            scroll_handle = "#b5bcc6"
            scroll_handle_hover = "#9ea7b3"

        self.setStyleSheet(f"""
            QWidget#fullscreenLabelWindow {{
                background-color: {window_bg};
            }}
            QWidget#fullscreenListPanel {{
                background: transparent;
                border: none;
            }}
            QWidget#fullscreenBottomCategoryPanel {{
                background: transparent;
                border: none;
                padding: 0;
            }}
            QScrollArea#fullscreenThumbScroll {{
                background: transparent;
                border: none;
            }}
            QWidget#fullscreenThumbContainer {{
                background: transparent;
            }}
            QScrollArea#fullscreenThumbScroll QWidget#qt_scrollarea_viewport {{
                background-color: transparent;
            }}
            QScrollArea#fullscreenCategoryScroll {{
                background: transparent;
                border: none;
            }}
            QWidget#fullscreenCategoryContainer {{
                background: transparent;
            }}
            QScrollArea#fullscreenCategoryScroll QWidget#qt_scrollarea_viewport {{
                background-color: transparent;
            }}
            QScrollArea#fullscreenThumbScroll QScrollBar:vertical {{
                background: {scroll_track};
                width: 10px;
                margin: 0;
                border-radius: 5px;
            }}
            QScrollArea#fullscreenThumbScroll QScrollBar::handle:vertical {{
                background: {scroll_handle};
                min-height: 24px;
                border-radius: 5px;
            }}
            QScrollArea#fullscreenThumbScroll QScrollBar::handle:vertical:hover {{
                background: {scroll_handle_hover};
            }}
            QScrollArea#fullscreenThumbScroll QScrollBar::add-line:vertical,
            QScrollArea#fullscreenThumbScroll QScrollBar::sub-line:vertical,
            QScrollArea#fullscreenThumbScroll QScrollBar::add-page:vertical,
            QScrollArea#fullscreenThumbScroll QScrollBar::sub-page:vertical {{
                background: transparent;
                height: 0;
            }}
            QScrollArea#fullscreenCategoryScroll QScrollBar:horizontal {{
                background: {scroll_track};
                height: 10px;
                margin: 0;
                border-radius: 5px;
            }}
            QScrollArea#fullscreenCategoryScroll QScrollBar::handle:horizontal {{
                background: {scroll_handle};
                min-width: 24px;
                border-radius: 5px;
            }}
            QScrollArea#fullscreenCategoryScroll QScrollBar::handle:horizontal:hover {{
                background: {scroll_handle_hover};
            }}
            QScrollArea#fullscreenCategoryScroll QScrollBar::add-line:horizontal,
            QScrollArea#fullscreenCategoryScroll QScrollBar::sub-line:horizontal,
            QScrollArea#fullscreenCategoryScroll QScrollBar::add-page:horizontal,
            QScrollArea#fullscreenCategoryScroll QScrollBar::sub-page:horizontal {{
                background: transparent;
                width: 0;
            }}
        """)
        self.titleLabel.setStyleSheet(f"QLabel {{ color: {text_color}; font-size: 18px; font-weight: bold; }}")
        self.indexLabel.setStyleSheet(f"QLabel {{ color: {sub_text}; font-size: 14px; }}")
        self.setCurrentLabelText("当前标记: 无", None, label_bg)

    def _applyImageBorderStyle(self, border_color=None):
        if border_color:
            border_style = f"""
                QLabel {{
                    background-color: #1a1a1a;
                    border: 3px solid {border_color};
                    border-radius: 4px;
                }}
            """
        else:
            border_style = """
                QLabel {
                    background-color: #1a1a1a;
                    border: 3px solid #3a3a3a;
                    border-radius: 4px;
                }
            """
        self.imageDisplay.imageLabel.setStyleSheet(border_style)

    def _getSourcePixmap(self, image_path):
        key = str(image_path)
        pixmap = self._sourcePixmapCache.get(key)
        if pixmap is not None and not pixmap.isNull():
            if key in self._sourcePixmapOrder:
                self._sourcePixmapOrder.remove(key)
            self._sourcePixmapOrder.append(key)
            return pixmap

        pixmap = QPixmap(key)
        if pixmap.isNull():
            return None

        self._sourcePixmapCache[key] = pixmap
        self._sourcePixmapOrder.append(key)
        while len(self._sourcePixmapOrder) > self._sourcePixmapLimit:
            old_key = self._sourcePixmapOrder.pop(0)
            self._sourcePixmapCache.pop(old_key, None)
        return pixmap

    def setCurrentLabelText(self, text, color=None, fallback_bg=None):
        if isDarkTheme():
            base_text = "#888"
            bg = fallback_bg or "#2d2d2d"
        else:
            base_text = "#666"
            bg = fallback_bg or "#f1f2f4"

        text_color = color if color else base_text
        border = f"2px solid {color}" if color else "1px solid transparent"
        self.currentLabelLabel.setText(text)
        self.currentLabelLabel.setStyleSheet(f"""
            QLabel {{
                color: {text_color};
                font-size: 14px;
                padding: 8px;
                background-color: {bg};
                border-radius: 4px;
                border: {border};
            }}
        """)

    def clearShortcuts(self):
        for shortcut in self.shortcutKeys.values():
            if shortcut:
                try:
                    shortcut.deleteLater()
                except Exception:
                    pass
        self.shortcutKeys = {}

    def refreshCategories(self):
        interface = self.label_interface
        self.applyThemeStyles()
        self.clearShortcuts()
        self.categoryButtons = {}

        while self.categoryListLayout.count():
            item = self.categoryListLayout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not interface.project:
            self.categoryListLayout.addStretch()
            return

        for category in interface.project.categories:
            row = QWidget(self.categoryContainer)
            row.setObjectName("fullscreenCategoryRow")
            row.setMinimumWidth(150)
            row.setMaximumWidth(150)
            row.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            row_bg = "#222222" if isDarkTheme() else "#f3f4f6"
            row_border = "rgba(255,255,255,0.08)" if isDarkTheme() else "rgba(0,0,0,0.10)"
            row.setStyleSheet(f"""
                QWidget#fullscreenCategoryRow {{
                    background-color: {row_bg};
                    border: 1px solid {row_border};
                    border-radius: 6px;
                }}
            """)
            rowLayout = QVBoxLayout(row)
            rowLayout.setContentsMargins(4, 3, 4, 3)
            rowLayout.setSpacing(1)
            button = PushButton(category.name, row)
            button.setMinimumHeight(26)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setStyleSheet(self._buttonStyle(category.color or "#3498db"))
            button.clicked.connect(lambda checked, c=category: self.applyCategory(c.name))
            rowLayout.addWidget(button)

            metaRow = QHBoxLayout()
            metaRow.setSpacing(4)
            shortcut_text = category.shortcut_key if category.shortcut_key else "无"
            shortcutLabel = BodyLabel(f"快捷键: {shortcut_text}", row)
            shortcut_color = "#b3b3b3" if isDarkTheme() else "#666666"
            shortcutLabel.setStyleSheet(f"QLabel {{ color: {shortcut_color}; font-size: 10px; }}")
            metaRow.addWidget(shortcutLabel)
            metaRow.addStretch()
            count = interface.project.get_category_count(category.name)
            countLabel = BodyLabel(f"已标记: {count}", row)
            count_color = "#8f8f8f" if isDarkTheme() else "#7a7a7a"
            countLabel.setStyleSheet(f"QLabel {{ color: {count_color}; font-size: 10px; }}")
            metaRow.addWidget(countLabel)
            rowLayout.addLayout(metaRow)

            self.categoryListLayout.addWidget(row)
            self.categoryButtons[category.name] = (button, category.color or "#3498db")

            if category.shortcut_key:
                try:
                    if category.shortcut_key == "Space":
                        key_sequence = QKeySequence(Qt.Key_Space)
                    else:
                        key_sequence = QKeySequence(category.shortcut_key)
                    shortcut = QShortcut(key_sequence, self)
                    shortcut.activated.connect(lambda c=category: self.applyCategory(c.name))
                    self.shortcutKeys[category.name] = shortcut
                except Exception as e:
                    print(f"全屏窗口注册快捷键失败: {e}")

        self.categoryListLayout.addStretch()
        self.refreshImageList(force_rebuild=True)
        self.refreshView(refresh_image_list=False)

    def refreshImageList(self, force_rebuild=False):
        interface = self.label_interface
        images = interface.image_files if interface else []
        should_rebuild = force_rebuild or (len(self.thumbnailItems) != len(images))

        if should_rebuild:
            self.thumbnailItems = []
            while self.thumbnailListLayout.count():
                item = self.thumbnailListLayout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()

            for index, img_file in enumerate(images):
                item = ThumbnailListItem(index, img_file, self)
                self.thumbnailListLayout.addWidget(item)
                self.thumbnailItems.append(item)

            self.thumbnailListLayout.addStretch()
            self._lastActiveThumbnailIndex = -1

        total = len(self.thumbnailItems)
        if total == 0:
            return

        current = interface.current_index
        update_indices = set()

        if force_rebuild:
            # 首屏只刷新当前附近，避免打开全屏时全量刷导致卡顿
            if 0 <= current < total:
                start = max(0, current - 24)
                end = min(total - 1, current + 24)
                update_indices.update(range(start, end + 1))
            else:
                update_indices.update(range(min(total, 30)))
        else:
            if 0 <= self._lastActiveThumbnailIndex < total:
                update_indices.add(self._lastActiveThumbnailIndex)
            if 0 <= current < total:
                update_indices.add(current)
                # 预热当前附近缩略图
                update_indices.update(range(max(0, current - 4), min(total, current + 5)))

        if self._thumbnailThemeDirty:
            update_indices.update(range(total))

        for index in sorted(update_indices):
            item = self.thumbnailItems[index]
            label = interface.project.get_image_label(images[index]) if interface and interface.project else None
            border_color = None
            if label and interface and interface.project:
                category = interface.project.get_category(label)
                if category and category.color:
                    border_color = category.color
            load_thumb = (0 <= current < total and abs(index - current) <= 24) or force_rebuild
            if load_thumb:
                item.ensureThumbnailLoaded(self._thumbCache)
            item.updateState(active=(index == current), border_color=border_color, label_text=label)

        if 0 <= current < len(self.thumbnailItems):
            self.thumbnailScroll.ensureWidgetVisible(self.thumbnailItems[current], 12, 12)
        self._lastActiveThumbnailIndex = current
        self._thumbnailThemeDirty = False

    def selectImageIndex(self, index):
        interface = self.label_interface
        if not interface or index < 0 or index >= len(interface.image_files):
            return
        interface.current_index = index
        interface.loadCurrentImage()
        self.refreshView()

    def applyCategory(self, category_name):
        self.label_interface.labelCurrentImage(category_name)
        self.refreshView()

    def onPrevClicked(self):
        self.label_interface.prevImage()
        self.refreshView()

    def onNextClicked(self):
        self.label_interface.nextImage()
        self.refreshView()

    def refreshView(self, refresh_image_list=True):
        interface = self.label_interface
        self.applyThemeStyles()
        if refresh_image_list:
            self.refreshImageList(force_rebuild=False)
        if not interface.project or not interface.image_files:
            self.indexLabel.setText("0 / 0")
            self.setCurrentLabelText("当前标记: 无")
            self.imageDisplay.load_image(None)
            return

        if interface.current_index < 0 or interface.current_index >= len(interface.image_files):
            self.indexLabel.setText(f"0 / {len(interface.image_files)}")
            self.setCurrentLabelText("当前标记: 无")
            self.imageDisplay.load_image(None)
            return

        img_file = interface.image_files[interface.current_index]
        label = interface.project.get_image_label(img_file)
        border_color = None
        label_color = "#888"
        if label:
            category = interface.project.get_category(label)
            if category and category.color:
                border_color = category.color
                label_color = category.color

        image_area = self.imageDisplay.size()
        image_size = QSize(max(320, image_area.width() - 16), max(240, image_area.height() - 16))
        source_pixmap = self._getSourcePixmap(img_file)
        if source_pixmap is None:
            self.imageDisplay.load_image(str(img_file), image_size, border_color)
        else:
            scaled = source_pixmap.scaled(image_size, Qt.KeepAspectRatio, Qt.FastTransformation)
            self.imageDisplay.imageLabel.setPixmap(scaled)
            self.imageDisplay.image_path = str(img_file)
            self._applyImageBorderStyle(border_color)

        self.indexLabel.setText(f"{interface.current_index + 1} / {len(interface.image_files)}")
        if label:
            self.setCurrentLabelText(f"当前标记: {label}", label_color)
        else:
            self.setCurrentLabelText("当前标记: 未标记")

        for name, button_info in self.categoryButtons.items():
            button, color = button_info
            button.setStyleSheet(self._buttonStyle(color, active=(name == label)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refreshView()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        if event.key() == Qt.Key_Left:
            self.onPrevClicked()
            return
        if event.key() == Qt.Key_Right:
            self.onNextClicked()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.clearShortcuts()
        if self.label_interface:
            self.label_interface.fullscreenLabelWindow = None
        super().closeEvent(event)


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
        self.projectSelectionCard = None
        self.categoryManagementCard = None
        self.imageLabelingCard = None
        self.exportCard = None
        self.fullscreenLabelWindow = None
        self._imageListPopulateToken = 0
        
        self.initUI()
        self.bindContentCards()
        self.updateContentVisibility()
    
    def initUI(self):
        self.addExampleCard(
            title="项目选择",
            widget=self.createProjectSelectionWidget(),
            sourcePath="",
            stretch=1
        )
        
        self.addExampleCard(
            title="分类管理",
            widget=self.createCategoryManagementWidget(),
            sourcePath="",
            stretch=1
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
            sourcePath="",
            stretch=1
        )

    def bindContentCards(self):
        """Capture cards created by `addExampleCard` in init order."""
        cards = []
        for i in range(self.vBoxLayout.count()):
            item = self.vBoxLayout.itemAt(i)
            widget = item.widget() if item else None
            if widget is not None:
                cards.append(widget)

        if len(cards) >= 4:
            self.projectSelectionCard = cards[0]
            self.categoryManagementCard = cards[1]
            self.imageLabelingCard = cards[2]
            self.exportCard = cards[3]

    def updateContentVisibility(self):
        """Hide non-path content before a project folder is selected."""
        has_project = self.project is not None
        for card in (self.categoryManagementCard, self.imageLabelingCard, self.exportCard):
            if card is not None:
                card.setVisible(has_project)
    
    def createProjectSelectionWidget(self):
        widget = QWidget()
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        buttonLayout = QHBoxLayout()
        buttonLayout.setSpacing(15)
        
        self.selectFolderButton = PushButton("选择图片文件夹", self, FIF.FOLDER)
        self.selectFolderButton.clicked.connect(self.selectProjectFolder)
        buttonLayout.addWidget(self.selectFolderButton)
        
        self.pasteFolderButton = PushButton("粘贴", self, FIF.PASTE)
        self.pasteFolderButton.clicked.connect(self.pasteFolderFromClipboard)
        buttonLayout.addWidget(self.pasteFolderButton)
        buttonLayout.addStretch()
        layout.addLayout(buttonLayout)
        
        self.folderPathLabel = BodyLabel("未选择", self)
        self.folderPathLabel.setWordWrap(True)
        self.folderPathLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.folderPathLabel.setMinimumWidth(0)
        self.folderPathLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.folderPathLabel.setStyleSheet("""
            QLabel {
                padding: 6px 8px;
            }
        """)
        layout.addWidget(self.folderPathLabel)
        
        return widget
    
    def createCategoryManagementWidget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 添加类别输入区域 - 去掉CardWidget和标题
        inputWidget = QWidget()
        inputWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        inputLayout = QHBoxLayout(inputWidget)
        inputLayout.setSpacing(10)
        inputLayout.setContentsMargins(0, 0, 0, 0)
        
        # 类别名称输入
        nameLabel = BodyLabel("名称:", self)
        inputLayout.addWidget(nameLabel)
        self.newCategoryName = LineEdit(self)
        self.newCategoryName.setPlaceholderText("输入类别名称")
        self.newCategoryName.setFixedWidth(150)
        self.newCategoryName.setClearButtonEnabled(True)
        inputLayout.addWidget(self.newCategoryName)
        
        # 颜色选择
        colorLabel = BodyLabel("颜色:", self)
        inputLayout.addWidget(colorLabel)
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
        for i, (name, code) in enumerate(colors):
            self.newCategoryColor.addItem(name)
            self.newCategoryColor.setItemData(i, code)
        self.newCategoryColor.setCurrentIndex(0)
        inputLayout.addWidget(self.newCategoryColor)
        
        # 快捷键输入
        shortcutLabel = BodyLabel("快捷键:", self)
        inputLayout.addWidget(shortcutLabel)
        self.newCategoryShortcut = ShortcutLineEdit(self)
        self.newCategoryShortcut.setFixedWidth(150)
        inputLayout.addWidget(self.newCategoryShortcut)
        
        # 添加按钮
        self.addCategoryButton = PushButton("添加", self, FIF.ADD)
        self.addCategoryButton.clicked.connect(self.addCategory)
        inputLayout.addWidget(self.addCategoryButton)
        
        inputLayout.addStretch()
        layout.addWidget(inputWidget)
        
        # 类别卡片容器
        self.categoryContainer = QWidget()
        self.categoryContainer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.categoryFlowLayout = FlowLayout(self.categoryContainer, isTight=False)
        self.categoryFlowLayout.setHorizontalSpacing(8)
        self.categoryFlowLayout.setVerticalSpacing(8)
        self.categoryFlowLayout.setContentsMargins(0, 0, 0, 0)
        self.categoryCards = {}
        layout.addWidget(self.categoryContainer, 1)
        
        return widget
    
    def createImageLabelingWidget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 当前标记标签 - 放在图片前面
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
        
        # 图片显示
        self.imageDisplay = ImageDisplayWidget(self)
        layout.addWidget(self.imageDisplay, 1)
        
        # 控制区域
        controlLayout = QHBoxLayout()
        controlLayout.setSpacing(15)
        
        thumbnailLabel = BodyLabel("图片列表:", self)
        controlLayout.addWidget(thumbnailLabel)
        
        self.imageListCombo = ComboBox(self)
        self.imageListCombo.setFixedWidth(250)
        self.imageListCombo.currentIndexChanged.connect(self.onImageSelected)
        controlLayout.addWidget(self.imageListCombo)
        
        self.prevButton = PushButton("上一张", self, FIF.LEFT_ARROW)
        self.prevButton.clicked.connect(self.prevImage)
        
        self.nextButton = PushButton("下一张", self, FIF.RIGHT_ARROW)
        self.nextButton.clicked.connect(self.nextImage)
        
        self.imageIndexLabel = BodyLabel("0 / 0", self)
        self.fullscreenButton = PushButton("大窗标记", self)
        self.fullscreenButton.clicked.connect(self.openFullscreenLabeler)
        
        controlLayout.addWidget(self.prevButton)
        controlLayout.addWidget(self.imageIndexLabel)
        controlLayout.addWidget(self.nextButton)
        controlLayout.addWidget(self.fullscreenButton)
        controlLayout.addStretch()
        
        layout.addLayout(controlLayout)
        
        # 提示信息
        hintLabel = BodyLabel("提示: 左键点击图片=下一张，右键点击图片=上一张", self)
        hint_color = "#888888" if isDarkTheme() else "#666666"
        hintLabel.setStyleSheet(f"QLabel {{ color: {hint_color}; font-size: 12px; }}")
        layout.addWidget(hintLabel)
        
        # 快捷键按钮
        self.shortcutLayout = QHBoxLayout()
        self.shortcutLayout.setSpacing(10)
        self.shortcutButtons = {}
        layout.addLayout(self.shortcutLayout)
        
        return widget
    
    def createExportWidget(self):
        widget = QWidget()
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(widget)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        topLayout = QHBoxLayout()
        topLayout.setSpacing(15)
        
        self.exportInfoLabel = BodyLabel("已标记: 0 / 0 张图片", self)
        topLayout.addWidget(self.exportInfoLabel)
        
        self.exportButton = PushButton("导出已标记图片", self, FIF.SAVE)
        self.exportButton.clicked.connect(self.exportImages)
        
        self.openOutputButton = PushButton("打开输出文件夹", self, FIF.FOLDER)
        self.openOutputButton.clicked.connect(self.openOutputFolder)

        topLayout.addWidget(self.exportButton)
        topLayout.addWidget(self.openOutputButton)
        topLayout.addStretch()
        layout.addLayout(topLayout)

        self.progressLabel = BodyLabel("导出目录: 未导出", self)
        self.progressLabel.setWordWrap(True)
        self.progressLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.progressLabel.setMinimumWidth(0)
        self.progressLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.progressLabel.setStyleSheet("""
            QLabel {
                padding: 6px 8px;
            }
        """)
        layout.addWidget(self.progressLabel)
        
        return widget

    def openFullscreenLabeler(self):
        """Open large labeling window (maximized, not true fullscreen)."""
        if not self.project or not self.image_files:
            InfoBar.warning(
                "警告",
                "请先选择包含图片的文件夹",
                duration=2000,
                parent=self
            )
            return

        if self.fullscreenLabelWindow and self.fullscreenLabelWindow.isVisible():
            self.fullscreenLabelWindow.activateWindow()
            self.fullscreenLabelWindow.raise_()
            return

        self.fullscreenLabelWindow = FullscreenLabelWindow(self)
        self.fullscreenLabelWindow.showMaximized()
        self.fullscreenLabelWindow.refreshCategories()
        self.fullscreenLabelWindow.refreshView()

    def refreshFullscreenLabelWindow(self, refresh_categories=False):
        if not self.fullscreenLabelWindow or not self.fullscreenLabelWindow.isVisible():
            return
        if refresh_categories:
            self.fullscreenLabelWindow.refreshCategories()
        self.fullscreenLabelWindow.refreshView()
    
    def lighten_color(self, color, amount=0.2):
        import colorsys
        color = color.lstrip('#')
        r, g, b = tuple(int(color[i:i+2], 16) for i in (0, 2, 4))
        h, l, s = colorsys.rgb_to_hls(r/255, g/255, b/255)
        l = min(1, l + amount)
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return '#{:02x}{:02x}{:02x}'.format(int(r*255), int(g*255), int(b*255))
    
    def selectProjectFolder(self):
        startDir = self.resolveDialogDirectory(self.project.project_dir if self.project else None)
        folderPath = QFileDialog.getExistingDirectory(
            self,
            "选择图片文件夹",
            startDir
        )
        
        if folderPath:
            self.loadProjectFolder(folderPath)

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
            QStandardPaths.PicturesLocation,
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
        self.folderPathLabel.setText(folderPath)
        self.selectFolderButton.setEnabled(False)
        self.pasteFolderButton.setEnabled(False)
        QTimer.singleShot(0, lambda p=folderPath: self._loadProjectFolderInternal(p))

    def _loadProjectFolderInternal(self, folderPath):
        try:
            self.project = LabelProject(folderPath)
            self.image_files = self.project.get_image_files()
            self.updateContentVisibility()
            
            self.folderPathLabel.setText(folderPath)
            self.progressLabel.setText("导出目录: 未导出")
            self.updateCategoryList()
            self.updateImageList()
            self.refreshFullscreenLabelWindow(refresh_categories=True)
            
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
        except Exception as e:
            InfoBar.error(
                "错误",
                f"加载文件夹失败: {str(e)}",
                duration=3000,
                parent=self
            )
        finally:
            self.selectFolderButton.setEnabled(True)
            self.pasteFolderButton.setEnabled(True)
    
    def updateCategoryList(self):
        """更新类别列表"""
        if not self.project:
            return
        
        # 清除现有卡片 - 使用更可靠的方法
        # 先收集所有需要删除的widget
        widgets_to_delete = list(self.categoryCards.values())
        
        # 清空布局
        while self.categoryFlowLayout.count() > 0:
            item = self.categoryFlowLayout.takeAt(0)
            if item:
                # 尝试获取widget
                try:
                    widget = item.widget()
                    if widget and widget not in widgets_to_delete:
                        widgets_to_delete.append(widget)
                except (AttributeError, RuntimeError):
                    pass
        
        # 清空字典
        self.categoryCards = {}
        
        # 延迟删除widgets
        for widget in widgets_to_delete:
            if widget:
                try:
                    widget.setParent(None)
                    widget.deleteLater()
                except (RuntimeError, AttributeError):
                    pass
        
        # 计算每个类别的数量并创建新卡片
        for category in self.project.categories:
            # 确保类别有颜色
            if not category.color:
                category.color = "#3498db"
            count = self.project.get_category_count(category.name)
            card = CategoryCard(category, self, count)
            self.categoryFlowLayout.addWidget(card)
            self.categoryCards[category.name] = card
        
        # 更新容器大小 - 让高度自适应
        self.categoryContainer.updateGeometry()
        self.categoryContainer.update()
        
        self.updateShortcutButtons()
        self.registerShortcuts()
        self.updateAllCategoryCardColors()
        self.refreshFullscreenLabelWindow(refresh_categories=True)
    
    def updateShortcutButtons(self):
        """更新快捷键按钮"""
        if not self.shortcutLayout:
            return
        
        while self.shortcutLayout.count():
            item = self.shortcutLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self.shortcutButtons = {}
        
        if not self.project:
            return
        
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
    
    def registerShortcuts(self):
        """注册快捷键"""
        # 清除现有快捷键
        for key, shortcut in self.shortcutKeys.items():
            if shortcut:
                try:
                    shortcut.deleteLater()
                except:
                    pass
        
        self.shortcutKeys = {}
        
        if not self.project:
            return
        
        # 收集所有快捷键，避免重复
        used_shortcuts = {}
        for category in self.project.categories:
            if category.shortcut_key:
                # 检查快捷键是否已被使用
                if category.shortcut_key in used_shortcuts:
                    # 跳过重复的快捷键
                    continue
                used_shortcuts[category.shortcut_key] = category.name
                
                try:
                    # 处理空格键
                    if category.shortcut_key == "Space":
                        key_sequence = QKeySequence(Qt.Key_Space)
                    else:
                        key_sequence = QKeySequence(category.shortcut_key)
                    shortcut = QShortcut(key_sequence, self)
                    shortcut.activated.connect(lambda c=category: self.labelCurrentImage(c.name))
                    self.shortcutKeys[category.name] = shortcut
                except Exception as e:
                    print(f"注册快捷键失败: {e}")
    
    def updateAllCategoryCardColors(self):
        """更新所有分类卡片的颜色主题"""
        for card in self.categoryCards.values():
            card.updateLabelColors(display_mode=True)
            card.updateColorIndicator()
    
    def onThemeChanged(self, theme):
        """主题变化时的处理"""
        self.updateAllCategoryCardColors()
        self.refreshFullscreenLabelWindow(refresh_categories=True)
    
    def updateImageList(self):
        """更新图片列表"""
        self._imageListPopulateToken += 1
        token = self._imageListPopulateToken

        self.imageListCombo.blockSignals(True)
        self.imageListCombo.clear()
        total = len(self.image_files)
        if total == 0:
            self.imageListCombo.blockSignals(False)
            return

        # 先快速填充首批，避免一次性大量 addItem 导致界面卡顿
        first_batch_end = max(200, self.current_index + 1 if self.current_index >= 0 else 1)
        first_batch_end = min(total, first_batch_end)
        for i in range(first_batch_end):
            img_file = self.image_files[i]
            label = self.project.get_image_label(img_file) if self.project else None
            display_text = f"{i+1}. {img_file.name} [{label}]" if label else f"{i+1}. {img_file.name}"
            self.imageListCombo.addItem(display_text, str(img_file))
        self.imageListCombo.blockSignals(False)

        if 0 <= self.current_index < self.imageListCombo.count():
            self.imageListCombo.setCurrentIndex(self.current_index)

        if first_batch_end < total:
            QTimer.singleShot(0, lambda t=token, s=first_batch_end: self.appendImageListChunk(t, s))

    def appendImageListChunk(self, token, start_index):
        if token != self._imageListPopulateToken:
            return

        total = len(self.image_files)
        if start_index >= total:
            return

        end_index = min(total, start_index + 400)
        self.imageListCombo.blockSignals(True)
        for i in range(start_index, end_index):
            img_file = self.image_files[i]
            label = self.project.get_image_label(img_file) if self.project else None
            display_text = f"{i+1}. {img_file.name} [{label}]" if label else f"{i+1}. {img_file.name}"
            self.imageListCombo.addItem(display_text, str(img_file))
        self.imageListCombo.blockSignals(False)

        if end_index < total:
            QTimer.singleShot(0, lambda t=token, s=end_index: self.appendImageListChunk(t, s))
    
    def onImageSelected(self, index):
        """图片选择改变"""
        if index >= 0 and index < len(self.image_files):
            self.current_index = index
            self.loadCurrentImage()
    
    def loadCurrentImage(self):
        """加载当前图片"""
        if self.current_index < 0 or self.current_index >= len(self.image_files):
            return
        
        img_file = self.image_files[self.current_index]
        
        # 获取标记和颜色
        label = self.project.get_image_label(img_file) if self.project else None
        border_color = None
        label_color = "#888"
        
        if label:
            # 获取类别的颜色
            category = self.project.get_category(label) if self.project else None
            if category and category.color:
                border_color = category.color
                label_color = category.color
        
        # 加载图片，传入边框颜色
        self.imageDisplay.load_image(str(img_file), QSize(800, 500), border_color)
        
        self.imageIndexLabel.setText(f"{self.current_index + 1} / {len(self.image_files)}")
        
        # 更新图片列表显示（包含标记信息）
        self.imageListCombo.blockSignals(True)
        if self.current_index < self.imageListCombo.count():
            self.imageListCombo.setCurrentIndex(self.current_index)
            # 更新当前项的文本以显示标记
            if label:
                display_text = f"{self.current_index + 1}. {img_file.name} [{label}]"
            else:
                display_text = f"{self.current_index + 1}. {img_file.name}"
            self.imageListCombo.setItemText(self.current_index, display_text)
        self.imageListCombo.blockSignals(False)
        
        # 更新当前标记标签，显示颜色
        if label:
            self.currentLabelLabel.setText(f"当前标记: {label}")
            self.currentLabelLabel.setStyleSheet(f"""
                QLabel {{
                    color: {label_color};
                    font-size: 14px;
                    padding: 8px;
                    background-color: #2d2d2d;
                    border-radius: 4px;
                    border: 2px solid {label_color};
                }}
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
        
        if self.project:
            labeled_count = len(self.project.labeled_images)
            self.exportInfoLabel.setText(f"已标记: {labeled_count} / {len(self.image_files)} 张图片")
        self.refreshFullscreenLabelWindow()
    
    def prevImage(self):
        """上一张图片"""
        if self.current_index > 0:
            self.current_index -= 1
            self.loadCurrentImage()
    
    def nextImage(self):
        """下一张图片"""
        if self.current_index < len(self.image_files) - 1:
            self.current_index += 1
            self.loadCurrentImage()
    
    def labelCurrentImage(self, category_name):
        """标记当前图片"""
        if self.current_index < 0 or self.current_index >= len(self.image_files):
            return
        
        labeled_index = self.current_index
        img_file = self.image_files[self.current_index]
        self.project.label_image(img_file, category_name)
        
        # 获取类别颜色
        category = self.project.get_category(category_name)
        border_color = category.color if category and category.color else None
        label_color = border_color if border_color else "#4ecdc4"
        
        # 更新图片边框颜色
        self.imageDisplay.load_image(str(img_file), QSize(800, 500), border_color)
        
        # 更新图片列表显示
        label = self.project.get_image_label(img_file)
        self.imageListCombo.blockSignals(True)
        if labeled_index < self.imageListCombo.count():
            if label:
                display_text = f"{labeled_index + 1}. {img_file.name} [{label}]"
            else:
                display_text = f"{labeled_index + 1}. {img_file.name}"
            self.imageListCombo.setItemText(labeled_index, display_text)
        self.imageListCombo.blockSignals(False)
        
        # 更新当前标记标签，显示颜色
        self.currentLabelLabel.setText(f"当前标记: {category_name}")
        self.currentLabelLabel.setStyleSheet(f"""
            QLabel {{
                color: {label_color};
                font-size: 14px;
                padding: 8px;
                background-color: #2d2d2d;
                border-radius: 4px;
                border: 2px solid {label_color};
            }}
        """)
        
        # 更新类别卡片数量（只更新对应卡片，不全部重建）
        if category_name in self.categoryCards:
            count = self.project.get_category_count(category_name)
            self.categoryCards[category_name].updateCount(count)
        
        # 更新导出信息
        labeled_count = len(self.project.labeled_images)
        self.exportInfoLabel.setText(f"已标记: {labeled_count} / {len(self.image_files)} 张图片")
        
        InfoBar.success(
            "成功",
            f"已标记为: {category_name}",
            duration=1000,
            parent=self
        )

        # 自动跳转到下一张（最后一张保持当前位置）
        if labeled_index < len(self.image_files) - 1:
            self.current_index = labeled_index + 1
            self.loadCurrentImage()
        else:
            self.refreshFullscreenLabelWindow()
    
    def addCategory(self):
        """添加类别"""
        if not self.project:
            InfoBar.warning(
                "警告",
                "请先选择图片文件夹",
                duration=2000,
                parent=self
            )
            return
        
        name = self.newCategoryName.text().strip()
        # 获取颜色 - 使用itemData获取
        current_index = self.newCategoryColor.currentIndex()
        if current_index >= 0:
            color = self.newCategoryColor.itemData(current_index)
            if color is None:
                # 如果itemData返回None，使用默认颜色列表
                colors = ["#3498db", "#2ecc71", "#e74c3c", "#f1c40f", "#9b59b6", "#e67e22", "#95a5a6"]
                if current_index < len(colors):
                    color = colors[current_index]
                else:
                    color = "#3498db"
        else:
            color = "#3498db"  # 默认蓝色
        
        shortcut = self.newCategoryShortcut.get_shortcut()
        
        if not name:
            InfoBar.warning(
                "警告",
                "类别名称不能为空",
                duration=2000,
                parent=self
            )
            return
        
        # 检查快捷键是否重复
        if shortcut:
            for category in self.project.categories:
                if category.shortcut_key == shortcut:
                    InfoBar.warning(
                        "警告",
                        f"快捷键 '{shortcut}' 已被类别 '{category.name}' 使用",
                        duration=3000,
                        parent=self
                    )
                    return
        
        if self.project.add_category(name, color, shortcut):
            # 清空输入框
            self.newCategoryName.clear()
            self.newCategoryShortcut.setText("")
            self.newCategoryShortcut.shortcut_text = ""
            
            # 只更新一次类别列表
            self.updateCategoryList()
            
            InfoBar.success(
                "成功",
                f"已添加类别: {name}",
                duration=2000,
                parent=self
            )
        else:
            InfoBar.warning(
                "警告",
                "类别名称已存在",
                duration=2000,
                parent=self
            )
    
    def editCategory(self, category):
        """编辑类别"""
        if not self.project:
            return
        
        dialog = CategoryEditDialog(category, self.window())
        if dialog.exec():
            name, color, shortcut = dialog.get_data()
            
            if not name:
                InfoBar.warning(
                    "警告",
                    "类别名称不能为空",
                    duration=2000,
                    parent=self
                )
                return
            
            # 检查名称是否与其他类别重复
            for c in self.project.categories:
                if c.name != category.name and c.name == name:
                    InfoBar.warning(
                        "警告",
                        "类别名称已存在",
                        duration=2000,
                        parent=self
                    )
                    return
            
            old_name = category.name
            self.project.update_category(old_name, name, color, shortcut)
            self.updateCategoryList()
            
            InfoBar.success(
                "成功",
                "类别已更新",
                duration=2000,
                parent=self
            )
    
    def deleteCategory(self, category):
        """删除类别"""
        if not self.project:
            return
        
        # 确认删除
        count = self.project.get_category_count(category.name)
        message = f"确定要删除类别 '{category.name}' 吗？"
        if count > 0:
            message += f"\n该类别下已有 {count} 张图片被标记，删除后这些标记将被清除。"
        
        w = MessageBox("确认删除", message, self.window())
        if w.exec():
            self.project.delete_category(category.name)
            self.updateCategoryList()
            
            InfoBar.success(
                "成功",
                f"已删除类别: {category.name}",
                duration=2000,
                parent=self
            )
    
    def exportImages(self):
        """导出图片"""
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
            self.progressLabel.setText(f"导出目录:\n{output_dir}")
            
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
        """打开输出文件夹"""
        if not self.project:
            InfoBar.warning(
                "警告",
                "请先选择图片文件夹",
                duration=2000,
                parent=self
            )
            return
        
        output_dir = self.project.get_output_base_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        
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
