# Privacy Policy

**Last updated: 2026-08-11**

SynVid ("AI-Video Synthesizer") is a local, Apple Silicon-only macOS app for
generating and editing images and video with on-device AI models. This
policy describes what data SynVid handles and, just as importantly, what it
does not.

## No account, no telemetry

SynVid requires no account, sign-in, or registration of any kind. There is
no analytics SDK, no crash-reporting service, and no telemetry anywhere in
the app or its bundled worker process.

## What SynVid stores, and where

Everything SynVid produces or downloads is written under your own
`~/Library/Application Support/SynVid/` directory on your Mac: generated
images and video, edited media, narration audio, Story Mode project
documents, and application logs. None of this data is uploaded, synced, or
shared by SynVid. Nothing leaves your Mac unless you explicitly export or
share it yourself using the operating system's normal file tools.

## Network access

SynVid makes network calls only for one explicit, user-initiated action:
downloading an AI model you have chosen, over HTTPS from Hugging Face.
Every model download shows its license, size, and pinned revision before
anything transfers, and requires your explicit confirmation. Model
downloads never use a Hugging Face account or API token — they are made
anonymously. Aside from a model download you started, SynVid makes no other
network calls: no background check-ins, no update pings, no advertising or
analytics network traffic.

## Diagnostics (opt-in only)

Settings includes an opt-in Diagnostics panel. Choosing "Preview
diagnostics" builds a small, bounded text bundle (app/OS version, worker
connection state, recent local log lines) with your home-folder path and
any token-shaped string automatically redacted. This bundle is shown to you
in full before you decide whether to save it anywhere. SynVid never
collects or transmits this data automatically — it is generated locally and
only leaves your Mac if you choose to save and share the file yourself.

## Deletion

You control deletion of everything SynVid has made or downloaded. Removing
a model, deleting an output, deleting a story, or clearing temporary files
are all explicit actions you take inside the app. Deleting
`~/Library/Application Support/SynVid/` by hand removes everything SynVid
has ever made or downloaded. Uninstalling the app (moving it to the Trash)
does not by itself delete this data.

## Third-party AI models

SynVid downloads openly licensed AI model weights on demand from Hugging
Face for local, on-device use. SynVid does not send your prompts, images,
or generated media to any third-party service — generation runs entirely
on your Mac using the downloaded model weights.

## Changes to this policy

If this policy changes, the "Last updated" date above will be revised and
the updated text will be published at the same URL.

## Contact

Questions about this policy can be raised via
[GitHub Issues](https://github.com/alpinist-GH/synvid/issues).
