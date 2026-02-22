#!/usr/bin/env python3
"""AI-LogOps 통합 서버 실행 진입점.

모든 서버 컴포넌트(TCP 서버, 대시보드, 텔레그램 봇, 헬스 모니터)를
단일 asyncio 이벤트 루프에서 동시에 실행한다.

사용법:
    # 전체 컴포넌트 실행
    python run_server.py

    # 설정 파일 지정
    python run_server.py --config server/config.yaml

    # 특정 컴포넌트 비활성화
    python run_server.py --no-dashboard
    python run_server.py --no-telegram
    python run_server.py --no-health
    python run_server.py --no-dashboard --no-telegram

환경변수 (.env 또는 시스템):
    OPENAI_API_KEY          OpenAI API 키 (config.yaml 값 덮어쓰기)
    ANTHROPIC_API_KEY       Anthropic API 키 (config.yaml 값 덮어쓰기)
    TELEGRAM_SERVER_BOT_TOKEN  텔레그램 서버봇 토큰 (config.yaml 값 덮어쓰기)
    TELEGRAM_AGENT_BOT_TOKEN   텔레그램 에이전트봇 토큰 (config.yaml 값 덮어쓰기)
    TELEGRAM_AGENT_CHAT_ID     에이전트 채팅 ID (config.yaml 값 덮어쓰기)
    TELEGRAM_ADMIN_CHAT_ID     텔레그램 관리자 채팅 ID (config.yaml 값 덮어쓰기)
    DASHBOARD_SECRET_KEY    대시보드 JWT 시크릿 키 (config.yaml 값 덮어쓰기)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

# 프로젝트 루트를 sys.path 맨 앞에 추가
_ROOT = os.path.abspath(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.utils import load_dotenv, load_yaml_config, setup_logging  # noqa: E402

logger = setup_logging("run_server")

# ---------------------------------------------------------------------------
# 배너 출력
# ---------------------------------------------------------------------------

_BANNER = r"""
╔══════════════════════════════════════════════════════════╗
║              AI-LogOps Integrated Server                 ║
╚══════════════════════════════════════════════════════════╝
"""


def _print_banner(
    tcp_host: str,
    tcp_port: int,
    dashboard_enabled: bool,
    dashboard_host: str,
    dashboard_port: int,
    telegram_enabled: bool,
    health_enabled: bool,
) -> None:
    try:
        print(_BANNER)
    except UnicodeEncodeError:
        print("=" * 58)
        print("  AI-LogOps Integrated Server")
        print("=" * 58)
    print(f"  TCP Server   : {tcp_host}:{tcp_port}")
    if dashboard_enabled:
        print(f"  Dashboard    : http://{dashboard_host}:{dashboard_port}")
    else:
        print("  Dashboard    : disabled")
    print(f"  Telegram Bot : {'enabled' if telegram_enabled else 'disabled'}")
    print(f"  Health Mon.  : {'enabled' if health_enabled else 'disabled'}")
    print()


# ---------------------------------------------------------------------------
# CLI 인수 파싱
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI-LogOps 통합 서버 — TCP, 대시보드, 텔레그램, 헬스 모니터 동시 실행",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default="server/config.yaml",
        help="설정 파일 경로 (기본값: server/config.yaml)",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="대시보드(FastAPI/uvicorn) 비활성화",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="텔레그램 봇 비활성화",
    )
    parser.add_argument(
        "--no-health",
        action="store_true",
        help="헬스 모니터 비활성화",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# 설정 로드 및 환경변수 오버라이드
# ---------------------------------------------------------------------------


def _load_config(config_path: str) -> dict[str, Any]:
    """YAML 설정 로드 후 환경변수로 덮어쓴다."""
    cfg: dict[str, Any] = load_yaml_config(config_path)

    # AI 키 오버라이드
    ai_cfg: dict[str, Any] = cfg.setdefault("ai", {})
    openai_cfg: dict[str, Any] = ai_cfg.setdefault("openai", {})
    claude_cfg: dict[str, Any] = ai_cfg.setdefault("claude", {})

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        openai_cfg["api_key"] = openai_key

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        claude_cfg["api_key"] = anthropic_key

    # 텔레그램 오버라이드
    tg_cfg: dict[str, Any] = cfg.setdefault("telegram", {})
    server_bot_token = os.environ.get("TELEGRAM_SERVER_BOT_TOKEN")
    if server_bot_token:
        tg_cfg["server_bot_token"] = server_bot_token

    agent_bot_token = os.environ.get("TELEGRAM_AGENT_BOT_TOKEN")
    if agent_bot_token:
        tg_cfg["agent_bot_token"] = agent_bot_token

    tg_agent_chat = os.environ.get("TELEGRAM_AGENT_CHAT_ID")
    if tg_agent_chat:
        try:
            tg_cfg["agent_chat_id"] = int(tg_agent_chat)
        except ValueError:
            logger.warning(
                "TELEGRAM_AGENT_CHAT_ID 값이 정수가 아닙니다: %s", tg_agent_chat
            )

    tg_admin = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
    if tg_admin:
        try:
            tg_cfg["admin_chat_ids"] = [int(tg_admin)]
        except ValueError:
            logger.warning("TELEGRAM_ADMIN_CHAT_ID 값이 정수가 아닙니다: %s", tg_admin)

    # 대시보드 시크릿 오버라이드
    dash_cfg: dict[str, Any] = cfg.setdefault("dashboard", {})
    dash_secret = os.environ.get("DASHBOARD_SECRET_KEY")
    if dash_secret:
        dash_cfg["secret_key"] = dash_secret

    return cfg


# ---------------------------------------------------------------------------
# 텔레그램 봇 실행 태스크
# ---------------------------------------------------------------------------


async def _run_telegram(
    server_bot_token: str,
    admin_chat_ids: list[int],
    tcp_server: Any,
    session_mgr: Any,
    storage_mgr: Any,
    ai_pipeline: Any,
    shutdown_event: asyncio.Event,
) -> None:
    """서버봇 텔레그램: 관리자 명령 수신 → TCP 기반 처리 → 응답 전송."""
    try:
        from telegram import Update  # type: ignore[import]
        from telegram.ext import (  # type: ignore[import]
            Application,
            CommandHandler,
            MessageHandler,
            filters,
        )
    except ImportError:
        logger.error(
            "python-telegram-bot 패키지가 설치되지 않았습니다. "
            "pip install python-telegram-bot 으로 설치하세요."
        )
        return

    from server.telegram.handler import TelegramHandler
    from server.telegram.log_commands import LogCommandHandler
    from server.telegram.deploy_commands import DeployCommandHandler
    from server.telegram.ai_commands import AICommandHandler
    from server.telegram.rec_commands import RecCommandHandler

    # ── TelegramHandler (TCP 기반 명령 라우터) ──
    tg_handler = TelegramHandler(admin_chat_ids=admin_chat_ids)

    log_cmd = LogCommandHandler(tcp_server=tcp_server, session_mgr=session_mgr)
    log_cmd.register_all(tg_handler)

    storage_dir = str(storage_mgr.base_dir) if storage_mgr is not None else "./storage"
    deploy_cmd = DeployCommandHandler(
        tcp_server=tcp_server,
        session_mgr=session_mgr,
        storage_dir=storage_dir,
    )
    deploy_cmd.register_all(tg_handler)

    if ai_pipeline is not None:
        ai_cmd = AICommandHandler(pipeline=ai_pipeline, session_mgr=session_mgr)
        ai_cmd.register_all(tg_handler)

    rec_cmd = RecCommandHandler(tcp_server=tcp_server, session_mgr=session_mgr)
    rec_cmd.register_all(tg_handler)

    # ── 서버봇: 결과 수신 폴링 ──
    application = Application.builder().token(server_bot_token).build()

    async def _handle_result(update: Update, context: Any) -> None:
        """에이전트가 서버봇으로 보낸 결과 수신 → 로그 출력."""
        if update.message is None or update.message.text is None:
            return
        chat_id = update.message.chat_id
        text = update.message.text
        sender = update.message.from_user
        sender_name = sender.first_name if sender else "unknown"

        logger.info(
            "에이전트 결과 수신 [chat_id=%s, from=%s]: %s",
            chat_id,
            sender_name,
            text[:200],
        )
        # 콘솔에도 출력
        print(f"\n{'=' * 60}")
        print(f"[에이전트 결과] from={sender_name} chat_id={chat_id}")
        print(f"{text}")
        print(f"{'=' * 60}\n")

    async def _handle_command(update: Update, context: Any) -> None:
        """서버봇에서 관리자 명령 수신 → TCP 기반 명령 처리."""
        if update.message is None or update.message.text is None:
            return
        chat_id = update.message.chat_id
        text = update.message.text

        # 관리자 인증
        if chat_id not in set(admin_chat_ids):
            return

        # TCP 기반 명령 라우터로 처리 → 관리자에게 응답
        response = await tg_handler.handle(text=text, chat_id=chat_id)
        await update.message.reply_text(response)

    # 명령 + 텍스트 모두 처리
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_result)
    )

    known_commands = [
        "log_hist",
        "log_real",
        "deploy",
        "rollback",
        "analyze",
        "plan",
        "fix",
        "auto",
        "rec_analyze",
    ]
    for cmd_name in known_commands:
        application.add_handler(CommandHandler(cmd_name, _handle_command))

    logger.info("서버봇 폴링 시작 중 (결과 수신용)...")
    try:
        await application.initialize()
        await application.start()
        if application.updater is not None:
            await application.updater.start_polling(drop_pending_updates=True)
        logger.info("서버봇 폴링 시작됨 (에이전트 결과 대기 중)")

        await shutdown_event.wait()

    finally:
        logger.info("서버봇 종료 중...")
        try:
            if application.updater is not None:
                await application.updater.stop()
            await application.stop()
            await application.shutdown()
        except Exception:
            logger.exception("서버봇 종료 중 오류 발생")


# ---------------------------------------------------------------------------
# 대시보드(uvicorn) 실행 태스크
# ---------------------------------------------------------------------------


class _PollingAccessLogFilter(logging.Filter):
    """고빈도 폴링 엔드포인트를 uvicorn access log에서 제외."""

    _SUPPRESS = ("/api/deploy/status",)

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(ep in msg for ep in self._SUPPRESS)


async def _run_dashboard(
    fastapi_app: Any,
    host: str,
    port: int,
    shutdown_event: asyncio.Event,
) -> None:
    """uvicorn.Server를 asyncio 태스크로 실행한다 (블로킹 없음)."""
    try:
        import uvicorn  # type: ignore[import]
    except ImportError:
        logger.error(
            "uvicorn 패키지가 설치되지 않았습니다. pip install uvicorn 으로 설치하세요."
        )
        return

    # 폴링 엔드포인트 access log 필터 적용
    logging.getLogger("uvicorn.access").addFilter(_PollingAccessLogFilter())

    config = uvicorn.Config(
        app=fastapi_app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)

    # uvicorn이 자체 시그널 핸들러를 설치하지 않도록 설정 (Windows 호환)
    # setattr으로 인스턴스 메서드를 no-op으로 교체
    setattr(server, "install_signal_handlers", lambda: None)

    logger.info("대시보드 시작 중: http://%s:%s", host, port)

    # serve()를 태스크로 실행하고 shutdown_event 대기
    serve_task = asyncio.create_task(server.serve())

    await shutdown_event.wait()

    # uvicorn 정상 종료
    server.should_exit = True
    try:
        await asyncio.wait_for(serve_task, timeout=10.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        serve_task.cancel()
        try:
            await serve_task
        except (asyncio.CancelledError, Exception):
            pass

    logger.info("대시보드 종료됨")


# ---------------------------------------------------------------------------
# 메인 비동기 진입점
# ---------------------------------------------------------------------------


async def _main(args: argparse.Namespace) -> None:
    # 1. .env 로드
    load_dotenv()

    # 2. 설정 로드
    config_path = args.config
    if not Path(config_path).exists():
        logger.error("설정 파일을 찾을 수 없습니다: %s", config_path)
        sys.exit(1)

    cfg = _load_config(config_path)

    # 3. 설정값 추출
    server_cfg: dict[str, Any] = cfg.get("server", {})
    tcp_host: str = str(server_cfg.get("tcp_host", "0.0.0.0"))
    tcp_port: int = int(server_cfg.get("tcp_port", 9500))
    max_agents: int = int(server_cfg.get("max_agents", 10))

    ai_cfg: dict[str, Any] = cfg.get("ai", {})
    default_provider: str = str(ai_cfg.get("default_provider", "openai"))
    openai_cfg: dict[str, Any] = ai_cfg.get("openai", {})
    claude_cfg: dict[str, Any] = ai_cfg.get("claude", {})

    tg_cfg: dict[str, Any] = cfg.get("telegram", {})
    server_bot_token: str = str(tg_cfg.get("server_bot_token", ""))
    admin_chat_ids: list[int] = [int(x) for x in tg_cfg.get("admin_chat_ids", [])]

    dash_cfg: dict[str, Any] = cfg.get("dashboard", {})
    dash_host: str = str(dash_cfg.get("host", "0.0.0.0"))
    dash_port: int = int(dash_cfg.get("port", 8080))
    dash_public_url: str = str(dash_cfg.get("public_url", "")).strip()
    dash_secret: str = str(dash_cfg.get("secret_key", "CHANGE_ME"))

    storage_cfg: dict[str, Any] = cfg.get("storage", {})
    storage_base_dir: str = str(storage_cfg.get("base_dir", "./storage"))
    max_retention_days: int = int(storage_cfg.get("max_log_retention_days", 30))
    max_backups: int = int(storage_cfg.get("max_backups_per_agent", 5))

    health_cfg: dict[str, Any] = cfg.get("health", {})

    # 4. 컴포넌트 활성화 여부 결정
    dashboard_enabled = not args.no_dashboard
    health_enabled = not args.no_health

    # 텔레그램: --no-telegram 또는 토큰 미설정 시 비활성화
    telegram_enabled = not args.no_telegram
    if telegram_enabled and (
        not server_bot_token or server_bot_token in ("YOUR_SERVER_BOT_TOKEN", "")
    ):
        logger.warning(
            "서버봇 토큰이 설정되지 않았습니다. 텔레그램을 비활성화합니다. "
            "TELEGRAM_SERVER_BOT_TOKEN 환경변수 또는 config.yaml의 "
            "telegram.server_bot_token을 설정하세요."
        )
        telegram_enabled = False
    if telegram_enabled and not admin_chat_ids:
        logger.warning(
            "관리자 채팅 ID가 설정되지 않았습니다. 텔레그램을 비활성화합니다. "
            "TELEGRAM_ADMIN_CHAT_ID 환경변수 또는 config.yaml의 "
            "telegram.admin_chat_ids를 설정하세요."
        )
        telegram_enabled = False

    # 5. 배너 출력
    _print_banner(
        tcp_host=tcp_host,
        tcp_port=tcp_port,
        dashboard_enabled=dashboard_enabled,
        dashboard_host=dash_host,
        dashboard_port=dash_port,
        telegram_enabled=telegram_enabled,
        health_enabled=health_enabled,
    )

    # 6. 핵심 컴포넌트 초기화
    from server.core.session_mgr import SessionManager
    from server.core.tcp_server import TCPServer
    from server.storage.manager import StorageManager
    from server.airec.rec_handler import RecHandler
    from server.airec.storage import RecordingStorage

    session_mgr = SessionManager(max_agents=max_agents)
    storage_mgr = StorageManager(
        base_dir=storage_base_dir,
        max_retention_days=max_retention_days,
        max_backups=max_backups,
    )
    # upload_base_url: 에이전트가 HTTP로 접근 가능한 주소여야 함
    if dash_public_url:
        upload_base_url = dash_public_url.rstrip("/")
    else:
        upload_base_url = f"http://{dash_host}:{dash_port}"
        if dash_host in ("0.0.0.0", "::"):
            logger.warning(
                "dashboard.public_url 미설정 + host=%s → 원격 에이전트가 녹취 업로드 불가. "
                "server/config.yaml 의 dashboard.public_url 을 설정하세요.",
                dash_host,
            )
    rec_handler = RecHandler(upload_base_url=upload_base_url)
    rec_storage = RecordingStorage(base_dir=str(Path(storage_base_dir) / "recordings"))

    # 7. AI 프로바이더 및 파이프라인 초기화
    ai_pipeline = None
    try:
        from server.ai.provider import AIProviderFactory
        from server.ai.pipeline import AIPipeline

        openai_key: str = str(openai_cfg.get("api_key", ""))
        openai_model: str = str(openai_cfg.get("model", "gpt-4o"))
        claude_key: str = str(claude_cfg.get("api_key", ""))
        claude_model: str = str(claude_cfg.get("model", "claude-sonnet-4-20250514"))

        # 폴백 프로바이더 구성 (기본 → 폴백)
        if (
            default_provider == "openai"
            and openai_key
            and not openai_key.startswith("sk-...")
        ):
            fallback_provider = None
            if claude_key and not claude_key.startswith("sk-ant-..."):
                fallback_provider = AIProviderFactory.create(
                    "claude", claude_key, claude_model
                )
            primary_provider = AIProviderFactory.create(
                "openai", openai_key, openai_model, fallback=fallback_provider
            )
        elif (
            default_provider == "claude"
            and claude_key
            and not claude_key.startswith("sk-ant-...")
        ):
            fallback_provider = None
            if openai_key and not openai_key.startswith("sk-..."):
                fallback_provider = AIProviderFactory.create(
                    "openai", openai_key, openai_model
                )
            primary_provider = AIProviderFactory.create(
                "claude", claude_key, claude_model, fallback=fallback_provider
            )
        else:
            logger.warning(
                "유효한 AI API 키가 설정되지 않았습니다. AI 파이프라인을 비활성화합니다."
            )
            primary_provider = None

        if primary_provider is not None:
            ai_pipeline = AIPipeline(
                provider=primary_provider,
                storage_dir=storage_base_dir,
            )
            logger.info("AI 파이프라인 초기화 완료: provider=%s", default_provider)

    except Exception:
        logger.exception("AI 프로바이더 초기화 실패. AI 기능이 비활성화됩니다.")

    # 8. 텔레그램 알림 함수 (TCPServer, HealthMonitor 공용)
    async def _telegram_notify(message: str) -> None:
        """텔레그램으로 관리자에게 알림 전송."""
        if not telegram_enabled or not admin_chat_ids:
            return
        try:
            from telegram import Bot  # type: ignore[import]

            bot = Bot(token=server_bot_token)
            for chat_id in admin_chat_ids:
                await bot.send_message(chat_id=chat_id, text=message)
        except Exception:
            logger.debug("텔레그램 알림 전송 실패")

    async def _health_notify(message: str) -> None:
        """헬스 모니터 전용 알림 (⚠️ 접두사 추가)."""
        await _telegram_notify(f"⚠️ {message}")

    # 9. TCP 서버 초기화
    # auth_token: 환경변수 또는 기본값 사용
    auth_token: str = os.environ.get("TCP_AUTH_TOKEN", "default-auth-token")

    tcp_server = TCPServer(
        host=tcp_host,
        port=tcp_port,
        session_mgr=session_mgr,
        auth_token=auth_token,
        storage_mgr=storage_mgr,
        rec_handler=rec_handler,
        rec_storage=rec_storage,
        notify_callback=_telegram_notify if telegram_enabled else None,
    )

    # 10. 헬스 모니터 초기화
    health_monitor = None
    if health_enabled:
        from server.core.health_monitor import HealthMonitor

        health_monitor = HealthMonitor(
            config=health_cfg,
            telegram_notifier=_health_notify if telegram_enabled else None,
        )

    # 11. 대시보드 앱 생성
    fastapi_app = None
    if dashboard_enabled:
        try:
            from server.dashboard.app import create_app

            fastapi_app = create_app(
                session_mgr=session_mgr,
                storage_mgr=storage_mgr,
                tcp_server=tcp_server,
                secret_key=dash_secret,
                rec_storage=rec_storage,
            )
            logger.info("대시보드 앱 생성 완료")
        except Exception:
            logger.exception("대시보드 앱 생성 실패. 대시보드를 비활성화합니다.")
            dashboard_enabled = False

    # 12. 종료 이벤트
    shutdown_event = asyncio.Event()

    # 13. TCP 서버 시작
    try:
        bound_port = await tcp_server.start()
        logger.info("TCP 서버 시작됨: %s:%s", tcp_host, bound_port)
    except Exception:
        logger.exception("TCP 서버 시작 실패")
        sys.exit(1)

    # 14. 헬스 모니터 시작
    if health_monitor is not None:
        await health_monitor.start()
        logger.info("헬스 모니터 시작됨")

    # 15. 비동기 태스크 목록 구성
    tasks: list[asyncio.Task[Any]] = []

    # TCP 서버 서빙 태스크 (asyncio.start_server는 이미 실행 중이므로 대기만)
    async def _wait_tcp_shutdown() -> None:
        await shutdown_event.wait()

    tasks.append(asyncio.create_task(_wait_tcp_shutdown(), name="tcp-shutdown-waiter"))

    # 대시보드 태스크
    if dashboard_enabled and fastapi_app is not None:
        tasks.append(
            asyncio.create_task(
                _run_dashboard(fastapi_app, dash_host, dash_port, shutdown_event),
                name="dashboard",
            )
        )

    # 텔레그램 태스크
    if telegram_enabled:
        tasks.append(
            asyncio.create_task(
                _run_telegram(
                    server_bot_token=server_bot_token,
                    admin_chat_ids=admin_chat_ids,
                    tcp_server=tcp_server,
                    session_mgr=session_mgr,
                    storage_mgr=storage_mgr,
                    ai_pipeline=ai_pipeline,
                    shutdown_event=shutdown_event,
                ),
                name="telegram",
            )
        )

    logger.info("모든 컴포넌트 시작 완료. Ctrl+C로 종료하세요.")

    # 16. 메인 루프 — KeyboardInterrupt(Ctrl+C) 대기
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt 수신. 종료 중...")
    finally:
        # 종료 이벤트 설정 → 모든 태스크에 종료 신호
        shutdown_event.set()

        # 태스크 완료 대기 (타임아웃 15초)
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=15.0)
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # 헬스 모니터 종료
        if health_monitor is not None:
            await health_monitor.stop()
            logger.info("헬스 모니터 종료됨")

        # TCP 서버 종료
        await tcp_server.stop()
        logger.info("TCP 서버 종료됨")

        logger.info("서버가 정상적으로 종료되었습니다.")


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------


def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
