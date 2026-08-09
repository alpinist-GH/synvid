"""Deterministic, hard-cut Story Mode composition over immutable scene media."""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable

from .stories import StoryError


class StoryComposeError(RuntimeError):
    pass


def compose_hard_cuts(story: dict, outputs: Path, destination: Path, *, target_facts: dict | None = None,
                      cancelled: Callable[[], bool] = lambda: False) -> list[str]:
    """Normalize selected scene movies and concatenate them without rewriting sources.

    The caller owns a partial directory.  This deliberately accepts only the
    current narration descendant (or, for silent scenes, current clip) and
    records every contributing output ID for explicit multi-source lineage.
    """
    if not story.get("scenes"):
        raise StoryComposeError("a story needs at least one approved scene")
    if target_facts is None:
        target_facts = {"width": None, "height": None, "fps": 24}
    width, height, fps = (target_facts.get(key) for key in ("width", "height", "fps"))
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (fps,)) or any(value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0) for value in (width, height)):
        raise StoryComposeError("story composition media facts are invalid")
    inputs: list[tuple[Path, dict]] = []; ids: list[str] = []
    for scene in story["scenes"]:
        if cancelled(): raise InterruptedError("story composition cancelled")
        if not scene.get("approved"): raise StoryComposeError("all story scenes must be approved before composition")
        artifacts = scene.get("artifacts", {})
        selected = artifacts.get("narration") or artifacts.get("clip")
        if not isinstance(selected, dict) or not isinstance(selected.get("output_id"), str):
            raise StoryComposeError("every approved scene needs a current clip or narration")
        output_id = selected["output_id"]
        path = outputs / output_id / "video.mp4"
        if not path.is_file(): raise StoryComposeError("a current scene artifact is unavailable")
        shot = scene.get("shot", {})
        if not isinstance(shot, dict) or shot.get("transition", "hard_cut") != "hard_cut": raise StoryComposeError("story shot is invalid")
        inputs.append((path, shot)); ids.append(output_id)
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        # Normalize every segment first.  The concat demuxer then cannot copy
        # incompatible video/audio layouts by accident.
        normalized = []
        for index, (source, shot) in enumerate(inputs):
            target = destination.parent / f"segment-{index}.mp4"
            start = float(shot.get("trim_start_seconds", 0.0)); end = float(shot.get("trim_end_seconds", 0.0))
            command = [ffmpeg, "-y", "-i", str(source)]
            if start: command += ["-ss", f"{start:.6f}"]
            if end: command += ["-t", f"{end - start:.6f}"]
            command += ["-map", "0:v:0"]
            if not shot.get("narration_muted", False): command += ["-map", "0:a?"]
            else: command += ["-an"]
            if width is not None and height is not None:
                command += ["-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"]
            command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), "-c:a", "aac", "-ar", "48000", "-ac", "2", str(target)]
            subprocess.run(command, check=True, capture_output=True, text=True)
            normalized.append(target)
        listing = destination.parent / "concat.txt"
        listing.write_text("".join("file '" + str(path).replace("'", "'\\''") + "'\n" for path in normalized))
        subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(destination)], check=True, capture_output=True, text=True)
    except (ImportError, OSError, subprocess.SubprocessError) as error:
        destination.unlink(missing_ok=True)
        raise StoryComposeError("could not compose the story movie") from error
    if not destination.is_file() or destination.stat().st_size == 0: raise StoryComposeError("story composition produced no movie")
    return ids
