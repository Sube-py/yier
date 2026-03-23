from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from yier_channel.service import ChannelWorkspaceService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yier-channel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list")
    subparsers.add_parser("status")
    subparsers.add_parser("doctor")

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--storage-root", default="")

    stop_parser = subparsers.add_parser("stop")
    stop_parser.add_argument("--platform", default="weixin")
    stop_parser.add_argument("--account-id", required=True)

    login_parser = subparsers.add_parser("login")
    login_parser.add_argument("--platform", default="weixin")
    login_parser.add_argument("--account-id", default="")

    send_parser = subparsers.add_parser("send")
    send_parser.add_argument("--platform", default="weixin")
    send_parser.add_argument("--account-id", required=True)
    send_parser.add_argument("--to", required=True)
    send_parser.add_argument("--text", default="")
    send_parser.add_argument("--file", default="")

    return parser


async def run_cli(args: argparse.Namespace) -> int:
    storage_root = Path(args.storage_root).expanduser().resolve() if getattr(args, "storage_root", "") else None
    service = ChannelWorkspaceService(storage_root=storage_root)

    if args.command == "list":
        print(json.dumps(service.get_registered_platforms(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "status":
        snapshot = await service.get_workspace_snapshot()
        print(snapshot.model_dump_json(indent=2))
        return 0

    if args.command == "doctor":
        snapshot = await service.get_workspace_snapshot()
        healthy = all(account.last_error is None for account in snapshot.accounts)
        print(snapshot.model_dump_json(indent=2))
        return 0 if healthy else 1

    if args.command == "login":
        result = await service.login(platform=args.platform, account_id=args.account_id or None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "send":
        if not args.text and not args.file:
            raise SystemExit("Either --text or --file must be provided.")
        if args.file:
            result = await service.send_file(
                platform=args.platform,
                account_id=args.account_id,
                peer_id=args.to,
                file_path=Path(args.file).expanduser().resolve(),
                text=args.text,
            )
        else:
            result = await service.send_text(
                platform=args.platform,
                account_id=args.account_id,
                peer_id=args.to,
                text=args.text,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "start":
        await service.start()
        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            await service.stop()
            return 0

    if args.command == "stop":
        await service.stop_account(platform=args.platform, account_id=args.account_id)
        return 0

    return 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_cli(args)))
