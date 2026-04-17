# ruff: noqa: E402
import asyncio
from pathlib import Path
import sys
from typing import cast, Iterable

_EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(_EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES_ROOT))

from codex_app_server import AppServerConfig, TextInput
from codex_app_server.generated.v2_all import (  # noqa: E402
    CollaborationMode,
    ModeKind,
    ReadOnlyAccess,
    ReadOnlySandboxPolicy,
    RestrictedReadOnlyAccess,
    SandboxPolicy,
    Settings,
)

from yier_web.codex.sdk.client import (
    ApprovalAwareAppServerClient,
    ToolRequestUserInputParams,
)


def find_turn_by_id(turns: Iterable[object] | None, turn_id: str) -> object | None:
    for turn in turns or []:
        if getattr(turn, "id", None) == turn_id:
            return turn
    return None


def assistant_text_from_turn(turn: object | None) -> str:
    if turn is None:
        return ""

    chunks: list[str] = []
    for item in getattr(turn, "items", []) or []:
        raw_item = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if not isinstance(raw_item, dict):
            continue

        item_type = raw_item.get("type")
        if item_type == "agentMessage":
            text = raw_item.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)
            continue

        if item_type != "message" or raw_item.get("role") != "assistant":
            continue

        for content in raw_item.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            text = content.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)

    return "".join(chunks)


def runtime_config():
    """Return an example-friendly AppServerConfig for repo-source SDK usage."""
    return AppServerConfig(codex_bin="/opt/homebrew/bin/codex")


def serialize_answers(answers: dict[str, list[str]]):
    return {
        question_id: {"answers": answer_list}
        for question_id, answer_list in answers.items()
    }


def auto_approval_callback(
    request_id: str,
    method: str,
    params: dict[str, object],
) -> dict[str, object]:
    """Keep the demo focused on requestUserInput; reject unrelated approvals."""
    print(f"\n[auto approval] {method} request_id={request_id} params={params}")
    return {"decision": "decline"}


async def main() -> None:
    client = ApprovalAwareAppServerClient(
        config=runtime_config(),
        approval_callback=auto_approval_callback,
        manual_request_methods=frozenset({"item/tool/requestUserInput"}),
    )
    await client.start()
    try:
        await client.initialize()
        started = await client.thread_start(
            {"model": "gpt-5.4", "config": {"model_reasoning_effort": "high"}}
        )
        thread = client.thread(started.thread.id)
        turn = await thread.turn(
            TextInput("我想体验一下plan模式的选择框, 请给我两个选择框让我体验一下"),
            collaboration_mode=CollaborationMode(
                mode=ModeKind.plan,
                settings=Settings(
                    reasoning_effort="medium",
                    model="gpt-5.4",
                ),
            ),
            sandbox_policy=SandboxPolicy(
                root=ReadOnlySandboxPolicy(
                    access=ReadOnlyAccess(
                        RestrictedReadOnlyAccess(
                            include_platform_defaults=True,
                            readable_roots=[],
                            type="restricted",
                        ),
                    ),
                    type="readOnly",
                )
            ),
        )
        event_count = 0
        saw_started = False
        saw_delta = False
        completed_status = "unknown"

        async for event in turn.stream():
            event_count += 1
            # continue
            if event.method == "turn/started":
                saw_started = True
                print("stream.started")
                continue
            elif event.method == "item/agentMessage/delta":
                delta = getattr(event.payload, "delta", "")
                if delta:
                    if not saw_delta:
                        print("assistant> ", end="", flush=True)
                    print(delta, end="", flush=True)
                    saw_delta = True
                continue
            elif event.method == "turn/completed":
                completed_status = getattr(
                    event.payload.turn.status, "value", str(event.payload.turn.status)
                )
            elif event.method == "item/tool/requestUserInput":
                print("\n[tool requested user input, interrupting turn]")
                print("tool input prompt>", event.payload)
                payload = cast(ToolRequestUserInputParams, event.payload)
                request_id = event.request_id
                if request_id is None:
                    raise RuntimeError("requestUserInput event missing request_id")
                response_msg = {}
                for question in payload.questions:
                    print(f"标题: {question.header}")
                    print(f"描述: {question.question}")
                    for idx, option in enumerate(question.options, start=1):
                        print(f"{idx}. {option.label}({option.description})")
                    while True:
                        user_input = input("user input> ")
                        if user_input.isdigit():
                            if 1 <= int(user_input) <= len(question.options):
                                option = question.options[int(user_input) - 1]
                                response_msg[question.id] = [option.label]
                                break
                            else:
                                continue
                        else:
                            response_msg[question.id] = [user_input]
                            break
                await turn.respond_to_server_request(
                    request_id,
                    {"answers": serialize_answers(response_msg)},
                )
            else:
                print(f"\n[unhandled event: {event.method}]", event.payload)

        if saw_delta:
            print()
        else:
            persisted = await thread.read(include_turns=True)
            persisted_turn = find_turn_by_id(persisted.thread.turns, turn.id)
            final_text = (
                assistant_text_from_turn(persisted_turn).strip()
                or "[no assistant text]"
            )
            print("assistant>", final_text)

        print("stream.started.seen:", saw_started)
        print("stream.completed:", completed_status)
        print("events.count:", event_count)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
