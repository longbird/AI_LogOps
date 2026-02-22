"""Database helper functions for recording analysis CRUD."""

from __future__ import annotations

import logging
from datetime import datetime

import pymysql

from shared.utils import setup_logging

logger: logging.Logger = setup_logging("db_helpers")

# ---------------------------------------------------------------------------
# rec_no resolution — rec_his / rec_his_YYYYMM 테이블에서 조회
# ---------------------------------------------------------------------------


def _resolve_rec_no(
    conn: pymysql.connections.Connection,
    filename: str,
    recording_date: str = "",
) -> int | None:
    """filename으로 rec_his 계열 테이블에서 rec_no를 조회한다.

    - 오늘 날짜 녹취 → ``rec_his`` 에서 검색
    - 이전 날짜 녹취 → ``rec_his_YYYYMM`` 에서 검색
    - recording_date 미지정 → ``rec_his`` 먼저, 없으면 ``rec_his_YYYYMM`` (당월) 시도
    - 못 찾으면 ``None`` 반환 (INSERT 불가)
    """
    today = datetime.now().strftime("%Y%m%d")
    current_yyyymm = today[:6]

    # 조회할 테이블 목록 결정
    if recording_date:
        if recording_date == today:
            tables = ["rec_his"]
        else:
            yyyymm = recording_date[:6]
            tables = [f"rec_his_{yyyymm}"]
    else:
        # 날짜 미지정: rec_his(오늘) → rec_his_YYYYMM(당월 아카이브) 순서
        tables = ["rec_his", f"rec_his_{current_yyyymm}"]

    stem = filename.rsplit(".", 1)[0] if "." in filename else filename

    for table in tables:
        try:
            with conn.cursor() as cur:
                # 정확 일치
                cur.execute(
                    f"SELECT rec_no FROM {table} WHERE file_name = %s LIMIT 1",
                    (filename,),
                )
                row = cur.fetchone()
                if row is not None:
                    val = row["rec_no"] if isinstance(row, dict) else row[0]  # pyright: ignore[reportCallIssue,reportArgumentType]
                    logger.debug(
                        "rec_no resolved: table=%s filename=%s → %s",
                        table,
                        filename,
                        val,
                    )
                    return int(str(val))

                # 확장자 없이 LIKE 매칭
                cur.execute(
                    f"SELECT rec_no FROM {table} WHERE file_name LIKE %s LIMIT 1",
                    (f"%{stem}%",),
                )
                row = cur.fetchone()
                if row is not None:
                    val = row["rec_no"] if isinstance(row, dict) else row[0]  # pyright: ignore[reportCallIssue,reportArgumentType]
                    logger.debug(
                        "rec_no resolved (LIKE): table=%s filename=%s → %s",
                        table,
                        filename,
                        val,
                    )
                    return int(str(val))
        except Exception:
            logger.debug(
                "rec_no lookup failed: table=%s filename=%s",
                table,
                filename,
                exc_info=True,
            )

    logger.warning(
        "rec_no not found in %s for filename=%s — analysis data will NOT be saved",
        tables,
        filename,
    )
    return None


# ---------------------------------------------------------------------------
# 미분석 녹취 조회
# ---------------------------------------------------------------------------


def fetch_unanalyzed_recordings(
    conn: pymysql.connections.Connection,
    date_str: str = "",
) -> list[tuple[int, str, int]]:
    """rec_his 계열 테이블에서 아직 분석(rec_transcript)이 없는 녹취 목록 조회.

    - 오늘 날짜 → ``rec_his``
    - 이전 날짜 → ``rec_his_YYYYMM``

    Returns:
        ``[(rec_no, filename, in_out), ...]`` — 미분석 녹취 목록
        in_out: 1=수신, 2=발신, 0=알수없음
    """
    today = datetime.now().strftime("%Y%m%d")
    if not date_str:
        date_str = today

    if date_str[:8] == today:
        table = "rec_his"
    else:
        yyyymm = date_str[:6]
        table = f"rec_his_{yyyymm}"

    sql = (
        f"SELECT h.rec_no, h.file_name, COALESCE(h.in_out, 0) AS in_out"
        f" FROM {table} h"
        f" LEFT JOIN rec_transcript t ON h.rec_no = t.rec_no"
        f" WHERE t.rec_no IS NULL"
        f" AND h.file_name IS NOT NULL AND h.file_name != ''"
    )

    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    except Exception:
        logger.warning(
            "fetch_unanalyzed_recordings failed: table=%s", table, exc_info=True
        )
        return []

    result: list[tuple[int, str, int]] = []
    for row in rows:
        rec_no = row["rec_no"] if isinstance(row, dict) else row[0]  # pyright: ignore[reportCallIssue,reportArgumentType]
        filename = row["file_name"] if isinstance(row, dict) else row[1]  # pyright: ignore[reportCallIssue,reportArgumentType]
        in_out = row["in_out"] if isinstance(row, dict) else row[2]  # pyright: ignore[reportCallIssue,reportArgumentType]
        result.append((int(str(rec_no)), str(filename), int(str(in_out or 0))))

    logger.info(
        "fetch_unanalyzed_recordings: table=%s date=%s → %d recordings",
        table,
        date_str,
        len(result),
    )
    return result


# ---------------------------------------------------------------------------
# INSERT / UPSERT helpers
# ---------------------------------------------------------------------------


def upsert_audio_quality(
    conn: pymysql.connections.Connection,
    filename: str,
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
    rec_no: int | None = None,
    recording_date: str = "",
) -> int:
    """``rec_audio_quality`` 에 INSERT 또는 UPDATE.

    *rec_no* 를 직접 전달하면 rec_his 조회를 건너뛴다.
    rec_no 를 찾지 못하면 ``-1`` 을 반환한다.
    """
    if rec_no is None:
        rec_no = _resolve_rec_no(conn, filename, recording_date)
    if rec_no is None:
        logger.warning(
            "Skipping rec_audio_quality — rec_no not found: filename=%s", filename
        )
        return -1

    values = (
        filename,
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
    )

    with conn.cursor() as cur:
        # 기존 레코드 확인
        cur.execute(
            "SELECT id FROM rec_audio_quality WHERE rec_no = %s LIMIT 1",
            (rec_no,),
        )
        existing = cur.fetchone()

        if existing:
            row_id = existing["id"] if isinstance(existing, dict) else existing[0]  # pyright: ignore[reportCallIssue,reportArgumentType]
            cur.execute(
                "UPDATE rec_audio_quality SET"
                " filename=%s, agent_id=%s, status=%s,"
                " left_rms_db=%s, right_rms_db=%s,"
                " left_silence=%s, right_silence=%s,"
                " dropout_count=%s, duration_wav=%s, duration_smdr=%s, is_stereo=%s"
                " WHERE rec_no=%s",
                (*values, rec_no),
            )
            logger.info(
                "Updated rec_audio_quality id=%s rec_no=%d filename=%s",
                row_id,
                rec_no,
                filename,
            )
        else:
            cur.execute(
                "INSERT INTO rec_audio_quality"
                " (rec_no, filename, agent_id, status, left_rms_db, right_rms_db,"
                "  left_silence, right_silence, dropout_count,"
                "  duration_wav, duration_smdr, is_stereo)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (rec_no, *values),
            )
            row_id = cur.lastrowid
            logger.info(
                "Inserted rec_audio_quality id=%s rec_no=%d filename=%s",
                row_id,
                rec_no,
                filename,
            )
        conn.commit()
    return int(str(row_id))


def upsert_transcript(
    conn: pymysql.connections.Connection,
    filename: str,
    full_text: str | None,
    agent_text: str | None,
    customer_text: str | None,
    segments_json: str | None,
    duration_sec: float | None,
    word_count: int | None,
    model_name: str = "faster-whisper-medium",
    language: str = "ko",
    rec_no: int | None = None,
    recording_date: str = "",
) -> int:
    """``rec_transcript`` 에 INSERT 또는 UPDATE.

    *rec_no* 를 직접 전달하면 rec_his 조회를 건너뛴다.
    rec_no 를 찾지 못하면 ``-1`` 을 반환한다.
    """
    if rec_no is None:
        rec_no = _resolve_rec_no(conn, filename, recording_date)
    if rec_no is None:
        logger.warning(
            "Skipping rec_transcript — rec_no not found: filename=%s", filename
        )
        return -1

    values = (
        filename,
        model_name,
        language,
        full_text,
        agent_text,
        customer_text,
        segments_json,
        duration_sec,
        word_count,
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM rec_transcript WHERE rec_no = %s LIMIT 1",
            (rec_no,),
        )
        existing = cur.fetchone()

        if existing:
            row_id = existing["id"] if isinstance(existing, dict) else existing[0]  # pyright: ignore[reportCallIssue,reportArgumentType]
            cur.execute(
                "UPDATE rec_transcript SET"
                " filename=%s, model_name=%s, language=%s,"
                " full_text=%s, agent_text=%s, customer_text=%s,"
                " segments_json=%s, duration_sec=%s, word_count=%s"
                " WHERE rec_no=%s",
                (*values, rec_no),
            )
            logger.info(
                "Updated rec_transcript id=%s rec_no=%d filename=%s",
                row_id,
                rec_no,
                filename,
            )
        else:
            cur.execute(
                "INSERT INTO rec_transcript"
                " (rec_no, filename, model_name, language, full_text, agent_text,"
                "  customer_text, segments_json, duration_sec, word_count)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (rec_no, *values),
            )
            row_id = cur.lastrowid
            logger.info(
                "Inserted rec_transcript id=%s rec_no=%d filename=%s",
                row_id,
                rec_no,
                filename,
            )
        conn.commit()
    return int(str(row_id))


def upsert_call_quality(
    conn: pymysql.connections.Connection,
    filename: str,
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
    rec_no: int | None = None,
    recording_date: str = "",
) -> int:
    """``rec_call_quality`` 에 INSERT 또는 UPDATE.

    *rec_no* 를 직접 전달하면 rec_his 조회를 건너뛴다.
    rec_no 를 찾지 못하면 ``-1`` 을 반환한다.
    """
    if rec_no is None:
        rec_no = _resolve_rec_no(conn, filename, recording_date)
    if rec_no is None:
        logger.warning(
            "Skipping rec_call_quality — rec_no not found: filename=%s", filename
        )
        return -1

    values = (
        filename,
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
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM rec_call_quality WHERE rec_no = %s LIMIT 1",
            (rec_no,),
        )
        existing = cur.fetchone()

        if existing:
            row_id = existing["id"] if isinstance(existing, dict) else existing[0]  # pyright: ignore[reportCallIssue,reportArgumentType]
            cur.execute(
                "UPDATE rec_call_quality SET"
                " filename=%s, transcript_id=%s, first_response_sec=%s,"
                " agent_talk_ratio=%s, customer_talk_ratio=%s, silence_ratio=%s,"
                " required_phrase_hit=%s, required_phrase_list=%s,"
                " forbidden_word_hit=%s, forbidden_word_list=%s,"
                " score_total=%s, score_response=%s, score_phrase=%s, score_silence=%s"
                " WHERE rec_no=%s",
                (*values, rec_no),
            )
            logger.info(
                "Updated rec_call_quality id=%s rec_no=%d filename=%s",
                row_id,
                rec_no,
                filename,
            )
        else:
            cur.execute(
                "INSERT INTO rec_call_quality"
                " (rec_no, filename, transcript_id, first_response_sec,"
                "  agent_talk_ratio, customer_talk_ratio, silence_ratio,"
                "  required_phrase_hit, required_phrase_list,"
                "  forbidden_word_hit, forbidden_word_list,"
                "  score_total, score_response, score_phrase, score_silence)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (rec_no, *values),
            )
            row_id = cur.lastrowid
            logger.info(
                "Inserted rec_call_quality id=%s rec_no=%d filename=%s",
                row_id,
                rec_no,
                filename,
            )
        conn.commit()
    return int(str(row_id))


# ---------------------------------------------------------------------------
# QUERY helpers
# ---------------------------------------------------------------------------

_RECORDING_JOIN_SQL = (
    "SELECT"
    "  aq.id            AS aq_id,"
    "  aq.filename,"
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
    " LEFT JOIN rec_call_quality cq ON aq.rec_no = cq.rec_no"
)


def query_recordings_list(
    conn: pymysql.connections.Connection,
    date_str: str = "",
) -> list[dict[str, object]]:
    """Return all analysed recordings for *date_str* (``YYYYMMDD``).

    녹취 날짜 기준으로 조회한다 (분석 수행일이 아닌 녹취 발생일).
    - 오늘 → ``rec_his`` INNER JOIN
    - 이전 → ``rec_his_YYYYMM`` INNER JOIN

    If *date_str* is empty the current date is used.
    """
    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")

    today = datetime.now().strftime("%Y%m%d")
    if date_str == today:
        his_table = "rec_his"
    else:
        his_table = f"rec_his_{date_str[:6]}"

    sql = (
        _RECORDING_JOIN_SQL
        + f" INNER JOIN {his_table} h ON aq.rec_no = h.rec_no"
        + " ORDER BY aq.analyzed_at DESC"
    )

    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows: list[dict[str, object]] = cur.fetchall()  # pyright: ignore[reportAssignmentType]
    except Exception:
        logger.warning(
            "query_recordings_list failed: his_table=%s", his_table, exc_info=True
        )
        return []
    logger.debug(
        "query_recordings_list date=%s table=%s → %d rows",
        date_str,
        his_table,
        len(rows),
    )
    return rows


def query_recording_detail(
    conn: pymysql.connections.Connection,
    filename: str,
) -> dict[str, object] | None:
    """Return a single recording by *filename*, or ``None`` if not found."""
    sql = _RECORDING_JOIN_SQL + " WHERE aq.filename = %s"

    with conn.cursor() as cur:
        cur.execute(sql, (filename,))
        row: dict[str, object] | None = cur.fetchone()  # pyright: ignore[reportAssignmentType]
    if row:
        logger.debug("query_recording_detail filename=%s found", filename)
    else:
        logger.debug("query_recording_detail filename=%s not found", filename)
    return row
