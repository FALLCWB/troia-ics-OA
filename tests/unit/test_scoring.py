"""Tests for metrics.scoring — confusion matrix arithmetic and pairing logic."""

from __future__ import annotations

import pytest

from metrics.scoring import ConfusionMatrix, score


def _ev(ts_ns: int, kind: str = "malformed") -> dict:
    return {"ts_ns": ts_ns, "kind": kind, "scenario": "test"}


def _anom(ts_ns: int, is_anomaly: bool, sc: float = -1.6) -> dict:
    return {"timestamp_ns": ts_ns, "is_anomaly": is_anomaly, "score": sc, "pid": 1, "features": {}}


# --- ConfusionMatrix arithmetic --------------------------------------------

def test_confusion_perfect_classifier():
    cm = ConfusionMatrix(tp=50, fp=0, fn=0, tn=100)
    assert cm.precision == 1.0
    assert cm.recall == 1.0
    assert cm.f1 == 1.0
    assert cm.fpr == 0.0


def test_confusion_silent_classifier_returns_zeros():
    # Predicts everything negative — recall=0, precision=0 by convention.
    cm = ConfusionMatrix(tp=0, fp=0, fn=10, tn=100)
    assert cm.precision == 0.0
    assert cm.recall == 0.0
    assert cm.f1 == 0.0
    assert cm.fpr == 0.0


def test_confusion_alarmist_classifier():
    # Flags everything — precision low, recall high.
    cm = ConfusionMatrix(tp=10, fp=90, fn=0, tn=0)
    assert cm.precision == pytest.approx(0.1)
    assert cm.recall == 1.0
    assert cm.fpr == 1.0


# --- score() pairing -------------------------------------------------------

SEC = 1_000_000_000


def test_score_exact_co_occurrence_is_tp():
    events = [_ev(10 * SEC)]
    anoms = [_anom(10 * SEC, is_anomaly=True), _anom(11 * SEC, is_anomaly=False)]
    cm = score(events, anoms, tolerance_sec=2.0)
    assert cm.tp == 1
    assert cm.fn == 0
    assert cm.fp == 0
    assert cm.tn == 1


def test_score_event_with_no_anomaly_is_fn():
    events = [_ev(10 * SEC)]
    anoms = [_anom(50 * SEC, is_anomaly=True)]  # outside tolerance
    cm = score(events, anoms, tolerance_sec=2.0)
    assert cm.fn == 1
    assert cm.fp == 1
    assert cm.tp == 0


def test_score_anomaly_with_no_event_is_fp():
    events: list[dict] = []
    anoms = [_anom(10 * SEC, is_anomaly=True), _anom(11 * SEC, is_anomaly=False)]
    cm = score(events, anoms, tolerance_sec=2.0)
    assert cm.tp == 0
    assert cm.fp == 1
    assert cm.fn == 0
    assert cm.tn == 1


def test_score_consumes_each_anomaly_at_most_once():
    # Two events, one anomaly flag — should be 1 TP and 1 FN, not 2 TP.
    events = [_ev(10 * SEC), _ev(11 * SEC)]
    anoms = [_anom(10 * SEC + 100, is_anomaly=True)]
    cm = score(events, anoms, tolerance_sec=2.0)
    assert cm.tp == 1
    assert cm.fn == 1
    assert cm.fp == 0


def test_score_picks_nearest_anomaly_to_event():
    events = [_ev(10 * SEC)]
    anoms = [
        _anom(8 * SEC, is_anomaly=True),       # 2 s away (boundary)
        _anom(10 * SEC + 100, is_anomaly=True),  # ~0 s away → preferred
        _anom(12 * SEC, is_anomaly=True),      # 2 s away
    ]
    cm = score(events, anoms, tolerance_sec=2.5)
    assert cm.tp == 1
    # The other two unmatched flagged anomalies → FP.
    assert cm.fp == 2
    assert cm.fn == 0


def test_score_realistic_mix():
    events = [_ev(t * SEC) for t in (10, 30, 50, 70)]
    anoms = [
        _anom(10 * SEC, True),   # TP
        _anom(20 * SEC, True),   # FP (no event)
        _anom(30 * SEC + 500_000_000, True),  # TP (0.5 s after event)
        _anom(40 * SEC, False),  # TN
        _anom(60 * SEC, False),  # TN
        # event at 50: no flag → FN
        _anom(70 * SEC, True),   # TP
    ]
    cm = score(events, anoms, tolerance_sec=2.0)
    assert cm.tp == 3
    assert cm.fn == 1
    assert cm.fp == 1
    assert cm.tn == 2
    assert cm.recall == 0.75
    assert cm.precision == 0.75
