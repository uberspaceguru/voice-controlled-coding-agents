"""Optional classifier evaluation, using synthetic text only; no app or dispatch."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manager import JevClient, _chosen


async def main():
    cases = json.loads(Path(__file__).with_name("exact_requests.json").read_text())
    client = JevClient(os.environ["JEV_API_KEY"])
    rows = []
    try:
        # Do not write the manager's call logs or read any real conversation.
        with patch("manager.record"), patch("manager.EXCHANGE", []):
            for case in cases:
                addressed, answer = await client.turn(
                    case["text"], [], {"goal": "A demo coding session"}
                )
                choice = _chosen(answer)
                # Actions may use the existing custom -> is_action route.
                allowed = [case["intent"]]
                if case["intent"] == "send_message":
                    allowed.append("custom")
                rows.append(
                    {
                        "text": case["text"],
                        "expected": allowed,
                        "actual": choice,
                        "addressed": addressed,
                        "passed": choice in allowed,
                    }
                )
    finally:
        await client._client.aclose()
    print(
        json.dumps(
            {"passed": sum(r["passed"] for r in rows), "total": len(rows), "cases": rows}, indent=2
        )
    )
    return 0 if all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
