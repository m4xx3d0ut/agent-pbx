from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass
from pathlib import Path
import mimetypes
import os
import shutil
import struct
from typing import Any
import zlib


IGNORED_DIRS = {
    ".git",
    ".codex",
    ".local",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".workerbee",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "runs",
    "state",
    "tmp",
}
IGNORED_NAMES = {
    ".env",
    "coverage.xml",
    "local.env",
}
TEXT_EXTENSIONS = {
    ".cfg",
    ".css",
    ".csv",
    ".env",
    ".gitignore",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".lock",
    ".log",
    ".md",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".tsx",
    ".txt",
    ".ts",
    ".xml",
    ".yaml",
    ".yml",
}
IMAGE_EXTENSIONS = {
    ".apng",
    ".avif",
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}
DEFAULT_LIST_LIMIT = 500
DEFAULT_PREVIEW_BYTES = 256 * 1024
DEFAULT_IMAGE_PREVIEW_BYTES = 4 * 1024 * 1024
IMAGE_PREVIEW_MAX_WIDTH = 64
IMAGE_PREVIEW_MAX_HEIGHT = 24
IMAGE_PREVIEW_CHARS = ".:-=+*#%@"
IMAGE_PREVIEW_MAX_SOURCE_PIXELS = 2_000_000
CHAFA_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class FileBrowserError:
    code: str
    message: str
    remediation: str | None = None

    def as_dict(self) -> dict[str, str]:
        data = {"code": self.code, "message": self.message}
        if self.remediation:
            data["remediation"] = self.remediation
        return data


@dataclass(frozen=True)
class ImagePreview:
    text: str | None = None
    ansi: str | None = None
    format: str | None = None


class AgentFileService:
    def __init__(
        self,
        *,
        list_limit: int = DEFAULT_LIST_LIMIT,
        preview_bytes: int = DEFAULT_PREVIEW_BYTES,
        image_preview_bytes: int = DEFAULT_IMAGE_PREVIEW_BYTES,
        ignored_dirs: set[str] | None = None,
    ) -> None:
        self.list_limit = max(1, list_limit)
        self.preview_bytes = max(1, preview_bytes)
        self.image_preview_bytes = max(self.preview_bytes, image_preview_bytes)
        self.ignored_dirs = ignored_dirs or IGNORED_DIRS

    def list_for_agent(
        self, agent: dict[str, Any], *, path: str = "."
    ) -> dict[str, Any]:
        base = self._base_response(agent, path=path)
        root_result = self._agent_root(agent)
        if isinstance(root_result, FileBrowserError):
            return {**base, "parent": None, "entries": [], "error": root_result.as_dict()}
        root = root_result
        target_result = self._resolve_agent_path(root, path)
        if isinstance(target_result, FileBrowserError):
            return {**base, "cwd": str(root), "parent": None, "entries": [], "error": target_result.as_dict()}
        target = target_result
        if not target.exists():
            error = FileBrowserError(
                "FILE_NOT_FOUND",
                f"path does not exist: {self._display_path(root, target)}",
            )
            return {**base, "cwd": str(root), "parent": None, "entries": [], "error": error.as_dict()}
        if not target.is_dir():
            error = FileBrowserError(
                "NOT_A_DIRECTORY",
                f"path is not a directory: {self._display_path(root, target)}",
            )
            return {**base, "cwd": str(root), "parent": None, "entries": [], "error": error.as_dict()}

        entries: list[dict[str, Any]] = []
        try:
            children = list(target.iterdir())
        except OSError as exc:
            error = FileBrowserError("READ_FAILED", str(exc))
            return {**base, "cwd": str(root), "parent": None, "entries": [], "error": error.as_dict()}

        for child in children:
            if len(entries) >= self.list_limit:
                break
            if child.name in IGNORED_NAMES:
                continue
            if child.name in self.ignored_dirs and child.is_dir():
                continue
            try:
                resolved = child.resolve(strict=True)
            except OSError:
                continue
            if not self._is_inside(root, resolved):
                continue
            entries.append(self._entry(root, child, resolved))

        entries.sort(key=lambda item: (item["kind"] != "directory", item["name"].lower()))
        parent = self._parent_path(root, target)
        return {
            **base,
            "cwd": str(root),
            "path": self._display_path(root, target),
            "parent": parent,
            "entries": entries,
            "error": None,
        }

    def preview_for_agent(
        self, agent: dict[str, Any], *, path: str
    ) -> dict[str, Any]:
        base = self._base_response(agent, path=path)
        root_result = self._agent_root(agent)
        if isinstance(root_result, FileBrowserError):
            return {**base, "kind": "error", "size": None, "mime_type": None, "text": None, "truncated": False, "error": root_result.as_dict()}
        root = root_result
        target_result = self._resolve_agent_path(root, path)
        if isinstance(target_result, FileBrowserError):
            return {**base, "cwd": str(root), "kind": "error", "size": None, "mime_type": None, "text": None, "truncated": False, "error": target_result.as_dict()}
        target = target_result
        if not target.exists():
            error = FileBrowserError(
                "FILE_NOT_FOUND",
                f"path does not exist: {self._display_path(root, target)}",
            )
            return {**base, "cwd": str(root), "kind": "error", "size": None, "mime_type": None, "text": None, "truncated": False, "error": error.as_dict()}
        if target.is_dir():
            error = FileBrowserError(
                "IS_DIRECTORY",
                f"path is a directory: {self._display_path(root, target)}",
            )
            return {**base, "cwd": str(root), "kind": "directory", "size": None, "mime_type": None, "text": None, "truncated": False, "error": error.as_dict()}

        try:
            stat = target.stat()
            with target.open("rb") as handle:
                sample = handle.read(self.preview_bytes + 1)
        except OSError as exc:
            error = FileBrowserError("READ_FAILED", str(exc))
            return {**base, "cwd": str(root), "kind": "error", "size": None, "mime_type": None, "text": None, "truncated": False, "error": error.as_dict()}

        mime_type = guess_mime_type(target)
        dimensions = image_dimensions_from_bytes(sample, target.suffix.lower())
        is_image = is_image_path(target, mime_type)
        is_gif = target.suffix.lower() == ".gif" or sample.startswith(b"GIF8")
        text = decode_text_preview(sample[: self.preview_bytes], target, mime_type)
        image_sample = sample[: self.preview_bytes]
        if is_image and stat.st_size <= self.image_preview_bytes:
            if stat.st_size > len(image_sample):
                try:
                    with target.open("rb") as handle:
                        image_sample = handle.read(self.image_preview_bytes + 1)
                except OSError:
                    image_sample = sample[: self.preview_bytes]
            image_sample = image_sample[: self.image_preview_bytes]
        if dimensions is None and is_image:
            dimensions = image_dimensions_with_pillow(image_sample)
        image_preview = (
            image_preview_from_bytes(
                image_sample,
                target.suffix.lower(),
                path=target if is_image and stat.st_size <= self.image_preview_bytes else None,
            )
            if is_image
            else ImagePreview()
        )
        truncated_limit = self.image_preview_bytes if is_image else self.preview_bytes
        truncated = stat.st_size > truncated_limit
        return {
            **base,
            "cwd": str(root),
            "path": self._display_path(root, target),
            "kind": "file",
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "extension": target.suffix.lower(),
            "mime_type": mime_type,
            "is_text": text is not None,
            "is_image": is_image,
            "is_gif": is_gif,
            "image_width": dimensions[0] if dimensions else None,
            "image_height": dimensions[1] if dimensions else None,
            "image_preview": image_preview.text,
            "image_preview_ansi": image_preview.ansi,
            "image_preview_format": image_preview.format,
            "text": text,
            "truncated": truncated,
            "error": None,
        }

    def _base_response(self, agent: dict[str, Any], *, path: str) -> dict[str, Any]:
        return {
            "agent_id": str(agent.get("agent_id") or ""),
            "cwd": None,
            "path": path or ".",
        }

    def _agent_root(self, agent: dict[str, Any]) -> Path | FileBrowserError:
        metadata = agent.get("metadata")
        cwd = metadata.get("cwd") if isinstance(metadata, dict) else None
        if not isinstance(cwd, str) or not cwd.strip():
            return FileBrowserError(
                "AGENT_CWD_MISSING",
                "agent metadata does not include an absolute cwd",
                "Register the agent with metadata.cwd set to its project directory.",
            )
        cwd_path = Path(cwd).expanduser()
        if not cwd_path.is_absolute():
            return FileBrowserError(
                "AGENT_CWD_NOT_ABSOLUTE",
                f"agent cwd is not absolute: {cwd}",
                "Register the agent with an absolute metadata.cwd.",
            )
        try:
            root = cwd_path.resolve(strict=True)
        except OSError:
            return FileBrowserError(
                "AGENT_CWD_UNAVAILABLE",
                f"agent cwd does not exist or is not accessible: {cwd}",
            )
        if not root.is_dir():
            return FileBrowserError(
                "AGENT_CWD_NOT_DIRECTORY",
                f"agent cwd is not a directory: {cwd}",
            )
        return root

    def _resolve_agent_path(
        self, root: Path, requested: str
    ) -> Path | FileBrowserError:
        clean = (requested or ".").strip() or "."
        requested_path = Path(clean)
        if requested_path.is_absolute():
            return FileBrowserError("PATH_OUTSIDE_CWD", "absolute paths are not allowed")
        if any(part == ".." for part in requested_path.parts):
            return FileBrowserError("PATH_OUTSIDE_CWD", "parent path traversal is not allowed")
        try:
            resolved = (root / requested_path).resolve(strict=False)
        except OSError as exc:
            return FileBrowserError("PATH_INVALID", str(exc))
        if not self._is_inside(root, resolved):
            return FileBrowserError("PATH_OUTSIDE_CWD", "path resolves outside the agent cwd")
        return resolved

    def _entry(self, root: Path, path: Path, resolved: Path) -> dict[str, Any]:
        try:
            stat = path.stat()
        except OSError:
            stat = os.stat_result((0,) * 10)
        if resolved.is_dir():
            kind = "directory"
        elif resolved.is_file():
            kind = "file"
        else:
            kind = "other"
        mime_type = guess_mime_type(path)
        return {
            "name": path.name,
            "path": self._display_path(root, resolved),
            "kind": kind,
            "size": stat.st_size if kind == "file" else None,
            "mtime": stat.st_mtime,
            "extension": path.suffix.lower(),
            "mime_type": mime_type,
            "is_text": kind == "file" and is_text_path(path, mime_type),
            "is_image": kind == "file" and is_image_path(path, mime_type),
            "is_gif": kind == "file" and path.suffix.lower() == ".gif",
        }

    def _display_path(self, root: Path, path: Path) -> str:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return "."
        text = relative.as_posix()
        return text if text else "."

    def _parent_path(self, root: Path, target: Path) -> str | None:
        if target == root:
            return None
        return self._display_path(root, target.parent)

    def _is_inside(self, root: Path, path: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True


def guess_mime_type(path: Path) -> str | None:
    mime_type, _encoding = mimetypes.guess_type(path.name)
    if mime_type:
        return mime_type
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return "text/plain"
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        return f"image/{path.suffix.lower().lstrip('.')}"
    return None


def is_text_path(path: Path, mime_type: str | None) -> bool:
    suffix = path.suffix.lower()
    return suffix in TEXT_EXTENSIONS or bool(mime_type and mime_type.startswith("text/"))


def is_image_path(path: Path, mime_type: str | None) -> bool:
    suffix = path.suffix.lower()
    return suffix in IMAGE_EXTENSIONS or bool(mime_type and mime_type.startswith("image/"))


def decode_text_preview(data: bytes, path: Path, mime_type: str | None) -> str | None:
    if not data:
        return "" if is_text_path(path, mime_type) else None
    if b"\x00" in data[:4096]:
        return None
    encodings = ("utf-8", "utf-16")
    if not is_text_path(path, mime_type):
        encodings = ("utf-8",)
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def image_dimensions_from_bytes(
    data: bytes, suffix: str
) -> tuple[int, int] | None:
    if data.startswith(b"GIF8") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return int(width), int(height)
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if suffix in {".jpg", ".jpeg"}:
        return jpeg_dimensions(data)
    return None


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2 or index + length > len(data):
            return None
        if 0xC0 <= marker <= 0xC3 and length >= 7:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += length
    return None


def image_dimensions_with_pillow(data: bytes) -> tuple[int, int] | None:
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None


def image_preview_from_bytes(
    data: bytes,
    suffix: str,
    *,
    path: Path | None = None,
) -> ImagePreview:
    decoded = decode_image_pixels_with_pillow(data)
    if decoded is None and data.startswith(b"\x89PNG\r\n\x1a\n"):
        decoded = decode_png_pixels(data)
    if decoded is None and data.startswith(b"GIF8"):
        decoded = decode_gif_pixels(data)
    if decoded is None:
        chafa_preview = render_chafa_preview(path) if path is not None else None
        return (
            ImagePreview(ansi=chafa_preview, format="ansi-chafa")
            if chafa_preview
            else ImagePreview()
        )
    width, height, pixels = decoded
    ansi = render_pixels_as_ansi_halfblocks(width, height, pixels)
    text = render_pixels_as_ascii(width, height, pixels)
    return ImagePreview(
        text=text,
        ansi=ansi,
        format="ansi-truecolor-halfblocks" if ansi else "grayscale-ascii",
    )


def decode_image_pixels_with_pillow(
    data: bytes,
) -> tuple[int, int, list[tuple[int, int, int]]] | None:
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            try:
                image.seek(0)
            except EOFError:
                return None
            if image.width <= 0 or image.height <= 0:
                return None
            if image.width * image.height > IMAGE_PREVIEW_MAX_SOURCE_PIXELS:
                image.thumbnail(
                    (IMAGE_PREVIEW_MAX_WIDTH * 4, IMAGE_PREVIEW_MAX_HEIGHT * 8)
                )
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
            background.alpha_composite(rgba)
            rgb = background.convert("RGB")
            pixels = [
                (int(red), int(green), int(blue))
                for red, green, blue in rgb.getdata()
            ]
            return int(rgb.width), int(rgb.height), pixels
    except Exception:
        return None


def render_chafa_preview(path: Path | None) -> str | None:
    chafa = shutil.which("chafa")
    if path is None or chafa is None:
        return None
    try:
        completed = subprocess.run(
            [
                chafa,
                "--size",
                f"{IMAGE_PREVIEW_MAX_WIDTH}x{IMAGE_PREVIEW_MAX_HEIGHT}",
                "--animate",
                "off",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=CHAFA_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = completed.stdout.strip()
    if completed.returncode != 0 or not output:
        return None
    return output


def render_pixels_as_ansi_halfblocks(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int]],
    *,
    max_width: int = IMAGE_PREVIEW_MAX_WIDTH,
    max_height: int = IMAGE_PREVIEW_MAX_HEIGHT,
) -> str | None:
    if width <= 0 or height <= 0 or not pixels:
        return None
    scaled_width, scaled_height = preview_pixel_dimensions(
        width,
        height,
        max_width=max_width,
        max_height=max_height * 2,
    )
    rows: list[str] = []
    for top_y in range(0, scaled_height, 2):
        cells: list[str] = []
        for out_x in range(scaled_width):
            top = sample_region_rgb(
                width,
                height,
                pixels,
                out_x,
                top_y,
                scaled_width,
                scaled_height,
            )
            bottom_y = min(top_y + 1, scaled_height - 1)
            bottom = sample_region_rgb(
                width,
                height,
                pixels,
                out_x,
                bottom_y,
                scaled_width,
                scaled_height,
            )
            cells.append(
                "\x1b[38;2;"
                f"{top[0]};{top[1]};{top[2]}m"
                "\x1b[48;2;"
                f"{bottom[0]};{bottom[1]};{bottom[2]}m▀"
            )
        rows.append("".join(cells) + "\x1b[0m")
    preview = "\n".join(rows).rstrip()
    return preview or None


def render_pixels_as_ascii(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int]],
    *,
    max_width: int = IMAGE_PREVIEW_MAX_WIDTH,
    max_height: int = IMAGE_PREVIEW_MAX_HEIGHT,
) -> str | None:
    if width <= 0 or height <= 0 or not pixels:
        return None
    output_width, output_height = preview_pixel_dimensions(
        width,
        height,
        max_width=max_width,
        max_height=max_height,
    )
    chars = IMAGE_PREVIEW_CHARS
    lines: list[str] = []
    for out_y in range(output_height):
        row_chars: list[str] = []
        for out_x in range(output_width):
            red, green, blue = sample_region_rgb(
                width,
                height,
                pixels,
                out_x,
                out_y,
                output_width,
                output_height,
            )
            luminance = int(0.2126 * red + 0.7152 * green + 0.0722 * blue)
            index = min(len(chars) - 1, luminance * len(chars) // 256)
            row_chars.append(chars[index])
        lines.append("".join(row_chars).rstrip())
    preview = "\n".join(lines).rstrip()
    return preview or None


def preview_pixel_dimensions(
    width: int,
    height: int,
    *,
    max_width: int,
    max_height: int,
) -> tuple[int, int]:
    scale = min(max_width / width, max_height / height, 1.0)
    output_width = max(1, min(max_width, int(width * scale)))
    output_height = max(1, min(max_height, int(height * scale)))
    return output_width, output_height


def sample_region_rgb(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int]],
    out_x: int,
    out_y: int,
    output_width: int,
    output_height: int,
) -> tuple[int, int, int]:
    source_y0 = int(out_y * height / output_height)
    source_y1 = max(source_y0 + 1, int((out_y + 1) * height / output_height))
    source_x0 = int(out_x * width / output_width)
    source_x1 = max(source_x0 + 1, int((out_x + 1) * width / output_width))
    red_total = green_total = blue_total = count = 0
    for source_y in range(source_y0, min(source_y1, height)):
        row_offset = source_y * width
        for source_x in range(source_x0, min(source_x1, width)):
            red, green, blue = pixels[row_offset + source_x]
            red_total += red
            green_total += green
            blue_total += blue
            count += 1
    if count == 0:
        return 0, 0, 0
    return red_total // count, green_total // count, blue_total // count


def decode_png_pixels(data: bytes) -> tuple[int, int, list[tuple[int, int, int]]] | None:
    if len(data) < 33 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    offset = 8
    width = height = bit_depth = color_type = interlace = None
    palette: list[tuple[int, int, int]] = []
    compressed = bytearray()
    while offset + 8 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        if chunk_end + 4 > len(data):
            return None
        chunk = data[chunk_start:chunk_end]
        offset = chunk_end + 4
        if chunk_type == b"IHDR":
            if len(chunk) != 13:
                return None
            width, height = struct.unpack(">II", chunk[:8])
            bit_depth = chunk[8]
            color_type = chunk[9]
            interlace = chunk[12]
        elif chunk_type == b"PLTE":
            palette = [
                (chunk[index], chunk[index + 1], chunk[index + 2])
                for index in range(0, len(chunk) - 2, 3)
            ]
        elif chunk_type == b"IDAT":
            compressed.extend(chunk)
        elif chunk_type == b"IEND":
            break
    if (
        width is None
        or height is None
        or bit_depth != 8
        or interlace != 0
        or color_type not in {0, 2, 3, 4, 6}
        or not compressed
        or width * height > IMAGE_PREVIEW_MAX_SOURCE_PIXELS
    ):
        return None
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    stride = width * channels
    try:
        raw = zlib.decompress(bytes(compressed))
    except zlib.error:
        return None
    expected = (stride + 1) * height
    if len(raw) < expected:
        return None
    rows: list[bytes] = []
    previous = bytes(stride)
    index = 0
    for _row in range(height):
        filter_type = raw[index]
        index += 1
        filtered = raw[index : index + stride]
        index += stride
        row = unfilter_png_row(filtered, previous, filter_type, channels)
        if row is None:
            return None
        rows.append(row)
        previous = row
    pixels: list[tuple[int, int, int]] = []
    for row in rows:
        for column in range(width):
            base = column * channels
            if color_type == 0:
                gray = row[base]
                pixels.append((gray, gray, gray))
            elif color_type == 2:
                pixels.append((row[base], row[base + 1], row[base + 2]))
            elif color_type == 3:
                palette_index = row[base]
                pixels.append(
                    palette[palette_index]
                    if palette_index < len(palette)
                    else (0, 0, 0)
                )
            elif color_type == 4:
                gray = row[base]
                pixels.append((gray, gray, gray))
            elif color_type == 6:
                pixels.append((row[base], row[base + 1], row[base + 2]))
    return width, height, pixels


def unfilter_png_row(
    filtered: bytes,
    previous: bytes,
    filter_type: int,
    bytes_per_pixel: int,
) -> bytes | None:
    row = bytearray(filtered)
    for index, value in enumerate(filtered):
        left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
        up = previous[index] if index < len(previous) else 0
        up_left = (
            previous[index - bytes_per_pixel]
            if index >= bytes_per_pixel and index - bytes_per_pixel < len(previous)
            else 0
        )
        if filter_type == 0:
            row[index] = value
        elif filter_type == 1:
            row[index] = (value + left) & 0xFF
        elif filter_type == 2:
            row[index] = (value + up) & 0xFF
        elif filter_type == 3:
            row[index] = (value + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            row[index] = (value + paeth_predictor(left, up, up_left)) & 0xFF
        else:
            return None
    return bytes(row)


def paeth_predictor(left: int, up: int, up_left: int) -> int:
    estimate = left + up - up_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_up_left = abs(estimate - up_left)
    if distance_left <= distance_up and distance_left <= distance_up_left:
        return left
    if distance_up <= distance_up_left:
        return up
    return up_left


def decode_gif_pixels(data: bytes) -> tuple[int, int, list[tuple[int, int, int]]] | None:
    if len(data) < 13 or not data.startswith(b"GIF8"):
        return None
    width, height = struct.unpack("<HH", data[6:10])
    if width * height > IMAGE_PREVIEW_MAX_SOURCE_PIXELS:
        return None
    packed = data[10]
    has_global_table = bool(packed & 0x80)
    global_table_size = 2 ** ((packed & 0x07) + 1)
    offset = 13
    global_table: list[tuple[int, int, int]] = []
    if has_global_table:
        global_table, offset = read_gif_color_table(data, offset, global_table_size)
        if not global_table:
            return None
    while offset < len(data):
        marker = data[offset]
        offset += 1
        if marker == 0x2C:
            return decode_gif_image(data, offset, global_table, width, height)
        if marker == 0x21:
            if offset >= len(data):
                return None
            offset += 1
            offset = skip_gif_sub_blocks(data, offset)
            if offset is None:
                return None
            continue
        if marker == 0x3B:
            return None
        return None
    return None


def read_gif_color_table(
    data: bytes,
    offset: int,
    size: int,
) -> tuple[list[tuple[int, int, int]], int]:
    byte_count = size * 3
    if offset + byte_count > len(data):
        return [], offset
    table = [
        (data[index], data[index + 1], data[index + 2])
        for index in range(offset, offset + byte_count, 3)
    ]
    return table, offset + byte_count


def skip_gif_sub_blocks(data: bytes, offset: int) -> int | None:
    while offset < len(data):
        size = data[offset]
        offset += 1
        if size == 0:
            return offset
        offset += size
        if offset > len(data):
            return None
    return None


def decode_gif_image(
    data: bytes,
    offset: int,
    global_table: list[tuple[int, int, int]],
    canvas_width: int,
    canvas_height: int,
) -> tuple[int, int, list[tuple[int, int, int]]] | None:
    if offset + 9 > len(data):
        return None
    left, top, width, height = struct.unpack("<HHHH", data[offset : offset + 8])
    packed = data[offset + 8]
    offset += 9
    has_local_table = bool(packed & 0x80)
    interlaced = bool(packed & 0x40)
    local_table_size = 2 ** ((packed & 0x07) + 1)
    color_table = global_table
    if has_local_table:
        color_table, offset = read_gif_color_table(data, offset, local_table_size)
    if not color_table or offset >= len(data):
        return None
    min_code_size = data[offset]
    offset += 1
    image_data = bytearray()
    while offset < len(data):
        size = data[offset]
        offset += 1
        if size == 0:
            break
        if offset + size > len(data):
            return None
        image_data.extend(data[offset : offset + size])
        offset += size
    indices = decode_gif_lzw(bytes(image_data), min_code_size, width * height)
    if indices is None:
        return None
    ordered_indices = deinterlace_gif_indices(indices, width, height) if interlaced else indices
    pixels: list[tuple[int, int, int]] = []
    for index in ordered_indices[: width * height]:
        pixels.append(color_table[index] if index < len(color_table) else (0, 0, 0))
    if len(pixels) < width * height:
        pixels.extend([(0, 0, 0)] * ((width * height) - len(pixels)))
    return width or canvas_width, height or canvas_height, pixels


def decode_gif_lzw(
    data: bytes,
    min_code_size: int,
    expected_size: int,
) -> list[int] | None:
    if min_code_size < 2 or min_code_size > 8:
        return None
    clear_code = 1 << min_code_size
    end_code = clear_code + 1
    next_code = end_code + 1
    code_size = min_code_size + 1
    table: dict[int, list[int]] = {
        index: [index] for index in range(clear_code)
    }
    bit_position = 0
    previous: list[int] | None = None
    output: list[int] = []
    while bit_position + code_size <= len(data) * 8:
        code = read_little_endian_bits(data, bit_position, code_size)
        bit_position += code_size
        if code == clear_code:
            table = {index: [index] for index in range(clear_code)}
            next_code = end_code + 1
            code_size = min_code_size + 1
            previous = None
            continue
        if code == end_code:
            break
        if code in table:
            entry = table[code]
        elif previous is not None and code == next_code:
            entry = previous + [previous[0]]
        else:
            return None
        output.extend(entry)
        if previous is not None and next_code < 4096:
            table[next_code] = previous + [entry[0]]
            next_code += 1
            if next_code == (1 << code_size) and code_size < 12:
                code_size += 1
        previous = entry
        if len(output) >= expected_size:
            return output[:expected_size]
    return output[:expected_size] if output else None


def read_little_endian_bits(data: bytes, bit_position: int, bit_count: int) -> int:
    value = 0
    for shift in range(bit_count):
        source_bit = bit_position + shift
        if data[source_bit // 8] & (1 << (source_bit % 8)):
            value |= 1 << shift
    return value


def deinterlace_gif_indices(indices: list[int], width: int, height: int) -> list[int]:
    rows = [indices[row * width : (row + 1) * width] for row in range(height)]
    output = [[0] * width for _row in range(height)]
    source_row = 0
    for start, step in ((0, 8), (4, 8), (2, 4), (1, 2)):
        for target_row in range(start, height, step):
            if source_row >= len(rows):
                break
            output[target_row] = rows[source_row]
            source_row += 1
    return [value for row in output for value in row]
