"""The session replay counts a split as joined only for the reason the new rules give."""

import unittest
from datetime import datetime

from evals.replay_turns import replay

T0 = 1790377463.0


def clock(offset):
    """A local HH:MM:SS.mmm stamp, as the audit writes them."""
    return datetime.fromtimestamp(T0 + offset).strftime("%H:%M:%S.%f")[:-3]


def line(offset, text, *, fragment=True, utterance_s=0.8, reached=True, voice_start=None,
         latency=None, intent="director_ask"):
    row = {"time": clock(offset), "ts": T0 + offset, "in_scope": True, "who": "AHMED", "via": "voice",
           "text": text, "labels": ["FRAGMENT"] if fragment else [], "utterance_s": utterance_s,
           "reached_director": reached, "voice_intent": intent, "model_latency_ms": latency,
           "audible": voice_start is not None, "voice_start": voice_start}
    return row


class Replay(unittest.TestCase):
    def test_each_rule_and_each_reason_to_stay_split(self):
        rows = [
            # merged: the second sentence is committed before the first reply starts
            line(0.0, "So what's on the docket?", voice_start=clock(2.5)),
            line(1.6, "What do I need to know?"),
            # held: the first part ends on "to" and he goes on within the hold
            line(100.0, "Tell the other agent to"),
            line(102.0, "run the tests.", utterance_s=1.0),  # pause about 2.2 s
            # split: the second part came after the reply had started
            line(200.0, "What needs me?", voice_start=clock(201.0)),
            line(203.0, "And what is ready?"),
            # split: the first part never reached Director
            line(300.0, "Because the GPU box has no GPU.", reached=False),
            line(301.0, "It used to have two.", reached=False),
        ]
        summary, results = replay(rows)
        self.assertEqual([r["outcome"] for r in results], ["merged", "held", "split", "split"])
        self.assertEqual(summary["fragment_rows"], 8)
        self.assertEqual((summary["merged_fragments"], summary["held_fragments"], summary["split_fragments"]),
                         (2, 2, 4))
        self.assertIn("ignored", results[3]["why"])


if __name__ == "__main__":
    unittest.main()
