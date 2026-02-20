#!/usr/bin/env python3
"""빌드 → 직접 업데이트 → Telegram 알림 자동 배포 스크립트.

사용법:
    python deploy.py --target-dir PATH                  # 빌드 + 직접 업데이트
    python deploy.py --target-dir PATH --bot-token ...  # 빌드 + 직접 업데이트 + Telegram 알림
    python deploy.py --skip-build --target-dir PATH     # 빌드 생략, 직접 업데이트만
    python deploy.py --bot-token TOKEN --chat-id ID     # Telegram 전송만 (수동 업데이트)

주의 - Telegram 전송 방식:
    Bot API(sendDocument)를 사용하므로 파일이 "봇이 보낸 메시지"로 표시된다.
    에이전트는 사용자→봇 방향 메시지만 처리하므로, 이 스크립트로 전송된 zip은
    에이전트가 자동 수신하지 않는다.
    관리자가 Telegram에서 수신한 zip을 봇 채팅에 직접 전달(포워딩)해야
    에이전트가 파일을 처리하고 /update 를 실행할 수 있다.
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

try:
    import httpx
    import yaml
except ImportError:
    print("ERROR: httpx, pyyaml 필요. pip install httpx pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
AGENT_DIR = DIST_DIR / "AILogOps-Agent"
ZIP_PATH = DIST_DIR / "AILogOps-Agent.zip"
CONFIG_PATH = ROOT / "agent" / "config.yaml"

TELEGRAM_API = "https://api.telegram.org"
MAX_SINGLE_SIZE = 20 * 1024 * 1024
PART_SIZE = 19 * 1024 * 1024

SERVICE_NAME = "AILogOps-Agent"
EXE_NAME = "AILogOps-Agent.exe"


def load_config() -> tuple[str, int]:
    """환경변수 → config.yaml 순으로 bot_token, admin_chat_id 읽기."""
    # .env 로드
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

    # 환경변수 우선
    bot_token = os.environ.get("TELEGRAM_AGENT_BOT_TOKEN", "")
    chat_id_str = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")

    # config.yaml 폴백 (agent → server 순으로 탐색)
    placeholders = {
        "YOUR_BOT_TOKEN",
        "YOUR_AGENT_BOT_TOKEN",
        "YOUR_SERVER_BOT_TOKEN",
        "",
    }
    for cfg_path in [CONFIG_PATH, ROOT / "server" / "config.yaml"]:
        if not cfg_path.exists():
            continue
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        tg = cfg.get("telegram", {})
        if not bot_token:
            # 배포 알림은 관리자에게 전송 → 서버봇 우선, 에이전트봇 폴백
            candidate = str(
                tg.get("server_bot_token", "")
                or tg.get("agent_bot_token", "")
                or tg.get("bot_token", "")
            )
            if candidate not in placeholders:
                bot_token = candidate
        if not chat_id_str or chat_id_str == "0":
            admin_ids = tg.get("admin_chat_ids", [])
            cid = tg.get("admin_chat_id", admin_ids[0] if admin_ids else 0)
            try:
                if int(cid):
                    chat_id_str = str(int(cid))
            except (ValueError, TypeError):
                pass

    try:
        return bot_token, int(chat_id_str) if chat_id_str else 0
    except (ValueError, TypeError):
        return bot_token, 0


def build() -> bool:
    """PyInstaller 빌드."""
    print("=== 빌드 시작 ===")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "agent.spec", "--noconfirm"],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        print("ERROR: 빌드 실패", file=sys.stderr)
        return False

    # PyInstaller 6.x는 datas를 _internal/에 배치하므로,
    # config.yaml을 배포 루트(exe 옆)에도 복사한다.
    config_dst = AGENT_DIR / "config.yaml"
    if not config_dst.exists() and CONFIG_PATH.exists():
        shutil.copy2(str(CONFIG_PATH), str(config_dst))
        print(f"  config.yaml → {config_dst}")

    print("=== 빌드 완료 ===")
    return True


def compress() -> Path:
    """dist/AILogOps-Agent → zip 압축."""
    import zipfile

    print("=== 압축 중 ===")
    if not AGENT_DIR.exists():
        print(f"ERROR: {AGENT_DIR} 없음", file=sys.stderr)
        sys.exit(1)

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in AGENT_DIR.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(AGENT_DIR))

    size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
    print(f"=== 압축 완료: {ZIP_PATH.name} ({size_mb:.1f} MB) ===")
    return ZIP_PATH


def split_zip(zip_path: Path) -> list[Path]:
    """20MB 초과 시 분할."""
    data = zip_path.read_bytes()
    if len(data) <= MAX_SINGLE_SIZE:
        return [zip_path]

    total_parts = math.ceil(len(data) / PART_SIZE)
    parts: list[Path] = []
    for i in range(total_parts):
        chunk = data[i * PART_SIZE : (i + 1) * PART_SIZE]
        part_path = (
            zip_path.parent / f"AILogOps-Agent.part{i + 1:02d}of{total_parts:02d}.zip"
        )
        part_path.write_bytes(chunk)
        parts.append(part_path)
        print(f"  파트: {part_path.name} ({len(chunk) / (1024 * 1024):.1f} MB)")
    return parts


def send_file(bot_token: str, chat_id: int, file_path: Path, caption: str = "") -> bool:
    """Telegram Bot API로 파일 전송."""
    url = f"{TELEGRAM_API}/bot{bot_token}/sendDocument"
    print(f"  전송 중: {file_path.name} ...", end=" ", flush=True)

    with file_path.open("rb") as f:
        files = {"document": (file_path.name, f, "application/zip")}
        data: dict[str, str | int] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        resp = httpx.post(url, data=data, files=files, timeout=300.0)

    if resp.status_code == 200:
        print("OK")
        return True
    else:
        print(f"FAIL ({resp.status_code})")
        print(f"  {resp.text[:300]}", file=sys.stderr)
        return False


def send_message(bot_token: str, chat_id: int, text: str) -> None:
    """Telegram 텍스트 메시지 전송."""
    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=30.0)


# ── 직접 업데이트 ───────────────────────────────────────


def _is_service_running() -> bool:
    """Windows 서비스 실행 여부 확인."""
    result = subprocess.run(
        ["sc", "query", SERVICE_NAME],
        capture_output=True,
        text=True,
    )
    return "RUNNING" in result.stdout


def _stop_agent(target_dir: Path) -> None:
    """에이전트 정지 (서비스 → 프로세스 순서로 시도)."""
    if _is_service_running():
        print("  서비스 정지 중...")
        subprocess.run(["net", "stop", SERVICE_NAME], capture_output=True)
        time.sleep(3)
        return

    result = subprocess.run(
        ["taskkill", "/IM", EXE_NAME, "/F"],
        capture_output=True,
        text=True,
    )
    if "SUCCESS" in result.stdout:
        print(f"  프로세스 종료: {EXE_NAME}")
        time.sleep(2)
    else:
        print("  실행 중인 에이전트 없음 (정상)")


def _start_agent(target_dir: Path) -> bool:
    """에이전트 시작 (서비스 등록 시 서비스, 아닌 경우 exe 직접 실행)."""
    result = subprocess.run(
        ["sc", "query", SERVICE_NAME],
        capture_output=True,
        text=True,
    )
    service_exists = "does not exist" not in result.stderr and result.returncode == 0

    if service_exists:
        print("  서비스 시작 중...")
        start_result = subprocess.run(
            ["net", "start", SERVICE_NAME],
            capture_output=True,
            text=True,
        )
        time.sleep(5)
        if _is_service_running():
            print("  서비스 시작 완료")
            return True
        print(f"  서비스 시작 실패: {start_result.stderr.strip()}")
        return False

    exe_path = target_dir / EXE_NAME
    if not exe_path.exists():
        print(f"  ERROR: {exe_path} 없음")
        return False

    print(f"  프로세스 시작: {exe_path.name}")
    subprocess.Popen(
        [str(exe_path)],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    time.sleep(3)
    return True


def direct_update(target_dir: Path) -> bool:
    """서비스 정지 → 백업 → 파일 교체 → 시작.

    Telegram 불필요. config.yaml 보호.
    """
    print(f"=== 직접 업데이트: {target_dir} ===")

    if not AGENT_DIR.exists():
        print(f"  ERROR: 빌드 결과 없음: {AGENT_DIR}", file=sys.stderr)
        return False

    target_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = target_dir / "backups" / time.strftime("%Y%m%d_%H%M%S")

    # 1. 에이전트 정지
    _stop_agent(target_dir)

    # 2. 백업 (기존 설치가 있을 경우)
    if (target_dir / EXE_NAME).exists():
        print(f"  백업: {backup_dir}")
        backup_dir.mkdir(parents=True, exist_ok=True)
        for item in target_dir.iterdir():
            if item.name in ("temp", "backups", "log"):
                continue
            dest = backup_dir / item.name
            if item.is_dir():
                shutil.copytree(str(item), str(dest))
            else:
                shutil.copy2(str(item), str(dest))
    else:
        print("  신규 설치 (백업 생략)")

    # 3. 파일 복사 (config.yaml 보호)
    print("  파일 복사 중...")
    config_backup = None
    target_config = target_dir / "config.yaml"
    if target_config.exists():
        config_backup = target_config.read_bytes()

    copied = 0
    for item in AGENT_DIR.iterdir():
        dest = target_dir / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(str(item), str(dest))
        else:
            shutil.copy2(str(item), str(dest))
        copied += 1

    if config_backup is not None:
        target_config.write_bytes(config_backup)
        print("  config.yaml 보호됨")

    print(f"  {copied}개 항목 복사 완료")

    # 4. 에이전트 시작
    started = _start_agent(target_dir)

    # 5. 스테이징 정리
    staging = target_dir / "temp" / "update"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    if started:
        print("=== 업데이트 완료 ===")
    else:
        print("=== 업데이트 완료 (시작 실패 - 수동 확인 필요) ===")

    return started


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-LogOps 빌드 + 배포")
    parser.add_argument("--skip-build", action="store_true", help="빌드 생략")
    parser.add_argument("--skip-send", action="store_true", help="Telegram 전송 생략")
    parser.add_argument("--bot-token", default="", help="Telegram Bot Token")
    parser.add_argument("--chat-id", type=int, default=0, help="Telegram Chat ID")
    parser.add_argument(
        "--target-dir",
        default="",
        help="에이전트 설치 경로 → 직접 업데이트 (예: D:\\AirSoft\\AILogOps-Agent)",
    )
    args = parser.parse_args()

    # 1. 빌드
    if not args.skip_build:
        if not build():
            sys.exit(1)

    # 2. 압축 파일 생성 (항상)
    if not args.skip_build or AGENT_DIR.exists():
        compress()

    # 3. --target-dir → 직접 업데이트 (서비스 정지 → 복사 → 시작)
    if args.target_dir:
        target_path = Path(args.target_dir)
        ok = direct_update(target_path)

        # Telegram 알림
        bot_token = args.bot_token
        chat_id = args.chat_id
        if not bot_token or not chat_id:
            cfg_token, cfg_chat_id = load_config()
            bot_token = bot_token or cfg_token
            chat_id = chat_id or cfg_chat_id

        if bot_token and chat_id:
            status = "성공" if ok else "실패 (수동 확인 필요)"
            send_message(
                bot_token,
                chat_id,
                f"에이전트 업데이트 {status}\n경로: {target_path}",
            )

        sys.exit(0 if ok else 1)

    # 4. --target-dir 없으면 Telegram 전송만 (수동 업데이트)
    zip_path = ZIP_PATH

    if args.skip_send:
        print("=== 완료 (전송 생략) ===")
        return

    bot_token = args.bot_token
    chat_id = args.chat_id
    if not bot_token or not chat_id:
        cfg_token, cfg_chat_id = load_config()
        bot_token = bot_token or cfg_token
        chat_id = chat_id or cfg_chat_id

    if not bot_token or not chat_id:
        print(
            "ERROR: bot_token / chat_id 없음. --bot-token, --chat-id 또는 config.yaml 설정 필요",
            file=sys.stderr,
        )
        sys.exit(1)

    print("=== Telegram 전송 ===")
    files = split_zip(zip_path)

    if len(files) == 1:
        ok = send_file(bot_token, chat_id, files[0], caption="AILogOps-Agent 업데이트")
    else:
        send_message(bot_token, chat_id, f"업데이트 파일 전송 ({len(files)}파트)")
        ok = all(send_file(bot_token, chat_id, f) for f in files)

    if ok:
        print("=== 배포 완료 (Telegram에서 zip 전달 후 /update 실행 필요) ===")
    else:
        print("ERROR: 일부 전송 실패", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
