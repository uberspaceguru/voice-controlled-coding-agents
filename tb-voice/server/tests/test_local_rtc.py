"""The Director app's WebRTC audio on 127.0.0.1, end to end, with no devices.

A headless peer stands in for the app: it sends a synthetic 440 Hz tone as its
"microphone" and records what the bot plays. The bot side is local_rtc.serve
with the same SmallWebRTCTransport settings bot.py uses, and a pipeline that
counts the microphone's frames and plays a 5 s tone back. No microphone, no
speakers, no keys. It proves the plumbing (signalling, token, ICE on loopback,
audio both ways, the child ending when the peer goes); the canceller itself
lives in the app's engine and is the acoustic drill's job.
"""

import asyncio
import fractions
import math
import socket
import time
import unittest

import httpx
import numpy as np
from aiortc import MediaStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from av import AudioFrame
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

import local_rtc

TOKEN = "t0ken"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Tone(MediaStreamTrack):
    """The app's microphone, synthesised: 440 Hz, 48 kHz mono, 20 ms frames."""
    kind = "audio"

    def __init__(self):
        super().__init__()
        self._n = 0

    async def recv(self):
        await asyncio.sleep(0.02)
        samples = 960
        t = (np.arange(samples) + self._n) / 48000
        pcm = (np.sin(2 * math.pi * 440 * t) * 8000).astype(np.int16)
        frame = AudioFrame(format="s16", layout="mono", samples=samples)
        frame.planes[0].update(pcm.tobytes())
        frame.sample_rate = 48000
        frame.pts = self._n
        frame.time_base = fractions.Fraction(1, 48000)
        self._n += samples
        return frame


class Counter(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.frames = 0
        self.loud = 0

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self.frames += 1
            pcm = np.frombuffer(frame.audio, dtype=np.int16)
            if pcm.size and np.abs(pcm).mean() > 500:
                self.loud += 1
        await self.push_frame(frame, direction)


def played_tone(secs=5.0, rate=48000) -> bytes:
    n = int(secs * rate)
    t = np.arange(n) / rate
    return (np.sin(2 * math.pi * 330 * t) * 8000).astype(np.int16).tobytes()


class LocalRTC(unittest.IsolatedAsyncioTestCase):
    async def test_offer_needs_the_token(self):
        app = local_rtc.build_app(lambda c: asyncio.sleep(0), TOKEN)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x") as c:
            r = await c.post("/api/offer", json={"sdp": "v=0", "type": "offer"})
            self.assertEqual(r.status_code, 401)
            r = await c.patch("/api/offer", json={"pc_id": "x", "candidates": []},
                              headers={"Authorization": "Bearer wrong"})
            self.assertEqual(r.status_code, 401)

    async def test_audio_both_ways_on_loopback(self):
        port = free_port()
        counter = Counter()
        sessions = []

        async def session(connection):
            transport = SmallWebRTCTransport(webrtc_connection=connection, params=TransportParams(
                audio_in_enabled=True, audio_out_enabled=True,
                audio_in_sample_rate=16000, audio_out_sample_rate=48000))
            worker = PipelineWorker(Pipeline([transport.input(), counter, transport.output()]),
                                    params=PipelineParams())
            runner = WorkerRunner(handle_sigint=False)
            await runner.add_workers(worker)

            @transport.event_handler("on_client_connected")
            async def connected(transport, client):
                sessions.append("connected")
                await worker.queue_frame(OutputAudioRawFrame(played_tone(), 48000, 1))

            @transport.event_handler("on_client_disconnected")
            async def disconnected(transport, client):
                sessions.append("disconnected")
                await runner.cancel()

            await runner.run()

        server = asyncio.create_task(local_rtc.serve(session, port, TOKEN))

        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))  # loopback: no STUN, as the app needs none here
        pc.addTrack(Tone())
        heard = {"frames": 0, "loud": 0, "last_loud": 0.0}
        reading = []

        @pc.on("track")
        def on_track(track):
            async def read():
                while True:
                    try:
                        frame = await track.recv()
                    except Exception:
                        return
                    pcm = frame.to_ndarray()
                    heard["frames"] += 1
                    if np.abs(pcm).mean() > 500:
                        heard["loud"] += 1
                        heard["last_loud"] = time.monotonic()
            reading.append(asyncio.create_task(read()))

        await pc.setLocalDescription(await pc.createOffer())
        answer = None
        async with httpx.AsyncClient() as c:
            for _ in range(40):  # the app retries until the child's server is up
                try:
                    r = await c.post(f"http://127.0.0.1:{port}/api/offer",
                                     json={"sdp": pc.localDescription.sdp, "type": "offer"},
                                     headers={"Authorization": f"Bearer {TOKEN}"}, timeout=10)
                    answer = r.json()
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.1)
        self.assertIsNotNone(answer)
        self.assertIn("pc_id", answer)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))

        for _ in range(60):
            await asyncio.sleep(0.1)
            if counter.loud > 20 and heard["loud"] > 20:
                break
        self.assertEqual(sessions[:1], ["connected"])
        self.assertGreater(counter.loud, 20, "the bot never heard the app's microphone")
        self.assertGreater(heard["loud"], 20, "the app never heard the bot's voice")

        # Talking over it: an interruption (what BargeInStrategy's turn start
        # broadcasts) must silence the line at the far end fast. This is the
        # tail of the 500 ms budget, after the words are heard: the output
        # queue flushed, plus the app's jitter buffer.
        cut = time.monotonic()
        await counter.broadcast_interruption()
        await asyncio.sleep(1.0)
        tail_ms = (heard["last_loud"] - cut) * 1000
        print(f"\ninterruption to silence at the peer: {tail_ms:.0f} ms")
        self.assertLess(tail_ms, 250, "the voice outlived the interruption")

        await pc.close()
        for task in reading:
            task.cancel()
        # The peer going ends the session, and with it the child.
        await asyncio.wait_for(server, 15)
        self.assertIn("disconnected", sessions)


if __name__ == "__main__":
    unittest.main()
