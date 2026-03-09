"""
crypto_utils.py - Fernet 기반 민감한 설정 필드 암호화 유틸리티

민감한 설정값(비밀번호, 토큰, API 키 등)을 암호화하고
복호화하는 기능을 제공합니다.
"""

from __future__ import annotations

import copy
import os

try:
    from cryptography.fernet import Fernet
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    Fernet = None  # type: ignore[assignment,misc]


# 민감한 필드 이름 패턴 목록
SENSITIVE_PATTERNS: list[str] = [
    "password",
    "token",
    "api_key",
    "secret",
    "bot_token",
]

# 마스킹 시 사용할 대체 값
MASK_VALUE = "********"


def _require_crypto() -> None:
    """cryptography 라이브러리가 설치되어 있는지 확인합니다."""
    if not _CRYPTO_AVAILABLE:
        raise ImportError(
            "cryptography 라이브러리가 필요합니다. "
            "'pip install cryptography' 명령으로 설치하세요."
        )


def is_sensitive_field(field_name: str) -> bool:
    """필드 이름이 민감한 패턴을 포함하는지 확인합니다.

    Args:
        field_name: 확인할 필드 이름

    Returns:
        민감한 패턴이 포함되어 있으면 True, 아니면 False
    """
    lower = field_name.lower()
    return any(pattern in lower for pattern in SENSITIVE_PATTERNS)


def generate_key() -> bytes:
    """새로운 Fernet 키를 생성합니다.

    Returns:
        새로 생성된 Fernet 키 바이트
    """
    _require_crypto()
    return Fernet.generate_key()


def load_or_create_key(env_path: str) -> "Fernet":
    """.env 파일에서 FERNET_KEY를 로드하거나, 없으면 새로 생성하여 저장합니다.

    Args:
        env_path: .env 파일 경로

    Returns:
        Fernet 인스턴스
    """
    _require_crypto()

    # .env 파일에서 기존 키 탐색
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("FERNET_KEY="):
                    key_value = line[len("FERNET_KEY="):].strip()
                    if key_value:
                        return Fernet(key_value.encode())

    # 환경 변수에서도 확인
    env_key = os.environ.get("FERNET_KEY")
    if env_key:
        return Fernet(env_key.encode())

    # 키가 없으면 새로 생성하여 .env 파일에 추가
    new_key = Fernet.generate_key()
    key_str = new_key.decode()

    with open(env_path, "a", encoding="utf-8") as f:
        f.write(f"\nFERNET_KEY={key_str}\n")

    return Fernet(new_key)


def encrypt_value(fernet: "Fernet", value: str) -> str:
    """문자열 값을 암호화합니다.

    이미 암호화된 값("ENC:"로 시작)은 그대로 반환합니다.

    Args:
        fernet: Fernet 인스턴스
        value: 암호화할 문자열

    Returns:
        "ENC:" 접두사가 붙은 암호화된 문자열
    """
    _require_crypto()
    token = fernet.encrypt(value.encode()).decode()
    return f"ENC:{token}"


def decrypt_value(fernet: "Fernet", value: str) -> str:
    """암호화된 문자열을 복호화합니다.

    "ENC:"로 시작하지 않으면 원본 값을 그대로 반환합니다.

    Args:
        fernet: Fernet 인스턴스
        value: 복호화할 문자열 ("ENC:" 접두사 포함 가능)

    Returns:
        복호화된 문자열 또는 원본 문자열
    """
    _require_crypto()
    if not value.startswith("ENC:"):
        return value
    token = value[len("ENC:"):]
    return fernet.decrypt(token.encode()).decode()


def encrypt_config(config: dict, fernet: "Fernet") -> dict:
    """설정 딕셔너리에서 민감한 필드를 재귀적으로 암호화합니다.

    - 민감한 패턴의 필드 이름을 가진 문자열 값만 암호화합니다.
    - 이미 "ENC:"로 시작하는 값은 건너뜁니다.
    - 원본 딕셔너리를 수정하지 않고 깊은 복사본을 반환합니다.

    Args:
        config: 암호화할 설정 딕셔너리
        fernet: Fernet 인스턴스

    Returns:
        민감한 필드가 암호화된 새 딕셔너리
    """
    _require_crypto()
    result = copy.deepcopy(config)
    _encrypt_dict_inplace(result, fernet)
    return result


def _encrypt_dict_inplace(d: dict, fernet: "Fernet") -> None:
    """딕셔너리를 재귀적으로 순회하며 민감한 필드를 암호화합니다 (내부용)."""
    for key, value in d.items():
        if isinstance(value, dict):
            _encrypt_dict_inplace(value, fernet)
        elif isinstance(value, str) and is_sensitive_field(key):
            if not value.startswith("ENC:"):
                d[key] = encrypt_value(fernet, value)


def decrypt_config(config: dict, fernet: "Fernet") -> dict:
    """설정 딕셔너리에서 암호화된 필드를 재귀적으로 복호화합니다.

    "ENC:"로 시작하는 모든 문자열 값을 복호화합니다.
    원본 딕셔너리를 수정하지 않고 깊은 복사본을 반환합니다.

    Args:
        config: 복호화할 설정 딕셔너리
        fernet: Fernet 인스턴스

    Returns:
        암호화된 필드가 복호화된 새 딕셔너리
    """
    _require_crypto()
    result = copy.deepcopy(config)
    _decrypt_dict_inplace(result, fernet)
    return result


def _decrypt_dict_inplace(d: dict, fernet: "Fernet") -> None:
    """딕셔너리를 재귀적으로 순회하며 암호화된 값을 복호화합니다 (내부용)."""
    for key, value in d.items():
        if isinstance(value, dict):
            _decrypt_dict_inplace(value, fernet)
        elif isinstance(value, str) and value.startswith("ENC:"):
            d[key] = decrypt_value(fernet, value)


def mask_config(config: dict) -> dict:
    """설정 딕셔너리에서 민감한 필드 값을 마스킹합니다.

    민감한 패턴의 필드 이름을 가진 값을 MASK_VALUE로 대체합니다.
    원본 딕셔너리를 수정하지 않고 깊은 복사본을 반환합니다.

    Args:
        config: 마스킹할 설정 딕셔너리

    Returns:
        민감한 필드가 마스킹된 새 딕셔너리
    """
    result = copy.deepcopy(config)
    _mask_dict_inplace(result)
    return result


def _mask_dict_inplace(d: dict) -> None:
    """딕셔너리를 재귀적으로 순회하며 민감한 필드를 마스킹합니다 (내부용)."""
    for key, value in d.items():
        if isinstance(value, dict):
            _mask_dict_inplace(value)
        elif is_sensitive_field(key):
            d[key] = MASK_VALUE


def apply_masked_update(original: dict, updated: dict) -> dict:
    """업데이트된 설정을 원본에 병합하되, 마스킹된 값은 건너뜁니다.

    updated 딕셔너리의 값이 MASK_VALUE("********")이면 해당 필드는
    원본 값을 유지합니다. 중첩 딕셔너리는 재귀적으로 처리합니다.
    원본 딕셔너리를 수정하지 않고 깊은 복사본을 반환합니다.

    Args:
        original: 원본 설정 딕셔너리
        updated: 병합할 업데이트 딕셔너리 (마스킹된 값 포함 가능)

    Returns:
        병합된 새 딕셔너리
    """
    result = copy.deepcopy(original)
    _apply_masked_update_inplace(result, updated)
    return result


def _apply_masked_update_inplace(target: dict, source: dict) -> None:
    """재귀적으로 업데이트를 병합하되, 마스킹된 값은 건너뜁니다 (내부용)."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            # 양쪽 모두 딕셔너리인 경우 재귀 병합
            _apply_masked_update_inplace(target[key], value)
        elif value == MASK_VALUE:
            # 마스킹된 값은 원본 유지 (변경 없음)
            pass
        else:
            target[key] = copy.deepcopy(value)
