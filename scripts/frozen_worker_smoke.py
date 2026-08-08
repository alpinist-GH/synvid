#!/usr/bin/env python3
"""Exercise a relocated PyInstaller worker without Homebrew on PATH."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import time


def send(process: subprocess.Popen[str], request_id: str, kind: str, payload: dict) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps({"version": 1, "request_id": request_id, "kind": kind, "payload": payload}) + "\n")
    process.stdin.flush()


def receive(selector: selectors.BaseSelector, timeout: float) -> dict:
    events = selector.select(timeout)
    if not events:
        raise TimeoutError("frozen worker did not respond before the timeout")
    line = events[0][0].fileobj.readline()
    if not line:
        raise RuntimeError("frozen worker exited unexpectedly")
    return json.loads(line)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", required=True, type=Path)
    parser.add_argument("--app-support", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--story-planner", action="store_true", help="exercise Draft scenes locally through frozen IPC")
    parser.add_argument("--model", choices=("ltx-video", "flux-schnell"), default="ltx-video")
    parser.add_argument("--recipe", choices=("Draft", "Balanced", "High"), default="Draft")
    parser.add_argument("--narrate-output-id", help="add a short stock-voice narration to an existing owned video output")
    args = parser.parse_args()
    environment = {**os.environ, "PATH": "/usr/bin:/bin", "SYNVID_APP_SUPPORT": str(args.app_support)}
    process = subprocess.Popen([str(args.worker)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        send(process, "frozen-hello", "hello", {"protocol_min": 1, "protocol_max": 1})
        hello = receive(selector, 20)
        if hello.get("kind") != "hello_ack":
            raise RuntimeError(f"invalid frozen-worker handshake: {hello}")
        if args.status_only:
            send(process, "frozen-status", "get_status", {})
            status = receive(selector, 20)
            models = status.get("payload", {}).get("available_models", {})
            if "ltx-video" not in models or "flux-schnell" not in models:
                raise RuntimeError(f"bundled worker did not advertise measured providers: {status}")
            print(json.dumps(status, sort_keys=True))
            return 0
        if args.story_planner:
            send(process, "frozen-story-create", "story_create", {
                "title": "Frozen planner smoke", "premise": "A lone boat reaches shore before a storm.",
                "style_bible": "cinematic watercolor", "aspect_ratio": "16:9",
            })
            story = receive(selector, 20)
            payload = story.get("payload", {})
            if story.get("kind") != "status" or not isinstance(payload.get("story_id"), str):
                raise RuntimeError(f"frozen worker could not create story: {story}")
            send(process, "frozen-story-draft", "story_draft_scenes", {
                "story_id": payload["story_id"], "expected_revision": payload["revision"], "count": 3,
            })
            drafted = receive(selector, args.timeout)
            scenes = drafted.get("payload", {}).get("scenes")
            if drafted.get("kind") != "status" or not isinstance(scenes, list) or len(scenes) != 3:
                raise RuntimeError(f"frozen planner draft failed: {drafted}")
            print(json.dumps(drafted, sort_keys=True))
            return 0
        if args.narrate_output_id:
            send(process, "frozen-narrate", "narrate", {"source_output_id": args.narrate_output_id, "text": "Hi."})
            accepted = receive(selector, 20)
            if accepted.get("kind") != "accepted":
                raise RuntimeError(f"frozen worker rejected narration: {accepted}")
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                event = receive(selector, deadline - time.monotonic())
                if event.get("kind") == "terminal":
                    if event.get("payload", {}).get("state") != "succeeded":
                        raise RuntimeError(f"frozen narration failed: {event}")
                    print(json.dumps(event, sort_keys=True))
                    return 0
            raise TimeoutError("frozen narration did not complete")
        send(process, "frozen-generate", "generate", {"model_id": args.model, "prompt": "A yellow flower gently moving in a spring breeze", "seed": 42, "recipe": args.recipe})
        accepted = receive(selector, 20)
        if accepted.get("kind") != "accepted":
            raise RuntimeError(f"frozen worker rejected generation: {accepted}")
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            event = receive(selector, deadline - time.monotonic())
            if event.get("kind") == "terminal":
                if event.get("payload", {}).get("state") != "succeeded":
                    raise RuntimeError(f"frozen generation failed: {event}")
                print(json.dumps(event, sort_keys=True))
                return 0
        raise TimeoutError("frozen generation did not complete")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        if process.stderr is not None:
            stderr = process.stderr.read()
            if stderr:
                print(stderr, file=os.sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
