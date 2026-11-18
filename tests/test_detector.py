import random

from clipper.config import Detection
from clipper.detector import MomentDetector


def run(det, until, chat_rate_fn, audio_fn=None):
    moments = []
    t = 0.0
    rnd = random.Random(1)
    while t < until:
        rate, hype = chat_rate_fn(t)
        n = sum(1 for _ in range(int(rate * 2)) if rnd.random() < 0.5)  # ~rate msgs/s, noisy
        for i in range(n):
            det.add_chat(t + i / max(n, 1), "KEKW NO WAY" if hype else "hello chat")
        if audio_fn:
            det.add_audio(t, audio_fn(t))
            det.add_audio(t + 0.5, audio_fn(t + 0.5))
        m = det.tick(t)
        if m:
            moments.append(m)
        t += 1.0
    return moments


def test_chat_spike_creates_one_moment():
    det = MomentDetector(Detection(), started_at=0.0)
    spike = lambda t: (25, True) if 250 <= t < 258 else (3, False)  # noqa: E731
    moments = run(det, 400, spike)
    assert len(moments) == 1
    m = moments[0]
    assert 230 <= m.event_ts <= 262
    assert m.start < 250 < m.end
    assert 20 <= m.end - m.start <= 59
    assert m.chat_sample


def test_steady_chat_creates_nothing():
    det = MomentDetector(Detection(), started_at=0.0)
    assert run(det, 600, lambda t: (5, False)) == []


def test_warmup_blocks_early_spikes():
    det = MomentDetector(Detection(warmup_seconds=120), started_at=0.0)
    assert run(det, 110, lambda t: (30, True) if t > 60 else (2, False)) == []


def test_audio_spike_alone_can_trigger():
    det = MomentDetector(Detection(threshold=6.0), started_at=0.0)
    loud = lambda t: -10.0 if 300 <= t < 304 else -35.0 + random.Random(int(t * 2)).uniform(-2, 2)  # noqa: E731
    moments = run(det, 400, lambda t: (3, False), loud)
    assert len(moments) == 1


def test_min_gap_between_moments():
    det = MomentDetector(Detection(min_gap_seconds=90), started_at=0.0)
    spikes = lambda t: (25, True) if (200 <= t < 206 or 240 <= t < 246 or 400 <= t < 406) else (3, False)  # noqa: E731
    moments = run(det, 520, spikes)
    assert len(moments) == 2  # the 240s spike is too close to the 200s one
