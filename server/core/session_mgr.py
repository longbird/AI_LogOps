from __future__ import annotations

import logging
import time
import uuid
from typing import Protocol, cast

from shared.models import AgentInfo, AgentSession, AgentState
from shared.utils import setup_logging


class SessionManager:
    """최대 max_agents 동시 세션 관리. 스펙 섹션 3.2 참조."""

    def __init__(self, max_agents: int = 10):
        self._max_agents: int = max_agents
        self._sessions: dict[str, AgentSession] = {}
        self._logger: logging.Logger = setup_logging(self.__class__.__name__)

    @property
    def sessions(self) -> dict[str, AgentSession]:
        """agent_id -> AgentSession mapping."""

        return self._sessions

    def create_session(
        self,
        agent_id: str,
        version: str,
        writer: _WriterLike,
    ) -> AgentSession | None:
        """새 세션 생성. max이고 새 agent면 None. 이미 존재하는 agent_id는 재접속(세션 교체)."""

        existing = self._sessions.get(agent_id)
        if existing is None and len(self._sessions) >= self._max_agents:
            self._logger.warning(
                "session limit reached: max_agents=%s", self._max_agents
            )
            return None

        if existing is not None and existing.writer is not None:
            try:
                old_writer = cast(_WriterLike, existing.writer)
                if not old_writer.is_closing():
                    old_writer.close()
            except Exception:
                self._logger.debug("failed closing previous writer for %s", agent_id)

        session = AgentSession(
            agent_info=AgentInfo(
                agent_id=agent_id,
                version=version,
                state=AgentState.CONNECTED,
            ),
            session_id=uuid.uuid4().hex[:16],
            writer=writer,
        )
        self._sessions[agent_id] = session
        self._logger.info(
            "session created: agent_id=%s session_id=%s", agent_id, session.session_id
        )
        return session

    def remove_session(self, agent_id: str) -> None:
        """세션 제거."""

        removed = self._sessions.pop(agent_id, None)
        if removed is not None:
            self._logger.info(
                "session removed: agent_id=%s session_id=%s",
                agent_id,
                removed.session_id,
            )

    def get_session(self, agent_id: str) -> AgentSession | None:
        return self._sessions.get(agent_id)

    def update_heartbeat(self, agent_id: str, process_status: int = 0) -> None:
        session = self._sessions.get(agent_id)
        if session is None:
            return
        session.last_heartbeat = time.time()
        session.process_status = process_status

    def get_all_sessions(self) -> list[AgentSession]:
        return list(self._sessions.values())


class _WriterLike(Protocol):
    def close(self) -> None: ...

    def is_closing(self) -> bool: ...
