from __future__ import annotations

from pathlib import Path

from shared.protocol import CmdDeployPayload, FileChunkPayload
from shared.utils import compute_sha256


class FileTransferReceiver:
    """Agent 측 파일 수신기. 청크 조립 + SHA-256 검증."""

    def __init__(self, target_dir: str):
        self.target_dir: Path = Path(target_dir)
        self._chunks: dict[int, bytes] = {}
        self._expected_size: int = 0
        self._expected_sha256: str = ""
        self._filename: str = ""
        self._is_receiving: bool = False

    def start_receive(self, cmd_deploy: CmdDeployPayload) -> None:
        """CMD_DEPLOY 수신 시 호출. 수신 상태 초기화."""
        self._chunks.clear()
        self._expected_size = cmd_deploy.file_size
        self._expected_sha256 = cmd_deploy.sha256
        self._filename = cmd_deploy.filename
        self._is_receiving = True

    def receive_chunk(self, chunk: FileChunkPayload) -> bool:
        """FILE_CHUNK 수신. 중복 seq 무시. 성공 시 True."""
        if not self._is_receiving:
            return False
        if chunk.seq_num in self._chunks:
            return True
        self._chunks[chunk.seq_num] = chunk.data
        return True

    def is_complete(self) -> bool:
        """모든 청크 수신 완료 여부."""
        total = sum(len(data) for data in self._chunks.values())
        return total >= self._expected_size

    def assemble(self) -> Path | None:
        """청크 조립 -> 파일 저장 -> SHA-256 검증. 성공 시 파일 경로 반환."""
        if not self.is_complete():
            return None

        sorted_data = b"".join(
            self._chunks[seq_num] for seq_num in sorted(self._chunks.keys())
        )
        sorted_data = sorted_data[: self._expected_size]

        self.target_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.target_dir / self._filename
        _ = file_path.write_bytes(sorted_data)

        actual_hash = compute_sha256(str(file_path))
        if actual_hash != self._expected_sha256:
            file_path.unlink()
            return None

        self._is_receiving = False
        return file_path
