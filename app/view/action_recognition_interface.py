# coding:utf-8
import json
import io
import os
import shutil
import subprocess
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QKeyEvent, QKeySequence, QPixmap
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QFrame,
    QShortcut,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon as FIF,
    FluentTitleBar,
    InfoBar,
    MessageBox,
    ProgressBar,
    PushButton,
    SpinBox,
    CardWidget,
    FlowLayout,
    ToolTipFilter,
)
from qframelesswindow import FramelessWindow

from .gallery_interface import GalleryInterface
from .image_label_interface import DEFAULT_CATEGORY_COLORS, INVALID_CATEGORY_NAME, INVALID_CATEGORY_DISPLAY_NAME, Category, LabelProject
from .video_frame_interface import VIDEO_EXTENSIONS
from ..common.config import cfg
from ..common.signal_bus import signalBus


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_STORE_DIR = PROJECT_ROOT / "model"
MODEL_REGISTRY_PATH = MODEL_STORE_DIR / "models_registry.json"
ACTION_CONFIG_NAME = "action_analysis_config.json"
CONFIDENCE_THRESHOLD = 0.5
DEFAULT_CLASS_NAMES = ["closeup_celebration", "dinking", "drive_smash", "idle_walking", "serve"]
DEFAULT_CLASS_NAMES_CN = {
    "closeup_celebration": "近景庆祝",
    "dinking": "轻吊",
    "drive_smash": "抽击/扣杀",
    "idle_walking": "空闲/走动",
    "serve": "发球",
}


def safe_json_load(path, default):
    try:
        if Path(path).exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def safe_json_save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sanitize_file_stem(text):
    stem = str(text or "").strip() or "model"
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
    stem = stem.translate(replace_map).rstrip(" .")
    return stem or "model"


def format_confidence(confidence):
    try:
        value = max(0.0, min(1.0, float(confidence or 0)))
    except Exception:
        value = 0.0
    return f"{value * 100:.1f}%"


def get_ffmpeg_path():
    try:
        return cfg.get(cfg.ffmpegPath) if hasattr(cfg, "ffmpegPath") else "ffmpeg"
    except Exception:
        return "ffmpeg"


def get_ffprobe_path():
    ffmpeg_path = Path(get_ffmpeg_path())
    suffix = ".exe" if sys.platform == "win32" else ""
    sibling = ffmpeg_path.with_name(f"ffprobe{suffix}")
    if ffmpeg_path.parent != Path(".") and sibling.exists():
        return str(sibling)
    return "ffprobe"


def parse_frame_rate(value):
    try:
        text = str(value or "0/1")
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator = float(denominator)
            return float(numerator) / denominator if denominator else 0.0
        return float(text)
    except Exception:
        return 0.0


def probe_video_info(video_path):
    cmd = [
        get_ffprobe_path(),
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "FFprobe 无法读取视频信息")

    data = json.loads(result.stdout or "{}")
    video_stream = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break
    if not video_stream:
        raise RuntimeError("未找到视频流")

    fps = parse_frame_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
    duration = float(data.get("format", {}).get("duration") or video_stream.get("duration") or 0)
    total_frames = int(float(video_stream.get("nb_frames") or 0))
    if total_frames <= 0 and duration > 0 and fps > 0:
        total_frames = int(round(duration * fps))

    return {
        "total_frames": total_frames,
        "fps": fps,
        "duration_ms": int(duration * 1000) if duration > 0 else 0,
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
    }


class ActionModelRegistry:
    def __init__(self):
        self.models = []
        self.load()

    def load(self):
        data = safe_json_load(MODEL_REGISTRY_PATH, {"version": 1, "models": []})
        models = data.get("models", []) if isinstance(data, dict) else []
        self.models = []
        for item in models:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id", "")).strip()
            name = str(item.get("name", "")).strip()
            path = str(item.get("path", "")).strip()
            if model_id and name and path and Path(path).exists():
                self.models.append({"id": model_id, "name": name, "path": path})

    def save(self):
        safe_json_save(MODEL_REGISTRY_PATH, {"version": 1, "models": self.models})

    def add_model(self, source_path, display_name=None):
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(str(source))

        model_id = uuid.uuid4().hex
        name = str(display_name or source.stem).strip() or source.stem
        target_name = f"{sanitize_file_stem(name)}_{model_id[:8]}{source.suffix}"
        MODEL_STORE_DIR.mkdir(parents=True, exist_ok=True)
        target_path = MODEL_STORE_DIR / target_name
        shutil.copy2(source, target_path)

        item = {"id": model_id, "name": name, "path": str(target_path)}
        self.models.append(item)
        self.models.sort(key=lambda x: x["name"].lower())
        self.save()
        return item

    def rename_model(self, model_id, new_name):
        new_name = str(new_name or "").strip()
        if not new_name:
            return False
        for item in self.models:
            if item["id"] == model_id:
                item["name"] = new_name
                self.models.sort(key=lambda x: x["name"].lower())
                self.save()
                return True
        return False

    def delete_model(self, model_id):
        target = None
        remaining = []
        for item in self.models:
            if item["id"] == model_id:
                target = item
            else:
                remaining.append(item)
        if not target:
            return False

        self.models = remaining
        self.save()
        try:
            Path(target["path"]).unlink(missing_ok=True)
        except Exception:
            pass
        return True

    def get_model(self, model_id):
        for item in self.models:
            if item["id"] == model_id:
                return item
        return None


class ActionVideoProject:
    """Persistent storage for direct, frame-by-frame video analysis."""

    def __init__(self, video_path):
        self.video_path = Path(video_path)
        self.project_dir = self.resolve_project_dir(self.video_path)
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.label_project = LabelProject(self.project_dir)
        self.config_path = self.label_project.get_output_base_dir() / ACTION_CONFIG_NAME
        self.selected_model_id = ""
        self.window_size = 5
        self.sample_rate = 1
        self.total_frames = 0
        self.fps = 0.0
        self.duration_ms = 0
        self.model_predictions = {}
        self.labeled_frames = {}
        self.load()

    @staticmethod
    def resolve_project_dir(video_path):
        video_path = Path(video_path)
        if video_path.parent.name.lower() == video_path.stem.lower():
            return video_path.parent
        return video_path.parent / video_path.stem

    def load(self):
        data = safe_json_load(self.config_path, {})
        self.selected_model_id = str(data.get("model", data.get("selected_model_id", ""))).strip()
        self.window_size = int(data.get("window", data.get("window_size", 5)) or 5)
        self.sample_rate = int(data.get("sample_rate", data.get("sampleRate", 1)) or 1)
        self.total_frames = int(data.get("frames", data.get("total_frames", 0)) or 0)
        self.fps = float(data.get("fps", 0.0) or 0.0)
        self.duration_ms = int(data.get("duration", data.get("duration_ms", 0)) or 0)
        raw_predictions = data.get("p", data.get("model_predictions", {})) if isinstance(data, dict) else {}
        raw_labels = data.get("l", data.get("labeled_frames", {})) if isinstance(data, dict) else {}
        self.model_predictions = self.expand_predictions(raw_predictions)
        self.labeled_frames = self.expand_labels(raw_labels)
        if not isinstance(self.model_predictions, dict):
            self.model_predictions = {}
        if not isinstance(self.labeled_frames, dict):
            self.labeled_frames = {}

    def save(self):
        safe_json_save(
            self.config_path,
            {
                "version": 3,
                "video_path": str(self.video_path),
                "selected_model_id": self.selected_model_id,
                "window_size": self.window_size,
                "sample_rate": self.sample_rate,
                "total_frames": self.total_frames,
                "fps": self.fps,
                "duration_ms": self.duration_ms,
                "model_predictions": self.compact_predictions(),
                "labeled_frames": self.compact_labels(),
            },
        )

    def expand_predictions(self, raw_predictions):
        result = {}
        if not isinstance(raw_predictions, dict):
            return result

        for model_id, frames in raw_predictions.items():
            if not isinstance(frames, dict):
                continue
            model_bucket = {}
            for frame_index, item in frames.items():
                if isinstance(item, dict):
                    class_name = item.get("class_name") or item.get("c") or ""
                    confidence = item.get("confidence", item.get("s", 0))
                    top_class_name = item.get("top_class_name") or item.get("t") or ""
                    probabilities = item.get("probabilities") or item.get("p") or {}
                    window_size = item.get("window_size", item.get("w", 0))
                elif isinstance(item, list) and item:
                    class_name = item[0]
                    confidence = item[1] if len(item) > 1 else 0
                    top_class_name = item[2] if len(item) > 2 else ""
                    probabilities = {}
                    window_size = 0
                else:
                    class_name = item
                    confidence = 0
                    top_class_name = ""
                    probabilities = {}
                    window_size = 0
                class_name = str(class_name or "").strip()
                if not class_name:
                    continue
                prediction = {
                    "class_name": class_name,
                    "confidence": float(confidence or 0),
                }
                top_class_name = str(top_class_name or "").strip()
                if top_class_name:
                    prediction["top_class_name"] = top_class_name
                if isinstance(probabilities, dict):
                    clean_probabilities = {}
                    for name, score in probabilities.items():
                        name = str(name or "").strip()
                        if name:
                            clean_probabilities[name] = float(score or 0)
                    if clean_probabilities:
                        prediction["probabilities"] = clean_probabilities
                try:
                    window_size = int(window_size or 0)
                except Exception:
                    window_size = 0
                if window_size > 0:
                    prediction["window_size"] = window_size
                model_bucket[str(frame_index)] = prediction
            result[str(model_id)] = model_bucket
        return result

    def expand_labels(self, raw_labels):
        result = {}
        if isinstance(raw_labels, dict):
            for frame_index, class_name in raw_labels.items():
                class_name = str(class_name or "").strip()
                if class_name:
                    result[str(frame_index)] = class_name
        return result

    def compact_predictions(self):
        predictions = {}
        for model_id, frames in self.model_predictions.items():
            if not isinstance(frames, dict):
                continue
            readable_frames = {}
            for frame_index, item in frames.items():
                if not isinstance(item, dict):
                    continue
                class_name = str(item.get("class_name", "")).strip()
                if not class_name:
                    continue
                confidence = round(float(item.get("confidence", 0) or 0), 6)
                probabilities = item.get("probabilities", {})
                top_class_name = str(item.get("top_class_name", "") or "").strip()
                window_size = int(item.get("window_size", 0) or 0)
                prediction_item = {
                    "class_name": class_name,
                    "confidence": confidence,
                }
                if probabilities:
                    prediction_item["probabilities"] = {
                        str(name): round(float(score or 0), 6)
                        for name, score in probabilities.items()
                        if str(name or "").strip()
                    }
                if top_class_name:
                    prediction_item["top_class_name"] = top_class_name
                if window_size > 0:
                    prediction_item["window_size"] = window_size
                readable_frames[str(frame_index)] = prediction_item
            predictions[str(model_id)] = readable_frames
        return predictions

    def compact_labels(self):
        return {
            str(frame_index): class_name
            for frame_index, class_name in self.labeled_frames.items()
            if class_name
        }

    def update_video_info(self, total_frames, fps):
        self.total_frames = int(max(0, total_frames))
        self.fps = float(fps or 0.0)
        self.duration_ms = int((self.total_frames / self.fps) * 1000) if self.fps > 0 else 0
        self.save()

    def get_prediction_bucket(self, model_id):
        bucket = self.model_predictions.setdefault(model_id, {})
        if not isinstance(bucket, dict):
            bucket = {}
            self.model_predictions[model_id] = bucket
        return bucket

    def build_sample_indices(self, sample_rate):
        sample_rate = max(1, int(sample_rate or 1))
        if self.total_frames <= 0:
            return []
        if self.fps <= 0:
            return [min(self.total_frames - 1, sample_rate - 1)]

        step = max(1, int(round(self.fps / sample_rate)))
        start = min(self.total_frames - 1, max(0, sample_rate - 1))
        return list(range(start, self.total_frames, step))

    def build_window_indices(self, center_index, window_size):
        window_size = max(1, int(window_size or 1))
        if window_size % 2 == 0:
            window_size += 1
        half = window_size // 2
        start = max(0, int(center_index) - half)
        end = min(self.total_frames, int(center_index) + half + 1)
        return list(range(start, end))

    def prune_predictions_to_samples(self, model_id, sample_indices):
        bucket = self.get_prediction_bucket(model_id)
        allowed = {str(index) for index in sample_indices}
        self.model_predictions[model_id] = {
            frame_index: prediction
            for frame_index, prediction in bucket.items()
            if frame_index in allowed
        }

    def pending_frame_indices(self, model_id, sample_indices=None, window_size=None):
        bucket = self.get_prediction_bucket(model_id)
        indices = sample_indices if sample_indices is not None else range(self.total_frames)
        expected_window = None
        if window_size is not None:
            expected_window = max(1, int(window_size or 1))
            if expected_window % 2 == 0:
                expected_window += 1

        pending = []
        for index in indices:
            prediction = bucket.get(str(index))
            if not isinstance(prediction, dict):
                pending.append(index)
                continue
            if expected_window is not None and int(prediction.get("window_size", 0) or 0) != expected_window:
                pending.append(index)
        return pending

    def set_raw_prediction(self, model_id, frame_index, prediction):
        self.get_prediction_bucket(model_id)[str(frame_index)] = self.normalize_prediction(prediction)

    def get_frame_label(self, frame_index):
        return self.labeled_frames.get(str(frame_index), "")

    def ensure_category(self, class_name, display_name):
        class_name = str(class_name or "").strip()
        if not class_name or class_name == INVALID_CATEGORY_NAME or class_name == INVALID_CATEGORY_DISPLAY_NAME:
            return False
        if self.label_project.get_category(class_name):
            return True

        editable_count = len(self.label_project.get_editable_categories())
        color = DEFAULT_CATEGORY_COLORS[editable_count % len(DEFAULT_CATEGORY_COLORS)][1]
        self.label_project.categories.append(
            Category(
                name=class_name,
                display_name=str(display_name or class_name).strip() or class_name,
                color=color,
                shortcut_key="",
            )
        )
        self.label_project.ensure_invalid_category()
        self.label_project.save_config()
        return True

    def get_nearest_frame_label(self, frame_index):
        prediction = self.get_nearest_frame_prediction(frame_index)
        return prediction.get("class_name", "") if prediction else ""

    def normalize_prediction(self, prediction):
        if not isinstance(prediction, dict):
            return {"class_name": "", "confidence": 0.0}

        probabilities = prediction.get("probabilities", {})
        if isinstance(probabilities, dict) and probabilities:
            return self.vote_probability_maps(
                [probabilities],
                window_size=prediction.get("window_size", 0),
            )

        class_name = str(prediction.get("class_name", "") or "").strip()
        confidence = float(prediction.get("confidence", 0) or 0)
        top_class_name = str(prediction.get("top_class_name", "") or "").strip()
        if confidence < CONFIDENCE_THRESHOLD and class_name != INVALID_CATEGORY_NAME:
            top_class_name = top_class_name or class_name
            class_name = INVALID_CATEGORY_NAME

        result = {
            "class_name": class_name,
            "confidence": confidence,
        }
        if top_class_name:
            result["top_class_name"] = top_class_name
        try:
            window_size = int(prediction.get("window_size", 0) or 0)
        except Exception:
            window_size = 0
        if window_size > 0:
            result["window_size"] = window_size
        return result

    def vote_probability_maps(self, probability_maps, window_size=0):
        totals = defaultdict(float)
        valid_count = 0
        for probability_map in probability_maps:
            if not isinstance(probability_map, dict):
                continue
            valid_count += 1
            for class_name, score in probability_map.items():
                class_name = str(class_name or "").strip()
                if class_name:
                    totals[class_name] += float(score or 0)

        if not totals or valid_count <= 0:
            return {"class_name": INVALID_CATEGORY_NAME, "confidence": 0.0}

        averaged = {
            class_name: totals[class_name] / valid_count
            for class_name in totals
        }
        top_class_name = max(averaged, key=averaged.get)
        confidence = float(averaged[top_class_name])
        class_name = top_class_name if confidence >= CONFIDENCE_THRESHOLD else INVALID_CATEGORY_NAME
        result = {
            "class_name": class_name,
            "top_class_name": top_class_name,
            "confidence": confidence,
            "probabilities": averaged,
        }
        try:
            window_size = int(window_size or 0)
        except Exception:
            window_size = 0
        if window_size > 0:
            result["window_size"] = window_size
        return result

    def get_nearest_frame_prediction(self, frame_index):
        if not self.labeled_frames:
            return {}
        try:
            target = int(frame_index)
            nearest_key = min(self.labeled_frames.keys(), key=lambda key: abs(int(key) - target))
            class_name = self.labeled_frames.get(nearest_key, "")
            prediction = {}
            if self.selected_model_id:
                prediction = self.get_prediction_bucket(self.selected_model_id).get(nearest_key, {})
            normalized = self.normalize_prediction(prediction) if prediction else {}
            if not normalized:
                normalized = {"class_name": class_name, "confidence": 0.0}
            normalized["class_name"] = class_name or normalized.get("class_name", "")
            normalized["frame_index"] = int(nearest_key)
            return normalized
        except Exception:
            return {}

    def apply_sliding_window_vote(self, model_id, window_size, sample_indices=None):
        window_size = max(1, int(window_size))
        if window_size % 2 == 0:
            window_size += 1

        bucket = self.get_prediction_bucket(model_id)
        indices = list(sample_indices if sample_indices is not None else range(self.total_frames))
        missing = [index for index in indices if str(index) not in bucket]
        if missing:
            return 0, missing

        changed = 0
        new_labeled_frames = {}

        for frame_index in indices:
            selected = self.normalize_prediction(bucket[str(frame_index)])
            selected_name = selected.get("class_name", "") or INVALID_CATEGORY_NAME
            bucket[str(frame_index)] = selected
            self.ensure_category(selected_name, selected_name)

            key = str(frame_index)
            if self.labeled_frames.get(key) != selected_name:
                changed += 1
            new_labeled_frames[key] = selected_name

        self.labeled_frames = new_labeled_frames
        self.window_size = window_size
        self.save()
        self.label_project.save_config()
        return changed, []

    def get_label_counts(self):
        counts = {}
        for category_name in self.labeled_frames.values():
            counts[category_name] = counts.get(category_name, 0) + 1
        return counts

    def get_label_stats(self):
        stats = defaultdict(lambda: {"count": 0, "confidence_total": 0.0, "confidence_count": 0})
        bucket = self.get_prediction_bucket(self.selected_model_id) if self.selected_model_id else {}
        for frame_index, category_name in self.labeled_frames.items():
            item = stats[category_name]
            item["count"] += 1
            prediction = bucket.get(str(frame_index), {})
            if isinstance(prediction, dict):
                try:
                    item["confidence_total"] += float(prediction.get("confidence", 0) or 0)
                    item["confidence_count"] += 1
                except Exception:
                    pass
        return stats


class TorchActionClassifier:
    def __init__(self, checkpoint_path):
        import torch
        import torch.nn as nn
        from torchvision import models, transforms

        self.torch = torch
        self.transforms = transforms
        self.nn = nn
        self.models = models
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.checkpoint_path = Path(checkpoint_path)

        try:
            checkpoint = torch.load(self.checkpoint_path, map_location=self.device, weights_only=False)
        except TypeError:
            checkpoint = torch.load(self.checkpoint_path, map_location=self.device)

        if isinstance(checkpoint, dict):
            self.state_dict = checkpoint.get("model_state_dict", checkpoint)
            self.model_name = checkpoint.get("model_name") or self.infer_model_name()
            self.class_names = checkpoint.get("class_names") or DEFAULT_CLASS_NAMES
            self.class_display_names = checkpoint.get("class_names_cn") or [
                DEFAULT_CLASS_NAMES_CN.get(name, name) for name in self.class_names
            ]
        else:
            self.state_dict = checkpoint
            self.model_name = self.infer_model_name()
            self.class_names = DEFAULT_CLASS_NAMES
            self.class_display_names = [DEFAULT_CLASS_NAMES_CN.get(name, name) for name in self.class_names]

        self.class_names = [str(name) for name in self.class_names]
        self.class_display_names = [str(name) for name in self.class_display_names]
        self.model = self.build_model(self.model_name, len(self.class_names)).to(self.device)
        self.model.load_state_dict(self.state_dict, strict=False)
        self.model.eval()
        self.transform = self.transforms.Compose([
            self.transforms.Resize((224, 224)),
            self.transforms.ToTensor(),
            self.transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

    def infer_model_name(self):
        name = self.checkpoint_path.name.lower()
        for candidate in ("resnet18", "resnet34", "resnet50", "mobilenet_v2", "efficientnet_b0"):
            if candidate in name:
                return candidate
        return "resnet50"

    def build_model(self, model_name, num_classes):
        if model_name == "resnet18":
            model = self.models.resnet18(weights=None)
        elif model_name == "resnet34":
            model = self.models.resnet34(weights=None)
        elif model_name == "resnet50":
            model = self.models.resnet50(weights=None)
        elif model_name == "mobilenet_v2":
            model = self.models.mobilenet_v2(weights=None)
        elif model_name == "efficientnet_b0":
            model = self.models.efficientnet_b0(weights=None)
        else:
            raise ValueError(f"不支持的模型名称: {model_name}")

        if model_name.startswith("resnet"):
            model.fc = self.nn.Sequential(self.nn.Dropout(0.5), self.nn.Linear(model.fc.in_features, num_classes))
        elif model_name == "mobilenet_v2":
            model.classifier[-1] = self.nn.Linear(model.classifier[-1].in_features, num_classes)
            if len(model.classifier) > 1:
                model.classifier[0] = self.nn.Dropout(0.5)
        elif model_name == "efficientnet_b0":
            model.classifier = self.nn.Sequential(
                self.nn.Dropout(0.5),
                self.nn.Linear(model.classifier[-1].in_features, num_classes),
            )
        return model

    def predict_pil(self, image):
        return self.vote_probabilities(self.predict_probability_batch([image]))

    def predict_probability_batch(self, images):
        if not images:
            return []

        batch = self.torch.stack([
            self.transform(image.convert("RGB")) for image in images
        ]).to(self.device, non_blocking=True)

        with self.torch.inference_mode():
            output = self.model(batch)
            probs = self.torch.softmax(output, dim=1).detach().cpu().tolist()

        probability_maps = []
        for row in probs:
            probability_maps.append({
                self.class_names[index] if index < len(self.class_names) else str(index): float(score)
                for index, score in enumerate(row)
            })
        return probability_maps

    def vote_probabilities(self, probability_maps):
        totals = {}
        for probability_map in probability_maps:
            for class_name, score in probability_map.items():
                totals[class_name] = totals.get(class_name, 0.0) + float(score)

        if not totals:
            return {"class_name": INVALID_CATEGORY_NAME, "confidence": 0.0}

        averaged = {
            class_name: totals[class_name] / max(1, len(probability_maps))
            for class_name in totals
        }
        top_class_name = max(averaged, key=averaged.get)
        confidence = float(averaged[top_class_name])
        class_name = top_class_name if confidence >= CONFIDENCE_THRESHOLD else INVALID_CATEGORY_NAME
        return {
            "class_name": class_name,
            "top_class_name": top_class_name,
            "confidence": confidence,
            "probabilities": averaged,
        }

    def predict_window(self, images):
        return self.vote_probabilities(self.predict_probability_batch(images))


class VideoAnalysisWorker(QThread):
    progress = pyqtSignal(int, int)
    frameDone = pyqtSignal(int, dict)
    videoInfo = pyqtSignal(int, float)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, video_path, model_info, pending_indices, window_size):
        super().__init__()
        self.video_path = str(video_path)
        self.model_info = model_info
        self.pending_indices = set(pending_indices)
        self.window_size = max(1, int(window_size or 1))
        if self.window_size % 2 == 0:
            self.window_size += 1
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        try:
            classifier = TorchActionClassifier(self.model_info["path"])
            try:
                self.run_with_cv2(classifier)
            except Exception:
                self.run_with_ffmpeg(classifier)
        except Exception as e:
            self.failed.emit(str(e))

    def build_window_indices(self, center_index, total_frames):
        half = self.window_size // 2
        start = max(0, int(center_index) - half)
        end = min(int(total_frames), int(center_index) + half + 1)
        return list(range(start, end))

    def run_with_cv2(self, classifier):
        import cv2
        from PIL import Image

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise RuntimeError("OpenCV 无法打开视频文件")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if total_frames <= 0:
            cap.release()
            raise RuntimeError("OpenCV 无法读取视频帧数")
        self.videoInfo.emit(total_frames, fps)

        pending = sorted(self.pending_indices)
        pending_total = len(pending)
        probability_cache = {}

        for done, frame_index in enumerate(pending, 1):
            if not self._running:
                break

            window_indices = self.build_window_indices(frame_index, total_frames)
            missing_indices = [index for index in window_indices if index not in probability_cache]
            images = []
            image_indices = []
            for window_index in missing_indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, window_index)
                ok, frame = cap.read()
                if not ok:
                    continue

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                images.append(Image.fromarray(rgb_frame))
                image_indices.append(window_index)

            if images:
                probabilities = classifier.predict_probability_batch(images)
                for window_index, probability_map in zip(image_indices, probabilities):
                    probability_cache[window_index] = probability_map

            window_probabilities = [
                probability_cache[index] for index in window_indices
                if index in probability_cache
            ]
            if not window_probabilities:
                self.progress.emit(done, pending_total)
                continue

            prediction = classifier.vote_probabilities(window_probabilities)
            prediction["window_size"] = self.window_size
            self.frameDone.emit(frame_index, prediction)
            self.progress.emit(done, pending_total)

        cap.release()
        if self._running:
            self.finished.emit()

    def run_with_ffmpeg(self, classifier):
        from PIL import Image

        info = probe_video_info(self.video_path)
        width = info["width"]
        height = info["height"]
        total_frames = info["total_frames"]
        fps = info["fps"]
        if total_frames <= 0:
            raise RuntimeError("FFprobe 无法读取视频帧数")
        self.videoInfo.emit(total_frames, fps)

        pending = sorted(self.pending_indices)
        pending_total = len(pending)
        probability_cache = {}

        for done, frame_index in enumerate(pending, 1):
            if not self._running:
                break

            window_indices = self.build_window_indices(frame_index, total_frames)
            missing_indices = [index for index in window_indices if index not in probability_cache]
            images = []
            image_indices = []
            for window_index in missing_indices:
                timestamp = (window_index / fps) if fps > 0 else 0
                cmd = [
                    get_ffmpeg_path(),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{timestamp:.6f}",
                    "-i",
                    self.video_path,
                    "-frames:v",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "png",
                    "pipe:1",
                ]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                if result.returncode != 0 or not result.stdout:
                    continue
                images.append(Image.open(io.BytesIO(result.stdout)).convert("RGB"))
                image_indices.append(window_index)

            if images:
                probabilities = classifier.predict_probability_batch(images)
                for window_index, probability_map in zip(image_indices, probabilities):
                    probability_cache[window_index] = probability_map

            window_probabilities = [
                probability_cache[index] for index in window_indices
                if index in probability_cache
            ]
            if not window_probabilities:
                self.progress.emit(done, pending_total)
                continue

            prediction = classifier.vote_probabilities(window_probabilities)
            prediction["window_size"] = self.window_size
            self.frameDone.emit(frame_index, prediction)
            self.progress.emit(done, pending_total)

        if self._running:
            self.finished.emit()


class FullscreenVideoWindow(FramelessWindow):
    closed = pyqtSignal()
    playPauseRequested = pyqtSignal()
    seekRequested = pyqtSignal(int)
    seekToRequested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(None)
        self.setWindowTitle("大窗播放")
        self.setObjectName("fullscreenVideoWindow")
        self.setWindowIcon(QIcon(":/gallery/images/logo.png"))
        self.setTitleBar(FluentTitleBar(self))
        self.titleBar.setTitle("大窗播放")
        self.setMinimumSize(960, 640)
        self.setFocusPolicy(Qt.StrongFocus)
        self._fallbackPixmap = None
        self._borderColor = "#3a3a3a"
        self.videoWidget = QVideoWidget(self)
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.videoWidget)
        self.imageLabel = QLabel(self)
        self.imageLabel.setAlignment(Qt.AlignCenter)
        self.imageLabel.setStyleSheet("QLabel { background: #111111; color: #dddddd; border: none; }")
        self.imageLabel.setVisible(False)
        self.videoFrame = QFrame(self)
        self.videoFrame.setObjectName("fullscreenVideoFrame")
        self.videoFrameLayout = QVBoxLayout(self.videoFrame)
        self.videoFrameLayout.setContentsMargins(6, 6, 6, 6)
        self.videoFrameLayout.setSpacing(0)
        self.videoFrameLayout.addWidget(self.videoWidget, 1)
        self.videoFrameLayout.addWidget(self.imageLabel, 1)

        self.infoLabel = QLabel("未加载视频", self)
        self.infoLabel.setAlignment(Qt.AlignCenter)
        self.infoLabel.setWordWrap(True)
        self.infoLabel.setStyleSheet(
            "QLabel { padding: 10px 12px; background: #202020; color: white; font-size: 16px; border-radius: 4px; }"
        )

        sliderLayout = QHBoxLayout()
        sliderLayout.setSpacing(10)
        self.positionLabel = BodyLabel("00:00", self)
        sliderLayout.addWidget(self.positionLabel)
        self.positionSlider = QSlider(Qt.Horizontal, self)
        self.positionSlider.setMinimumWidth(360)
        self.positionSlider.sliderReleased.connect(self._onSliderReleased)
        sliderLayout.addWidget(self.positionSlider, 1)
        self.durationLabel = BodyLabel("00:00", self)
        sliderLayout.addWidget(self.durationLabel)

        buttonLayout = QHBoxLayout()
        buttonLayout.setSpacing(8)
        self.backwardButton = PushButton("后退 3 秒", self, FIF.LEFT_ARROW)
        self.backwardButton.clicked.connect(lambda: self.seekRequested.emit(-3))
        buttonLayout.addWidget(self.backwardButton)

        self.playPauseButton = PushButton("播放", self, FIF.PLAY)
        self.playPauseButton.clicked.connect(self.playPauseRequested.emit)
        buttonLayout.addWidget(self.playPauseButton)

        self.forwardButton = PushButton("前进 3 秒", self, FIF.RIGHT_ARROW)
        self.forwardButton.clicked.connect(lambda: self.seekRequested.emit(3))
        buttonLayout.addWidget(self.forwardButton)

        self.closeButton = PushButton("关闭", self, FIF.CLOSE)
        self.closeButton.clicked.connect(self.close)
        buttonLayout.addWidget(self.closeButton)
        buttonLayout.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 56, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self.videoFrame, 1)
        layout.addWidget(self.infoLabel)
        layout.addLayout(sliderLayout)
        layout.addLayout(buttonLayout)
        self.installPlaybackShortcuts()
        self.setBorderColor(None)
        self.setTimelineState(0, 0)
        self.setPlaybackState(False)

    def setLabelText(self, text):
        self.infoLabel.setText(text)

    def setBorderColor(self, color):
        self._borderColor = color or "#3a3a3a"
        self.setStyleSheet(
            f"""
            QWidget#fullscreenVideoWindow {{
                background-color: #111111;
            }}
            QFrame#fullscreenVideoFrame {{
                background-color: #151515;
                border: 4px solid {self._borderColor};
                border-radius: 6px;
            }}
            """
        )

    def setTimelineState(self, position, duration):
        duration = max(0, int(duration or 0))
        position = max(0, int(position or 0))
        self.positionSlider.blockSignals(True)
        self.positionSlider.setRange(0, duration)
        self.positionSlider.setValue(min(position, duration))
        self.positionSlider.blockSignals(False)
        self.positionLabel.setText(self.formatMs(position))
        self.durationLabel.setText(self.formatMs(duration))

    def setPlaybackState(self, playing):
        self.playPauseButton.setText("暂停" if playing else "播放")

    def installPlaybackShortcuts(self):
        self.playbackShortcuts = []
        shortcuts = [
            (Qt.Key_Space, self.playPauseRequested.emit),
            (Qt.Key_Left, lambda: self.seekRequested.emit(-3)),
            (Qt.Key_Right, lambda: self.seekRequested.emit(3)),
            (Qt.Key_Escape, self.close),
        ]
        for key, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self.playbackShortcuts.append(shortcut)

    def setFallbackPixmap(self, pixmap):
        if pixmap and not pixmap.isNull():
            self._fallbackPixmap = pixmap
            self.videoWidget.setVisible(False)
            self.imageLabel.setVisible(True)
            self.imageLabel.setPixmap(
                pixmap.scaled(self.imageLabel.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def setFallbackText(self, text):
        self._fallbackPixmap = None
        self.videoWidget.setVisible(False)
        self.imageLabel.setVisible(True)
        self.imageLabel.setPixmap(QPixmap())
        self.imageLabel.setText(text)

    def loadMedia(self, video_path, position, playing):
        self._fallbackPixmap = None
        self.imageLabel.setText("")
        self.imageLabel.setVisible(False)
        self.videoWidget.setVisible(True)
        self.player.setMedia(QMediaContent(QUrl.fromLocalFile(str(video_path))))
        self.player.setPosition(max(0, int(position)))
        self.setPlaybackState(bool(playing))
        if playing:
            self.player.play()
        else:
            self.player.pause()

    def setPlaybackPosition(self, position):
        self.positionSlider.blockSignals(True)
        self.positionSlider.setValue(max(0, int(position or 0)))
        self.positionSlider.blockSignals(False)
        self.positionLabel.setText(self.formatMs(position))

    def _onSliderReleased(self):
        self.seekToRequested.emit(self.positionSlider.value())

    def formatMs(self, ms):
        seconds = max(0, int(ms / 1000))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        if event.key() == Qt.Key_Space:
            self.playPauseRequested.emit()
            return
        if event.key() == Qt.Key_Left:
            self.seekRequested.emit(-3)
            return
        if event.key() == Qt.Key_Right:
            self.seekRequested.emit(3)
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fallbackPixmap and not self._fallbackPixmap.isNull() and self.imageLabel.isVisible():
            self.imageLabel.setPixmap(
                self._fallbackPixmap.scaled(self.imageLabel.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def closeEvent(self, event):
        self.player.stop()
        self.closed.emit()
        super().closeEvent(event)


class ActionCategoryCard(CardWidget):
    def __init__(self, category, count=0, avg_confidence=None, parent=None):
        super().__init__(parent)
        self.category = category
        self.setMinimumHeight(64)
        self.setFixedWidth(220)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.initUI(count, avg_confidence)

    def initUI(self, count, avg_confidence):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        headerLayout = QHBoxLayout()
        headerLayout.setSpacing(8)

        self.colorIndicator = QFrame(self)
        self.colorIndicator.setFixedSize(18, 18)
        self.updateColorIndicator()
        headerLayout.addWidget(self.colorIndicator)

        self.nameLabel = BodyLabel(self.category.display_name, self)
        self.nameLabel.setStyleSheet("QLabel { font-weight: bold; font-size: 14px; color: #ffffff; }")
        headerLayout.addWidget(self.nameLabel, 1)
        layout.addLayout(headerLayout)

        confidence_text = format_confidence(avg_confidence) if avg_confidence is not None else "-"
        self.countLabel = BodyLabel(f"已标注 {count} 帧 | 平均置信度 {confidence_text}", self)
        self.countLabel.setStyleSheet("QLabel { color: #aaaaaa; font-size: 12px; }")
        layout.addWidget(self.countLabel)

        self.nameInfoLabel = BodyLabel(f"name: {self.category.name}", self)
        self.nameInfoLabel.setStyleSheet("QLabel { color: #8a8a8a; font-size: 11px; }")
        layout.addWidget(self.nameInfoLabel)

        self.installEventFilter(ToolTipFilter(self))
        self.setToolTip(f"{self.category.display_name}: {count} 帧，平均置信度 {confidence_text}")

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


class ActionRecognitionInterface(GalleryInterface):
    def __init__(self, parent=None):
        super().__init__(
            title="动作识别",
            subtitle="按滑动窗口分析视频帧并生成动作标签",
            parent=parent,
        )
        self.setObjectName("actionRecognitionInterface")

        self.registry = ActionModelRegistry()
        self.project = None
        self.video_paths = {}
        self.worker = None
        self.is_slider_pressed = False
        self.fullscreenWindow = None
        self.previewAvailable = False
        self.useFallbackPreview = False
        self.fallbackPositionMs = 0
        self.fallbackCapture = None
        self.fallbackCapturePath = None
        self.fallbackPixmap = None
        self.requestedPlayback = False

        self.player = QMediaPlayer(self)
        self.videoWidget = QVideoWidget(self)
        self.player.setVideoOutput(self.videoWidget)
        self.fallbackTimer = QTimer(self)
        self.fallbackTimer.setInterval(250)
        self.fallbackTimer.timeout.connect(self.onFallbackPreviewTick)

        self.initUI()
        self.refreshModelList()
        self.refreshVideoList()
        self.updateContentVisibility()
        self.connectPlayerSignals()
        self.installPlaybackShortcuts()
        signalBus.workDirectoryChanged.connect(lambda _: self.refreshVideoList())

    def initUI(self):
        self.videoDirCard = self.addExampleCard("工作视频", self.createVideoDirectoryWidget(), "", stretch=1)
        self.modelCard = self.addExampleCard("模型管理", self.createModelWidget(), "", stretch=1)
        self.analysisCard = self.addExampleCard("动作分析", self.createAnalysisWidget(), "", stretch=1)
        self.playerCard = self.addExampleCard("视频播放器", self.createPlayerWidget(), "", stretch=1)

    def updateContentVisibility(self):
        has_video = self.project is not None
        for card in (self.analysisCard, self.playerCard):
            card.setVisible(has_video)

    def createVideoDirectoryWidget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        buttonLayout = QHBoxLayout()
        self.openVideoDirButton = PushButton("打开工作目录", self, FIF.FOLDER)
        self.openVideoDirButton.clicked.connect(self.openVideoDirectory)
        buttonLayout.addWidget(self.openVideoDirButton)

        self.refreshVideoButton = PushButton("刷新", self, FIF.ROTATE)
        self.refreshVideoButton.clicked.connect(self.refreshVideoList)
        buttonLayout.addWidget(self.refreshVideoButton)
        buttonLayout.addStretch()
        layout.addLayout(buttonLayout)

        selectLayout = QHBoxLayout()
        selectLayout.addWidget(BodyLabel("工作目录视频:", self))
        self.videoComboBox = ComboBox(self)
        self.videoComboBox.setMinimumWidth(360)
        selectLayout.addWidget(self.videoComboBox, 1)

        self.loadVideoButton = PushButton("加载视频", self, FIF.PLAY)
        self.loadVideoButton.clicked.connect(self.loadSelectedVideo)
        selectLayout.addWidget(self.loadVideoButton)
        layout.addLayout(selectLayout)

        self.videoDirLabel = BodyLabel("未设置工作目录", self)
        self.videoDirLabel.setWordWrap(True)
        self.videoDirLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.videoDirLabel.setStyleSheet("QLabel { padding: 6px 8px; }")
        layout.addWidget(self.videoDirLabel)
        return widget

    def createModelWidget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        topLayout = QHBoxLayout()
        topLayout.addWidget(BodyLabel("当前模型:", self))
        self.modelComboBox = ComboBox(self)
        self.modelComboBox.setMinimumWidth(280)
        self.modelComboBox.currentTextChanged.connect(self.onSelectedModelChanged)
        topLayout.addWidget(self.modelComboBox, 1)

        self.importModelButton = PushButton("导入模型", self, FIF.ADD)
        self.importModelButton.clicked.connect(self.importModel)
        topLayout.addWidget(self.importModelButton)

        self.renameModelButton = PushButton("重命名", self)
        self.renameModelButton.clicked.connect(self.renameSelectedModel)
        topLayout.addWidget(self.renameModelButton)

        self.deleteModelButton = PushButton("删除", self, FIF.DELETE)
        self.deleteModelButton.clicked.connect(self.deleteSelectedModel)
        topLayout.addWidget(self.deleteModelButton)
        layout.addLayout(topLayout)

        self.modelPathLabel = BodyLabel("未导入模型", self)
        self.modelPathLabel.setWordWrap(True)
        self.modelPathLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.modelPathLabel.setStyleSheet("QLabel { padding: 6px 8px; }")
        layout.addWidget(self.modelPathLabel)
        return widget

    def createAnalysisWidget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        controlLayout = QHBoxLayout()
        controlLayout.addWidget(BodyLabel("窗口:", self))
        self.windowSizeSpinBox = SpinBox(self)
        self.windowSizeSpinBox.setRange(1, 31)
        self.windowSizeSpinBox.setValue(5)
        controlLayout.addWidget(self.windowSizeSpinBox)

        controlLayout.addWidget(BodyLabel("每秒分析:", self))
        self.sampleRateSpinBox = SpinBox(self)
        self.sampleRateSpinBox.setRange(1, 60)
        self.sampleRateSpinBox.setValue(1)
        controlLayout.addWidget(self.sampleRateSpinBox)
        controlLayout.addWidget(BodyLabel("次", self))

        self.startAnalysisButton = PushButton("开始分析", self, FIF.PLAY)
        self.startAnalysisButton.clicked.connect(self.startAnalysis)
        controlLayout.addWidget(self.startAnalysisButton)

        self.stopAnalysisButton = PushButton("停止", self)
        self.stopAnalysisButton.setEnabled(False)
        self.stopAnalysisButton.clicked.connect(self.stopAnalysis)
        controlLayout.addWidget(self.stopAnalysisButton)

        self.openActionJsonButton = PushButton("打开动作 JSON", self, FIF.DOCUMENT)
        self.openActionJsonButton.clicked.connect(self.openActionJson)
        controlLayout.addWidget(self.openActionJsonButton)
        controlLayout.addStretch()
        layout.addLayout(controlLayout)

        self.analysisProgressBar = ProgressBar(self)
        self.analysisProgressBar.setValue(0)
        layout.addWidget(self.analysisProgressBar)

        self.analysisStatusLabel = BodyLabel("请选择视频和模型", self)
        self.analysisStatusLabel.setWordWrap(True)
        layout.addWidget(self.analysisStatusLabel)

        self.categoryContainer = QWidget(self)
        self.categoryContainer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.categoryFlowLayout = FlowLayout(self.categoryContainer, isTight=False)
        self.categoryFlowLayout.setHorizontalSpacing(8)
        self.categoryFlowLayout.setVerticalSpacing(8)
        self.categoryFlowLayout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.categoryContainer)
        return widget

    def createPlayerWidget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.videoWidget.setMinimumHeight(360)
        self.videoWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.videoWidget, 1)

        self.fallbackFrameLabel = QLabel(self)
        self.fallbackFrameLabel.setText("视频预览不可用\n仍可播放时间轴并显示最近分析结果")
        self.fallbackFrameLabel.setMinimumHeight(360)
        self.fallbackFrameLabel.setAlignment(Qt.AlignCenter)
        self.fallbackFrameLabel.setStyleSheet("QLabel { background: #111111; color: #dddddd; }")
        self.fallbackFrameLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.fallbackFrameLabel.setVisible(False)
        layout.addWidget(self.fallbackFrameLabel, 1)

        self.playbackLabel = QLabel("未加载视频", self)
        self.playbackLabel.setAlignment(Qt.AlignCenter)
        self.playbackLabel.setWordWrap(True)
        self.playbackLabel.setStyleSheet(
            "QLabel { padding: 10px; border-radius: 6px; background: #202020; color: white; font-size: 16px; }"
        )
        layout.addWidget(self.playbackLabel)

        sliderLayout = QHBoxLayout()
        self.positionLabel = BodyLabel("00:00", self)
        sliderLayout.addWidget(self.positionLabel)
        self.positionSlider = QSlider(Qt.Horizontal, self)
        self.positionSlider.sliderPressed.connect(self.onSliderPressed)
        self.positionSlider.sliderReleased.connect(self.onSliderReleased)
        sliderLayout.addWidget(self.positionSlider, 1)
        self.durationLabel = BodyLabel("00:00", self)
        sliderLayout.addWidget(self.durationLabel)
        layout.addLayout(sliderLayout)

        buttonLayout = QHBoxLayout()
        self.backwardButton = PushButton("后退 3 秒", self)
        self.backwardButton.clicked.connect(lambda: self.seekBySeconds(-3))
        buttonLayout.addWidget(self.backwardButton)

        self.playPauseButton = PushButton("播放", self, FIF.PLAY)
        self.playPauseButton.clicked.connect(self.togglePlayPause)
        buttonLayout.addWidget(self.playPauseButton)

        self.forwardButton = PushButton("前进 3 秒", self)
        self.forwardButton.clicked.connect(lambda: self.seekBySeconds(3))
        buttonLayout.addWidget(self.forwardButton)

        self.fullscreenButton = PushButton("大窗播放", self)
        self.fullscreenButton.clicked.connect(self.openFullscreenPlayer)
        buttonLayout.addWidget(self.fullscreenButton)
        buttonLayout.addStretch()
        layout.addLayout(buttonLayout)
        return widget

    def connectPlayerSignals(self):
        self.player.positionChanged.connect(self.onPositionChanged)
        self.player.durationChanged.connect(self.onDurationChanged)
        self.player.stateChanged.connect(self.onPlayerStateChanged)
        self.player.error.connect(self.onPlayerError)

    def installPlaybackShortcuts(self):
        self.playbackShortcuts = []
        shortcuts = [
            (Qt.Key_Space, self.togglePlayPause),
            (Qt.Key_Left, lambda: self.seekBySeconds(-3)),
            (Qt.Key_Right, lambda: self.seekBySeconds(3)),
        ]
        for key, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self.playbackShortcuts.append(shortcut)

    def refreshVideoList(self):
        if not hasattr(self, "videoComboBox"):
            return

        self.videoComboBox.clear()
        self.video_paths = {}
        work_dir = cfg.get(cfg.workDirectory)
        self.videoDirLabel.setText(f"工作目录: {work_dir or '未设置'}")

        if not work_dir or not os.path.isdir(work_dir):
            self.videoComboBox.addItem("未设置工作目录")
            self.loadVideoButton.setEnabled(False)
            return

        work_path = Path(work_dir)
        scan_dirs = [work_path]
        scan_dirs.extend(
            sorted(
                [Path(entry.path) for entry in os.scandir(work_path) if entry.is_dir() and not entry.name.startswith(".")],
                key=lambda path: path.name.lower(),
            )
        )

        videos = []
        for scan_dir in scan_dirs:
            with os.scandir(scan_dir) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if entry.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
                        videos.append(path)
        videos.sort(key=lambda path: path.stat().st_mtime, reverse=True)

        for path in videos:
            try:
                display = str(path.relative_to(work_path))
            except ValueError:
                display = path.name
            self.videoComboBox.addItem(display)
            self.video_paths[display] = str(path)
        if not videos:
            self.videoComboBox.addItem("工作目录或一级子目录中未找到视频")
        self.loadVideoButton.setEnabled(bool(videos))

    def openVideoDirectory(self):
        work_dir = cfg.get(cfg.workDirectory)
        if not work_dir:
            InfoBar.warning("警告", "请先设置工作目录", duration=2000, parent=self)
            return
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        self.openPath(work_dir)

    def loadSelectedVideo(self):
        video_path = self.video_paths.get(self.videoComboBox.currentText())
        if video_path:
            self.loadVideoFile(video_path)

    def loadVideoFile(self, video_path):
        self.flushProjectSave()
        try:
            self.project = ActionVideoProject(video_path)
            self.windowSizeSpinBox.setValue(self.project.window_size)
            self.sampleRateSpinBox.setValue(max(1, self.project.sample_rate))
            if self.project.selected_model_id:
                self.selectModelById(self.project.selected_model_id)

            self.updateVideoInfoFromFile()
            self.loadPreviewMedia()
            self.updateContentVisibility()
            self.refreshSummaryTable()
            self.updateAnalysisStatus()
            self.updatePlaybackLabel()
            InfoBar.success("成功", f"已加载视频: {self.project.video_path.name}", duration=2000, parent=self)
        except Exception as e:
            InfoBar.error("错误", f"加载视频失败: {str(e)}", duration=3000, parent=self)

    def loadPreviewMedia(self):
        self.previewAvailable = False
        self.useFallbackPreview = False
        self.fallbackTimer.stop()
        self.fallbackPositionMs = 0
        self.fallbackPixmap = None
        self.requestedPlayback = False
        self.releaseFallbackCapture()
        self.videoWidget.setVisible(True)
        self.fallbackFrameLabel.setVisible(False)
        self.fallbackFrameLabel.clear()
        self.fallbackFrameLabel.setText("视频预览不可用\n仍可播放时间轴并显示最近分析结果")
        self.player.stop()
        try:
            self.player.setVideoOutput(self.videoWidget)
            self.player.setMedia(QMediaContent(QUrl.fromLocalFile(str(self.project.video_path))))
            self.previewAvailable = True
            self.positionSlider.setRange(0, self.project.duration_ms)
            self.positionSlider.setValue(0)
            self.positionLabel.setText(self.formatMs(0))
            self.durationLabel.setText(self.formatMs(self.project.duration_ms))
        except Exception as e:
            self.activateFallbackPreview(f"系统预览不可用: {str(e)}")

    def onPlayerError(self, *_):
        if not self.project:
            return

        message = self.player.errorString() or "当前系统播放器无法解码或渲染该视频"
        self.activateFallbackPreview(message, start_playback=self.requestedPlayback)
        InfoBar.warning(
            "预览已切换",
            "DirectShow 无法解码或渲染该视频，已切换为逐帧预览。",
            duration=5000,
            parent=self,
        )

    def activateFallbackPreview(self, message, start_playback=False):
        self.previewAvailable = True
        self.useFallbackPreview = True
        self.player.stop()
        if start_playback:
            self.fallbackTimer.start()
            self.playPauseButton.setText("暂停")
        else:
            self.fallbackTimer.stop()
            self.playPauseButton.setText("播放")
        self.videoWidget.setVisible(False)
        self.fallbackFrameLabel.setVisible(True)
        self.fallbackFrameLabel.setPixmap(QPixmap())
        self.fallbackFrameLabel.setText("正在使用逐帧预览")
        self.positionSlider.setRange(0, self.project.duration_ms)
        self.positionSlider.setValue(self.fallbackPositionMs)
        self.positionLabel.setText(self.formatMs(self.fallbackPositionMs))
        self.durationLabel.setText(self.formatMs(self.project.duration_ms))
        frame_ok = self.renderFallbackFrame(self.fallbackPositionMs)
        if frame_ok:
            self.playbackLabel.setText(
                f"系统预览不可用: {message}。已切换为逐帧预览。"
            )
        else:
            self.playbackLabel.setText(
                f"系统预览不可用: {message}。暂时无法读取预览画面，但仍可继续分析。"
            )
        if self.fullscreenWindow:
            self.fullscreenWindow.setLabelText(self.playbackLabel.text())
            if self.fallbackPixmap:
                self.fullscreenWindow.setFallbackPixmap(self.fallbackPixmap)
            else:
                self.fullscreenWindow.setFallbackText("暂时无法读取该视频画面")
            self.fullscreenWindow.setPlaybackState(self.fallbackTimer.isActive())
            self.fullscreenWindow.setTimelineState(self.fallbackPositionMs, self.project.duration_ms)

    def onFallbackPreviewTick(self):
        if not self.project:
            return
        self.fallbackPositionMs += self.fallbackTimer.interval()
        duration = self.project.duration_ms
        if duration > 0 and self.fallbackPositionMs >= duration:
            self.fallbackPositionMs = duration
            self.fallbackTimer.stop()
            self.requestedPlayback = False
            self.playPauseButton.setText("播放")
        self.positionSlider.setValue(self.fallbackPositionMs)
        self.positionLabel.setText(self.formatMs(self.fallbackPositionMs))
        self.renderFallbackFrame(self.fallbackPositionMs)
        self.updatePlaybackLabel()
        if self.fullscreenWindow:
            self.fullscreenWindow.setPlaybackPosition(self.fallbackPositionMs)
            self.fullscreenWindow.setTimelineState(self.fallbackPositionMs, duration)
            self.fullscreenWindow.setPlaybackState(self.fallbackTimer.isActive())

    def releaseFallbackCapture(self):
        if self.fallbackCapture:
            self.fallbackCapture.release()
        self.fallbackCapture = None
        self.fallbackCapturePath = None

    def ensureFallbackCapture(self):
        if not self.project:
            return None
        video_path = str(self.project.video_path)
        if self.fallbackCapture and self.fallbackCapturePath == video_path and self.fallbackCapture.isOpened():
            return self.fallbackCapture

        self.releaseFallbackCapture()
        try:
            import cv2
        except Exception:
            return None

        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            capture.release()
            return None

        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if total_frames > 0 and (self.project.total_frames <= 0 or self.project.fps <= 0):
            self.project.update_video_info(total_frames, fps)
            self.positionSlider.setRange(0, self.project.duration_ms)
            self.durationLabel.setText(self.formatMs(self.project.duration_ms))

        self.fallbackCapture = capture
        self.fallbackCapturePath = video_path
        return capture

    def renderFallbackFrame(self, position_ms=None):
        if not self.project:
            return False

        capture = self.ensureFallbackCapture()
        if not capture:
            return self.renderFallbackFrameWithFfmpeg(position_ms)

        try:
            import cv2
        except Exception:
            return self.renderFallbackFrameWithFfmpeg(position_ms)

        position_ms = self.fallbackPositionMs if position_ms is None else max(0, int(position_ms))
        total_frames = max(0, int(self.project.total_frames or capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0))
        duration_ms = max(0, int(self.project.duration_ms or 0))
        fps = float(self.project.fps or capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if total_frames > 0 and duration_ms > 0:
            ratio = min(1.0, max(0.0, position_ms / duration_ms))
            frame_index = min(total_frames - 1, int(round(ratio * (total_frames - 1))))
        elif fps > 0:
            frame_index = max(0, int(round(position_ms * fps / 1000)))
        else:
            frame_index = 0

        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
        if not ok or frame is None:
            return self.renderFallbackFrameWithFfmpeg(position_ms)

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb_frame.shape
        bytes_per_line = channels * width
        image = QImage(rgb_frame.data, width, height, bytes_per_line, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        self.setFallbackFramePixmap(pixmap)
        return True

    def renderFallbackFrameWithFfmpeg(self, position_ms=None):
        if not self.project:
            return False

        seconds = max(0.0, (self.fallbackPositionMs if position_ms is None else int(position_ms)) / 1000.0)
        cmd = [
            get_ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{seconds:.3f}",
            "-i",
            str(self.project.video_path),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "pipe:1",
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
            )
        except Exception:
            result = None

        if not result or result.returncode != 0 or not result.stdout:
            self.fallbackPixmap = None
            self.fallbackFrameLabel.setPixmap(QPixmap())
            self.fallbackFrameLabel.setText("无法读取当前位置的视频帧")
            return False

        pixmap = QPixmap()
        if not pixmap.loadFromData(result.stdout):
            self.fallbackPixmap = None
            self.fallbackFrameLabel.setPixmap(QPixmap())
            self.fallbackFrameLabel.setText("无法显示当前位置的视频帧")
            return False

        self.setFallbackFramePixmap(pixmap)
        return True

    def setFallbackFramePixmap(self, pixmap):
        if not pixmap or pixmap.isNull():
            return
        self.fallbackPixmap = pixmap
        self.fallbackFrameLabel.setText("")
        self.fallbackFrameLabel.setPixmap(
            pixmap.scaled(self.fallbackFrameLabel.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        if self.fullscreenWindow and self.useFallbackPreview:
            self.fullscreenWindow.setFallbackPixmap(pixmap)

    def updateVideoInfoFromFile(self):
        if not self.project:
            return
        info = probe_video_info(self.project.video_path)
        self.project.update_video_info(info["total_frames"], info["fps"])
        if info.get("duration_ms"):
            self.project.duration_ms = info["duration_ms"]
            self.project.save()

    def refreshModelList(self, selected_id=None):
        current_id = selected_id or self.getSelectedModelId()
        self.modelComboBox.blockSignals(True)
        self.modelComboBox.clear()
        self.modelIdByDisplay = {}
        for item in self.registry.models:
            display = item["name"]
            self.modelComboBox.addItem(display)
            self.modelIdByDisplay[display] = item["id"]
        self.modelComboBox.blockSignals(False)

        if current_id:
            self.selectModelById(current_id)
        self.updateModelControls()
        self.updateModelPathLabel()

    def getSelectedModelId(self):
        return getattr(self, "modelIdByDisplay", {}).get(self.modelComboBox.currentText())

    def getSelectedModelInfo(self):
        return self.registry.get_model(self.getSelectedModelId())

    def selectModelById(self, model_id):
        for item in self.registry.models:
            if item["id"] == model_id:
                self.modelComboBox.setCurrentText(item["name"])
                return

    def updateModelControls(self):
        has_model = bool(self.getSelectedModelId())
        self.renameModelButton.setEnabled(has_model)
        self.deleteModelButton.setEnabled(has_model)

    def updateModelPathLabel(self):
        model = self.getSelectedModelInfo()
        self.modelPathLabel.setText(model["path"] if model else "未导入模型")

    def onSelectedModelChanged(self, *_):
        if self.project:
            self.project.selected_model_id = self.getSelectedModelId() or ""
            self.project.save()
        self.updateModelControls()
        self.updateModelPathLabel()
        self.updateAnalysisStatus()

    def importModel(self):
        start_dir = str(MODEL_STORE_DIR if MODEL_STORE_DIR.exists() else Path.home())
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 PyTorch 模型",
            start_dir,
            "PyTorch Model (*.pth *.pt);;All Files (*)",
        )
        if not file_path:
            return

        try:
            item = self.registry.add_model(file_path)
            self.refreshModelList(item["id"])
            InfoBar.success("成功", f"已导入模型到项目 model 文件夹: {item['name']}", duration=2500, parent=self)
        except Exception as e:
            InfoBar.error("错误", f"导入模型失败: {str(e)}", duration=3000, parent=self)

    def renameSelectedModel(self):
        model = self.getSelectedModelInfo()
        if not model:
            return
        new_name, ok = QInputDialog.getText(self, "重命名模型", "模型名称", text=model["name"])
        if ok and new_name.strip():
            self.registry.rename_model(model["id"], new_name.strip())
            self.refreshModelList(model["id"])

    def deleteSelectedModel(self):
        model = self.getSelectedModelInfo()
        if not model:
            return
        confirm = MessageBox("删除模型", f"确定删除模型 '{model['name']}' 吗？", self.window())
        if confirm.exec() and self.registry.delete_model(model["id"]):
            self.refreshModelList()
            InfoBar.success("成功", "模型已删除", duration=2000, parent=self)

    def startAnalysis(self):
        if not self.project:
            InfoBar.warning("警告", "请先选择并加载视频", duration=2000, parent=self)
            return
        model = self.getSelectedModelInfo()
        if not model:
            InfoBar.warning("警告", "请先导入并选择模型", duration=2000, parent=self)
            return
        if self.project.total_frames <= 0:
            InfoBar.warning("警告", "无法读取视频帧数", duration=2500, parent=self)
            return

        window_size = self.windowSizeSpinBox.value()
        if window_size % 2 == 0:
            window_size += 1
            self.windowSizeSpinBox.setValue(window_size)
        sample_rate = max(1, self.sampleRateSpinBox.value())
        sample_indices = self.project.build_sample_indices(sample_rate)
        if not sample_indices:
            InfoBar.warning("警告", "没有可分析的采样帧", duration=2500, parent=self)
            return

        self.project.selected_model_id = model["id"]
        self.project.window_size = window_size
        self.project.sample_rate = sample_rate
        self.project.prune_predictions_to_samples(model["id"], sample_indices)
        pending = self.project.pending_frame_indices(model["id"], sample_indices, window_size)
        if not pending:
            self.applyVoteAndRefresh(model["id"], window_size, sample_indices)
            InfoBar.success("完成", "采样帧已有该模型预测，已重新执行滑动窗口投票", duration=3000, parent=self)
            return

        self.startAnalysisButton.setEnabled(False)
        self.stopAnalysisButton.setEnabled(True)
        self.analysisProgressBar.setValue(0)
        analyzed = len(sample_indices) - len(pending)
        self.analysisStatusLabel.setText(
            f"开始采样分析: 每秒 {sample_rate} 次，共 {len(sample_indices)} 个采样帧，已有 {analyzed} 帧缓存，本次分析 {len(pending)} 帧"
        )
        self.worker = VideoAnalysisWorker(self.project.video_path, model, pending, window_size)
        self.worker.videoInfo.connect(self.onWorkerVideoInfo)
        self.worker.frameDone.connect(lambda index, prediction: self.onFrameAnalyzed(model["id"], index, prediction))
        self.worker.progress.connect(self.onAnalysisProgress)
        self.worker.failed.connect(self.onAnalysisFailed)
        self.worker.finished.connect(lambda: self.onAnalysisFinished(model["id"], window_size, sample_indices))
        self.worker.start()

    def stopAnalysis(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
        self.worker = None
        self.startAnalysisButton.setEnabled(True)
        self.stopAnalysisButton.setEnabled(False)
        self.analysisStatusLabel.setText("分析已停止，已完成的帧预测会保留")
        if self.project:
            self.project.save()

    def onWorkerVideoInfo(self, total_frames, fps):
        if self.project:
            self.project.update_video_info(total_frames, fps)

    def onFrameAnalyzed(self, model_id, frame_index, prediction):
        if self.project:
            self.project.set_raw_prediction(model_id, frame_index, prediction)
            if frame_index % 30 == 0:
                self.project.save()

    def onAnalysisProgress(self, done, total):
        if total:
            self.analysisProgressBar.setValue(int(done * 100 / total))
        self.analysisStatusLabel.setText(f"正在采样分析: {done} / {total} 个采样帧")

    def onAnalysisFailed(self, message):
        self.worker = None
        self.startAnalysisButton.setEnabled(True)
        self.stopAnalysisButton.setEnabled(False)
        self.analysisStatusLabel.setText(f"分析失败: {message}")
        InfoBar.error("错误", f"分析失败: {message}", duration=6000, parent=self)
        if self.project:
            self.project.save()

    def onAnalysisFinished(self, model_id, window_size, sample_indices):
        self.worker = None
        self.startAnalysisButton.setEnabled(True)
        self.stopAnalysisButton.setEnabled(False)
        self.analysisProgressBar.setValue(100)
        if self.project:
            self.project.save()
        self.applyVoteAndRefresh(model_id, window_size, sample_indices)

    def applyVoteAndRefresh(self, model_id, window_size, sample_indices=None):
        changed, missing = self.project.apply_sliding_window_vote(model_id, window_size, sample_indices)
        if missing:
            self.analysisStatusLabel.setText(f"还有 {len(missing)} 帧缺少原始预测，无法投票")
            return
        self.refreshSummaryTable()
        self.updatePlaybackLabel()
        sample_count = len(sample_indices) if sample_indices is not None else len(self.project.labeled_frames)
        invalid_count = self.project.get_label_counts().get(INVALID_CATEGORY_NAME, 0)
        self.analysisStatusLabel.setText(
            f"分析完成: 已对 {window_size} 帧窗口内的类别概率取平均，"
            f"低于 {format_confidence(CONFIDENCE_THRESHOLD)} 标为无效类，"
            f"更新 {changed} 个采样标记，共 {sample_count} 个采样点，无效 {invalid_count} 个"
        )

    def refreshSummaryTable(self):
        if not self.project:
            self.clearCategoryCards()
            return

        self.clearCategoryCards()
        stats = self.project.get_label_stats()
        categories = list(self.project.label_project.categories)
        for category in categories:
            item = stats.get(category.name, {})
            count = item.get("count", 0)
            confidence_count = item.get("confidence_count", 0)
            avg_confidence = None
            if confidence_count:
                avg_confidence = item.get("confidence_total", 0.0) / confidence_count
            card = ActionCategoryCard(category, count, avg_confidence, self.categoryContainer)
            self.categoryFlowLayout.addWidget(card)

        self.categoryContainer.updateGeometry()
        self.categoryContainer.update()

    def clearCategoryCards(self):
        if not hasattr(self, "categoryFlowLayout"):
            return
        while self.categoryFlowLayout.count() > 0:
            item = self.categoryFlowLayout.takeAt(0)
            widget = None
            if item:
                widget_getter = getattr(item, "widget", None)
                widget = widget_getter() if callable(widget_getter) else item
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def updateAnalysisStatus(self):
        if not hasattr(self, "analysisStatusLabel"):
            return
        if not self.project:
            self.analysisStatusLabel.setText("请选择视频和模型")
            return
        model = self.getSelectedModelInfo()
        model_text = model["name"] if model else "未选择模型"
        self.analysisStatusLabel.setText(
            f"视频: {self.project.video_path.name} | 总帧数: {self.project.total_frames} | "
            f"模型: {model_text} | 每秒分析: {self.sampleRateSpinBox.value()} 次 | 滑动窗口: {self.windowSizeSpinBox.value()}"
        )

    def togglePlayPause(self):
        if not self.project:
            return
        if not self.previewAvailable:
            InfoBar.warning(
                "播放预览不可用",
                "当前系统播放器无法解码/渲染该视频，但仍可开始逐帧分析。",
                duration=3000,
                parent=self,
            )
            return
        if self.useFallbackPreview:
            if self.fallbackTimer.isActive():
                self.fallbackTimer.stop()
                self.requestedPlayback = False
                self.playPauseButton.setText("播放")
            else:
                self.fallbackTimer.start()
                self.requestedPlayback = True
                self.playPauseButton.setText("暂停")
            if self.fullscreenWindow:
                self.fullscreenWindow.setPlaybackState(self.fallbackTimer.isActive())
            return
        if self.player.state() == QMediaPlayer.PlayingState:
            self.requestedPlayback = False
            self.player.pause()
        else:
            self.requestedPlayback = True
            self.player.play()
        if self.fullscreenWindow:
            self.fullscreenWindow.setPlaybackState(self.player.state() == QMediaPlayer.PlayingState)

    def seekBySeconds(self, seconds):
        if not self.previewAvailable:
            return
        if self.useFallbackPreview:
            duration = self.project.duration_ms if self.project else 0
            self.fallbackPositionMs = max(0, min(duration, self.fallbackPositionMs + seconds * 1000))
            self.positionSlider.setValue(self.fallbackPositionMs)
            self.positionLabel.setText(self.formatMs(self.fallbackPositionMs))
            self.renderFallbackFrame(self.fallbackPositionMs)
            self.updatePlaybackLabel()
            if self.fullscreenWindow:
                self.fullscreenWindow.setPlaybackPosition(self.fallbackPositionMs)
                self.fullscreenWindow.setTimelineState(self.fallbackPositionMs, duration)
            return
        target = max(0, min(self.player.duration(), self.player.position() + seconds * 1000))
        self.player.setPosition(target)
        if self.fullscreenWindow:
            self.fullscreenWindow.setPlaybackPosition(target)

    def onSliderPressed(self):
        self.is_slider_pressed = True

    def onSliderReleased(self):
        self.is_slider_pressed = False
        if self.useFallbackPreview:
            self.fallbackPositionMs = self.positionSlider.value()
            self.positionLabel.setText(self.formatMs(self.fallbackPositionMs))
            self.renderFallbackFrame(self.fallbackPositionMs)
            self.updatePlaybackLabel()
            if self.fullscreenWindow:
                self.fullscreenWindow.setPlaybackPosition(self.fallbackPositionMs)
                self.fullscreenWindow.setTimelineState(self.fallbackPositionMs, self.project.duration_ms if self.project else 0)
            return
        self.player.setPosition(self.positionSlider.value())
        if self.fullscreenWindow:
            self.fullscreenWindow.setPlaybackPosition(self.positionSlider.value())

    def onPositionChanged(self, position):
        if self.useFallbackPreview:
            return
        if not self.is_slider_pressed:
            self.positionSlider.setValue(position)
        self.positionLabel.setText(self.formatMs(position))
        self.updatePlaybackLabel()
        if self.fullscreenWindow:
            self.fullscreenWindow.setPlaybackPosition(position)
            self.fullscreenWindow.setTimelineState(position, self.player.duration())

    def onDurationChanged(self, duration):
        if self.useFallbackPreview:
            return
        self.positionSlider.setRange(0, duration)
        self.durationLabel.setText(self.formatMs(duration))
        if self.fullscreenWindow:
            self.fullscreenWindow.setTimelineState(self.player.position(), duration)

    def onPlayerStateChanged(self, state):
        if self.useFallbackPreview:
            return
        self.playPauseButton.setText("暂停" if state == QMediaPlayer.PlayingState else "播放")
        if self.fullscreenWindow:
            self.fullscreenWindow.setPlaybackState(state == QMediaPlayer.PlayingState)

    def formatMs(self, ms):
        seconds = max(0, int(ms / 1000))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def currentFrameIndex(self):
        if not self.project or self.project.total_frames <= 0:
            return -1
        position = self.fallbackPositionMs if self.useFallbackPreview else self.player.position()
        duration = (self.project.duration_ms if self.useFallbackPreview else self.player.duration()) or self.project.duration_ms
        if duration > 0:
            ratio = min(1.0, max(0.0, position / duration))
            return min(self.project.total_frames - 1, int(round(ratio * (self.project.total_frames - 1))))
        return 0

    def updatePlaybackLabel(self):
        if not self.project:
            return
        if not self.previewAvailable and not self.useFallbackPreview:
            text = (
                f"视频已加载: {self.project.video_path.name} | "
                "当前系统播放器无法预览，仍可开始逐帧分析"
            )
            self.playbackLabel.setText(text)
            if self.fullscreenWindow:
                self.fullscreenWindow.setLabelText(text)
                self.fullscreenWindow.setBorderColor(None)
            return
        index = self.currentFrameIndex()
        if index < 0:
            self.playbackLabel.setText(f"已加载视频: {self.project.video_path.name}")
            if self.fullscreenWindow:
                self.fullscreenWindow.setLabelText(self.playbackLabel.text())
                self.fullscreenWindow.setBorderColor(None)
            return

        prediction = self.project.get_nearest_frame_prediction(index)
        label_name = prediction.get("class_name", "") if prediction else ""
        category = self.project.label_project.get_category(label_name) if label_name else None
        label_text = self.project.label_project.get_category_display_name(label_name) if label_name else "未标记"
        border_color = category.color if category else None
        confidence_text = format_confidence(prediction.get("confidence", 0) if prediction else 0)
        top_class_name = prediction.get("top_class_name", "") if prediction else ""
        if label_name == INVALID_CATEGORY_NAME and top_class_name:
            top_text = self.project.label_project.get_category_display_name(top_class_name)
            text = f"帧 {index + 1} / {self.project.total_frames} | 最近采样动作: {label_text} | 最高候选: {top_text} | 置信度: {confidence_text}"
        else:
            text = f"帧 {index + 1} / {self.project.total_frames} | 最近采样动作: {label_text} | 置信度: {confidence_text}"
        self.playbackLabel.setText(text)
        if self.fullscreenWindow:
            self.fullscreenWindow.setLabelText(text)
            self.fullscreenWindow.setBorderColor(border_color)

    def openFullscreenPlayer(self):
        if not self.project:
            return
        if not self.previewAvailable:
            InfoBar.warning(
                "播放预览不可用",
                "当前系统播放器无法解码/渲染该视频，无法大窗播放。",
                duration=3000,
                parent=self,
            )
            return
        if self.fullscreenWindow and self.fullscreenWindow.isVisible():
            self.fullscreenWindow.activateWindow()
            self.fullscreenWindow.raise_()
            return

        self.fullscreenWindow = FullscreenVideoWindow(self)
        self.fullscreenWindow.closed.connect(self.restoreInlineVideoOutput)
        self.fullscreenWindow.playPauseRequested.connect(self.togglePlayPause)
        self.fullscreenWindow.seekRequested.connect(self.seekBySeconds)
        self.fullscreenWindow.seekToRequested.connect(self.seekToPosition)
        if self.useFallbackPreview:
            self.fullscreenWindow.videoWidget.setVisible(False)
            self.fullscreenWindow.imageLabel.setVisible(True)
            if self.fallbackPixmap:
                self.fullscreenWindow.setFallbackPixmap(self.fallbackPixmap)
            else:
                self.fullscreenWindow.setFallbackText("暂时无法读取该视频画面")
        else:
            self.fullscreenWindow.imageLabel.setVisible(False)
            self.fullscreenWindow.videoWidget.setVisible(True)
            self.player.setVideoOutput(self.fullscreenWindow.videoWidget)
        self.updatePlaybackLabel()
        self.syncFullscreenWindowState()
        self.fullscreenWindow.showMaximized()

    def restoreInlineVideoOutput(self):
        if not self.useFallbackPreview:
            self.player.setVideoOutput(self.videoWidget)
        self.fullscreenWindow = None

    def syncFullscreenWindowState(self):
        if not self.fullscreenWindow:
            return
        if self.useFallbackPreview:
            duration = self.project.duration_ms if self.project else 0
            position = self.fallbackPositionMs
            self.fullscreenWindow.setPlaybackState(self.fallbackTimer.isActive())
        else:
            duration = self.player.duration()
            position = self.player.position()
            self.fullscreenWindow.setPlaybackState(self.player.state() == QMediaPlayer.PlayingState)
        self.fullscreenWindow.setTimelineState(position, duration)
        self.fullscreenWindow.setLabelText(self.playbackLabel.text())
        if self.useFallbackPreview:
            if self.fallbackPixmap:
                self.fullscreenWindow.setFallbackPixmap(self.fallbackPixmap)
            else:
                self.fullscreenWindow.setFallbackText("暂时无法读取该视频画面")

    def seekToPosition(self, position):
        if not self.previewAvailable:
            return
        if self.useFallbackPreview:
            duration = self.project.duration_ms if self.project else 0
            self.fallbackPositionMs = max(0, min(duration, int(position)))
            self.positionSlider.setValue(self.fallbackPositionMs)
            self.positionLabel.setText(self.formatMs(self.fallbackPositionMs))
            self.renderFallbackFrame(self.fallbackPositionMs)
            self.updatePlaybackLabel()
            if self.fullscreenWindow:
                self.fullscreenWindow.setPlaybackPosition(self.fallbackPositionMs)
                self.fullscreenWindow.setTimelineState(self.fallbackPositionMs, duration)
            return
        self.player.setPosition(max(0, int(position)))
        if self.fullscreenWindow:
            self.fullscreenWindow.setPlaybackPosition(max(0, int(position)))

    def openActionJson(self):
        if not self.project:
            InfoBar.warning("警告", "请先选择视频", duration=2000, parent=self)
            return
        self.project.save()
        self.openPath(self.project.config_path)

    def openPath(self, path):
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(["open", str(path)])
            else:
                import subprocess
                subprocess.run(["xdg-open", str(path)])
        except Exception as e:
            InfoBar.error("错误", f"无法打开: {str(e)}", duration=3000, parent=self)

    def flushProjectSave(self):
        if self.project:
            self.project.save()
            self.project.label_project.save_config()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.useFallbackPreview and self.fallbackPixmap and not self.fallbackPixmap.isNull():
            self.fallbackFrameLabel.setPixmap(
                self.fallbackPixmap.scaled(
                    self.fallbackFrameLabel.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )

    def closeEvent(self, event):
        self.flushProjectSave()
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
        self.fallbackTimer.stop()
        self.releaseFallbackCapture()
        self.player.stop()
        super().closeEvent(event)
