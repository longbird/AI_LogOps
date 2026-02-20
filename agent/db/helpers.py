"""Database helper functions for recording analysis CRUD."""

from __future__ import annotations

import logging
from datetime import datetime
import pymysql

from shared.utils import setup_logging

logger: logging.Logger = setup_logging("db_helpers")

# ---------------------------------------------------------------------------
# INSERT helpers
# ---------------------------------------------------------------------------


def insert_audio_quality(
    conn: pymysql.connections.Connection,
    rec_no: int,
    agent_id: str,
    status: str,
    left_rms_db: float | None,
    right_rms_db: float | None,
    left_silence: float | None,
    right_silence: float | None,
    dropout_count: int,
    duration_wav: float | None,
    duration_smdr: float | None,
    is_stereo: bool,
) -> int:
    """Insert a row into ``rec_audio_quality`` and return its id."""
    sql = (
        "INSERT INTO rec_audio_quality"
        " (rec_no, agent_id, status, left_rms_db, right_rms_db,"
        "  left_silence, right_silence, dropout_count,"
        "  duration_wav, duration_smdr, is_stereo)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                rec_no,
                agent_id,
                status,
                left_rms_db,
                right_rms_db,
                left_silence,
                right_silence,
                dropout_count,
                duration_wav,
                duration_smdr,
                int(is_stereo),
            ),
        )
        conn.commit()
        row_id: int = cur.lastrowid  # type: ignore[assignment]
    logger.debug("Inserted rec_audio_quality id=%d for rec_no=%d", row_id, rec_no)
    return row_id


def insert_transcript(
    conn: pymysql.connections.Connection,
    rec_no: int,
    full_text: str | None,
    agent_text: str | None,
    customer_text: str | None,
    segments_json: str | None,
    duration_sec: float | None,
    word_count: int | None,
    model_name: str = "faster-whisper-medium",
    language: str = "ko",
) -> int:
    """Insert a row into ``rec_transcript`` and return the transcript_id."""
    sql = (
        "INSERT INTO rec_transcript"
        " (rec_no, model_name, language, full_text, agent_text,"
        "  customer_text, segments_json, duration_sec, word_count)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                rec_no,
                model_name,
                language,
                full_text,
                agent_text,
                customer_text,
                segments_json,
                duration_sec,
                word_count,
            ),
        )
        conn.commit()
        transcript_id: int = cur.lastrowid  # type: ignore[assignment]
    logger.debug("Inserted rec_transcript id=%d for rec_no=%d", transcript_id, rec_no)
    return transcript_id


def insert_call_quality(
    conn: pymysql.connections.Connection,
    rec_no: int,
    transcript_id: int | None,
    first_response_sec: float | None,
    agent_talk_ratio: float | None,
    customer_talk_ratio: float | None,
    silence_ratio: float | None,
    required_phrase_hit: bool,
    required_phrase_list: str | None,
    forbidden_word_hit: bool,
    forbidden_word_list: str | None,
    score_total: float | None,
    score_response: float | None,
    score_phrase: float | None,
    score_silence: float | None,
) -> int:
    """Insert a row into ``rec_call_quality`` and return its id."""
    sql = (
        "INSERT INTO rec_call_quality"
        " (rec_no, transcript_id, first_response_sec,"
        "  agent_talk_ratio, customer_talk_ratio, silence_ratio,"
        "  required_phrase_hit, required_phrase_list,"
        "  forbidden_word_hit, forbidden_word_list,"
        "  score_total, score_response, score_phrase, score_silence)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                rec_no,
                transcript_id,
                first_response_sec,
                agent_talk_ratio,
                customer_talk_ratio,
                silence_ratio,
                int(required_phrase_hit),
                required_phrase_list,
                int(forbidden_word_hit),
                forbidden_word_list,
                score_total,
                score_response,
                score_phrase,
                score_silence,
            ),
        )
        conn.commit()
        row_id: int = cur.lastrowid  # type: ignore[assignment]
    logger.debug("Inserted rec_call_quality id=%d for rec_no=%d", row_id, rec_no)
    return row_id


# ---------------------------------------------------------------------------
# QUERY helpers
# ---------------------------------------------------------------------------

_RECORDING_JOIN_SQL = (
    "SELECT"
    "  aq.id            AS aq_id,"
    "  aq.rec_no,"
    "  aq.agent_id,"
    "  aq.analyzed_at   AS aq_analyzed_at,"
    "  aq.status,"
    "  aq.left_rms_db,"
    "  aq.right_rms_db,"
    "  aq.left_silence,"
    "  aq.right_silence,"
    "  aq.dropout_count,"
    "  aq.duration_wav,"
    "  aq.duration_smdr,"
    "  aq.is_stereo,"
    "  tr.id            AS transcript_id,"
    "  tr.transcribed_at,"
    "  tr.model_name,"
    "  tr.language,"
    "  tr.full_text,"
    "  tr.agent_text,"
    "  tr.customer_text,"
    "  tr.segments_json,"
    "  tr.duration_sec,"
    "  tr.word_count,"
    "  cq.id            AS cq_id,"
    "  cq.analyzed_at   AS cq_analyzed_at,"
    "  cq.first_response_sec,"
    "  cq.agent_talk_ratio,"
    "  cq.customer_talk_ratio,"
    "  cq.silence_ratio,"
    "  cq.required_phrase_hit,"
    "  cq.required_phrase_list,"
    "  cq.forbidden_word_hit,"
    "  cq.forbidden_word_list,"
    "  cq.score_total,"
    "  cq.score_response,"
    "  cq.score_phrase,"
    "  cq.score_silence"
    " FROM rec_audio_quality aq"
    " LEFT JOIN rec_transcript tr ON aq.rec_no = tr.rec_no"
    " LEFT JOIN rec_call_quality cq ON tr.rec_no = cq.rec_no"
)


def query_recordings_list(
    conn: pymysql.connections.Connection,
    date_str: str = "",
) -> list[dict[str, object]]:
    """Return all recordings analysed on *date_str* (``YYYYMMDD``).

    If *date_str* is empty the current date is used.
    """
    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")

    sql = _RECORDING_JOIN_SQL + " WHERE DATE_FORMAT(aq.analyzed_at, '%%Y%%m%%d') = %s"

    with conn.cursor() as cur:
        cur.execute(sql, (date_str,))
        rows: list[dict[str, object]] = cur.fetchall()  # pyright: ignore[reportAssignmentType]
    logger.debug("query_recordings_list date=%s → %d rows", date_str, len(rows))
    return rows


def query_recording_detail(
    conn: pymysql.connections.Connection,
    rec_no: int,
) -> dict[str, object] | None:
    """Return a single recording by *rec_no*, or ``None`` if not found."""
    sql = _RECORDING_JOIN_SQL + " WHERE aq.rec_no = %s"

    with conn.cursor() as cur:
        cur.execute(sql, (rec_no,))
        row: dict[str, object] | None = cur.fetchone()  # pyright: ignore[reportAssignmentType]
    if row:
        logger.debug("query_recording_detail rec_no=%d found", rec_no)
    else:
        logger.debug("query_recording_detail rec_no=%d not found", rec_no)
    return row
