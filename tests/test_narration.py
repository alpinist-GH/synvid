import json
import sys
import tempfile
import threading
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from worker.narration import KokoroNarrator, NarrationError, pad_or_reject_wav, replace_audio, synthesize_segmented, wav_duration_seconds, write_srt
from worker.paths import AppPaths
from worker.providers.fake import FakeProvider
from worker.resources import Estimate
from worker.service import GenerationService


class FakeNarrator:
    def __init__(self, seconds=0.25):
        self.seconds = seconds
        self.unloaded = False

    def synthesize(self, text, destination, cancelled):
        if cancelled():
            raise InterruptedError("cancelled")
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(8000)
            output.writeframes(b"\x01\0" * round(8000 * self.seconds))

    def unload(self):
        self.unloaded = True


class NarrationTests(unittest.TestCase):
    def test_kokoro_uses_the_reviewed_release_asset_names_and_stock_voice(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp)
            (snapshot / "kokoro-v1.0.fp16.onnx").write_bytes(b"model")
            (snapshot / "voices-v1.0.bin").write_bytes(b"voices")
            calls = []

            class FakeKokoro:
                def __init__(self, model, voices):
                    calls.append(("init", model, voices))

                def create(self, text, voice, lang):
                    calls.append(("create", text, voice, lang))
                    return [0.0, 0.5, -0.5], 24000

            output = snapshot / "voice.wav"
            with patch.dict(sys.modules, {"kokoro_onnx": types.SimpleNamespace(Kokoro=FakeKokoro)}):
                KokoroNarrator(snapshot).synthesize("Hello", output, lambda: False)
            self.assertEqual(calls[0][1:], (str(snapshot / "kokoro-v1.0.fp16.onnx"), str(snapshot / "voices-v1.0.bin")))
            self.assertEqual(calls[1], ("create", "Hello", "af_bella", "en-us"))
            self.assertEqual(wav_duration_seconds(output), 3 / 24000)

    def test_short_wav_is_padded_to_the_video_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            wav = Path(temp) / "voice.wav"
            FakeNarrator(0.25).synthesize("test", wav, lambda: False)
            facts = pad_or_reject_wav(wav, 1.0)
            self.assertEqual(facts["speech_duration_seconds"], 0.25)
            self.assertEqual(wav_duration_seconds(wav), 1.0)

    def test_long_wav_is_rejected_with_measured_durations(self):
        with tempfile.TemporaryDirectory() as temp:
            wav = Path(temp) / "voice.wav"
            FakeNarrator(1.2).synthesize("test", wav, lambda: False)
            with self.assertRaisesRegex(NarrationError, r"1.20s.*1.00s"):
                pad_or_reject_wav(wav, 1.0)

    def test_segmented_narration_uses_measured_sentence_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "voice.wav"
            cues = synthesize_segmented(FakeNarrator(0.1), "First. Second!", destination, lambda: False)
            self.assertEqual([cue["text"] for cue in cues], ["First.", "Second!"])
            self.assertAlmostEqual(cues[0]["end"], 0.1); self.assertAlmostEqual(cues[1]["end"], 0.2)
            subtitle = Path(temp) / "captions.srt"; write_srt(subtitle, cues)
            self.assertIn("00:00:00,100 --> 00:00:00,200", subtitle.read_text())

    def test_write_srt_uses_real_newlines_between_multiple_cues(self):
        # A regression test: an earlier version wrote the literal two-character
        # sequence "\n" (backslash, n) instead of an actual line break, which
        # produced a file no SRT player could parse. A substring-only
        # assertion on read_text() cannot distinguish the two, so this checks
        # the raw bytes and the exact expected block structure.
        with tempfile.TemporaryDirectory() as temp:
            subtitle = Path(temp) / "captions.srt"
            write_srt(subtitle, [{"start": 0.0, "end": 1.5, "text": "First cue."}, {"start": 1.5, "end": 3.0, "text": "Second cue."}])
            raw = subtitle.read_bytes()
            self.assertNotIn(b"\\n", raw)
            self.assertEqual(
                raw.decode("utf-8"),
                "1\n00:00:00,000 --> 00:00:01,500\nFirst cue.\n\n"
                "2\n00:00:01,500 --> 00:00:03,000\nSecond cue.\n\n",
            )

    def test_audio_replacement_uses_bundled_ffmpeg_without_a_shell(self):
        with tempfile.TemporaryDirectory() as temp, patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="/fixed/ffmpeg"), patch("worker.narration.subprocess.run") as run:
            source, wav, output = (Path(temp) / name for name in ("source.mp4", "voice.wav", "output.mp4"))
            source.write_bytes(b"source"); wav.write_bytes(b"wav")
            def produce(command, **_kwargs): output.write_bytes(b"output")
            run.side_effect = produce
            replace_audio(source, wav, output, 1.0)
            command = run.call_args.args[0]
            self.assertEqual(command[0], "/fixed/ffmpeg")
            self.assertIn("-map", command); self.assertIn("0:v:0", command); self.assertIn("1:a:0", command)
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_narration_creates_immutable_descendant_and_unloads_model(self):
        with tempfile.TemporaryDirectory() as temp:
            narrator = FakeNarrator(0.25)
            service = GenerationService(AppPaths.under(Path(temp)), FakeProvider(), Estimate(1, True), narrator=narrator)
            source_id = "11111111-1111-1111-1111-111111111111"
            source_dir = service.paths.outputs / source_id; source_dir.mkdir(parents=True)
            (source_dir / "video.mp4").write_bytes(b"source")
            (source_dir / "metadata.json").write_text(json.dumps({"request": {"capability": "video_generation", "width": 64, "height": 64, "frames": 8, "fps": 8}}))
            terminal = threading.Event(); received = []
            def mux(_source, _wav, destination, _duration): destination.write_bytes(b"narrated")
            with patch("worker.service.replace_audio", side_effect=mux):
                service.submit_narration({"source_output_id": source_id, "text": "A brief narration."}, lambda _job: None, lambda job, output: (received.append((job, output)), terminal.set()))
                self.assertTrue(terminal.wait(2))
            output_id = received[0][1]["output_id"]
            metadata = json.loads((service.paths.outputs / output_id / "metadata.json").read_text())
            self.assertEqual(metadata["lineage"], [{"output_id": source_id, "relation": "narrated_from"}])
            self.assertEqual(metadata["provider"], "kokoro-onnx")
            self.assertEqual(metadata["provider_revision"], "0.5.0")
            self.assertTrue((source_dir / "video.mp4").is_file())
            self.assertTrue((service.paths.outputs / output_id / "video.mp4").is_file())
            self.assertFalse((service.paths.outputs / output_id / "narration.wav").exists())
            self.assertTrue(narrator.unloaded)
            self.assertTrue(service.provider.unloaded)

    def test_export_keeps_an_optional_narration_track(self):
        with tempfile.TemporaryDirectory() as temp, patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="/fixed/ffmpeg"), patch("worker.service.subprocess.run") as run:
            service = GenerationService(AppPaths.under(Path(temp)), FakeProvider(), Estimate(1, True))
            source = service.paths.outputs / "11111111-1111-1111-1111-111111111111"
            source.mkdir(); (source / "video.mp4").write_bytes(b"source")
            temporary = source / "exports" / "high.partial.mp4"
            def produce(command, **_kwargs):
                temporary.parent.mkdir(exist_ok=True); temporary.write_bytes(b"export")
            run.side_effect = produce
            service.export(source.name, "high")
            command = run.call_args.args[0]
            self.assertIn("0:a?", command)
            self.assertNotIn("-an", command)
