"""Read exact Pin text and validate the private bytes that will be uploaded."""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from .errors import PublishError

MAX_VIDEO_BYTES = 200_000_000


def read_text(title_file=None, description_file=None, metadata_file=None) -> tuple[str, str]:
    if metadata_file and (title_file or description_file):
        raise PublishError("INVALID_ARGUMENTS", "Metadata and manual title/description sources are mutually exclusive.")
    if metadata_file:
        try:
            from metadata_automation import validate
            envelope = json.loads(Path(metadata_file).expanduser().read_text(encoding="utf-8"))
            if not isinstance(envelope, dict) or envelope.get("schema_version") != "2.0.0":
                raise PublishError("UNSUPPORTED_METADATA_VERSION", "Pinterest requires the complete metadata-automation schema_version 2.0.0 envelope.")
            validate(envelope)
            title, description = envelope["platforms"]["pinterest"]["title"], envelope["platforms"]["pinterest"]["description"]
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            raise PublishError("INVALID_METADATA", f"Complete metadata envelope is invalid ({type(exc).__name__}).") from exc
        validate_text(title, description)
        return title, description
    if not title_file or not description_file:
        raise PublishError("INVALID_ARGUMENTS", "Supply both --title-file and --description-file.")
    try:
        with Path(title_file).expanduser().open(encoding="utf-8", newline="") as stream:
            title = stream.read()
        with Path(description_file).expanduser().open(encoding="utf-8", newline="") as stream:
            description = stream.read()
    except (OSError, UnicodeError) as exc:
        raise PublishError("INVALID_TEXT", "Title and description must be readable UTF-8 files.") from exc
    validate_text(title, description)
    return title, description


def validate_text(title: str, description: str):
    if not title.strip() or not description.strip() or len(title) > 100 or len(description) > 800:
        raise PublishError("INVALID_TEXT", "Use a nonempty title of at most 100 characters and description of at most 800; text is never truncated.")


def copy_video(source: str, destination: Path) -> str:
    if Path(source).suffix.lower() not in {".mp4", ".m4v"}:
        raise PublishError("INVALID_MEDIA", "Use a local MP4 or M4V video.")
    created, completed = False, False
    try:
        with os.fdopen(os.open(source, os.O_RDONLY | os.O_NONBLOCK), "rb") as incoming:
            source_stat = os.fstat(incoming.fileno())
            if not stat.S_ISREG(source_stat.st_mode):
                raise PublishError("INVALID_MEDIA", "Video must be a readable regular local file.")
            if source_stat.st_size > MAX_VIDEO_BYTES:
                raise PublishError("INVALID_MEDIA", "Pinterest web videos must be at most 200,000,000 bytes.")
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(descriptor, "wb") as outgoing:
                total = 0
                while chunk := incoming.read(min(1024 * 1024, MAX_VIDEO_BYTES - total + 1)):
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise PublishError("INVALID_MEDIA", "Video grew beyond Pinterest's 200,000,000 byte limit while copying.")
                    outgoing.write(chunk)
        with destination.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        completed = True
        return digest
    except OSError as exc:
        raise PublishError("INVALID_MEDIA", "Video must be a readable regular local file.") from exc
    finally:
        if created and not completed:
            # Keep no partial snapshot after overflow, interruption or failed hashing.
            # An existing O_EXCL destination belongs to a different operation.
            destination.unlink(missing_ok=True)


def probe_video(path: Path):
    if not 0 < path.stat().st_size <= MAX_VIDEO_BYTES:
        raise PublishError("INVALID_MEDIA", "Video must be nonempty and at most 200,000,000 bytes.")
    try:
        process = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, timeout=30, check=True)
        document = json.loads(process.stdout)
        video = next(item for item in document["streams"] if item["codec_type"] == "video")
        if video["codec_name"] not in {"h264", "hevc"} or not 4 <= float(document["format"]["duration"]) <= 300 or min(video["width"], video["height"]) < 1:
            raise PublishError("INVALID_MEDIA", "Video must contain H.264/H.265, a duration of 4–300 seconds and positive dimensions.")
        container = document["format"]
        # ffprobe joins repeated container tags with a semicolon, and some
        # muxers write major_brand twice ("isom;isom"). The brand is the value,
        # not the repetition, so read the first one.
        brand = container.get("tags", {}).get("major_brand", "").split(";")[0].strip()
        mp4_brands = {"isom", "mp41", "mp42", "M4V", "M4VH", "M4VP", "avc1", "hvc1", "hev1", "MSNV", "dash", "cmfc", "cmfs"}
        if "mov" not in container["format_name"].split(",") or (brand not in mp4_brands and not (len(brand) == 4 and brand.startswith("iso") and brand[3] in "23456789")):
            raise PublishError("INVALID_MEDIA", "Video bytes must be an MP4/M4V container.")
    except FileNotFoundError as exc:
        raise PublishError("FFPROBE_MISSING", "Install ffmpeg (ffprobe) before publishing.", 3) from exc
    except (subprocess.SubprocessError, ValueError, KeyError, StopIteration, TypeError) as exc:
        raise PublishError("INVALID_MEDIA", "ffprobe could not validate this video.") from exc
