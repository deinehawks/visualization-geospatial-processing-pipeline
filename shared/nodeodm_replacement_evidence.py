"""Read-only validation of an operator-copied NodeODM root image list."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path


MAX_IMAGE_LIST_BYTES = 8 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


def inspect_nodeodm_image_list(path: str | Path, source_sha256: str) -> dict[str, object]:
    """Validate a copy of <node-task-uuid>/img_list.txt against its source hash."""
    evidence_path = Path(path)
    if evidence_path.name != "img_list.txt" or evidence_path.is_symlink():
        raise ValueError("NodeODM evidence must be a regular img_list.txt file")
    try:
        node_uuid = str(uuid.UUID(evidence_path.parent.name))
    except ValueError as exc:
        raise ValueError(
            "NodeODM evidence parent directory must be the node task UUID"
        ) from exc
    if evidence_path.parent.name.lower() != node_uuid:
        raise ValueError("NodeODM evidence directory must use the canonical task UUID")
    if not _SHA256_RE.fullmatch(source_sha256 or ""):
        raise ValueError("NodeODM evidence requires a 64-character source SHA-256")
    try:
        size = evidence_path.stat().st_size
        if not evidence_path.is_file() or size == 0 or size > MAX_IMAGE_LIST_BYTES:
            raise ValueError("NodeODM image list is missing, empty or too large")
        content = evidence_path.read_bytes()
    except OSError as exc:
        raise ValueError("Could not read NodeODM image list evidence") from exc
    if not content or len(content) > MAX_IMAGE_LIST_BYTES:
        raise ValueError("NodeODM image list is empty or too large")
    digest = hashlib.sha256(content).hexdigest()
    if digest != source_sha256.lower():
        raise ValueError("NodeODM image list differs from its recorded source SHA-256")
    try:
        lines = content.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("NodeODM image list is not UTF-8 text") from exc
    if not lines:
        raise ValueError("NodeODM image list has no images")
    seen: set[str] = set()
    for line in lines:
        name = line.replace("\\", "/").rsplit("/", 1)[-1]
        if (
            line != line.strip()
            or not name
            or Path(name).suffix.casefold() not in {".jpg", ".jpeg"}
            or any(ord(character) < 32 for character in line)
        ):
            raise ValueError("NodeODM image list has an invalid image entry")
        folded = name.casefold()
        if folded in seen:
            raise ValueError("NodeODM image list has duplicate image names")
        seen.add(folded)
    return {"node_uuid": node_uuid, "count": len(lines), "sha256": digest}
