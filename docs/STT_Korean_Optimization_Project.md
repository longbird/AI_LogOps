# AI-LogOps STT 한국어 최적화 프로젝트 문서

> 작성일: 2026-02-24  
> 대상 파일: `server/airec/analyzer/stt.py`, `server/config.yaml`  
> 테스트 데이터: `storage/recordings/PC-DAERIGO/20260221~20260224`

---

## 1. 프로젝트 개요

### 배경

AI-LogOps는 대리운전 콜센터 녹취를 자동으로 STT(음성→텍스트) 변환하고 통화 품질을 분석하는 시스템이다. 로컬 Whisper 엔진(`faster-whisper`)을 사용하며, 서버는 61.42.53.61에서 운영된다.

### 핵심 요구사항

사용자가 제시한 개선 요구는 다음과 같았다:

> "whisper 모델에서 한글 특화해서 더 정확하게 STT 인식할 수 있는 방법을 찾아줘"

→ 이후 6가지 개선 항목으로 구체화:

1. 한국어 파인튜닝 모델 도입
2. 콜센터 상담 도메인 설정 자동 업데이트
3. 자료 축적 및 자동 학습
4. VAD(Voice Activity Detection) 활성화
5. 환각 방지(Hallucination Prevention)
6. 오디오 전처리 추가

### 주요 문제 (최종 과제)

> "녹취 분석시 1덩어리로 표시됨 → 시간대별로 대화 형식으로 분리 분석, 표시해줘"

전체 대화가 1개의 세그먼트로 출력되어 시간대별 발화 구분이 불가능한 상태였다.

---

## 2. 제시된 프롬프트 목록 (사용자 요청 원문)

| # | 프롬프트 (원문) | 분류 |
|---|---|---|
| 1 | "whisper 모델에서 한글 특화해서 더 정확하게 STT 인식할 수 있는 방법을 찾아줘" | 탐색/연구 |
| 2 | "1. 한국어 파인튜닝 모델을 도입. 2. 콜센터 상담 도메인 설정 자동 업데이트. 3. 자료 축적 및 자동 학습. 4. VAD 활성화. 5. 환각 방지. 6. 오디오 전처리 추가." | 구현 지시 |
| 3 | "ralph loop 방식으로 완료할때까지 진행" | 실행 방식 |
| 4 | "해당 파일들을 이용해서 자체 테스트를 통해 적용 효과가 제대로 나오는지 확인하고 최대한의 효과가 나올때까지 방법을 찾아서 적용해줘" | 검증 요구 |
| 5 | "녹취 분석시 1덩어리로 표시됨 → 시간대별로 대화 형식으로 분리 분석, 표시해줘" | 핵심 버그 수정 |

---

## 3. 도메인 특성

| 항목 | 내용 |
|---|---|
| 서비스 | 대리운전 콜센터 |
| 녹음 형식 | µ-law fmt=7 (모노), PCM (스테레오), 8kHz |
| 채널 구성 | 스테레오: Left=고객(외부), Right=상담원(내선) |
| 통화 시간 | 10~92초 |
| 주요 발화 내용 | 출발지·도착지, 요금 협상, 배차 요청 |
| 언어 특성 | 짧은 발화("네", "예"), 지명(역명, 동명), 구어체 |

---

## 4. 핵심 발견사항

### 4.1 한국어 파인튜닝 모델 실패

- **테스트 모델**: `ghost613/faster-whisper-large-v3-turbo-korean`
- **결과**: µ-law 8kHz 전화 음성에서 0개 유효 세그먼트 생성 → **채택 불가**
- **원인**: 모델이 깨끗한 음성(마이크 녹음) 기준으로 파인튜닝되어 전화 음성 특성을 처리하지 못함
- **결론**: 기본 `large-v3-turbo` 모델이 전화 녹음에서 가장 안정적

### 4.2 1덩어리 문제 근본 원인 분석

| 원인 | 내용 |
|---|---|
| VAD `min_silence_duration_ms: 700` | 전화 대화의 자연 멈춤(200~400ms)보다 높아 발화 구분 실패 |
| Whisper 내부 처리 방식 | 긴 오디오를 1개의 raw 세그먼트로 생성, VAD가 N개로 분할하지만 word timestamps가 raw 세그먼트 기준으로 저장됨 |
| 세그먼트 분할 로직 부재 | 26초짜리 세그먼트를 추가 분할하는 후처리 없음 |

### 4.3 A/B 테스트 결과 (최적 파라미터)

10개 파일, 4개 날짜, 모노+스테레오, 3가지 설정 비교:

| 설정 | 결과 |
|---|---|
| 기본값 (medium 모델, VAD 비활성) | 세그먼트 수 최소, 인식률 낮음 |
| large-v3-turbo + 전처리 활성 | **최적** — 전처리가 텍스트 길이는 약간 줄이나 품질 향상 |
| 한국어 파인튜닝 모델 | 전화 음성 0개 출력 — 채택 불가 |

---

## 5. 구현 내용

### 5.1 `server/config.yaml` — `stt:` 섹션 추가

```yaml
stt:
  whisper:
    model: "large-v3-turbo"          # 전화 녹음 최적 모델
    device: "cpu"
    compute_type: "int8"
    beam_size: 5
    language: "ko"
    vad_filter: true
    vad_parameters:
      threshold: 0.35                # 전화 음성 맞춤 (기본값보다 낮춤)
      min_speech_duration_ms: 150    # 짧은 "네", "예" 캡처
      min_silence_duration_ms: 300   # 700→300 (핵심 수정)
      speech_pad_ms: 200
    hallucination_prevention:
      compression_ratio_threshold: 2.4
      log_prob_threshold: -1.0
      no_speech_threshold: 0.8       # 0.6→0.8
      condition_on_previous_text: true
      temperature: [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
  preprocessing:
    enabled: true
    resample_rate: 16000
    bandpass: {enabled: true, low_freq: 300, high_freq: 3400}
    noise_reduction: {enabled: true, prop_decrease: 0.8}
    normalize: {enabled: true, target_dbfs: -20.0}
```

### 5.2 `server/airec/analyzer/stt.py` — 주요 추가 기능

#### (1) 오디오 전처리 파이프라인 (`_preprocess_audio`)

```
WAV 로드 → µ-law 디코딩 → 리샘플링(16kHz) → 대역필터(300~3400Hz) → 노이즈감소 → 음량정규화(-20dBFS)
```

- 의존성: `scipy`, `noisereduce`
- 전화 음성 대역(300~3400Hz)만 추출하여 잡음 제거

#### (2) 도메인 프롬프트 (`_get_domain_prompt`, `_update_domain_prompt`)

```
대리운전 도메인 어휘 → Whisper initial_prompt로 주입
STT 결과에서 위치/지명 자동 추출 → 50건마다 프롬프트 자동 갱신
```

초기 프롬프트:
> "안녕하세요, 대리운전입니다. 어디로 모실까요? 출발지와 도착지를 말씀해 주세요. 강남역, 홍대입구역, 서울역..."

#### (3) VAD 최적화

```python
vad_parameters = {
    "threshold": 0.35,              # 민감도 향상
    "min_speech_duration_ms": 150,  # 200→150: "네" 캡처
    "min_silence_duration_ms": 300, # 700→300: 핵심 수정
    "speech_pad_ms": 200,
}
```

VAD 파라미터 변경 효과: 92초 파일 기준 1개 → 8개 세그먼트

#### (4) 환각 방지

```python
no_speech_threshold: 0.8   # 0.6→0.8
compression_ratio_threshold: 2.4
log_prob_threshold: -1.0
temperature: [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]  # 폴백 체인
```

#### (5) 데이터 축적 및 자동 학습 (`_accumulate_stt_data`)

- STT 결과에서 위치/지명 패턴 자동 추출
- `storage/stt/training_data/`에 누적 저장
- 100건마다 vocabulary 갱신, 50건마다 프롬프트 갱신

#### (6) 모델명 동적 매핑 (`pipeline.py`)

```python
def _get_engine_model_name(engine: str) -> str:
    cfg = _get_stt_config()
    if engine == "local":
        return cfg.get("whisper", {}).get("model", "large-v3-turbo")
    ...
```

---

## 6. 세그먼트 분할 알고리즘 (핵심 구현)

### 6.1 문제 구조

```
Whisper 출력: [raw_seg_0: 4.9~89.8s, 20개 단어]
         ↓ VAD 분할
결과 세그먼트: [seg_0: 4.9~13.3s] [seg_1: 31.8~32.4s] ... [seg_7: 33.8~57.4s]
word_segments: [seg_0: 20개 단어]  [seg_1: 0개]        ... [seg_7: 0개]
                ↑ 인덱스 불일치!
```

### 6.2 구현된 함수들

#### `_map_words_to_segments(segments, word_segments) → list[list]`

- **역할**: word timestamps를 VAD 분할 이후의 실제 세그먼트에 재매핑
- **방법**: 모든 단어를 시간순 정렬 후 단어 중간 시점(mid)이 세그먼트 범위에 속하면 해당 세그먼트에 할당

```python
w_mid = (w_start + w_end) / 2
if w_mid <= seg.end + 0.1 or seg_idx == len(segments) - 1:
    mapped[seg_idx].append(word)
```

#### `_split_long_segments(segments, max_duration=8.0, word_segments) → list[Segment]`

- **역할**: 8초 초과 세그먼트를 후처리로 분할
- **흐름**:
  1. word timestamps 있음 → `_split_by_words()`
  2. word timestamps 없음 → `_split_by_text()`
  3. 분할 후에도 초과 세그먼트 존재 → `_split_by_time()` 재적용

#### `_split_by_words(seg, words, max_duration) → list[Segment]`

- 한국어 문장 종결 패턴(`_KO_SENTENCE_END`)으로 분할 포인트 탐색
- **핵심 수정**: `elapsed` 계산을 항상 첫 단어 기준이 아닌 **마지막 분할 시점** 기준으로 변경

```python
last_split_time = words[0][1]
for i, (word, w_start, w_end) in enumerate(words):
    if _KO_SENTENCE_END.search(word):
        elapsed = w_end - last_split_time  # ← 수정: words[0][1] 아님
        if elapsed >= max_duration * 0.4:
            split_points.append(i)
            last_split_time = w_end
```

- 분할 후 잔여 세그먼트가 여전히 초과이면 `_split_by_time()`으로 재분할

#### `_split_by_time(seg, words, max_duration) → list[Segment]`

두 가지 전략:

1. **Gap 우선 분할**: 단어 사이 무음 > 2초이면 해당 지점에서 강제 분할
2. **균등 분할 폴백**: Gap 없으면 총 시간 ÷ 필요 청크 수로 균등 분할

```python
# 전략 1: 단어 간 gap > 2초이면 분할
gap_threshold = 2.0
for i in range(len(words) - 1):
    if words[i+1][1] - words[i][2] >= gap_threshold:
        gap_splits.append(i)
```

#### `_split_by_text(seg, max_duration) → list[Segment]`

- word timestamps 없을 때 텍스트 기반 fallback
- `_KO_SENTENCE_END` 패턴으로 분할 위치 탐색 → 텍스트 길이 비율로 시간 배분

#### `_KO_SENTENCE_END` 정규식

```python
_KO_SENTENCE_END = re.compile(
    r'(?<=[.?!。])'
    r'|(?<=합니다)|(?<=습니다)|(?<=세요)|(?<=거든요)|(?<=잖아요)'
    r'|(?<=는데요)|(?<=이에요)|(?<=예요)|(?<=이요)|(?<=해요)'
    r'|(?<=하죠)|(?<=구요)|(?<=네요)|(?<=군요)'
    r'|(?<=인데)|(?<=는데)|(?<=지요)|(?<=죠)'
    r'|(?<=요)\s'
)
```

---

## 7. 테스트 결과

### 7.1 테스트 파일

| 라벨 | 파일 | 형식 | 시간 |
|---|---|---|---|
| 92s-hard | `000022.1.01022229821._.3306.2390.7006.1439.wav` | 스테레오 PCM | 92초 |
| 34s-medium | `000019.1.01083883978._.3319.9627.7095.4653.wav` | 모노 µ-law | 34초 |
| 28s-reference | `000016.1.01089500883._.3306.2157.7006.5221.wav` | 모노 µ-law | 28초 |
| 23s-stereo | `000006.2._.01090527648.3317._._.9792.wav` | 스테레오 | 23초 |
| edge-case | `000023.1.01057231092._.3322.2099.7005.4826.wav` | 모노 µ-law | 13초 |

### 7.2 최종 결과 (모든 테스트 PASS)

| 파일 | 수정 전 (최대 세그먼트) | 수정 후 (최대 세그먼트) | 세그먼트 수 |
|---|---|---|---|
| 92s-hard (스테레오 전체 파이프라인) | 1개 덩어리 → 26초 | **5.9초** | 21개 |
| 34s-medium | 1개 → 4개 (8.8초) | **3.3초** | 10개 |
| 28s-reference | 14개 (이미 양호) | **2.5초** | 14개 |
| 23s-stereo | N/A | **7.8초** | 10개 |
| edge-case | 8.8초 | **5.0초** | 4개 |

### 7.3 파이프라인 출력 예시 (92초 스테레오 파일)

```
[ 0]   0.6~  4.4s ( 3.7s) [agent   ]: 안녕하세요, 사장님입니다. 어디세요? 어디세요?
[ 1]   4.9~  5.2s ( 0.3s) [customer]: 그렇죠.
[ 2]   5.1~  8.5s ( 3.5s) [agent   ]: 네, 네. 지금이 어디세요?
[ 3]   8.2~  8.7s ( 0.5s) [customer]: 여기가,
[ 4]   8.9~  9.2s ( 0.3s) [agent   ]: 네.
[ 5]  12.3~ 13.3s ( 0.9s) [customer]: 바꿨죠.
...
[20]  87.9~ 88.9s ( 1.0s) [agent   ]: 네.
```

---

## 8. 작업 순서 (타임라인)

### 세션 1 (이전 세션)

| 단계 | 작업 | 파일 |
|---|---|---|
| 1 | STT 설정 전체 구조 설계 | `config.yaml` |
| 2 | 오디오 전처리 파이프라인 구현 | `stt.py` |
| 3 | 도메인 프롬프트 시스템 구현 | `stt.py` |
| 4 | VAD 활성화 | `stt.py` |
| 5 | 환각 방지 파라미터 추가 | `stt.py` |
| 6 | 데이터 축적 및 자동 학습 구현 | `stt.py` |
| 7 | 동적 모델명 매핑 | `pipeline.py` |
| 8 | A/B 테스트 (10개 파일, 3가지 설정) | `tests/test_stt_korean_optimization.py` |
| 9 | 한국어 파인튜닝 모델 실패 확인 → large-v3-turbo 채택 | — |

### 세션 2 (이전 세션)

| 단계 | 작업 |
|---|---|
| 10 | `no_speech_threshold` 0.6 → 0.8 |
| 11 | `_DEFAULT_STT_CONFIG` 동기화 |
| 12 | A/B 테스트 v2 (모노+스테레오) |
| 13 | VAD 파라미터 튜닝: `min_silence_duration_ms` 700→300 |
| 14 | `word_timestamps=True` 추가 |
| 15 | `_split_long_segments()` 기본 구조 구현 |
| 16 | `_KO_SENTENCE_END` 정규식 정의 |

### 세션 3 (이번 세션)

| 단계 | 작업 | 결과 |
|---|---|---|
| 17 | `_split_by_text()` 누락된 `return` 문 추가 | 버그 수정 |
| 18 | `_map_words_to_segments()` 구현 | word_segments 인덱스 불일치 해결 |
| 19 | `transcribe()` 에서 매핑 호출 추가 | 재매핑 적용 |
| 20 | `_split_by_words()` elapsed 기준 수정 | 첫 단어 → 마지막 분할 시점 기준 |
| 21 | 분할 후 초과 세그먼트 재분할 추가 | `_split_by_time()` 재적용 |
| 22 | `_split_by_time()` gap 감지 로직 추가 | 22초 무음 구간 처리 |
| 23 | 전체 테스트: 모든 파일 ≤8초 확인 | **모두 PASS** |
| 24 | 파이프라인 통합 테스트 | 21개 세그먼트, 최대 5.9초 |

---

## 9. 최종 파라미터 요약

```python
# 모델
model: "large-v3-turbo"   # µ-law 8kHz 전화 음성 최적
device: "cpu"
compute_type: "int8"

# VAD
vad_filter: True
threshold: 0.35
min_speech_duration_ms: 150   # 단, "네" 캡처
min_silence_duration_ms: 300  # 핵심: 700 → 300
speech_pad_ms: 200

# 환각 방지
no_speech_threshold: 0.8
compression_ratio_threshold: 2.4
condition_on_previous_text: True
temperature: [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

# 세그먼트 분할
word_timestamps: True
max_segment_duration: 8.0초
split_strategy: "sentence_boundary → gap_detection → time_equal"

# 전처리
resample_rate: 16000Hz
bandpass: 300~3400Hz
noise_reduction: 0.8
normalize: -20dBFS
```

---

## 10. 수정된 파일 목록

| 파일 | 변경 내용 |
|---|---|
| `server/config.yaml` | `stt:` 섹션 전체 추가 (VAD, 환각방지, 전처리, 프롬프트, 학습 설정) |
| `server/airec/analyzer/stt.py` | 전처리, VAD, 환각방지, 도메인 프롬프트, 데이터축적, word_timestamps, 세그먼트 분할 함수 일체 |
| `server/airec/analyzer/pipeline.py` | 동적 모델명 매핑 (`_get_engine_model_name`) |
| `requirements.txt` | `scipy`, `noisereduce` 추가 |
| `tests/test_stt_korean_optimization.py` | A/B 테스트 스크립트 (v2) |

---

## 11. 알려진 제약사항

| 항목 | 내용 |
|---|---|
| 처리 속도 | CPU int8 모드로 실시간보다 느림 (92초 파일 기준 약 3~5분) |
| 한국어 파인튜닝 모델 | µ-law 전화 음성 미지원 — 깨끗한 마이크 음성에만 사용 가능 |
| 세그먼트 분할 한계 | 22초 이상 무음 구간이 있는 경우 gap 분할이 적용되나 텍스트가 단편적일 수 있음 |
| 화자 분리 | 스테레오(채널 기반)는 정확하나 모노는 화자 구분 불가 (speaker="unknown") |
| Word timestamps | Whisper가 sparse하게 생성하는 경우(단어 수 < 5) 분할 품질 저하 가능 |
