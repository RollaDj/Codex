"""Fast unit tests (no AI model needed): python -m pytest -q"""
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from decorpipe.composite import cover_fit, harmonize_gains  # noqa: E402
from decorpipe.costs import BudgetError, estimate_usd, guard_spend  # noqa: E402
from decorpipe.faces import mouth_box  # noqa: E402
from decorpipe.ingest import target_fps  # noqa: E402
from decorpipe.qc import _faststart, _xcorr_lag  # noqa: E402
from fractions import Fraction  # noqa: E402


def test_costs_match_published_rates():
    assert estimate_usd("local", 10) == 0.0
    assert estimate_usd("runway", 10) == pytest.approx(2.80)   # 28 credits/s × $0.01
    assert estimate_usd("runway", 1) == pytest.approx(0.56)    # 56-credit minimum
    assert estimate_usd("kling", 10) == pytest.approx(1.68)    # $0.168/s
    assert estimate_usd("kling", 2) == pytest.approx(0.50)     # 3 s minimum input


def test_spend_guard_requires_confirmation_and_budget():
    assert guard_spend("local", 60, 4, None, False) == 0.0
    with pytest.raises(BudgetError):
        guard_spend("runway", 10, 1, 5.0, confirmed=False)
    with pytest.raises(BudgetError):
        guard_spend("runway", 30, 4, 5.0, confirmed=True)    # 33.60 $ > 5 $
    assert guard_spend("kling", 10, 1, 5.0, confirmed=True) == pytest.approx(1.68)
    with pytest.raises(BudgetError):
        guard_spend("kling", 12, 1, 5.0, confirmed=True)     # Kling edit: 10 s max per call


def test_xcorr_finds_offset():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(48000).astype(np.float32)
    y = np.concatenate([np.zeros(7, np.float32), x[:-7]])
    lag, corr = _xcorr_lag(x, y, 100)
    assert abs(lag) == 7 and corr > 0.99
    lag0, corr0 = _xcorr_lag(x, x, 100)
    assert lag0 == 0 and corr0 == pytest.approx(1.0)


def test_strict_mode_never_touches_subject_colour():
    img = np.full((40, 30, 3), 128, np.uint8)
    plate = np.full((40, 30, 3), 0.8, np.float32)
    assert np.array_equal(harmonize_gains([(img, img[..., 0])], plate, 0.0), np.ones(3))
    g = harmonize_gains([(img, img[..., 0])], plate * np.array([1.0, 0.8, 0.5], np.float32), 1.0)
    assert np.all(g >= 0.94) and np.all(g <= 1.06)             # capped white-balance trim


def test_cover_fit_shape():
    img = np.zeros((2400, 1350, 3), np.uint8)
    assert cover_fit(img, 1210, 2150).shape == (2150, 1210, 3)
    wide = np.zeros((1080, 1920, 3), np.uint8)
    assert cover_fit(wide, 1080, 1920).shape == (1920, 1080, 3)


def test_mouth_box_from_landmarks():
    f = np.zeros(15, np.float32)
    f[10:14] = [100, 200, 160, 200]            # mouth corners
    x, y, w, h = mouth_box(f)
    assert x < 100 and x + w > 160 and y < 200 < y + h


def test_target_fps_snaps_iphone_rates():
    assert target_fps(Fraction(2997, 100), False) == Fraction(30000, 1001)
    assert target_fps(Fraction(60), False) == Fraction(30)
    assert target_fps(Fraction(25), False) == Fraction(25)
    assert target_fps(Fraction(24), False) == Fraction(24)            # was snapped to 23.976
    assert target_fps(Fraction(24000, 1001), False) == Fraction(24000, 1001)
    assert target_fps(Fraction(5994, 100), False) == Fraction(30000, 1001)


def test_faststart_detection(tmp_path):
    out = tmp_path / "t.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.2",
                    "-c:v", "libx264", "-movflags", "+faststart", str(out)], check=True)
    assert _faststart(out)
    slow = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.2",
                    "-c:v", "libx264", str(slow)], check=True)
    assert not _faststart(slow)
