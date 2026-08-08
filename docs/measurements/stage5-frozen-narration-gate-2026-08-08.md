# Stage 5 — frozen narration gate (2026-08-08)

The frozen one-folder worker initially failed when the installed `narrate`
request reached Kokoro: PyInstaller had omitted `language_tags` JSON resources,
the `espeakng_loader` data directory, and its native `libespeak-ng.dylib`.
The worker build now collects both dependency packages and explicitly places
the eSpeak data files beside its native library, as required by that library's
own resource lookup. The local validation bundle was rebuilt with those
verified package resources and copied into the Tauri app resource tree before
the DMG was created.

The repaired frozen worker received `narrate` for the immutable LTX output
`c5260af3-1ef3-49ec-b566-b62789f38135` and returned one successful terminal
event. It atomically created descendant
`50ad87de-2b01-4d91-b48a-9b82d289ca17` with `narrated_from` lineage. `ffprobe`
reported one H.264 video stream and one AAC audio stream, with unchanged
1.125-second container duration. Metadata recorded 0.597333 seconds of speech,
so the required silence padding was applied.

This closes the frozen runtime-data defect and proves the automatic stream and
duration checks. It does not replace the remaining manual acceptance: listen
to the installed-app result, perform the VoiceOver/keyboard review, and repeat
the flow from a clean macOS account.
