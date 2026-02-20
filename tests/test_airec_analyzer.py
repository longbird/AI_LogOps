"""Tests for AirREC server-side analyzer modules.

Tests call_quality and keywords without requiring faster-whisper or MySQL.
"""

from __future__ import annotations

import pytest

from server.airec.analyzer.keywords import check_required_phrases, check_forbidden_words
from server.airec.analyzer.call_quality import (
    CallQualityResult,
    compute_first_response,
    compute_talk_ratios,
    compute_scores,
    analyze_call_quality,
)
from server.airec.analyzer.pipeline import PipelineResult


class TestKeywords:
    def test_required_phrases_hit(self):
        hit, matched = check_required_phrases("안녕하세요, 감사합니다")
        assert hit is True
        assert "감사합니다" in matched
        assert "안녕하세요" in matched

    def test_required_phrases_miss(self):
        hit, matched = check_required_phrases("이것은 테스트입니다")
        assert hit is False
        assert matched == []

    def test_forbidden_words_hit(self):
        hit, detected = check_forbidden_words("짜증나네요 이거")
        assert hit is True
        assert "짜증" in detected

    def test_forbidden_words_miss(self):
        hit, detected = check_forbidden_words("안녕하세요 도와드리겠습니다")
        assert hit is False
        assert detected == []


class TestComputeFirstResponse:
    def test_no_segments(self):
        assert compute_first_response([], []) == 0.0

    def test_no_customer(self):
        agent = [type("S", (), {"start": 1.0, "end": 2.0})()]
        assert compute_first_response(agent, []) == 0.0

    def test_normal_response(self):
        cust = [type("S", (), {"start": 1.0, "end": 2.0})()]
        agent = [type("S", (), {"start": 3.0, "end": 4.0})()]
        result = compute_first_response(agent, cust)
        assert abs(result - 2.0) < 0.01

    def test_agent_before_customer(self):
        """Agent spoke before customer - no valid response."""
        cust = [type("S", (), {"start": 5.0, "end": 6.0})()]
        agent = [type("S", (), {"start": 1.0, "end": 2.0})()]
        result = compute_first_response(agent, cust)
        assert result == 0.0


class TestComputeTalkRatios:
    def test_zero_duration(self):
        assert compute_talk_ratios([], [], 0.0) == (0.0, 0.0, 1.0)

    def test_all_silence(self):
        a, c, s = compute_talk_ratios([], [], 60.0)
        assert a == 0.0
        assert c == 0.0
        assert s == 1.0

    def test_with_segments(self):
        agent = [type("S", (), {"start": 0.0, "end": 10.0})()]
        cust = [type("S", (), {"start": 10.0, "end": 20.0})()]
        a, c, s = compute_talk_ratios(agent, cust, 30.0)
        assert abs(a - 0.3333) < 0.01
        assert abs(c - 0.3333) < 0.01
        assert abs(s - 0.3333) < 0.01


class TestComputeScores:
    def test_perfect_scores(self):
        total, resp, phrase, silence = compute_scores(
            first_response=2.0,
            silence_ratio=0.1,
            required_hit=True,
            forbidden_hit=False,
        )
        assert resp == 100.0
        assert phrase == 100.0
        assert silence == 100.0
        assert total == 100.0

    def test_worst_scores(self):
        total, resp, phrase, silence = compute_scores(
            first_response=20.0,
            silence_ratio=0.8,
            required_hit=False,
            forbidden_hit=True,
        )
        assert resp == 0.0
        assert phrase == 0.0
        assert silence == 0.0
        assert total == 0.0

    def test_no_response_data(self):
        total, resp, phrase, silence = compute_scores(
            first_response=0.0,
            silence_ratio=0.3,
            required_hit=False,
            forbidden_hit=False,
        )
        assert resp == 50.0  # neutral when no data
        assert phrase == 50.0


class TestAnalyzeCallQuality:
    def test_with_mock_transcript(self):
        """Test with a mock TranscriptResult-like object."""

        class MockTranscript:
            agent_segments = [type("S", (), {"start": 1.0, "end": 5.0})()]
            customer_segments = [type("S", (), {"start": 0.0, "end": 1.0})()]
            duration_sec = 10.0
            agent_text = "안녕하세요 감사합니다"
            customer_text = "네 부탁드립니다"
            full_text = "네 부탁드립니다 안녕하세요 감사합니다"

        result = analyze_call_quality(MockTranscript())
        assert isinstance(result, CallQualityResult)
        assert result.score_total > 0
        assert result.agent_talk_ratio > 0

    def test_empty_transcript(self):
        class EmptyTranscript:
            agent_segments = []
            customer_segments = []
            duration_sec = 0.0
            agent_text = ""
            customer_text = ""
            full_text = ""

        result = analyze_call_quality(EmptyTranscript())
        assert isinstance(result, CallQualityResult)
        assert result.first_response_sec == 0.0


class TestPipelineResult:
    def test_dataclass(self):
        r = PipelineResult(
            filename="test.wav",
            full_text="test",
            agent_text="a",
            customer_text="c",
            segments_json="[]",
            duration_sec=10.0,
            word_count=1,
            score_total=85.0,
            score_response=90.0,
            score_phrase=80.0,
            score_silence=85.0,
        )
        assert r.filename == "test.wav"
        assert r.score_total == 85.0
