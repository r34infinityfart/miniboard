from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .audio_engine import AudioMixerEngine, SoundboardClip, list_devices


APP_NAME = "Miniboard"
SETTINGS_PATH = Path(os.getenv("APPDATA", ".")) / "miniboard.settings.json"


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(980, 620)
        self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)

        self._engine: Optional[AudioMixerEngine] = None
        self._clips: list[SoundboardClip] = []

        root = QtWidgets.QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self._titlebar = self._build_titlebar()
        layout.addWidget(self._titlebar)

        top = QtWidgets.QHBoxLayout()
        layout.addLayout(top)

        self.device_in = QtWidgets.QComboBox()
        self.device_out = QtWidgets.QComboBox()
        self.device_monitor = QtWidgets.QComboBox()

        self.btn_start = QtWidgets.QPushButton("Start")
        self.btn_start.setCheckable(True)
        self.btn_add = QtWidgets.QPushButton("Add sound")
        self.btn_stop_sounds = QtWidgets.QPushButton("Stop sounds")

        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Normal", "normal")
        self.mode.addItem("Layer / Repeat", "layer")

        top.addWidget(self._labeled("Mic", self.device_in))
        top.addWidget(self._labeled("Output", self.device_out))
        top.addWidget(self._labeled("Monitor", self.device_monitor))
        top.addWidget(self._labeled("Mode", self.mode))
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_stop_sounds)
        top.addWidget(self.btn_start)

        mid = QtWidgets.QHBoxLayout()
        layout.addLayout(mid)

        pads_wrap = QtWidgets.QWidget()
        pads_wrap.setObjectName("padsWrap")
        pads_l = QtWidgets.QVBoxLayout(pads_wrap)
        pads_l.setContentsMargins(0, 0, 0, 0)
        pads_l.setSpacing(10)

        pads_head = QtWidgets.QHBoxLayout()
        pads_l.addLayout(pads_head)
        self.pads_title = QtWidgets.QLabel("Pads")
        self.pads_title.setObjectName("h2")
        pads_head.addWidget(self.pads_title)
        pads_head.addStretch(1)

        self.pad_grid = QtWidgets.QGridLayout()
        self.pad_grid.setSpacing(10)
        pads_l.addLayout(self.pad_grid, 1)
        mid.addWidget(pads_wrap, 2)

        panel = QtWidgets.QWidget()
        panel.setObjectName("panel")
        right = QtWidgets.QVBoxLayout(panel)
        right.setContentsMargins(14, 14, 14, 14)
        right.setSpacing(12)
        mid.addWidget(panel, 1)

        self.slider_mic = self._slider_row("Mic gain", 0, 200, 100)
        self.slider_board = self._slider_row("Board gain", 0, 200, 100)
        self.slider_monitor = self._slider_row("Monitor", 0, 200, 0)
        self.slider_block = self._slider_row("Blocksize", 64, 1024, 128)

        self.chk_monitor_mic = QtWidgets.QCheckBox("Monitor mic")
        self.chk_monitor_mic.setChecked(True)

        self.slider_mic["slider"].valueChanged.connect(self._on_mic_gain_changed)
        self.slider_board["slider"].valueChanged.connect(self._on_board_gain_changed)
        self.slider_monitor["slider"].valueChanged.connect(self._on_monitor_gain_changed)
        self.chk_monitor_mic.toggled.connect(self._on_monitor_mic_toggled)

        right.addWidget(self.slider_mic["widget"])
        right.addWidget(self.slider_board["widget"])
        right.addWidget(self.slider_monitor["widget"])
        right.addWidget(self.chk_monitor_mic)
        right.addWidget(self.slider_block["widget"])
        right.addStretch(1)

        self.status = QtWidgets.QLabel("Ready")
        layout.addWidget(self.status)

        self.btn_add.clicked.connect(self._on_add_sound)
        self.btn_start.toggled.connect(self._on_start_toggled)
        self.btn_stop_sounds.clicked.connect(self._on_stop_sounds)

        self._load_devices()
        self._load_settings()
        self._rebuild_pads()

    def _labeled(self, label: str, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(box)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(4)
        t = QtWidgets.QLabel(label)
        t.setObjectName("mini")
        l.addWidget(t)
        l.addWidget(widget)
        return box

    def _build_titlebar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setObjectName("titlebar")
        bar.setFixedHeight(44)

        l = QtWidgets.QHBoxLayout(bar)
        l.setContentsMargins(10, 6, 10, 6)
        l.setSpacing(10)

        self._appdot = QtWidgets.QLabel("M")
        self._appdot.setObjectName("dot")
        self._title = QtWidgets.QLabel(APP_NAME)
        self._title.setObjectName("title")
        l.addWidget(self._appdot)
        l.addWidget(self._title)
        l.addStretch(1)

        self._btn_min = QtWidgets.QPushButton("—")
        self._btn_max = QtWidgets.QPushButton("□")
        self._btn_close = QtWidgets.QPushButton("×")
        for b in (self._btn_min, self._btn_max, self._btn_close):
            b.setObjectName("winbtn")
            b.setFixedSize(36, 28)
        self._btn_close.setObjectName("winbtnClose")

        self._btn_min.clicked.connect(self.showMinimized)
        self._btn_max.clicked.connect(self._toggle_max_restore)
        self._btn_close.clicked.connect(self.close)

        l.addWidget(self._btn_min)
        l.addWidget(self._btn_max)
        l.addWidget(self._btn_close)

        bar.mousePressEvent = self._on_title_mouse_press  # type: ignore[method-assign]
        bar.mouseMoveEvent = self._on_title_mouse_move  # type: ignore[method-assign]
        return bar

    def _toggle_max_restore(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _on_title_mouse_press(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def _on_title_mouse_move(self, event: QtGui.QMouseEvent) -> None:
        if event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            if getattr(self, "_drag_pos", None) is None:
                return
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def _slider_row(self, title: str, mn: int, mx: int, val: int):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lbl = QtWidgets.QLabel(title)
        lbl.setObjectName("mini")
        s = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        s.setRange(mn, mx)
        s.setValue(val)
        v = QtWidgets.QLabel(str(val))
        v.setFixedWidth(46)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(s, 1)
        row.addWidget(v)
        lay.addWidget(lbl)
        lay.addLayout(row)
        s.valueChanged.connect(lambda x: v.setText(str(x)))
        return {"widget": w, "slider": s, "value": v}

    def _load_devices(self) -> None:
        devs = list_devices()
        self._devs = devs

        self.device_in.clear()
        self.device_out.clear()
        self.device_monitor.clear()

        self.device_monitor.addItem("Off", None)

        for d in devs:
            if d.max_input_channels > 0:
                self.device_in.addItem(d.name, d.index)
            if d.max_output_channels > 0:
                self.device_out.addItem(d.name, d.index)
                self.device_monitor.addItem(d.name, d.index)

    def _settings_payload(self) -> dict:
        return {
            "in": self.device_in.currentData(),
            "out": self.device_out.currentData(),
            "mon": self.device_monitor.currentData(),
            "mic_gain": self.slider_mic["slider"].value(),
            "board_gain": self.slider_board["slider"].value(),
            "monitor_gain": self.slider_monitor["slider"].value(),
            "monitor_mic": bool(self.chk_monitor_mic.isChecked()),
            "block": self.slider_block["slider"].value(),
            "clips": [c.path for c in self._clips],
        }

    def _load_settings(self) -> None:
        try:
            payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return

        def set_by_data(cb: QtWidgets.QComboBox, data) -> None:
            for i in range(cb.count()):
                if cb.itemData(i) == data:
                    cb.setCurrentIndex(i)
                    return

        set_by_data(self.device_in, payload.get("in"))
        set_by_data(self.device_out, payload.get("out"))
        set_by_data(self.device_monitor, payload.get("mon"))

        if isinstance(payload.get("mic_gain"), int):
            self.slider_mic["slider"].setValue(int(payload["mic_gain"]))
        if isinstance(payload.get("board_gain"), int):
            self.slider_board["slider"].setValue(int(payload["board_gain"]))
        if isinstance(payload.get("monitor_gain"), int):
            self.slider_monitor["slider"].setValue(int(payload["monitor_gain"]))
        if isinstance(payload.get("monitor_mic"), bool):
            self.chk_monitor_mic.setChecked(bool(payload["monitor_mic"]))
        if isinstance(payload.get("block"), int):
            self.slider_block["slider"].setValue(int(payload["block"]))

        clips = payload.get("clips")
        if isinstance(clips, list):
            self._clips = []
            for p in clips:
                if isinstance(p, str) and p:
                    try:
                        self._clips.append(SoundboardClip(p))
                    except Exception:
                        pass

    def _save_settings(self) -> None:
        try:
            SETTINGS_PATH.write_text(json.dumps(self._settings_payload(), indent=2), encoding="utf-8")
        except Exception:
            pass

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._save_settings()
        if self._engine is not None:
            self._engine.stop()
            self._engine = None
        super().closeEvent(event)

    def _rebuild_pads(self) -> None:
        while self.pad_grid.count():
            item = self.pad_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        cols = 4
        for i, clip in enumerate(self._clips):
            btn = QtWidgets.QPushButton(Path(clip.path).stem)
            btn.setMinimumHeight(64)
            btn.clicked.connect(lambda _=False, c=clip: self._trigger_clip(c))
            r = i // cols
            c = i % cols
            self.pad_grid.addWidget(btn, r, c)

    def _on_mic_gain_changed(self, value: int) -> None:
        if self._engine is not None:
            self._engine.set_mic_gain(value / 100.0)

    def _on_board_gain_changed(self, value: int) -> None:
        if self._engine is not None:
            self._engine.set_board_gain(value / 100.0)

    def _on_monitor_gain_changed(self, value: int) -> None:
        if self._engine is not None:
            self._engine.set_monitor_gain(value / 100.0)

    def _on_monitor_mic_toggled(self, on: bool) -> None:
        if self._engine is not None:
            self._engine.set_monitor_mic(bool(on))

    def _on_stop_sounds(self) -> None:
        if self._engine is not None:
            self._engine.stop_all_clips()

    def _trigger_clip(self, clip: SoundboardClip) -> None:
        if self._engine is None:
            return
        mode = str(self.mode.currentData())
        exclusive = mode == "normal"
        self._engine.trigger_clip(clip, gain=1.0, exclusive=exclusive)

    def _on_add_sound(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Add sound",
            str(Path.home()),
            "Audio files (*.wav *.flac *.ogg *.mp3);;All files (*.*)",
        )
        if not path:
            return
        try:
            clip = SoundboardClip(path)
        except Exception as e:
            self.status.setText(f"Failed to load: {e}")
            return

        self._clips.append(clip)
        self._rebuild_pads()
        self._save_settings()

    def _on_start_toggled(self, on: bool) -> None:
        if on:
            try:
                in_device = int(self.device_in.currentData())
                out = int(self.device_out.currentData())
                mon = self.device_monitor.currentData()
                mic_gain = self.slider_mic["slider"].value() / 100.0
                board_gain = self.slider_board["slider"].value() / 100.0
                monitor_gain = self.slider_monitor["slider"].value() / 100.0
                monitor_mic = bool(self.chk_monitor_mic.isChecked())
                block = int(self.slider_block["slider"].value())

                mon_device = int(mon) if mon is not None else None

                self._engine = AudioMixerEngine(
                    input_device=in_device,
                    output_device=out,
                    monitor_device=mon_device,
                    samplerate=48000,
                    blocksize=block,
                    channels_out=2,
                    mic_gain=mic_gain,
                    board_gain=board_gain,
                    monitor_gain=monitor_gain,
                    monitor_mic=monitor_mic,
                )
                self._engine.start()
                self.status.setText("Running")
                self.btn_start.setText("Stop")
            except Exception as e:
                self.status.setText(f"Start failed: {e}")
                self.btn_start.setChecked(False)
        else:
            if self._engine is not None:
                self._engine.stop()
                self._engine = None
            self.status.setText("Stopped")
            self.btn_start.setText("Start")


def _apply_style(app: QtWidgets.QApplication) -> None:
    app.setStyle("Fusion")
    app.setFont(QtGui.QFont("Segoe UI", 10))
    app.setStyleSheet(
        """
        QWidget#root { background: #07080d; }

        QWidget#titlebar { background: #0b0d14; border: 1px solid #1d2233; border-radius: 12px; }
        QLabel#title { color: #f4f7ff; font-weight: 700; letter-spacing: 0.4px; }
        QLabel#dot { color: #00f5d4; font-size: 18px; }

        QWidget#panel { background: #0b0d14; border: 1px solid #1d2233; border-radius: 14px; }
        QWidget#padsWrap { background: transparent; }
        QLabel#h2 { color: #f4f7ff; font-weight: 700; font-size: 14px; }

        QLabel { color: #e6ecff; }
        QLabel#mini { color: #9aa7cc; font-size: 11px; }

        QComboBox, QPushButton {
            background: #0f1220;
            color: #f4f7ff;
            border: 1px solid #242b42;
            padding: 9px 12px;
            border-radius: 12px;
        }
        QComboBox::drop-down { border: 0; width: 28px; }
        QComboBox:hover, QPushButton:hover { border-color: #00f5d4; }
        QPushButton:pressed { background: #151a2e; }
        QPushButton:checked { background: #111738; border-color: #7c5cff; }

        QPushButton#winbtn { padding: 0px; border-radius: 8px; min-width: 36px; min-height: 28px; background: transparent; }
        QPushButton#winbtn:hover { background: #13182a; }
        QPushButton#winbtnClose:hover { background: #2a1117; border-color: #ff3b6a; }

        QSlider::groove:horizontal { height: 8px; background: #0f1220; border: 1px solid #242b42; border-radius: 4px; }
        QSlider::sub-page:horizontal { background: #00f5d4; border-radius: 4px; }
        QSlider::handle:horizontal { width: 18px; margin: -6px 0; background: #f4f7ff; border: 1px solid #00f5d4; border-radius: 9px; }
        """
    )


def run() -> None:
    app = QtWidgets.QApplication([])
    _apply_style(app)
    w = MainWindow()
    w.show()
    app.exec()
