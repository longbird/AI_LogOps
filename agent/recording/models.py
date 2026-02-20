"""Analysis result data models."""

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class AnalysisStatus(str, Enum):
    OK = "OK"
    EMPTY = "EMPTY"
    MUTED_L = "MUTED_L"
    MUTED_R = "MUTED_R"
    DROPOUT = "DROPOUT"
    MISMATCH = "MISMATCH"
    START_MISS = "START_MISS"


@dataclass
class ChannelStats:
    rms_db: float = -96.0
    silence_ratio: float = 1.0
    vad_ratio: float = 0.0


@dataclass
class AnalysisResult:
    filename: str
    status: AnalysisStatus = AnalysisStatus.OK
    left: ChannelStats = field(default_factory=ChannelStats)
    right: ChannelStats = field(default_factory=ChannelStats)
    dropout_count: int = 0
    dropout_total_sec: float = 0.0
    duration_wav: float = 0.0
    duration_smdr: float = 0.0
    duration_diff: float = 0.0
    analyzed_at: datetime = field(default_factory=datetime.now)
    is_stereo: bool = False
