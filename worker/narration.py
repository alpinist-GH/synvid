"""Local, bounded narration and audio replacement for immutable videos."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import wave
import re
from typing import Callable, Protocol


class NarrationError(ValueError):
    pass


class Narrator(Protocol):
    """Writes one mono WAV beneath the caller-owned output directory."""

    def synthesize(self, text: str, destination: Path, cancelled: Callable[[], bool]) -> None: ...

    def unload(self) -> None: ...


class KokoroNarrator:
    """Small stock-voice ONNX narrator; models stay under SynVid storage."""

    def __init__(self, snapshot: Path):
        self.snapshot = snapshot
        self._model = None

    def synthesize(self, text: str, destination: Path, cancelled: Callable[[], bool]) -> None:
        if cancelled():
            raise InterruptedError("narration cancelled")
        model_path = self.snapshot / "kokoro-v1.0.fp16.onnx"
        voices_path = self.snapshot / "voices-v1.0.bin"
        if not model_path.is_file() or not voices_path.is_file():
            raise NarrationError("Kokoro narration model is not installed in SynVid storage")
        try:
            from kokoro_onnx import Kokoro
        except ImportError as error:
            raise NarrationError("Kokoro narration dependency is unavailable") from error
        if self._model is None:
            self._model = Kokoro(str(model_path), str(voices_path))
        samples, sample_rate = self._model.create(text, voice="af_bella", lang="en-us")
        if cancelled():
            raise InterruptedError("narration cancelled")
        _write_array_wav(destination, samples, int(sample_rate))

    def unload(self) -> None:
        self._model = None


def wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as source:
            if source.getframerate() <= 0:
                raise NarrationError("narration WAV has an invalid sample rate")
            return source.getnframes() / source.getframerate()
    except (OSError, wave.Error) as error:
        raise NarrationError("narration provider did not produce a valid WAV") from error


def pad_or_reject_wav(path: Path, video_duration: float, tolerance_seconds: float = 0.05) -> dict[str, float]:
    """Match narration to the authoritative video timeline without retiming."""
    speech_duration = wav_duration_seconds(path)
    if speech_duration > video_duration + tolerance_seconds:
        raise NarrationError(
            f"Narration is {speech_duration:.2f}s but the video is {video_duration:.2f}s; shorten the script."
        )
    try:
        with wave.open(str(path), "rb") as source:
            params = source.getparams()
            frames = source.readframes(source.getnframes())
    except (OSError, wave.Error) as error:
        raise NarrationError("narration provider did not produce a valid WAV") from error
    target_frames = round(video_duration * params.framerate)
    # A tiny encoder/mux tolerance is allowed by the product contract.  Larger
    # speech is rejected above; this only discards sub-tolerance sample drift.
    frames = frames[:target_frames * params.sampwidth * params.nchannels]
    missing = target_frames - min(target_frames, len(frames) // (params.sampwidth * params.nchannels))
    if missing > 0:
        frames += b"\0" * (missing * params.sampwidth * params.nchannels)
    try:
        with wave.open(str(path), "wb") as destination:
            destination.setparams(params)
            destination.writeframes(frames)
    except (OSError, wave.Error) as error:
        raise NarrationError("could not prepare narration audio") from error
    return {"speech_duration_seconds": speech_duration, "video_duration_seconds": video_duration}


def replace_audio(source: Path, narration_wav: Path, destination: Path, video_duration: float) -> None:
    """Use the bundled imageio-ffmpeg binary, never a shell or user path."""
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run(
            [ffmpeg, "-y", "-i", str(source), "-i", str(narration_wav),
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
             "-t", f"{video_duration:.6f}", "-movflags", "+faststart", str(destination)],
            check=True, capture_output=True, text=True,
        )
    except (ImportError, OSError, subprocess.SubprocessError) as error:
        destination.unlink(missing_ok=True)
        raise NarrationError("could not replace the video audio track") from error
    if not destination.is_file() or destination.stat().st_size == 0:
        raise NarrationError("audio replacement did not produce a video")


def synthesize_segmented(narrator: Narrator, text: str, destination: Path, cancelled: Callable[[], bool]) -> list[dict[str, object]]:
    """Synthesize sentence WAVs, join their exact samples, and return SRT cues."""
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", text.strip()) if item.strip()]
    if not sentences: return []
    cues: list[dict[str, object]] = []; params = None; raw = bytearray(); offset = 0.0
    for index, sentence in enumerate(sentences):
        if cancelled(): raise InterruptedError("narration cancelled")
        part = destination.with_name(f"sentence-{index}.wav"); narrator.synthesize(sentence, part, cancelled)
        try:
            with wave.open(str(part), "rb") as source:
                current = source.getparams(); frames = source.readframes(source.getnframes())
        except (OSError, wave.Error) as error: raise NarrationError("narration provider did not produce a valid WAV") from error
        finally: part.unlink(missing_ok=True)
        if params is None: params = current
        elif (current.nchannels, current.sampwidth, current.framerate, current.comptype) != (params.nchannels, params.sampwidth, params.framerate, params.comptype): raise NarrationError("narration sentence formats differ")
        duration = len(frames) / (current.framerate * current.sampwidth * current.nchannels)
        cues.append({"start": offset, "end": offset + duration, "text": sentence}); offset += duration; raw.extend(frames)
    try:
        with wave.open(str(destination), "wb") as output:
            output.setparams(params); output.writeframes(bytes(raw))
    except (OSError, wave.Error) as error: raise NarrationError("could not join narration sentences") from error
    return cues


def write_srt(path: Path, cues: list[dict[str, object]]) -> None:
    def stamp(seconds: float) -> str:
        milliseconds = round(seconds * 1000); hours, milliseconds = divmod(milliseconds, 3_600_000); minutes, milliseconds = divmod(milliseconds, 60_000); seconds, milliseconds = divmod(milliseconds, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"
    path.write_text("".join(f"{number}\\n{stamp(float(cue['start']))} --> {stamp(float(cue['end']))}\\n{str(cue['text'])}\\n\\n" for number, cue in enumerate(cues, 1)), encoding="utf-8")


def _write_array_wav(destination: Path, samples: object, sample_rate: int) -> None:
    try:
        import numpy
        pcm = (numpy.clip(numpy.asarray(samples).reshape(-1), -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(sample_rate); output.writeframes(pcm)
    except (OSError, ValueError, wave.Error) as error:
        raise NarrationError("Kokoro did not return valid audio samples") from error
