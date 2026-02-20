"""Claude Code CLI spawn test.

claude CLI를 child process로 spawn하여 OpenClaw 스타일 연동을 검증한다.

이 파일은 pytest에서 자동 수집되지 않도록 설계되었다.
직접 실행: python tests/test_claude_cli_spawn.py
"""

from __future__ import annotations

# pytest 자동 수집 방지
__test__ = False

import asyncio
import json
import subprocess
import uuid


async def spawn_claude(
    prompt: str,
    *,
    model: str = "sonnet",
    system_prompt: str | None = None,
    session_id: str | None = None,
    resume_session: str | None = None,
    max_turns: int | None = None,
    timeout: float = 60.0,
) -> dict:
    """claude CLI를 subprocess로 실행하고 JSON 결과를 반환한다."""

    args: list[str] = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
    ]

    if system_prompt:
        args.extend(["--append-system-prompt", system_prompt])

    if session_id:
        args.extend(["--session-id", session_id])

    if resume_session:
        args.extend(["--resume", resume_session])

    if max_turns:
        args.extend(["--max-turns", str(max_turns)])

    args.append(prompt)

    print(f"\n{'=' * 60}")
    print(f"[SPAWN] {' '.join(args[:6])}...")
    print(f"[PROMPT] {prompt[:80]}")

    env = {**__import__("os").environ, "PYTHONIOENCODING": "utf-8"}

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return {"error": "timeout", "timeout_seconds": timeout}

    if proc.returncode != 0:
        return {
            "error": "process_failed",
            "returncode": proc.returncode,
            "stderr": stderr.decode("utf-8", errors="replace")[:500],
        }

    raw = stdout.decode("utf-8", errors="replace").strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "json_parse_failed", "raw_output": raw[:500]}

    return data


def print_result(label: str, data: dict) -> None:
    """결과를 보기 좋게 출력한다."""
    print(f"\n{'─' * 60}")
    print(f"[{label}]")

    if "error" in data:
        print(f"  ERROR: {data['error']}")
        if "stderr" in data:
            print(f"  STDERR: {data['stderr']}")
        if "raw_output" in data:
            print(f"  RAW: {data['raw_output']}")
        return

    print(f"  Result   : {data.get('result', 'N/A')[:200]}")
    print(f"  Session  : {data.get('session_id', 'N/A')}")
    print(f"  Duration : {data.get('duration_ms', 'N/A')}ms")
    print(f"  Turns    : {data.get('num_turns', 'N/A')}")
    print(f"  Cost     : ${data.get('total_cost_usd', 0):.4f}")

    usage = data.get("usage", {})
    print(
        f"  Tokens   : in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')}"
    )


async def test_1_basic_query() -> dict:
    """Test 1: 기본 쿼리 — -p + --output-format json."""
    data = await spawn_claude(
        "What is 2 + 2? Answer with just the number.",
        model="sonnet",
    )
    print_result("Test 1: Basic Query", data)
    assert data.get("result") is not None, "No result"
    assert "4" in data["result"], f"Expected '4' in result, got: {data['result']}"
    return data


async def test_2_system_prompt() -> dict:
    """Test 2: --append-system-prompt으로 행동 커스터마이즈."""
    data = await spawn_claude(
        "Introduce yourself.",
        model="sonnet",
        system_prompt="You are a pirate. Always speak like a pirate. Keep it under 20 words.",
    )
    print_result("Test 2: System Prompt", data)
    assert data.get("result") is not None, "No result"
    return data


async def test_3_session_id() -> dict:
    """Test 3: --session-id로 세션 시작."""
    sid = str(uuid.uuid4())
    data = await spawn_claude(
        "My favorite color is blue. What is 10 + 5? Answer the math and repeat my favorite color.",
        model="sonnet",
        session_id=sid,
    )
    print_result("Test 3: Session ID", data)
    assert data.get("session_id") is not None, "No session_id in response"
    return data


async def test_4_session_resume(previous_session_id: str) -> dict:
    """Test 4: --resume으로 이전 세션 컨텍스트 복원."""
    data = await spawn_claude(
        "What was my favorite color that I mentioned earlier?",
        model="sonnet",
        resume_session=previous_session_id,
    )
    print_result("Test 4: Session Resume", data)
    assert data.get("result") is not None, "No result"
    return data


async def test_5_max_turns() -> dict:
    """Test 5: --max-turns으로 턴 수 제한."""
    data = await spawn_claude(
        "List 3 colors.",
        model="sonnet",
        max_turns=1,
    )
    print_result("Test 5: Max Turns", data)
    assert data.get("num_turns", 0) <= 2, "Exceeded max turns"
    return data


async def main() -> None:
    print("=" * 60)
    print("  Claude CLI Spawn Integration Test")
    print("=" * 60)

    results: dict[str, bool] = {}

    # Test 1: Basic
    try:
        await test_1_basic_query()
        results["basic_query"] = True
    except Exception as e:
        print(f"  FAIL: {e}")
        results["basic_query"] = False

    # Test 2: System prompt
    try:
        await test_2_system_prompt()
        results["system_prompt"] = True
    except Exception as e:
        print(f"  FAIL: {e}")
        results["system_prompt"] = False

    # Test 3 + 4: Session
    try:
        data3 = await test_3_session_id()
        results["session_id"] = True

        sid = data3.get("session_id")
        if sid:
            await test_4_session_resume(sid)
            results["session_resume"] = True
        else:
            print("  SKIP: No session_id from Test 3")
            results["session_resume"] = False
    except Exception as e:
        print(f"  FAIL: {e}")
        results["session_id"] = False
        results["session_resume"] = False

    # Test 5: Max turns
    try:
        await test_5_max_turns()
        results["max_turns"] = True
    except Exception as e:
        print(f"  FAIL: {e}")
        results["max_turns"] = False

    # Summary
    print(f"\n{'=' * 60}")
    print("  RESULTS")
    print(f"{'=' * 60}")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n  {passed}/{total} passed")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
