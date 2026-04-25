# coding:utf-8
import os
import sys
import json
import shutil
import re
from pathlib import Path

from PyQt5.QtCore import Qt, QSize, QStandardPaths, QTimer
from PyQt5.QtGui import QKeySequence, QPixmap, QKeyEvent
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFileDialog,
    QLabel,
    QShortcut,
    QFrame,
    QSizePolicy,
    QScrollArea,
)
from qfluentwidgets import (
    PushButton,
    ComboBox,
    StrongBodyLabel,
    BodyLabel,
    LineEdit,
    InfoBar,
    FluentIcon as FIF,
    CardWidget,
    FlowLayout,
    ToolTipFilter,
    isDarkTheme,
    MessageBox,
    MessageBoxBase,
    ToolButton,
    SubtitleLabel,
)

from .gallery_interface import GalleryInterface
from ..common.config import cfg
from ..common.signal_bus import signalBus


INVALID_CATEGORY_NAME = "无效类"
INVALID_CATEGORY_COLOR = "#000000"
INVALID_CATEGORY_SHORTCUT = "Space"
DEFAULT_CATEGORY_COLORS = [
    ("蓝色", "#3498db"),
    ("绿色", "#2ecc71"),
    ("红色", "#e74c3c"),
    ("黄色", "#f1c40f"),
    ("紫色", "#9b59b6"),
    ("橙色", "#e67e22"),
    ("灰色", "#95a5a6"),
]
CATEGORY_PRESETS_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "category_presets.json"

FILTER_ALL = "__all__"
FILTER_UNLABELED = "__unlabeled__"


class Category:
    def __init__(self, name, display_name=None, color="#3498db", shortcut_key=""):
        self.name = str(name or "").strip()
        self.display_name = str(display_name if display_name is not None else self.name).strip()
        self.color = color or "#3498db"
        self.shortcut_key = shortcut_key or ""

    def to_dict(self):
        return {
            "name": self.name,
            "display_name": self.display_name,
            "color": self.color,
            "shortcut_key": self.shortcut_key,
        }

    @classmethod
    def from_dict(cls, data):
        name = str(data.get("name", "")).strip()
        display_name = str(data.get("display_name", name)).strip() if isinstance(data, dict) else name
        color = data.get("color", "#3498db") if isinstance(data, dict) else "#3498db"
        shortcut_key = data.get("shortcut_key", "") if isinstance(data, dict) else ""
        return cls(name=name, display_name=display_name or name, color=color, shortcut_key=shortcut_key)


class CategoryPresetStore:
    """Store category presets in a global standalone config file."""

    def __init__(self, config_path=None):
        self.config_path = Path(config_path or CATEGORY_PRESETS_CONFIG_PATH)
        self.presets = []
        self.load()

    def load(self):
        if not self.config_path.exists():
            self.save()
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            self.presets = []
            return

        raw_presets = data.get("presets", []) if isinstance(data, dict) else []
        parsed = []
        for item in raw_presets:
            name = str(item.get("name", "")).strip()
            if not name:
                continue

            categories = []
            seen_names = set()
            for category_data in item.get("categories", []):
                category = Category.from_dict(category_data)
                if (
                    not category.name
                    or category.name == INVALID_CATEGORY_NAME
                    or category.name in seen_names
                ):
                    continue
                if not category.display_name:
                    category.display_name = category.name
                seen_names.add(category.name)
                categories.append(category)

            parsed.append({"name": name, "categories": categories})

        self.presets = parsed

    def save(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "presets": [
                {
                    "name": item["name"],
                    "categories": [c.to_dict() for c in item["categories"]],
                }
                for item in self.presets
            ],
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_names(self):
        return [item["name"] for item in self.presets]

    def get_preset_categories(self, preset_name):
        for item in self.presets:
            if item["name"] == preset_name:
                return [
                    Category(
                        name=c.name,
                        display_name=c.display_name,
                        color=c.color,
                        shortcut_key=c.shortcut_key,
                    )
                    for c in item["categories"]
                ]
        return []

    def has_preset(self, preset_name):
        return any(item["name"] == preset_name for item in self.presets)

    def upsert_preset(self, preset_name, categories):
        normalized_name = str(preset_name or "").strip()
        if not normalized_name:
            return False

        result_categories = []
        used_names = set()
        for category in categories:
            if isinstance(category, dict):
                category = Category.from_dict(category)
            if not isinstance(category, Category):
                continue

            name = category.name.strip()
            if not name or name == INVALID_CATEGORY_NAME or name in used_names:
                continue
            used_names.add(name)
            result_categories.append(
                Category(
                    name=name,
                    display_name=(category.display_name or name),
                    color=(category.color or "#3498db"),
                    shortcut_key=(category.shortcut_key or ""),
                )
            )

        new_item = {"name": normalized_name, "categories": result_categories}
        for i, item in enumerate(self.presets):
            if item["name"] == normalized_name:
                self.presets[i] = new_item
                self.save()
                return True

        self.presets.append(new_item)
        self.presets.sort(key=lambda x: x["name"].lower())
        self.save()
        return True

    def delete_preset(self, preset_name):
        initial_len = len(self.presets)
        self.presets = [item for item in self.presets if item["name"] != preset_name]
        if len(self.presets) == initial_len:
            return False
        self.save()
        return True


class LabelProject:
    def __init__(self, project_dir):
        project_path = Path(project_dir)
        if project_path.name == "origin_pic":
            project_path = project_path.parent

        self.project_dir = project_path
        self.origin_dir = self.project_dir / "origin_pic"
        self.categories = []
        self.current_image_index = 0
        self.labeled_images = {}
        self.load_config()

    def get_config_path(self):
        return self.get_output_base_dir() / "label_config.json"

    def get_legacy_config_path(self):
        return self.project_dir / "label_config.json"

    def get_legacy_config_paths(self):
        return [
            self.project_dir / "label_config.json",
            self.origin_dir / "label_config.json",
        ]

    def get_output_base_dir(self):
        return self.project_dir / "labeled_pic"

    @staticmethod
    def is_reserved_category(name):
        return str(name or "").strip() == INVALID_CATEGORY_NAME

    def _categories_signature(self):
        return [c.to_dict() for c in self.categories]

    def ensure_invalid_category(self):
        """Ensure reserved category exists and immutable as first item."""
        before = self._categories_signature()
        cleaned = []
        used_names = {INVALID_CATEGORY_NAME}

        for category in self.categories:
            if not isinstance(category, Category):
                continue
            name = str(category.name or "").strip()
            if not name:
                continue
            if name == INVALID_CATEGORY_NAME:
                continue
            if name in used_names:
                continue

            used_names.add(name)
            display_name = str(category.display_name or name).strip() or name
            cleaned.append(
                Category(
                    name=name,
                    display_name=display_name,
                    color=category.color or "#3498db",
                    shortcut_key=category.shortcut_key or "",
                )
            )

        self.categories = [
            Category(
                name=INVALID_CATEGORY_NAME,
                display_name=INVALID_CATEGORY_NAME,
                color=INVALID_CATEGORY_COLOR,
                shortcut_key=INVALID_CATEGORY_SHORTCUT,
            )
        ] + cleaned

        return before != self._categories_signature()

    def build_category_alias_map(self):
        alias_map = {}
        for category in self.categories:
            alias_map[category.name] = category.name
            alias_map[category.display_name] = category.name
        return alias_map

    def resolve_category_name(self, name_or_alias):
        key = str(name_or_alias or "").strip()
        if not key:
            return ""
        alias_map = self.build_category_alias_map()
        return alias_map.get(key, key)

    def get_category(self, name_or_alias):
        canonical = self.resolve_category_name(name_or_alias)
        for category in self.categories:
            if category.name == canonical:
                return category
        return None

    def get_category_display_name(self, name_or_alias):
        category = self.get_category(name_or_alias)
        if not category:
            raw = str(name_or_alias or "").strip()
            return raw
        return category.display_name

    def load_config(self):
        config_path = self.get_config_path()
        if not config_path.exists():
            for legacy_config_path in self.get_legacy_config_paths():
                if legacy_config_path.exists():
                    config_path = legacy_config_path
                    break

        migrated = False
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                self.categories = [Category.from_dict(c) for c in data.get("categories", [])]
                self.current_image_index = data.get("current_image_index", 0)

                self.ensure_invalid_category()
                alias_map = self.build_category_alias_map()

                labeled_images_raw = data.get("labeled_images", {})
                self.labeled_images = {}
                for image_path, category_value in labeled_images_raw.items():
                    abs_path = Path(image_path)
                    if abs_path.is_absolute():
                        try:
                            rel_path = str(abs_path.relative_to(self.origin_dir))
                        except ValueError:
                            try:
                                rel_path = str(abs_path.relative_to(self.project_dir))
                            except ValueError:
                                rel_path = image_path
                    else:
                        rel_path = image_path

                    category_key = str(category_value or "").strip()
                    canonical = alias_map.get(category_key, category_key)
                    if canonical != category_key:
                        migrated = True
                    self.labeled_images[rel_path] = canonical

                if config_path != self.get_config_path():
                    migrated = True
            except Exception as e:
                print(f"加载配置失败: {e}")
                self.categories = []
                self.labeled_images = {}

        if not self.categories:
            self.categories = []

        if self.ensure_invalid_category():
            migrated = True

        if migrated or not self.get_config_path().exists():
            self.save_config()

    def save_config(self):
        config_path = self.get_config_path()
        data = {
            "categories": [c.to_dict() for c in self.categories],
            "current_image_index": self.current_image_index,
            "labeled_images": self.labeled_images,
        }
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置失败: {e}")

    def get_image_files(self):
        image_extensions = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff")
        images = []
        if not self.origin_dir.exists():
            return images

        with os.scandir(self.origin_dir) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                if entry.name.lower().endswith(image_extensions):
                    images.append(Path(entry.path))
        images.sort(key=lambda x: x.name)
        return images

    def add_category(self, name, display_name, color="#3498db", shortcut_key=""):
        name = str(name or "").strip()
        display_name = str(display_name or name).strip()
        if not name or not display_name or self.is_reserved_category(name):
            return False
        if any(c.name == name for c in self.categories):
            return False

        self.categories.append(
            Category(
                name=name,
                display_name=display_name,
                color=(color or "#3498db"),
                shortcut_key=(shortcut_key or ""),
            )
        )
        self.ensure_invalid_category()
        self.save_config()
        return True

    def delete_category(self, name):
        canonical = self.resolve_category_name(name)
        if self.is_reserved_category(canonical):
            return False

        self.categories = [c for c in self.categories if c.name != canonical]
        self.labeled_images = {k: v for k, v in self.labeled_images.items() if v != canonical}
        self.ensure_invalid_category()
        self.save_config()
        return True

    def update_category(self, old_name, new_name, new_display_name, color, shortcut_key=""):
        old_name = self.resolve_category_name(old_name)
        new_name = str(new_name or "").strip()
        new_display_name = str(new_display_name or new_name).strip()

        if self.is_reserved_category(old_name) or self.is_reserved_category(new_name):
            return False
        if not new_name or not new_display_name:
            return False
        if old_name != new_name and any(c.name == new_name for c in self.categories):
            return False

        if old_name != new_name:
            self.labeled_images = {
                k: (new_name if v == old_name else v)
                for k, v in self.labeled_images.items()
            }

        for c in self.categories:
            if c.name == old_name:
                c.name = new_name
                c.display_name = new_display_name
                c.color = color or "#3498db"
                c.shortcut_key = shortcut_key or ""
                break

        self.ensure_invalid_category()
        self.save_config()
        return True

    def replace_categories_from_preset(self, categories):
        new_categories = [
            Category(
                name=INVALID_CATEGORY_NAME,
                display_name=INVALID_CATEGORY_NAME,
                color=INVALID_CATEGORY_COLOR,
                shortcut_key=INVALID_CATEGORY_SHORTCUT,
            )
        ]
        used_names = {INVALID_CATEGORY_NAME}

        for category in categories:
            if isinstance(category, dict):
                category = Category.from_dict(category)
            if not isinstance(category, Category):
                continue

            category_name = str(category.name or "").strip()
            if (
                not category_name
                or self.is_reserved_category(category_name)
                or category_name in used_names
            ):
                continue

            used_names.add(category_name)
            new_categories.append(
                Category(
                    name=category_name,
                    display_name=(category.display_name or category_name),
                    color=(category.color or "#3498db"),
                    shortcut_key=(category.shortcut_key or ""),
                )
            )

        self.categories = new_categories
        valid_names = {c.name for c in self.categories}
        self.labeled_images = {
            image_path: category_name
            for image_path, category_name in self.labeled_images.items()
            if category_name in valid_names
        }
        self.save_config()

    def get_editable_categories(self):
        return [c for c in self.categories if not self.is_reserved_category(c.name)]

    def get_category_count(self, category_name_or_alias):
        canonical = self.resolve_category_name(category_name_or_alias)
        return sum(1 for value in self.labeled_images.values() if value == canonical)

    def label_image(self, image_path, category_name_or_alias):
        canonical = self.resolve_category_name(category_name_or_alias)
        if not self.get_category(canonical):
            return False
        rel_path = str(Path(image_path).relative_to(self.origin_dir))
        self.labeled_images[rel_path] = canonical
        self.save_config()
        return True

    def get_image_label(self, image_path):
        rel_path = str(Path(image_path).relative_to(self.origin_dir))
        return self.labeled_images.get(rel_path)

    def create_output_folders(self):
        output_dir = self.get_output_base_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        category_dirs = self.get_export_category_dirs(output_dir)
        for category_dir in category_dirs.values():
            category_dir.mkdir(parents=True, exist_ok=True)
        return output_dir, category_dirs

    def sanitize_export_folder_name(self, category_name):
        """Convert category *name* to safe single-level folder name."""
        safe_name = str(category_name or "").strip()
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
        safe_name = re.sub(r"[\x00-\x1f]", "", safe_name)
        safe_name = safe_name.rstrip(" .")
        if not safe_name:
            safe_name = "unclassified"

        reserved = {
            "CON", "PRN", "AUX", "NUL",
            "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
        }
        if safe_name.upper() in reserved:
            safe_name = f"_{safe_name}"

        return safe_name

    def get_export_category_dirs(self, output_dir):
        category_dirs = {}
        used_names = set()

        for category in self.categories:
            # Must use internal name for export folder, not display_name.
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
            image_file = self.origin_dir / rel_path
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("点击后按下快捷键组合")
        self.setReadOnly(True)
        self.shortcut_text = ""
        self.setFocusPolicy(Qt.ClickFocus)

    def keyPressEvent(self, event: QKeyEvent):
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

        if key == Qt.Key_Space:
            shortcut = "+".join(modifiers) + "+Space" if modifiers else "Space"
            self.shortcut_text = shortcut
            self.setText(shortcut)
            event.accept()
            return

        key_name = QKeySequence(key).toString()
        if key_name:
            shortcut = "+".join(modifiers + [key_name]) if modifiers else key_name
            self.shortcut_text = shortcut
            self.setText(shortcut)
        event.accept()

    def get_shortcut(self):
        return self.shortcut_text


class CategoryEditDialog(MessageBoxBase):
    """Reusable dialog for category add/edit, supports name + display_name."""

    def __init__(self, category=None, parent=None):
        super().__init__(parent)
        self.category = category

        mode_text = "编辑类别" if category else "添加类别"
        self.titleLabel = SubtitleLabel(mode_text, self)

        self.nameLineEdit = LineEdit(self)
        self.nameLineEdit.setPlaceholderText("内部名称(name)，用于存储和导出目录")
        self.nameLineEdit.setClearButtonEnabled(True)

        self.displayNameLineEdit = LineEdit(self)
        self.displayNameLineEdit.setPlaceholderText("显示名称(display_name)，仅用于界面展示")
        self.displayNameLineEdit.setClearButtonEnabled(True)

        self.colorComboBox = ComboBox(self)
        for i, (name, code) in enumerate(DEFAULT_CATEGORY_COLORS):
            self.colorComboBox.addItem(name)
            self.colorComboBox.setItemData(i, code)

        self.shortcutLineEdit = ShortcutLineEdit(self)

        if category:
            self.nameLineEdit.setText(category.name)
            self.displayNameLineEdit.setText(category.display_name)
            for i in range(self.colorComboBox.count()):
                if self.colorComboBox.itemData(i) == category.color:
                    self.colorComboBox.setCurrentIndex(i)
                    break
            self.shortcutLineEdit.setText(category.shortcut_key)
            self.shortcutLineEdit.shortcut_text = category.shortcut_key

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(SubtitleLabel("name", self))
        self.viewLayout.addWidget(self.nameLineEdit)
        self.viewLayout.addWidget(SubtitleLabel("display_name", self))
        self.viewLayout.addWidget(self.displayNameLineEdit)
        self.viewLayout.addWidget(SubtitleLabel("颜色", self))
        self.viewLayout.addWidget(self.colorComboBox)
        self.viewLayout.addWidget(SubtitleLabel("快捷键", self))
        self.viewLayout.addWidget(self.shortcutLineEdit)

        self.widget.setMinimumWidth(460)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")

    def get_data(self):
        name = self.nameLineEdit.text().strip()
        display_name = self.displayNameLineEdit.text().strip()
        color = self.colorComboBox.currentData()
        shortcut = self.shortcutLineEdit.get_shortcut()
        return name, display_name, color, shortcut


class TextInputDialog(MessageBoxBase):
    def __init__(self, title, placeholder="", default_text="", parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(title, self)
        self.inputLineEdit = LineEdit(self)
        self.inputLineEdit.setPlaceholderText(placeholder)
        self.inputLineEdit.setText(default_text)
        self.inputLineEdit.setClearButtonEnabled(True)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.inputLineEdit)

        self.widget.setMinimumWidth(360)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")

    def get_text(self):
        return self.inputLineEdit.text().strip()


class ImageDisplayWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_interface = parent
        self.image_path = None
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.imageLabel = QLabel("请选择文件夹开始标记", self)
        self.imageLabel.setAlignment(Qt.AlignCenter)
        self.imageLabel.setStyleSheet(
            """
            QLabel {
                background-color: #2d2d2d;
                border: 2px dashed #555;
                border-radius: 8px;
                min-height: 400px;
                color: #888;
                font-size: 16px;
            }
            """
        )
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
        if self.parent_interface:
            if hasattr(self.parent_interface, "handleImageMouseNavigation"):
                if event.button() == Qt.LeftButton:
                    self.parent_interface.handleImageMouseNavigation("next")
                elif event.button() == Qt.RightButton:
                    self.parent_interface.handleImageMouseNavigation("prev")
            elif hasattr(self.parent_interface, "image_files") and self.parent_interface.image_files:
                if event.button() == Qt.LeftButton:
                    self.parent_interface.nextImage()
                elif event.button() == Qt.RightButton:
                    self.parent_interface.prevImage()
        super().mousePressEvent(event)


class CategoryCard(CardWidget):
    def __init__(self, category, parent=None, count=0):
        super().__init__(parent)
        self.category = category
        self.parent_widget = parent
        self.setMinimumHeight(64)
        self.setCursor(Qt.ArrowCursor)
        self.initUI(count)

    def initUI(self, count):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        self.setFixedWidth(220)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        headerLayout = QHBoxLayout()
        headerLayout.setSpacing(8)

        self.colorIndicator = QFrame(self)
        self.colorIndicator.setFixedSize(18, 18)
        self.colorIndicator.setAutoFillBackground(True)
        color = self.category.color if self.category.color else "#3498db"
        self.colorIndicator.setStyleSheet(
            f"""
            QFrame {{
                background-color: {color};
                border-radius: 9px;
                border: 1px solid rgba(0,0,0,0.1);
            }}
            """
        )
        headerLayout.addWidget(self.colorIndicator)

        self.nameLabel = BodyLabel(self.category.display_name, self)
        self.updateLabelColors(display_mode=True)
        headerLayout.addWidget(self.nameLabel, 1)

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

        textLayout = QHBoxLayout()
        textLayout.setSpacing(6)

        self.countLabel = BodyLabel(f"已标记: {count}", self)
        self.countLabel.setStyleSheet("QLabel { color: #aaaaaa; font-size: 12px; }")
        textLayout.addWidget(self.countLabel)

        textLayout.addStretch()

        shortcut_text = self.category.shortcut_key if self.category.shortcut_key else "无"
        self.shortcutLabel = BodyLabel(f"快捷键: {shortcut_text}", self)
        self.shortcutLabel.setStyleSheet("QLabel { color: #888888; font-size: 11px; }")
        textLayout.addWidget(self.shortcutLabel)

        layout.addLayout(textLayout)

        name_info = BodyLabel(f"name: {self.category.name}", self)
        name_info.setStyleSheet("QLabel { color: #8a8a8a; font-size: 11px; }")
        layout.addWidget(name_info)

        is_reserved = self.category.name == INVALID_CATEGORY_NAME
        self.editButton.setEnabled(not is_reserved)
        self.deleteButton.setEnabled(not is_reserved)

        self.installEventFilter(ToolTipFilter(self))
        if is_reserved:
            self.setToolTip("系统保留分类，不可编辑或删除")
        else:
            self.setToolTip("分类管理：可编辑/删除")

    def updateCount(self, count):
        if hasattr(self, "countLabel"):
            self.countLabel.setText(f"已标记: {count}")

    def updateLabelColors(self, display_mode=True):
        text_color = "#ffffff" if display_mode else "#999999"
        self.nameLabel.setStyleSheet(
            f"QLabel {{ font-weight: bold; font-size: 14px; color: {text_color}; }}"
        )

    def updateColorIndicator(self):
        color = self.category.color if self.category.color else "#3498db"
        self.colorIndicator.setStyleSheet(
            f"""
            QFrame {{
                background-color: {color};
                border-radius: 9px;
                border: 1px solid rgba(0,0,0,0.1);
            }}
            """
        )

    def onEditClicked(self):
        if self.parent_widget:
            self.parent_widget.editCategory(self.category)

    def onDeleteClicked(self):
        if self.parent_widget:
            self.parent_widget.deleteCategory(self.category)


class ThumbnailListItem(QFrame):
    """Thumbnail item shown in full-screen left image list."""

    def __init__(self, image_index, image_path, parent_window=None):
        super().__init__(parent_window)
        self.image_index = image_index
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

        self.nameLabel = BodyLabel(f"{self.image_index + 1}. {self.image_path.name}", self)
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
                Qt.FastTransformation,
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

        self.setStyleSheet(
            f"""
            QFrame#fullscreenThumbItem {{
                background-color: {background};
                border: {border_width}px solid {border};
                border-radius: 6px;
            }}
            """
        )

        if label_text:
            self.nameLabel.setText(f"{self.image_index + 1}. {self.image_path.name} [{label_text}]")
        else:
            self.nameLabel.setText(f"{self.image_index + 1}. {self.image_path.name}")

        self.nameLabel.setStyleSheet(f"QLabel {{ color: {text_color}; font-size: 12px; }}")
        self.thumbLabel.setStyleSheet(f"QLabel {{ background: transparent; color: {sub_text}; }}")

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self.parent_window:
            self.parent_window.selectImageIndex(self.image_index)


class FullscreenLabelWindow(QWidget):
    """Full-screen labeling window with filtered thumbnail list."""

    def __init__(self, label_interface):
        super().__init__(None)
        self.label_interface = label_interface
        self.shortcutKeys = {}
        self.categoryButtons = {}
        self.thumbnailItems = []
        self.filtered_indices = []
        self._thumbCache = {}
        self._sourcePixmapCache = {}
        self._sourcePixmapOrder = []
        self._sourcePixmapLimit = 8
        self._lastActiveThumbnailPosition = -1
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

        self.filterLabel = BodyLabel("列表筛选", self)
        self.filterCombo = ComboBox(self)
        self.filterCombo.setFixedWidth(220)
        self.filterCombo.currentIndexChanged.connect(self.onFilterChanged)

        self.prevButton = PushButton("上一张", self, FIF.LEFT_ARROW)
        self.prevButton.clicked.connect(self.onPrevClicked)
        self.nextButton = PushButton("下一张", self, FIF.RIGHT_ARROW)
        self.nextButton.clicked.connect(self.onNextClicked)

        topLayout.addWidget(self.titleLabel)
        topLayout.addSpacing(10)
        topLayout.addWidget(self.filterLabel)
        topLayout.addWidget(self.filterCombo)
        topLayout.addStretch()
        topLayout.addWidget(self.indexLabel)
        topLayout.addWidget(self.prevButton)
        topLayout.addWidget(self.nextButton)
        mainLayout.addLayout(topLayout)

        contentLayout = QHBoxLayout()
        contentLayout.setSpacing(16)
        mainLayout.addLayout(contentLayout, 1)

        listPanel = QWidget(self)
        listPanel.setObjectName("fullscreenListPanel")
        listPanel.setMinimumWidth(280)
        listPanel.setMaximumWidth(380)
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

        rightPanel = QWidget(self)
        rightPanel.setObjectName("fullscreenRightPanel")
        rightPanel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        rightPanelLayout = QVBoxLayout(rightPanel)
        rightPanelLayout.setContentsMargins(0, 0, 0, 0)
        rightPanelLayout.setSpacing(2)

        imagePanel = QWidget(self)
        imagePanelLayout = QVBoxLayout(imagePanel)
        imagePanelLayout.setContentsMargins(0, 0, 0, 0)
        imagePanelLayout.setSpacing(10)

        self.currentLabelLabel = BodyLabel("当前标记: 无", self)
        imagePanelLayout.addWidget(self.currentLabelLabel)

        self.imageDisplay = ImageDisplayWidget(self)
        self.imageDisplay.parent_interface = self
        imagePanelLayout.addWidget(self.imageDisplay, 1)
        rightPanelLayout.addWidget(imagePanel, 1)

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

        if current_dark:
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

        self.setStyleSheet(
            f"""
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
            QScrollArea#fullscreenThumbScroll,
            QScrollArea#fullscreenCategoryScroll {{
                background: transparent;
                border: none;
            }}
            QWidget#fullscreenThumbContainer,
            QWidget#fullscreenCategoryContainer {{
                background: transparent;
            }}
            QScrollArea#fullscreenThumbScroll QWidget#qt_scrollarea_viewport,
            QScrollArea#fullscreenCategoryScroll QWidget#qt_scrollarea_viewport {{
                background-color: transparent;
            }}

            QScrollArea#fullscreenThumbScroll QScrollBar:vertical {{
                background: {scroll_track};
                width: 10px;
                margin: 2px;
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
                margin: 2px;
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
            """
        )

        self.titleLabel.setStyleSheet(f"QLabel {{ color: {text_color}; font-size: 18px; font-weight: bold; }}")
        self.indexLabel.setStyleSheet(f"QLabel {{ color: {sub_text}; font-size: 14px; }}")
        self.filterLabel.setStyleSheet(f"QLabel {{ color: {sub_text}; font-size: 13px; }}")
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
        self.currentLabelLabel.setStyleSheet(
            f"""
            QLabel {{
                color: {text_color};
                font-size: 14px;
                padding: 8px;
                background-color: {bg};
                border-radius: 4px;
                border: {border};
            }}
            """
        )

    def clearShortcuts(self):
        for shortcut in self.shortcutKeys.values():
            if shortcut:
                try:
                    shortcut.deleteLater()
                except Exception:
                    pass
        self.shortcutKeys = {}

    def _fillFilterCombo(self):
        previous = self.filterCombo.currentData()

        self.filterCombo.blockSignals(True)
        self.filterCombo.clear()
        self.filterCombo.addItem("全部", FILTER_ALL)
        self.filterCombo.addItem("未标记", FILTER_UNLABELED)

        interface = self.label_interface
        if interface and interface.project:
            for category in interface.project.categories:
                self.filterCombo.addItem(category.display_name, category.name)

        target_index = 0
        for i in range(self.filterCombo.count()):
            if self.filterCombo.itemData(i) == previous:
                target_index = i
                break

        self.filterCombo.setCurrentIndex(target_index)
        self.filterCombo.blockSignals(False)

    def onFilterChanged(self, _):
        self.refreshImageList(force_rebuild=True)
        self.refreshView(refresh_image_list=False)

    def _computeFilteredIndices(self):
        interface = self.label_interface
        if not interface:
            return []

        images = interface.image_files
        project = interface.project
        if not project:
            return list(range(len(images)))

        selected_filter = self.filterCombo.currentData()
        if selected_filter in (None, FILTER_ALL):
            return list(range(len(images)))

        result = []
        if selected_filter == FILTER_UNLABELED:
            for i, img in enumerate(images):
                if not project.get_image_label(img):
                    result.append(i)
            return result

        category_name = project.resolve_category_name(selected_filter)
        for i, img in enumerate(images):
            if project.get_image_label(img) == category_name:
                result.append(i)
        return result

    def _currentFilteredPosition(self):
        interface = self.label_interface
        if not interface:
            return -1
        try:
            return self.filtered_indices.index(interface.current_index)
        except ValueError:
            return -1

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

        self._fillFilterCombo()

        if not interface.project:
            self.categoryListLayout.addStretch()
            return

        for category in interface.project.categories:
            row = QWidget(self.categoryContainer)
            row.setObjectName("fullscreenCategoryRow")
            row.setMinimumWidth(170)
            row.setMaximumWidth(170)
            row.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

            row_bg = "#222222" if isDarkTheme() else "#f3f4f6"
            row_border = "rgba(255,255,255,0.08)" if isDarkTheme() else "rgba(0,0,0,0.10)"
            row.setStyleSheet(
                f"""
                QWidget#fullscreenCategoryRow {{
                    background-color: {row_bg};
                    border: 1px solid {row_border};
                    border-radius: 6px;
                }}
                """
            )

            rowLayout = QVBoxLayout(row)
            rowLayout.setContentsMargins(4, 3, 4, 3)
            rowLayout.setSpacing(1)

            button_color = category.color or "#3498db"
            button = PushButton(category.display_name, row)
            button.setMinimumHeight(26)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setStyleSheet(self._buttonStyle(button_color))
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
            self.categoryButtons[category.name] = (button, button_color)

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
        filtered_indices = self._computeFilteredIndices()

        should_rebuild = (
            force_rebuild
            or filtered_indices != self.filtered_indices
            or len(self.thumbnailItems) != len(filtered_indices)
        )

        if should_rebuild:
            self.filtered_indices = filtered_indices
            self.thumbnailItems = []
            while self.thumbnailListLayout.count():
                item = self.thumbnailListLayout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()

            for original_index in self.filtered_indices:
                item = ThumbnailListItem(original_index, images[original_index], self)
                self.thumbnailListLayout.addWidget(item)
                self.thumbnailItems.append(item)

            self.thumbnailListLayout.addStretch()
            self._lastActiveThumbnailPosition = -1

        total = len(self.thumbnailItems)
        if total == 0:
            return

        active_pos = self._currentFilteredPosition()
        update_positions = set()

        if force_rebuild or should_rebuild:
            if 0 <= active_pos < total:
                start = max(0, active_pos - 24)
                end = min(total - 1, active_pos + 24)
                update_positions.update(range(start, end + 1))
            else:
                update_positions.update(range(min(total, 30)))
        else:
            if 0 <= self._lastActiveThumbnailPosition < total:
                update_positions.add(self._lastActiveThumbnailPosition)
            if 0 <= active_pos < total:
                update_positions.add(active_pos)
                update_positions.update(range(max(0, active_pos - 4), min(total, active_pos + 5)))

        if self._thumbnailThemeDirty:
            update_positions.update(range(total))

        project = interface.project
        for pos in sorted(update_positions):
            original_index = self.filtered_indices[pos]
            item = self.thumbnailItems[pos]
            label_name = project.get_image_label(images[original_index]) if project else None
            label_text = project.get_category_display_name(label_name) if project and label_name else None

            border_color = None
            if label_name and project:
                category = project.get_category(label_name)
                if category and category.color:
                    border_color = category.color

            load_thumb = (0 <= active_pos < total and abs(pos - active_pos) <= 24) or force_rebuild
            if load_thumb:
                item.ensureThumbnailLoaded(self._thumbCache)
            item.updateState(active=(pos == active_pos), border_color=border_color, label_text=label_text)

        if 0 <= active_pos < len(self.thumbnailItems):
            self.thumbnailScroll.ensureWidgetVisible(self.thumbnailItems[active_pos], 12, 12)

        self._lastActiveThumbnailPosition = active_pos
        self._thumbnailThemeDirty = False

    def selectImageIndex(self, image_index):
        interface = self.label_interface
        if not interface or image_index < 0 or image_index >= len(interface.image_files):
            return
        interface.current_index = image_index
        interface.loadCurrentImage()
        self.refreshView()

    def navigateFiltered(self, step):
        interface = self.label_interface
        if not interface or not interface.image_files:
            return

        filtered = self.filtered_indices or self._computeFilteredIndices()
        if not filtered:
            return

        current = interface.current_index
        if current in filtered:
            pos = filtered.index(current) + step
            pos = max(0, min(len(filtered) - 1, pos))
            target_index = filtered[pos]
        else:
            target_index = filtered[0] if step > 0 else filtered[-1]

        if target_index == current:
            return

        interface.current_index = target_index
        interface.loadCurrentImage()
        self.refreshView()

    def handleImageMouseNavigation(self, direction):
        # Mouse left/right follows filtered list only.
        if direction == "next":
            self.navigateFiltered(1)
        elif direction == "prev":
            self.navigateFiltered(-1)

    def applyCategory(self, category_name):
        self.label_interface.labelCurrentImage(category_name)
        self.refreshView()

    def onPrevClicked(self):
        # Keep button behavior on full list.
        self.label_interface.prevImage()
        self.refreshView()

    def onNextClicked(self):
        # Keep button behavior on full list.
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
        label_name = interface.project.get_image_label(img_file)
        label_display = interface.project.get_category_display_name(label_name) if label_name else None

        border_color = None
        label_color = "#888"
        if label_name:
            category = interface.project.get_category(label_name)
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
        if label_display:
            self.setCurrentLabelText(f"当前标记: {label_display}", label_color)
        else:
            self.setCurrentLabelText("当前标记: 未标记")

        for name, button_info in self.categoryButtons.items():
            button, color = button_info
            button.setStyleSheet(self._buttonStyle(color, active=(name == label_name)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refreshView()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        if event.key() == Qt.Key_Left:
            # Keyboard follows button behavior (full-list prev).
            self.onPrevClicked()
            return
        if event.key() == Qt.Key_Right:
            # Keyboard follows button behavior (full-list next).
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
            parent=parent,
        )

        self.setObjectName("imageLabelInterface")
        self.project = None
        self.image_files = []
        self.current_index = -1
        self.projectSelectionCard = None
        self.categoryManagementCard = None
        self.imageLabelingCard = None
        self.exportCard = None
        self.fullscreenLabelWindow = None
        self.categoryCards = {}
        self.presetStore = CategoryPresetStore()

        self.initUI()
        self.bindContentCards()
        self.updateContentVisibility()
        self.refreshPresetList()
        signalBus.workDirectoryChanged.connect(lambda _: self.refreshWorkDirectoryProjects())

    def initUI(self):
        self.addExampleCard(
            title="项目选择",
            widget=self.createProjectSelectionWidget(),
            sourcePath="",
            stretch=1,
        )

        self.addExampleCard(
            title="分类管理",
            widget=self.createCategoryManagementWidget(),
            sourcePath="",
            stretch=1,
        )

        self.addExampleCard(
            title="图片标记",
            widget=self.createImageLabelingWidget(),
            sourcePath="",
            stretch=1,
        )

        self.addExampleCard(
            title="导出结果",
            widget=self.createExportWidget(),
            sourcePath="",
            stretch=1,
        )

    def bindContentCards(self):
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

        self.selectFolderButton = PushButton("选择项目文件夹", self, FIF.FOLDER)
        self.selectFolderButton.clicked.connect(self.selectProjectFolder)
        buttonLayout.addWidget(self.selectFolderButton)

        self.pasteFolderButton = PushButton("粘贴", self, FIF.PASTE)
        self.pasteFolderButton.clicked.connect(self.pasteFolderFromClipboard)
        buttonLayout.addWidget(self.pasteFolderButton)
        buttonLayout.addStretch()
        layout.addLayout(buttonLayout)

        quickLayout = QHBoxLayout()
        quickLayout.setSpacing(10)
        quickLayout.addWidget(BodyLabel("工作目录项目:", self))
        self.quickProjectComboBox = ComboBox(self)
        self.quickProjectComboBox.setMinimumWidth(360)
        quickLayout.addWidget(self.quickProjectComboBox, 1)

        self.loadQuickProjectButton = PushButton("选择", self, FIF.TAG)
        self.loadQuickProjectButton.clicked.connect(self.selectQuickProject)
        quickLayout.addWidget(self.loadQuickProjectButton)

        self.refreshQuickProjectButton = PushButton("刷新", self, FIF.ROTATE)
        self.refreshQuickProjectButton.clicked.connect(self.refreshWorkDirectoryProjects)
        quickLayout.addWidget(self.refreshQuickProjectButton)
        layout.addLayout(quickLayout)

        self.quickProjectPaths = {}
        self.refreshWorkDirectoryProjects()

        self.folderPathLabel = BodyLabel("未选择", self)
        self.folderPathLabel.setWordWrap(True)
        self.folderPathLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.folderPathLabel.setMinimumWidth(0)
        self.folderPathLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.folderPathLabel.setStyleSheet("QLabel { padding: 6px 8px; }")
        layout.addWidget(self.folderPathLabel)

        return widget

    def createCategoryManagementWidget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        topRow = QHBoxLayout()
        topRow.setSpacing(10)

        self.addCategoryButton = PushButton("添加分类", self, FIF.ADD)
        self.addCategoryButton.clicked.connect(self.addCategory)
        topRow.addWidget(self.addCategoryButton)

        topRow.addWidget(BodyLabel("预设", self))
        self.presetComboBox = ComboBox(self)
        self.presetComboBox.setFixedWidth(220)
        topRow.addWidget(self.presetComboBox)

        self.applyPresetButton = PushButton("应用预设", self)
        self.applyPresetButton.clicked.connect(self.applySelectedPreset)
        topRow.addWidget(self.applyPresetButton)

        self.savePresetButton = PushButton("保存为预设", self, FIF.SAVE)
        self.savePresetButton.clicked.connect(self.savePreset)
        topRow.addWidget(self.savePresetButton)

        self.deletePresetButton = PushButton("删除预设", self, FIF.DELETE)
        self.deletePresetButton.clicked.connect(self.deleteSelectedPreset)
        topRow.addWidget(self.deletePresetButton)

        topRow.addStretch()
        layout.addLayout(topRow)

        self.categoryContainer = QWidget()
        self.categoryContainer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.categoryFlowLayout = FlowLayout(self.categoryContainer, isTight=False)
        self.categoryFlowLayout.setHorizontalSpacing(8)
        self.categoryFlowLayout.setVerticalSpacing(8)
        self.categoryFlowLayout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.categoryContainer, 1)

        return widget

    def createImageLabelingWidget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        self.labelingSummaryLabel = BodyLabel("请先选择包含图片的文件夹", self)
        self.labelingSummaryLabel.setWordWrap(True)
        self.labelingSummaryLabel.setStyleSheet(
            """
            QLabel {
                font-size: 14px;
                padding: 10px;
                border-radius: 6px;
                background-color: #2d2d2d;
                color: #d7d7d7;
            }
            """
        )
        layout.addWidget(self.labelingSummaryLabel)

        openRow = QHBoxLayout()
        self.fullscreenButton = PushButton("大窗标记", self)
        self.fullscreenButton.clicked.connect(self.openFullscreenLabeler)
        openRow.addWidget(self.fullscreenButton)
        openRow.addStretch()
        layout.addLayout(openRow)

        hintLabel = BodyLabel("提示: 分类标记操作仅在大窗中进行", self)
        hint_color = "#888888" if isDarkTheme() else "#666666"
        hintLabel.setStyleSheet(f"QLabel {{ color: {hint_color}; font-size: 12px; }}")
        layout.addWidget(hintLabel)

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
        self.progressLabel.setStyleSheet("QLabel { padding: 6px 8px; }")
        layout.addWidget(self.progressLabel)

        return widget

    def refreshPresetList(self, selected_name=None):
        names = self.presetStore.get_names()
        self.presetComboBox.blockSignals(True)
        self.presetComboBox.clear()
        for name in names:
            self.presetComboBox.addItem(name)
        self.presetComboBox.blockSignals(False)

        if selected_name and selected_name in names:
            self.presetComboBox.setCurrentText(selected_name)
        elif names:
            self.presetComboBox.setCurrentIndex(0)

    def getSelectedPresetName(self):
        text = self.presetComboBox.currentText().strip()
        return text if text else None

    def openFullscreenLabeler(self):
        if not self.project or not self.image_files:
            InfoBar.warning("警告", "请先选择包含图片的文件夹", duration=2000, parent=self)
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

        color = color.lstrip("#")
        r, g, b = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        l = min(1, l + amount)
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))

    def selectProjectFolder(self):
        startDir = self.resolveDialogDirectory(
            self.project.project_dir if self.project else None,
            cfg.get(cfg.workDirectory),
        )
        folderPath = QFileDialog.getExistingDirectory(self, "选择项目文件夹", startDir)
        if folderPath:
            self.loadProjectFolder(folderPath)

    def refreshWorkDirectoryProjects(self):
        """Refresh quick project list from configured work directory."""
        if not hasattr(self, "quickProjectComboBox"):
            return

        self.quickProjectComboBox.clear()
        self.quickProjectPaths = {}

        workDir = cfg.get(cfg.workDirectory)
        if not workDir or not os.path.isdir(workDir):
            self.quickProjectComboBox.addItem("未设置工作目录")
            self.loadQuickProjectButton.setEnabled(False)
            return

        projects = []
        with os.scandir(workDir) as entries:
            for entry in entries:
                if entry.is_dir() and not entry.name.startswith("."):
                    projects.append(Path(entry.path))

        projects.sort(key=self.getPathModifiedTime, reverse=True)
        for path in projects:
            self.quickProjectComboBox.addItem(path.name)
            self.quickProjectPaths[path.name] = str(path)

        if not projects:
            self.quickProjectComboBox.addItem("工作目录下未找到文件夹")

        self.loadQuickProjectButton.setEnabled(bool(projects))

    def getPathModifiedTime(self, path):
        try:
            return Path(path).stat().st_mtime
        except OSError:
            return 0

    def selectQuickProject(self):
        display = self.quickProjectComboBox.currentText()
        folderPath = self.quickProjectPaths.get(display)
        if folderPath:
            self.loadProjectFolder(folderPath)

    def resolveDialogDirectory(self, *candidates):
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
            QStandardPaths.HomeLocation,
        ):
            candidate = QStandardPaths.writableLocation(location)
            if candidate and os.path.isdir(candidate):
                return candidate

        home = str(Path.home())
        if os.path.isdir(home):
            return home

        return os.getcwd()

    def pasteFolderFromClipboard(self):
        from PyQt5.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        clipboardText = clipboard.text().strip()

        if not clipboardText:
            InfoBar.warning("警告", "剪贴板为空", duration=2000, parent=self)
            return

        path = Path(clipboardText)
        if not path.exists():
            InfoBar.error("错误", f"路径不存在: {clipboardText}", duration=3000, parent=self)
            return

        if not path.is_dir():
            InfoBar.error("错误", f"路径不是文件夹: {clipboardText}", duration=3000, parent=self)
            return

        self.loadProjectFolder(str(path))

    def loadProjectFolder(self, folderPath):
        projectPath = Path(folderPath)
        if projectPath.name == "origin_pic":
            projectPath = projectPath.parent
        folderPath = str(projectPath)

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
            self.refreshFullscreenLabelWindow(refresh_categories=True)

            if self.image_files:
                self.current_index = 0
                self.loadCurrentImage()
                InfoBar.success("成功", f"已加载 {len(self.image_files)} 张图片", duration=2000, parent=self)
            else:
                self.current_index = -1
                self.updateLabelingSummary()
                InfoBar.warning("警告", "文件夹中没有找到图片文件", duration=2000, parent=self)

            self.updateExportInfo()
        except Exception as e:
            InfoBar.error("错误", f"加载文件夹失败: {str(e)}", duration=3000, parent=self)
        finally:
            self.selectFolderButton.setEnabled(True)
            self.pasteFolderButton.setEnabled(True)

    def updateCategoryList(self):
        if not self.project:
            return

        widgets_to_delete = list(self.categoryCards.values())
        while self.categoryFlowLayout.count() > 0:
            item = self.categoryFlowLayout.takeAt(0)
            if item:
                try:
                    widget = item.widget()
                    if widget and widget not in widgets_to_delete:
                        widgets_to_delete.append(widget)
                except (AttributeError, RuntimeError):
                    pass

        self.categoryCards = {}
        for widget in widgets_to_delete:
            if widget:
                try:
                    widget.setParent(None)
                    widget.deleteLater()
                except (RuntimeError, AttributeError):
                    pass

        for category in self.project.categories:
            if category.name == INVALID_CATEGORY_NAME:
                category.color = INVALID_CATEGORY_COLOR
                category.shortcut_key = INVALID_CATEGORY_SHORTCUT
                category.display_name = INVALID_CATEGORY_NAME
            elif not category.color:
                category.color = "#3498db"

            count = self.project.get_category_count(category.name)
            card = CategoryCard(category, self, count)
            self.categoryFlowLayout.addWidget(card)
            self.categoryCards[category.name] = card

        self.categoryContainer.updateGeometry()
        self.categoryContainer.update()

        self.updateAllCategoryCardColors()
        self.updateLabelingSummary()
        self.updateExportInfo()
        self.refreshFullscreenLabelWindow(refresh_categories=True)

    def updateAllCategoryCardColors(self):
        for card in self.categoryCards.values():
            card.updateLabelColors(display_mode=True)
            card.updateColorIndicator()

    def updateExportInfo(self):
        total = len(self.image_files)
        labeled_count = len(self.project.labeled_images) if self.project else 0
        self.exportInfoLabel.setText(f"已标记: {labeled_count} / {total} 张图片")

    def updateLabelingSummary(self):
        if not self.project:
            self.labelingSummaryLabel.setText("请先选择包含图片的文件夹")
            return

        total = len(self.image_files)
        labeled_count = len(self.project.labeled_images)

        if self.current_index < 0 or self.current_index >= total:
            self.labelingSummaryLabel.setText(f"当前项目图片总数: {total}，已标记: {labeled_count}")
            return

        img_file = self.image_files[self.current_index]
        label_name = self.project.get_image_label(img_file)
        label_text = self.project.get_category_display_name(label_name) if label_name else "未标记"
        self.labelingSummaryLabel.setText(
            f"当前图片: {self.current_index + 1} / {total} | 文件: {img_file.name} | 当前标记: {label_text} | 已标记: {labeled_count}"
        )

    def onThemeChanged(self, theme):
        self.updateAllCategoryCardColors()
        self.refreshFullscreenLabelWindow(refresh_categories=True)

    def validateCategoryInput(self, name, display_name, shortcut, original_name=None):
        normalized_name = str(name or "").strip()
        normalized_display_name = str(display_name or "").strip()
        if not normalized_name:
            return False, "name 不能为空"
        if not normalized_display_name:
            return False, "display_name 不能为空"

        if normalized_name == INVALID_CATEGORY_NAME:
            return False, "“无效类”为系统保留分类"

        for category in self.project.categories:
            if category.name == normalized_name and category.name != original_name:
                return False, "name 已存在"

        normalized_shortcut = str(shortcut or "").strip()
        if normalized_shortcut:
            for category in self.project.categories:
                if category.name == original_name:
                    continue
                if category.shortcut_key == normalized_shortcut:
                    return False, f"快捷键 '{normalized_shortcut}' 已被类别 '{category.display_name}' 使用"

        return True, ""

    def openCategoryDialog(self, category=None):
        dialog = CategoryEditDialog(category, self.window())
        if not dialog.exec():
            return None

        name, display_name, color, shortcut = dialog.get_data()
        valid, message = self.validateCategoryInput(
            name,
            display_name,
            shortcut,
            category.name if category else None,
        )
        if not valid:
            InfoBar.warning("警告", message, duration=2500, parent=self)
            return None

        return name, display_name, color, shortcut

    def addCategory(self):
        if not self.project:
            InfoBar.warning("警告", "请先选择图片文件夹", duration=2000, parent=self)
            return

        result = self.openCategoryDialog(None)
        if not result:
            return

        name, display_name, color, shortcut = result
        if self.project.add_category(name, display_name, color, shortcut):
            self.updateCategoryList()
            InfoBar.success("成功", f"已添加类别: {display_name} ({name})", duration=2000, parent=self)
        else:
            InfoBar.warning("警告", "类别添加失败", duration=2000, parent=self)

    def editCategory(self, category):
        if not self.project:
            return
        if category.name == INVALID_CATEGORY_NAME:
            InfoBar.warning("警告", "系统保留分类不可编辑", duration=2000, parent=self)
            return

        result = self.openCategoryDialog(category)
        if not result:
            return

        name, display_name, color, shortcut = result
        if self.project.update_category(category.name, name, display_name, color, shortcut):
            self.updateCategoryList()
            InfoBar.success("成功", "类别已更新", duration=2000, parent=self)
        else:
            InfoBar.warning("警告", "类别更新失败", duration=2000, parent=self)

    def deleteCategory(self, category):
        if not self.project:
            return
        if category.name == INVALID_CATEGORY_NAME:
            InfoBar.warning("警告", "系统保留分类不可删除", duration=2000, parent=self)
            return

        count = self.project.get_category_count(category.name)
        message = f"确定要删除类别 '{category.display_name}' 吗？"
        if count > 0:
            message += f"\n该类别下已有 {count} 张图片被标记，删除后这些标记将被清除。"

        w = MessageBox("确认删除", message, self.window())
        if w.exec() and self.project.delete_category(category.name):
            self.updateCategoryList()
            self.loadCurrentImage()
            InfoBar.success("成功", f"已删除类别: {category.display_name}", duration=2000, parent=self)

    def savePreset(self):
        if not self.project:
            InfoBar.warning("警告", "请先选择图片文件夹", duration=2000, parent=self)
            return

        dialog = TextInputDialog("保存分类预设", "输入预设名称", self.getSelectedPresetName() or "", self.window())
        if not dialog.exec():
            return

        preset_name = dialog.get_text()
        if not preset_name:
            InfoBar.warning("警告", "预设名称不能为空", duration=2000, parent=self)
            return

        if self.presetStore.has_preset(preset_name):
            overwrite = MessageBox("覆盖预设", f"预设 '{preset_name}' 已存在，是否覆盖？", self.window())
            if not overwrite.exec():
                return

        editable_categories = self.project.get_editable_categories()
        self.presetStore.upsert_preset(preset_name, editable_categories)
        self.refreshPresetList(selected_name=preset_name)
        InfoBar.success("成功", f"已保存预设: {preset_name}", duration=2000, parent=self)

    def applySelectedPreset(self):
        if not self.project:
            InfoBar.warning("警告", "请先选择图片文件夹", duration=2000, parent=self)
            return

        preset_name = self.getSelectedPresetName()
        if not preset_name:
            InfoBar.warning("警告", "请先选择预设", duration=2000, parent=self)
            return

        categories = self.presetStore.get_preset_categories(preset_name)
        confirm = MessageBox("应用预设", f"应用预设 '{preset_name}' 将替换当前分类组，是否继续？", self.window())
        if not confirm.exec():
            return

        self.project.replace_categories_from_preset(categories)
        self.updateCategoryList()
        self.loadCurrentImage()
        InfoBar.success("成功", f"已应用预设: {preset_name}", duration=2000, parent=self)

    def deleteSelectedPreset(self):
        preset_name = self.getSelectedPresetName()
        if not preset_name:
            InfoBar.warning("警告", "请先选择预设", duration=2000, parent=self)
            return

        confirm = MessageBox("删除预设", f"确定删除预设 '{preset_name}' 吗？", self.window())
        if not confirm.exec():
            return

        if self.presetStore.delete_preset(preset_name):
            self.refreshPresetList()
            InfoBar.success("成功", f"已删除预设: {preset_name}", duration=2000, parent=self)

    def loadCurrentImage(self):
        if self.current_index < 0 or self.current_index >= len(self.image_files):
            self.updateLabelingSummary()
            self.updateExportInfo()
            self.refreshFullscreenLabelWindow()
            return

        self.updateLabelingSummary()
        self.updateExportInfo()
        self.refreshFullscreenLabelWindow()

    def prevImage(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.loadCurrentImage()

    def nextImage(self):
        if self.current_index < len(self.image_files) - 1:
            self.current_index += 1
            self.loadCurrentImage()

    def labelCurrentImage(self, category_name):
        if not self.project or self.current_index < 0 or self.current_index >= len(self.image_files):
            return

        img_file = self.image_files[self.current_index]
        labeled_index = self.current_index
        if not self.project.label_image(img_file, category_name):
            return

        self.updateCategoryList()

        display_name = self.project.get_category_display_name(category_name)
        InfoBar.success("成功", f"已标记为: {display_name}", duration=1000, parent=self)

        if labeled_index < len(self.image_files) - 1:
            self.current_index = labeled_index + 1
        self.loadCurrentImage()

    def exportImages(self):
        if not self.project:
            InfoBar.warning("警告", "请先选择图片文件夹", duration=2000, parent=self)
            return

        if not self.project.labeled_images:
            InfoBar.warning("警告", "没有已标记的图片", duration=2000, parent=self)
            return

        try:
            output_dir = self.project.export_labeled_images()
            self.progressLabel.setText(f"导出目录:\n{output_dir}")
            InfoBar.success(
                "成功",
                f"已导出 {len(self.project.labeled_images)} 张图片到 {output_dir}",
                duration=3000,
                parent=self,
            )
        except Exception as e:
            InfoBar.error("错误", f"导出失败: {str(e)}", duration=3000, parent=self)

    def openOutputFolder(self):
        if not self.project:
            InfoBar.warning("警告", "请先选择图片文件夹", duration=2000, parent=self)
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
            InfoBar.error("错误", f"无法打开文件夹: {str(e)}", duration=3000, parent=self)
