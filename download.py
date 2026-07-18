"""Download a test video into static/uploads/original/."""
import os
import subprocess
import sys

URL = "https://www.youtube.com/watch?v=p0jDRJ6-xuE"
OUT_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads", "original")


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else URL
    os.makedirs(OUT_DIR, exist_ok=True)
    subprocess.run(
        [
            "yt-dlp",
            "--extractor-args", "youtube:player_client=ios,android",
            "-f", "b[height<=720]/b",
            "--merge-output-format", "mp4",
            "-o", os.path.join(OUT_DIR, "%(title).60s.%(ext)s"),
            url,
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
