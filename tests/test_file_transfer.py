from __future__ import annotations

# pyright: reportPrivateUsage=false

from pathlib import Path

from agent.core.file_transfer import FileTransferReceiver
from shared.protocol import CmdDeployPayload, FileChunkPayload
from shared.utils import compute_sha256


def test_start_receive_initializes_state(tmp_path: Path) -> None:
    receiver = FileTransferReceiver(str(tmp_path))
    cmd = CmdDeployPayload(
        file_size=10,
        sha256="a" * 64,
        filename="deploy.bin",
    )

    receiver.start_receive(cmd)

    assert receiver._expected_size == 10
    assert receiver._expected_sha256 == "a" * 64
    assert receiver._filename == "deploy.bin"
    assert receiver._is_receiving is True
    assert receiver._chunks == {}


def test_receive_chunk_stores_data(tmp_path: Path) -> None:
    receiver = FileTransferReceiver(str(tmp_path))
    cmd = CmdDeployPayload(file_size=6, sha256="a" * 64, filename="app.bin")
    receiver.start_receive(cmd)

    chunk = FileChunkPayload(seq_num=1, data=b"abcdef")
    result = receiver.receive_chunk(chunk)

    assert result is True
    assert receiver._chunks[1] == b"abcdef"


def test_is_complete_after_all_chunks(tmp_path: Path) -> None:
    receiver = FileTransferReceiver(str(tmp_path))
    cmd = CmdDeployPayload(file_size=8, sha256="a" * 64, filename="app.bin")
    receiver.start_receive(cmd)

    _ = receiver.receive_chunk(FileChunkPayload(seq_num=0, data=b"abcd"))
    _ = receiver.receive_chunk(FileChunkPayload(seq_num=1, data=b"efgh"))

    assert receiver.is_complete() is True


def test_assemble_produces_correct_file(tmp_path: Path) -> None:
    data = b"hello-world-data"
    input_file = tmp_path / "source.bin"
    _ = input_file.write_bytes(data)
    expected_hash = compute_sha256(str(input_file))

    receiver = FileTransferReceiver(str(tmp_path / "out"))
    receiver.start_receive(
        CmdDeployPayload(
            file_size=len(data),
            sha256=expected_hash,
            filename="artifact.bin",
        )
    )
    _ = receiver.receive_chunk(FileChunkPayload(seq_num=1, data=data[5:]))
    _ = receiver.receive_chunk(FileChunkPayload(seq_num=0, data=data[:5]))

    file_path = receiver.assemble()

    assert file_path is not None
    assert file_path.exists()
    assert file_path.read_bytes() == data


def test_assemble_verifies_sha256(tmp_path: Path) -> None:
    data = b"verified-content"
    source = tmp_path / "source.bin"
    _ = source.write_bytes(data)
    expected_hash = compute_sha256(str(source))

    receiver = FileTransferReceiver(str(tmp_path / "out"))
    receiver.start_receive(
        CmdDeployPayload(
            file_size=len(data),
            sha256=expected_hash,
            filename="verified.bin",
        )
    )
    _ = receiver.receive_chunk(FileChunkPayload(seq_num=0, data=data))

    file_path = receiver.assemble()

    assert file_path is not None
    assert compute_sha256(str(file_path)) == expected_hash


def test_assemble_fails_on_sha256_mismatch(tmp_path: Path) -> None:
    data = b"mismatch-content"
    receiver = FileTransferReceiver(str(tmp_path))
    receiver.start_receive(
        CmdDeployPayload(
            file_size=len(data),
            sha256="0" * 64,
            filename="bad.bin",
        )
    )
    _ = receiver.receive_chunk(FileChunkPayload(seq_num=0, data=data))

    file_path = receiver.assemble()

    assert file_path is None
    assert not (tmp_path / "bad.bin").exists()


def test_duplicate_chunk_ignored(tmp_path: Path) -> None:
    data = b"abcdefgh"
    source = tmp_path / "source.bin"
    _ = source.write_bytes(data)
    expected_hash = compute_sha256(str(source))

    receiver = FileTransferReceiver(str(tmp_path / "out"))
    receiver.start_receive(
        CmdDeployPayload(
            file_size=len(data),
            sha256=expected_hash,
            filename="dup.bin",
        )
    )

    first = receiver.receive_chunk(FileChunkPayload(seq_num=0, data=b"abcd"))
    duplicate = receiver.receive_chunk(FileChunkPayload(seq_num=0, data=b"WXYZ"))
    second = receiver.receive_chunk(FileChunkPayload(seq_num=1, data=b"efgh"))
    file_path = receiver.assemble()

    assert first is True
    assert duplicate is True
    assert second is True
    assert file_path is not None
    assert file_path.read_bytes() == data
