from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np
import sounddevice as sd

try:
    import soundfile as sf
except ImportError:
    sf = None

try:
    import audioread
except ImportError:
    audioread = None


@dataclass(frozen=True)
class AudioDeviceInfo:
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float


def list_devices() -> list[AudioDeviceInfo]:
    devices = sd.query_devices()
    out: list[AudioDeviceInfo] = []
    for i, d in enumerate(devices):
        out.append(
            AudioDeviceInfo(
                index=i,
                name=str(d.get("name", i)),
                max_input_channels=int(d.get("max_input_channels", 0)),
                max_output_channels=int(d.get("max_output_channels", 0)),
                default_samplerate=float(d.get("default_samplerate", 48000.0)),
            )
        )
    return out


def _to_float32(x: np.ndarray) -> np.ndarray:
    if x.dtype == np.float32:
        return x
    return x.astype(np.float32, copy=False)


def _resample_linear(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr:
        return x
    if x.shape[0] < 2:
        return x

    ratio = float(dst_sr) / float(src_sr)
    n_out = max(1, int(round(x.shape[0] * ratio)))

    t_in = np.linspace(0.0, 1.0, num=x.shape[0], endpoint=False, dtype=np.float32)
    t_out = np.linspace(0.0, 1.0, num=n_out, endpoint=False, dtype=np.float32)

    y = np.empty((n_out, x.shape[1]), dtype=np.float32)
    for ch in range(x.shape[1]):
        y[:, ch] = np.interp(t_out, t_in, x[:, ch]).astype(np.float32)
    return y


def _load_audio(path: str) -> tuple[np.ndarray, int]:
    if sf is not None:
        try:
            data, sr = sf.read(path, always_2d=True, dtype="float32")
            return data, int(sr)
        except Exception:
            pass

    if audioread is not None:
        try:
            with audioread.audio_open(path) as f:
                sr = f.samplerate
                channels = f.channels
                frames = []
                for chunk in f:
                    # audioread yields raw PCM bytes (typically signed 16-bit).
                    arr_i16 = np.frombuffer(chunk, dtype=np.int16)
                    arr = (arr_i16.astype(np.float32) / 32768.0)
                    frames.append(arr)
                data_1d = np.concatenate(frames) if frames else np.zeros((0,), dtype=np.float32)
                if channels > 1:
                    data = data_1d.reshape(-1, channels)
                else:
                    data = data_1d.reshape(-1, 1)
                return data, sr
        except Exception:
            pass

    raise ValueError(f"Cannot load {path}. Install soundfile (wav/flac/ogg) or audioread (mp3).")


class SoundboardClip:
    __slots__ = ("path", "data", "samplerate", "channels")

    def __init__(self, path: str):
        self.path = path
        data, sr = _load_audio(path)
        self.data = data
        self.samplerate = int(sr)
        self.channels = int(data.shape[1])


class ClipPlayer:
    def __init__(self):
        self._lock = threading.Lock()
        self._active: list[tuple[SoundboardClip, int, float]] = []  # (clip, frame_idx, gain)
        self._last_mixed_any = False

    def trigger(self, clip: SoundboardClip, gain: float, *, exclusive: bool) -> None:
        with self._lock:
            if exclusive:
                self._active.clear()
            self._active.append((clip, 0, float(gain)))

    def stop_all(self) -> None:
        with self._lock:
            self._active.clear()

    def mix_into(self, out_frames: int, out_sr: int, out_channels: int) -> np.ndarray:
        mix = np.zeros((out_frames, out_channels), dtype=np.float32)
        with self._lock:
            self._last_mixed_any = False
            if not self._active:
                return mix

            next_active: list[tuple[SoundboardClip, int, float]] = []
            for clip, idx, gain in self._active:
                # Read enough source frames. If we need resampling, we may need more/less
                # than out_frames to produce out_frames.
                if clip.samplerate > 0:
                    src_needed = max(1, int(np.ceil(out_frames * (clip.samplerate / out_sr))))
                else:
                    src_needed = out_frames

                chunk = clip.data[idx : idx + src_needed]
                if chunk.shape[0] == 0:
                    continue

                if clip.samplerate != out_sr:
                    chunk = _resample_linear(chunk, clip.samplerate, out_sr)

                if clip.channels == 1 and out_channels == 2:
                    chunk = np.repeat(chunk, 2, axis=1)
                elif clip.channels == 2 and out_channels == 1:
                    chunk = np.mean(chunk, axis=1, keepdims=True)
                elif clip.channels != out_channels:
                    continue

                frames = min(out_frames, chunk.shape[0])
                if frames <= 0:
                    continue

                mix[:frames] += chunk[:frames] * gain
                self._last_mixed_any = True

                # Advance source index by the amount we consumed from the source.
                new_idx = idx + src_needed
                if new_idx < clip.data.shape[0]:
                    next_active.append((clip, new_idx, gain))

            self._active = next_active

        return mix

    def last_mixed_any(self) -> bool:
        with self._lock:
            return bool(self._last_mixed_any)


class AudioMixerEngine:
    def __init__(
        self,
        input_device: Optional[int],
        output_device: Optional[int],
        monitor_device: Optional[int] = None,
        samplerate: int = 48000,
        blocksize: int = 128,
        channels_out: int = 2,
        mic_gain: float = 1.0,
        board_gain: float = 1.0,
        monitor_gain: float = 0.0,
        monitor_mic: bool = True,
    ):
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize)
        self.channels_out = int(channels_out)
        self.input_device = input_device
        self.output_device = output_device
        self.monitor_device = monitor_device

        self.mic_gain = float(mic_gain)
        self.board_gain = float(board_gain)
        self.monitor_gain = float(monitor_gain)
        self.monitor_mic = bool(monitor_mic)

        self.clip_player = ClipPlayer()

        self._q_in: queue.Queue[np.ndarray] = queue.Queue(maxsize=8)
        self._last_mic: Optional[np.ndarray] = None

        self._stop = threading.Event()
        self._out_stream: Optional[sd.OutputStream] = None
        self._mon_stream: Optional[sd.OutputStream] = None
        self._in_stream: Optional[sd.InputStream] = None

        self._mix_buf = np.zeros((self.blocksize, self.channels_out), dtype=np.float32)
        self._mon_buf = np.zeros((self.blocksize, self.channels_out), dtype=np.float32)
        self._mix_lock = threading.Lock()

        self._rb_lock = threading.Lock()
        self._rb = np.zeros((self.samplerate // 2, self.channels_out), dtype=np.float32)  # ~500ms
        self._rb_w = 0
        self._rb_r = 0

    def start(self) -> None:
        self._stop.clear()

        # Reset state to avoid pops/glitches when restarting with a new blocksize.
        with self._rb_lock:
            self._rb = np.zeros((self.samplerate // 2, self.channels_out), dtype=np.float32)
            self._rb_w = 0
            self._rb_r = 0

        with self._mix_lock:
            self._mix_buf = np.zeros((self.blocksize, self.channels_out), dtype=np.float32)
            self._mon_buf = np.zeros((self.blocksize, self.channels_out), dtype=np.float32)

        while True:
            try:
                self._q_in.get_nowait()
            except queue.Empty:
                break

        self._last_mic = None

        def in_cb(indata: np.ndarray, frames: int, time_info, status) -> None:
            if status:
                pass
            if self._stop.is_set():
                return
            if indata.shape[0] != frames:
                return
            try:
                self._q_in.put_nowait(_to_float32(indata.copy()))
            except queue.Full:
                pass

        def _compute_mix(frames: int) -> tuple[np.ndarray, np.ndarray]:
            mic = None
            try:
                mic = self._q_in.get_nowait()
            except queue.Empty:
                pass

            if mic is not None:
                self._last_mic = mic
            else:
                mic = self._last_mic

            mic_mix = np.zeros((frames, self.channels_out), dtype=np.float32)
            board_mix = np.zeros((frames, self.channels_out), dtype=np.float32)

            if mic is not None:
                mic = mic[:, :1] if mic.shape[1] >= 1 else mic
                if self.channels_out == 2:
                    mic = np.repeat(mic[:, :1], 2, axis=1)
                if mic.shape[0] >= frames:
                    mic_mix += mic[:frames] * self.mic_gain
                else:
                    mic_mix[: mic.shape[0]] += mic * self.mic_gain

            board_mix += self.clip_player.mix_into(frames, self.samplerate, self.channels_out) * self.board_gain

            mix = mic_mix + board_mix

            # Hard safety limiter to avoid ear-piercing bursts if a buffer ever goes NaN/Inf
            mix = np.nan_to_num(mix, nan=0.0, posinf=0.0, neginf=0.0)
            board_mix = np.nan_to_num(board_mix, nan=0.0, posinf=0.0, neginf=0.0)

            # Soft clip then hard clamp
            mix = np.tanh(mix)
            mix = np.clip(mix, -0.98, 0.98)

            board_out = np.tanh(board_mix)
            board_out = np.clip(board_out, -0.98, 0.98)

            with self._mix_lock:
                if self._mix_buf.shape[0] != frames:
                    self._mix_buf = np.zeros((frames, self.channels_out), dtype=np.float32)
                    self._mon_buf = np.zeros((frames, self.channels_out), dtype=np.float32)

                self._mix_buf[:] = mix
                if self.monitor_mic:
                    self._mon_buf[:] = mix
                else:
                    self._mon_buf[:] = board_out

            # Feed monitor ring buffer from the monitor mix, time-aligned to the main output callback.
            with self._rb_lock:
                rb = self._rb
                n = frames
                if n >= rb.shape[0]:
                    # If someone sets huge blocksize, just keep the tail.
                    src = self._mon_buf[-rb.shape[0] :]
                    rb[:] = src
                    self._rb_w = 0
                    self._rb_r = 0
                else:
                    end = self._rb_w + n
                    if end <= rb.shape[0]:
                        rb[self._rb_w : end] = self._mon_buf[:n]
                    else:
                        first = rb.shape[0] - self._rb_w
                        rb[self._rb_w :] = self._mon_buf[:first]
                        rb[: end - rb.shape[0]] = self._mon_buf[first:n]
                    self._rb_w = end % rb.shape[0]
                    # If writer catches reader, move reader forward (drop old audio).
                    if self._rb_w == self._rb_r:
                        self._rb_r = (self._rb_r + n) % rb.shape[0]

            return mix, board_out

        def out_cb(outdata: np.ndarray, frames: int, time_info, status) -> None:
            if status:
                # In case of underflow/overflow, prefer silence over garbage.
                outdata[:] = np.zeros((frames, self.channels_out), dtype=np.float32)
                return
            mix, _ = _compute_mix(frames)
            outdata[:] = mix

        def mon_cb(outdata: np.ndarray, frames: int, time_info, status) -> None:
            if status:
                outdata[:] = np.zeros((frames, self.channels_out), dtype=np.float32)
                return
            if self.monitor_gain <= 0.0:
                outdata[:] = np.zeros((frames, self.channels_out), dtype=np.float32)
                return

            with self._rb_lock:
                rb = self._rb
                avail = (self._rb_w - self._rb_r) % rb.shape[0]
                if avail < frames:
                    out = np.zeros((frames, self.channels_out), dtype=np.float32)
                    n = avail
                else:
                    out = np.empty((frames, self.channels_out), dtype=np.float32)
                    n = frames

                if n > 0:
                    end = self._rb_r + n
                    if end <= rb.shape[0]:
                        out[:n] = rb[self._rb_r : end]
                    else:
                        first = rb.shape[0] - self._rb_r
                        out[:first] = rb[self._rb_r :]
                        out[first:n] = rb[: end - rb.shape[0]]
                    self._rb_r = end % rb.shape[0]

            outdata[:] = out * self.monitor_gain

        self._out_stream = sd.OutputStream(
            device=self.output_device,
            samplerate=self.samplerate,
            channels=self.channels_out,
            dtype="float32",
            blocksize=self.blocksize,
            callback=out_cb,
            latency="low",
        )

        if self.monitor_device is not None:
            self._mon_stream = sd.OutputStream(
                device=self.monitor_device,
                samplerate=self.samplerate,
                channels=self.channels_out,
                dtype="float32",
                blocksize=self.blocksize,
                callback=mon_cb,
                latency="low",
            )

        self._in_stream = sd.InputStream(
            device=self.input_device,
            samplerate=self.samplerate,
            channels=1,
            dtype="float32",
            blocksize=self.blocksize,
            callback=in_cb,
            latency="low",
        )

        self._out_stream.start()
        if self._mon_stream is not None:
            self._mon_stream.start()
        self._in_stream.start()

    def stop(self) -> None:
        self._stop.set()
        for s in (self._in_stream, self._mon_stream, self._out_stream):
            try:
                if s is not None:
                    s.stop()
                    s.close()
            except Exception:
                pass
        self._in_stream = None
        self._mon_stream = None
        self._out_stream = None

    def set_mic_gain(self, gain: float) -> None:
        self.mic_gain = float(gain)

    def set_board_gain(self, gain: float) -> None:
        self.board_gain = float(gain)

    def set_monitor_gain(self, gain: float) -> None:
        self.monitor_gain = float(gain)

    def set_monitor_mic(self, enabled: bool) -> None:
        self.monitor_mic = bool(enabled)

    def stop_all_clips(self) -> None:
        self.clip_player.stop_all()

    def trigger_clip(self, clip: SoundboardClip, gain: float = 1.0, *, exclusive: bool) -> None:
        self.clip_player.trigger(clip, gain, exclusive=exclusive)

    def last_soundboard_active(self) -> bool:
        return self.clip_player.last_mixed_any()
