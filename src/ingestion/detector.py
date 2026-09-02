import os
from typing import Literal


FORMAT = Literal["excel", "pdf", "docx", "image", "text", "email"]

_EXT_MAP: dict[str, FORMAT] = {
    "xlsx": "excel", "xls": "excel",
    "pdf":  "pdf",
    "docx": "docx",  "doc": "docx",
    "jpg":  "image", "jpeg": "image", "png": "image", "tiff": "image", "gif": "image", "webp": "image",
    "txt":  "text",
    "eml":  "email", "msg": "email",
}

# Magic-byte signatures for fallback detection
_MAGIC: list[tuple[bytes, FORMAT]] = [
    (b"PK\x03\x04", "excel"),  # ZIP-based (xlsx / docx) — refined below
    (b"%PDF",        "pdf"),
    (b"\xff\xd8\xff", "image"),
    (b"\x89PNG",      "image"),
]


def detect_format(file_path: str) -> FORMAT:
    ext = os.path.splitext(file_path)[1].lstrip(".").lower()
    if ext in _EXT_MAP:
        return _EXT_MAP[ext]

    # Fallback: magic bytes
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
        for magic, fmt in _MAGIC:
            if header.startswith(magic):
                if fmt == "excel" and file_path.lower().endswith(".docx"):
                    return "docx"
                return fmt
    except OSError:
        pass

    return "text"
