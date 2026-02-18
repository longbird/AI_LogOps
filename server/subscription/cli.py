"""구독 관리 CLI 도구.

사용법:
    python -m server.subscription.cli create --label "Factory-01" --openai-key "sk-..." --claude-key "sk-ant-..."
    python -m server.subscription.cli list
    python -m server.subscription.cli info <KEY>
    python -m server.subscription.cli revoke <KEY>
    python -m server.subscription.cli adduser --email admin@example.com --password pass123 --subscription-key <KEY>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from server.subscription.db import SubscriptionDB

_DEFAULT_DB = Path(__file__).resolve().parent / "subscriptions.db"


def _get_db(db_path: str | None) -> SubscriptionDB:
    db = SubscriptionDB(db_path or _DEFAULT_DB)
    db.open()
    return db


def _mask_key(key: str) -> str:
    """API 키를 마스킹한다."""
    if not key or len(key) < 8:
        return key or "(empty)"
    return key[:4] + "****" + key[-4:]


def cmd_create(args: argparse.Namespace) -> None:
    db = _get_db(args.db)
    record = db.create_subscription(
        label=args.label or "",
        agent_id=args.agent_id or "*",
        openai_api_key=args.openai_key or "",
        openai_model=args.openai_model or "gpt-4o-mini",
        claude_api_key=args.claude_key or "",
        claude_model=args.claude_model or "claude-sonnet-4-20250514",
        system_prompt=args.system_prompt or "",
        max_tokens=args.max_tokens or 2000,
        expires_at=args.expires_at,
    )
    db.close()
    if record is None:
        print("ERROR: Failed to create subscription.", file=sys.stderr)
        sys.exit(1)
    print(f"Subscription created successfully!")
    print(f"  Key:       {record['subscription_key']}")
    print(f"  Label:     {record['label']}")
    print(f"  Agent ID:  {record['agent_id']}")
    print(
        f"  OpenAI:    {_mask_key(record['openai_api_key'])} ({record['openai_model']})"
    )
    print(
        f"  Claude:    {_mask_key(record['claude_api_key'])} ({record['claude_model']})"
    )
    print(f"  Status:    {record['status']}")
    print(f"  Expires:   {record.get('expires_at') or 'never'}")
    print(
        f"\n  >>> Copy this key to your agent's Telegram: /subscribe {record['subscription_key']}"
    )


def cmd_list(args: argparse.Namespace) -> None:
    db = _get_db(args.db)
    rows = db.list_subscriptions()
    db.close()
    if not rows:
        print("No subscriptions found.")
        return
    print(f"{'Key':<36} {'Label':<20} {'Agent':<15} {'Status':<10} {'Expires':<20}")
    print("-" * 101)
    for row in rows:
        key = row["subscription_key"][:32] + "..."
        label = (row["label"] or "-")[:20]
        agent = (row["agent_id"] or "*")[:15]
        status = row["status"]
        expires = row.get("expires_at") or "never"
        print(f"{key:<36} {label:<20} {agent:<15} {status:<10} {expires:<20}")


def cmd_info(args: argparse.Namespace) -> None:
    db = _get_db(args.db)
    record = db.get_subscription(args.key)
    db.close()
    if record is None:
        print(f"Subscription not found: {args.key}", file=sys.stderr)
        sys.exit(1)
    print(f"  Key:            {record['subscription_key']}")
    print(f"  Label:          {record['label']}")
    print(f"  Agent ID:       {record['agent_id']}")
    print(f"  OpenAI Key:     {_mask_key(record['openai_api_key'])}")
    print(f"  OpenAI Model:   {record['openai_model']}")
    print(f"  Claude Key:     {_mask_key(record['claude_api_key'])}")
    print(f"  Claude Model:   {record['claude_model']}")
    print(f"  System Prompt:  {record.get('system_prompt', '')[:60] or '(default)'}")
    print(f"  Max Tokens:     {record['max_tokens']}")
    print(f"  Status:         {record['status']}")
    print(f"  Expires:        {record.get('expires_at') or 'never'}")
    print(f"  Created:        {record['created_at']}")
    print(f"  Last Validated:  {record.get('last_validated_at') or 'never'}")


def cmd_revoke(args: argparse.Namespace) -> None:
    db = _get_db(args.db)
    ok = db.revoke(args.key)
    db.close()
    if ok:
        print(f"Subscription revoked: {args.key}")
    else:
        print(f"Subscription not found: {args.key}", file=sys.stderr)
        sys.exit(1)


def cmd_adduser(args: argparse.Namespace) -> None:
    db = _get_db(args.db)
    # 구독 키로 subscription_id 조회
    subscription_id: int | None = None
    if args.subscription_key:
        sub = db.get_subscription(args.subscription_key)
        if sub is None:
            print(f"Subscription not found: {args.subscription_key}", file=sys.stderr)
            db.close()
            sys.exit(1)
        subscription_id = sub["id"]

    user = db.create_user(
        email=args.email,
        password=args.password,
        name=args.name or "",
        is_admin=args.admin,
        subscription_id=subscription_id,
    )
    db.close()
    if user is None:
        print(f"ERROR: User already exists: {args.email}", file=sys.stderr)
        sys.exit(1)
    print(f"User created!")
    print(f"  Email:          {user['email']}")
    print(f"  Name:           {user['name'] or '-'}")
    print(f"  Admin:          {bool(user['is_admin'])}")
    print(f"  Subscription:   {'linked' if subscription_id else 'none'}")
    print(f"\n  >>> 이 계정으로 브라우저 인증 페이지에서 로그인할 수 있습니다.")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-LogOps Subscription CLI")
    parser.add_argument(
        "--db", default=None, help="DB file path (default: subscriptions.db)"
    )
    sub = parser.add_subparsers(dest="command")

    # create
    p_create = sub.add_parser("create", help="Create a new subscription")
    p_create.add_argument("--label", default="", help="구독 레이블 (예: Factory-01)")
    p_create.add_argument("--agent-id", default="*", help="허용할 Agent ID (* = 모두)")
    p_create.add_argument("--openai-key", default="", help="OpenAI API key")
    p_create.add_argument("--openai-model", default="gpt-4o-mini", help="OpenAI model")
    p_create.add_argument("--claude-key", default="", help="Claude API key")
    p_create.add_argument(
        "--claude-model", default="claude-sonnet-4-20250514", help="Claude model"
    )
    p_create.add_argument("--system-prompt", default="", help="시스템 프롬프트")
    p_create.add_argument("--max-tokens", type=int, default=2000, help="Max tokens")
    p_create.add_argument(
        "--expires-at", default=None, help="만료일 (ISO 8601, 예: 2025-12-31T23:59:59)"
    )

    # list
    sub.add_parser("list", help="List all subscriptions")

    # info
    p_info = sub.add_parser("info", help="Show subscription details")
    p_info.add_argument("key", help="Subscription key")

    # revoke
    p_revoke = sub.add_parser("revoke", help="Revoke a subscription")
    p_revoke.add_argument("key", help="Subscription key")

    # adduser
    p_user = sub.add_parser("adduser", help="Create a user account for OAuth login")
    p_user.add_argument("--email", required=True, help="이메일")
    p_user.add_argument("--password", required=True, help="비밀번호")
    p_user.add_argument("--name", default="", help="이름")
    p_user.add_argument("--admin", action="store_true", help="관리자 권한")
    p_user.add_argument("--subscription-key", default="", help="연결할 구독 키")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "create": cmd_create,
        "list": cmd_list,
        "info": cmd_info,
        "revoke": cmd_revoke,
        "adduser": cmd_adduser,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
