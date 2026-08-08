"""PyInstaller entry point for the contained SynVid worker."""

from worker.__main__ import serve


if __name__ == "__main__":
    raise SystemExit(serve())
