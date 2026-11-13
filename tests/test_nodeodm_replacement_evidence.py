import hashlib

import pytest

from shared.nodeodm_replacement_evidence import inspect_nodeodm_image_list


NODE_UUID = "5b03d450-cb52-47fa-aca3-c4be832e15a9"


def _image_list(tmp_path, content=b"images/a.JPG\nimages/b.jpeg"):
    folder = tmp_path / NODE_UUID
    folder.mkdir()
    path = folder / "img_list.txt"
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()


def test_nodeodm_image_list_counts_verified_entries_without_final_newline(tmp_path):
    path, source_hash = _image_list(tmp_path)

    assert inspect_nodeodm_image_list(path, source_hash) == {
        "node_uuid": NODE_UUID,
        "count": 2,
        "sha256": source_hash,
    }


def test_nodeodm_image_list_rejects_copy_changed_after_source_hash(tmp_path):
    path, source_hash = _image_list(tmp_path)
    path.write_bytes(b"images/a.JPG\nimages/c.jpeg")

    with pytest.raises(ValueError, match="recorded source SHA-256"):
        inspect_nodeodm_image_list(path, source_hash)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"images/a.JPG\nimages/A.jpg", "duplicate"),
        (b"images/a.JPG\nnot-an-image.txt", "invalid image"),
        (b"images/a.JPG\n\nimages/b.JPG", "invalid image"),
    ],
)
def test_nodeodm_image_list_rejects_invalid_entries(tmp_path, content, message):
    path, source_hash = _image_list(tmp_path, content)

    with pytest.raises(ValueError, match=message):
        inspect_nodeodm_image_list(path, source_hash)


def test_nodeodm_image_list_requires_uuid_directory_and_source_hash(tmp_path):
    path, source_hash = _image_list(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    copied = other / "img_list.txt"
    copied.write_bytes(path.read_bytes())

    with pytest.raises(ValueError, match="parent directory"):
        inspect_nodeodm_image_list(copied, source_hash)
    with pytest.raises(ValueError, match="64-character"):
        inspect_nodeodm_image_list(path, "not-a-hash")
