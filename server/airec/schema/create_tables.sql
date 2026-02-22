-- AirREC Database Schema
-- Tables for recording analysis, transcription, and quality results

-- Audio Quality Analysis (agent-side analysis results)
-- Stores analysis results received from agents via TCP
CREATE TABLE IF NOT EXISTS rec_audio_quality (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    rec_no          INT UNSIGNED NOT NULL,
    filename        VARCHAR(100) DEFAULT NULL COMMENT 'Recording filename for reference',
    agent_id        VARCHAR(32) NOT NULL,
    analyzed_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status          VARCHAR(20) NOT NULL COMMENT 'OK, EMPTY, MUTED_L, MUTED_R, DROPOUT, MISMATCH',
    left_rms_db     FLOAT DEFAULT NULL,
    right_rms_db    FLOAT DEFAULT NULL,
    left_silence    FLOAT DEFAULT NULL,
    right_silence   FLOAT DEFAULT NULL,
    dropout_count   INT DEFAULT 0,
    duration_wav    FLOAT DEFAULT NULL,
    duration_smdr   FLOAT DEFAULT NULL,
    is_stereo       TINYINT(1) DEFAULT 0,
    INDEX idx_rec_no (rec_no),
    INDEX idx_agent_id (agent_id),
    INDEX idx_status (status),
    INDEX idx_analyzed_at (analyzed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Agent-reported audio quality analysis';

-- STT Transcript Results
-- Stores speech-to-text transcription output per recording
CREATE TABLE IF NOT EXISTS rec_transcript (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY
                    COMMENT 'Transcript ID',
    rec_no          INT UNSIGNED NOT NULL
                    COMMENT 'rec_his.rec_no FK',
    filename        VARCHAR(100) DEFAULT NULL
                    COMMENT 'Recording filename for reference',
    transcribed_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    COMMENT 'STT completion time',
    model_name      VARCHAR(50) DEFAULT 'faster-whisper-medium'
                    COMMENT 'STT model used',
    language        VARCHAR(10) DEFAULT 'ko'
                    COMMENT 'Recognition language',
    full_text       MEDIUMTEXT DEFAULT NULL
                    COMMENT 'Full merged transcript',
    agent_text      MEDIUMTEXT DEFAULT NULL
                    COMMENT 'Agent (L channel) transcript',
    customer_text   MEDIUMTEXT DEFAULT NULL
                    COMMENT 'Customer (R channel) transcript',
    segments_json   LONGTEXT DEFAULT NULL
                    COMMENT 'Full segment JSON [{time, end, speaker, text}, ...]',
    duration_sec    FLOAT DEFAULT NULL
                    COMMENT 'Transcript time span in seconds',
    word_count      INT DEFAULT NULL
                    COMMENT 'Total word count',
    INDEX idx_rec_no (rec_no),
    INDEX idx_transcribed_at (transcribed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='STT transcription results';

-- Call Quality Analysis Results
-- Stores quality metrics derived from STT analysis
CREATE TABLE IF NOT EXISTS rec_call_quality (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY
                        COMMENT 'Quality analysis ID',
    rec_no              INT UNSIGNED NOT NULL
                        COMMENT 'rec_his.rec_no FK',
    filename            VARCHAR(100) DEFAULT NULL
                        COMMENT 'Recording filename for reference',
    transcript_id       INT UNSIGNED DEFAULT NULL
                        COMMENT 'rec_transcript.id FK',
    analyzed_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                        COMMENT 'Analysis time',
    first_response_sec  FLOAT DEFAULT NULL
                        COMMENT 'First response time (seconds)',
    agent_talk_ratio    FLOAT DEFAULT NULL
                        COMMENT 'Agent talk ratio (0.0~1.0)',
    customer_talk_ratio FLOAT DEFAULT NULL
                        COMMENT 'Customer talk ratio (0.0~1.0)',
    silence_ratio       FLOAT DEFAULT NULL
                        COMMENT 'Silence ratio (0.0~1.0)',
    required_phrase_hit TINYINT(1) DEFAULT 0
                        COMMENT 'Required phrase detected (0/1)',
    required_phrase_list VARCHAR(500) DEFAULT NULL
                        COMMENT 'Matched required phrases',
    forbidden_word_hit  TINYINT(1) DEFAULT 0
                        COMMENT 'Forbidden word detected (0/1)',
    forbidden_word_list VARCHAR(500) DEFAULT NULL
                        COMMENT 'Detected forbidden words',
    score_total         FLOAT DEFAULT NULL
                        COMMENT 'Total quality score (0~100)',
    score_response      FLOAT DEFAULT NULL
                        COMMENT 'Response speed score',
    score_phrase        FLOAT DEFAULT NULL
                        COMMENT 'Phrase compliance score',
    score_silence       FLOAT DEFAULT NULL
                        COMMENT 'Silence penalty score',
    INDEX idx_rec_no (rec_no),
    INDEX idx_score (score_total),
    INDEX idx_analyzed_at (analyzed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Call quality analysis results';

-- ---------------------------------------------------------------------------
-- Migration: Add filename column if tables already exist without it
-- ---------------------------------------------------------------------------
ALTER TABLE rec_audio_quality ADD COLUMN IF NOT EXISTS filename VARCHAR(100) DEFAULT NULL COMMENT 'Recording filename for reference' AFTER rec_no;
ALTER TABLE rec_transcript ADD COLUMN IF NOT EXISTS filename VARCHAR(100) DEFAULT NULL COMMENT 'Recording filename for reference' AFTER rec_no;
ALTER TABLE rec_call_quality ADD COLUMN IF NOT EXISTS filename VARCHAR(100) DEFAULT NULL COMMENT 'Recording filename for reference' AFTER rec_no;
