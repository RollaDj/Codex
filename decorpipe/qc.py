"""Step 3 — Automatic quality control.

Every check compares the export against the working master (the untouched
source after import). Results: PASS / WARN / FAIL + a contact sheet for a
2-minute human review before publishing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

from .faces import FaceTrack, face_box, face_core_mask, mouth_box
from .ffmpeg_utils import (FrameReader, audio_pcm_md5, count_frames, media_info, probe,
                           read_audio_mono)

# Instagram Reels overlay margins on a 1080x1920 frame (username/caption at the
# bottom, buttons on the right, header at the top). Keep the face out of them.
IG_SAFE = {"top": 220, "bottom": 420, "left": 60, "right": 140}


@dataclass
class Check:
    name: str
    status: str      # PASS | WARN | FAIL | INFO
    value: str
    detail: str = ""


def _ssim_map(a: np.ndarray, b: np.ndarray, structure_only: bool = False) -> np.ndarray:
    """SSIM map; structure_only keeps the structure term (edges, features, lips) and
    ignores a global brightness/contrast change (a deliberate relight)."""
    a, b = a.astype(np.float32), b.astype(np.float32)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    g = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)
    ma, mb = g(a), g(b)
    va, vb, cov = g(a * a) - ma * ma, g(b * b) - mb * mb, g(a * b) - ma * mb
    if structure_only:
        c3 = c2 / 2
        return (cov + c3) / (np.sqrt(np.maximum(va, 0) * np.maximum(vb, 0)) + c3)
    return ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))


def _luma(rgb: np.ndarray) -> np.ndarray:
    return (rgb.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32))


def _psnr(a: np.ndarray, b: np.ndarray, m: np.ndarray) -> float:
    mse = float(((a[m] - b[m]) ** 2).mean()) if m.any() else 0.0
    return 99.0 if mse < 1e-6 else 10 * np.log10(255 ** 2 / mse)


def _xcorr_lag(x: np.ndarray, y: np.ndarray, max_lag: int) -> tuple[int, float]:
    n = min(len(x), len(y))
    x, y = x[:n] - x[:n].mean(), y[:n] - y[:n].mean()
    size = 1 << int(np.ceil(np.log2(2 * n)))
    c = np.fft.irfft(np.fft.rfft(x, size) * np.conj(np.fft.rfft(y, size)), size)
    c = np.concatenate([c[-max_lag:], c[:max_lag + 1]])
    lag = int(np.argmax(c)) - max_lag
    xs = x[max(0, lag):n + min(0, lag)]
    ys = y[max(0, -lag):n - max(0, lag)]
    denom = np.sqrt((xs * xs).sum() * (ys * ys).sum()) + 1e-12
    return lag, float((xs * ys).sum() / denom)


def _faststart(path: Path) -> bool:
    with open(path, "rb") as f:
        pos, order = 0, []
        size_total = path.stat().st_size
        while pos < size_total and len(order) < 12:
            f.seek(pos)
            hdr = f.read(16)
            if len(hdr) < 8:
                break
            size = int.from_bytes(hdr[:4], "big")
            kind = hdr[4:8].decode("latin-1")
            if size == 1:
                size = int.from_bytes(hdr[8:16], "big")
            order.append(kind)
            if size <= 0:
                break
            pos += size
    return "moov" in order and "mdat" in order and order.index("moov") < order.index("mdat")


def run_qc(master: Path, workdir: Path, final: Path, report_dir: Path,
           strict: bool, label: str, source: Path | None = None, trimmed: bool = True) -> dict:
    report_dir.mkdir(parents=True, exist_ok=True)
    checks: list[Check] = []
    m = media_info(master)
    W, H = m.width, m.height
    data = probe(final)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)

    # 1. Instagram spec ------------------------------------------------------
    spec_ok = (v["codec_name"] == "h264" and v.get("pix_fmt") == "yuv420p"
               and int(v["width"]) == 1080 and int(v["height"]) == 1920
               and v.get("sample_aspect_ratio", "1:1") in ("1:1", "0:1"))
    checks.append(Check("Format Instagram 9:16", "PASS" if spec_ok else "FAIL",
                        f"{v['codec_name']} {v.get('profile')} {v['width']}x{v['height']} "
                        f"{v.get('pix_fmt')} {v.get('r_frame_rate')} fps",
                        "H.264 High, yuv420p, 1080x1920, SAR 1:1"))
    cfr = v.get("r_frame_rate") == v.get("avg_frame_rate")
    checks.append(Check("Cadence constante (CFR)", "PASS" if cfr else "WARN",
                        f"r={v.get('r_frame_rate')} avg={v.get('avg_frame_rate')}"))
    fs = _faststart(final)
    checks.append(Check("Lecture progressive (faststart)", "PASS" if fs else "WARN",
                        "moov avant mdat" if fs else "moov après mdat"))
    dur = float(data["format"]["duration"])
    checks.append(Check("Durée Reel", "PASS" if dur <= 180 else "WARN", f"{dur:.2f}s",
                        "≤ 3 min pour un Reel"))
    mb = int(data["format"].get("bit_rate", 0)) / 1e6
    checks.append(Check("Débit", "INFO", f"{mb:.1f} Mb/s",
                        f"taille {final.stat().st_size / 1e6:.1f} Mo"))

    # 2. Frame-exact timing ---------------------------------------------------
    nf_m, nf_f = count_frames(master), count_frames(final)
    checks.append(Check("Nombre d'images identique", "PASS" if nf_m == nf_f else "FAIL",
                        f"source {nf_m} / export {nf_f}"))
    src_info = media_info(source) if source is not None else None
    if src_info is not None:
        f_fps = float(Fraction(v.get("avg_frame_rate", "0/1")))
        same = abs(f_fps - float(src_info.fps)) < 0.02
        checks.append(Check("Cadence identique à l'original", "PASS" if same else "WARN",
                            f"original {float(src_info.fps):.3f} i/s · export {f_fps:.3f} i/s",
                            "" if same else "cadence normalisée (source à cadence variable ou > 30 i/s)"))
        if not trimmed:
            gap = abs(float(v.get("duration") or 0) - src_info.duration)
            ok = gap <= 1.0 / max(f_fps, 1) + 1e-3
            checks.append(Check("Durée identique à l'original", "PASS" if ok else "FAIL",
                                f"écart {gap * 1000:.1f} ms", "tolérance : 1 image"))

    # 3. Audio = original -------------------------------------------------------
    if m.has_audio and a is not None:
        if src_info is not None and not trimmed and src_info.acodec == "aac" and a["codec_name"] == "aac":
            same = audio_pcm_md5(source) == audio_pcm_md5(final)
            checks.append(Check("Audio identique à l'original (bit à bit)", "PASS" if same else "FAIL",
                                "MD5 du son décodé identique au fichier iPhone" if same else "MD5 différent"))
        elif src_info is None and m.acodec == "aac" and a["codec_name"] == "aac":
            same = audio_pcm_md5(master) == audio_pcm_md5(final)
            checks.append(Check("Audio original bit à bit", "PASS" if same else "FAIL",
                                "MD5 PCM identique" if same else "MD5 différent"))
        rate = 48000
        xs, ys = read_audio_mono(master, rate), read_audio_mono(final, rate)
        lag, corr = _xcorr_lag(xs, ys, int(0.2 * rate))
        lag_ms = 1000 * lag / rate
        ok = abs(lag_ms) <= 1.0 and corr >= 0.99
        checks.append(Check("Audio : calage et fidélité", "PASS" if ok else "FAIL",
                            f"décalage {lag_ms:+.2f} ms · corrélation {corr:.4f}",
                            "seuil : |décalage| ≤ 1 ms, corrélation ≥ 0.99"))
        off_m = (m.a_start or 0) - m.v_start
        off_f = float(a.get("start_time") or 0) - float(v.get("start_time") or 0)
        d_ms = 1000 * (off_f - off_m)
        checks.append(Check("Décalage A/V conteneur", "PASS" if abs(d_ms) <= 1 else "FAIL",
                            f"{d_ms:+.2f} ms vs source"))
    else:
        checks.append(Check("Audio", "WARN", "pas de piste audio"))

    # 4/5. Picture: subject fidelity, lip motion, matte stability --------------
    track = FaceTrack(every=1, smooth=0.3)
    lip_src, lip_out, ssim_subj, psnr_subj, ssim_face, psnr_face = [], [], [], [], [], []
    tone_face: list[float] = []
    flicker, coverage, boxes = [], [], []
    prev_ms = prev_mo = prev_a = prev_y = None
    sheet_idx = {int(nf_m * f) for f in (0.08, 0.36, 0.64, 0.92)}
    sheet = []
    readers = zip(FrameReader(master, W, H), FrameReader(final, W, H),
                  FrameReader(workdir / "matte.mkv", W, H, gray=True))
    for i, (src, out, al) in enumerate(readers):
        face = track.update(src)
        box = face_box(face) if face is not None else None
        af = al.astype(np.float32) / 255
        coverage.append(float(af.mean()))
        ysrc = cv2.GaussianBlur(cv2.cvtColor(src, cv2.COLOR_RGB2GRAY).astype(np.float32), (5, 5), 0)
        if prev_a is not None:
            # True flicker = the matte changes while the picture does not (motion excluded).
            band = ((af > 0.02) & (af < 0.98)) | ((prev_a > 0.02) & (prev_a < 0.98))
            still = np.abs(ysrc - prev_y) < 2.0
            sel = band & still
            if sel.sum() > 200:
                flicker.append(float(np.abs(af - prev_a)[sel].mean()))
        prev_a, prev_y = af, ysrc
        if box is not None:
            boxes.append(box)
            mx, my, mw, mh = mouth_box(face)
            ms = cv2.cvtColor(src[my:my + mh, mx:mx + mw], cv2.COLOR_RGB2GRAY).astype(np.float32)
            mo = cv2.cvtColor(out[my:my + mh, mx:mx + mw], cv2.COLOR_RGB2GRAY).astype(np.float32)
            if prev_ms is not None and prev_ms.shape == ms.shape:
                lip_src.append(float(np.abs(ms - prev_ms).mean()))
                lip_out.append(float(np.abs(mo - prev_mo).mean()))
            prev_ms, prev_mo = ms, mo
        if i % 10 == 0:
            ys, yo = _luma(src), _luma(out)
            smap = _ssim_map(ys, yo, structure_only=True)
            subj = cv2.erode((al >= 252).astype(np.uint8), np.ones((13, 13), np.uint8)) > 0
            if subj.any():
                ssim_subj.append(float(smap[subj].mean()))
                psnr_subj.append(_psnr(ys, yo, subj))
            if box is not None:
                core = face_core_mask((H, W), face) > 0.5
                ssim_face.append(float(smap[core].mean()))
                psnr_face.append(_psnr(ys, yo, core))
                tone_face.append(float(yo[core].mean() / max(ys[core].mean(), 1e-3)))
        if i in sheet_idx:
            sheet.append((src.copy(), al.copy(), out.copy()))

    if ssim_subj:
        s_min, p_med = min(ssim_subj), float(np.median(psnr_subj))
        st = "PASS" if s_min >= 0.97 else ("WARN" if s_min >= 0.93 else "FAIL")
        checks.append(Check("Sujet inchangé (structure, SSIM)", st,
                            f"SSIM min {s_min:.4f} · PSNR médian {p_med:.1f} dB",
                            "mode strict : seul l'encodage H.264 intervient" if strict else
                            "inclut l'harmonisation colorimétrique légère du préréglage"))
    if ssim_face:
        f_min, fp = min(ssim_face), float(np.median(psnr_face))
        st = "PASS" if f_min >= 0.97 else ("WARN" if f_min >= 0.93 else "FAIL")
        checks.append(Check("Visage inchangé (structure, SSIM)", st,
                            f"SSIM min {f_min:.4f} · PSNR médian {fp:.1f} dB",
                            "traits, regard et lèvres ; la lumière globale est mesurée à part"))
        if tone_face:
            t = float(np.median(tone_face))
            checks.append(Check("Lumière du visage (intégration)", "INFO",
                                f"luminosité ×{t:.2f} ({np.log2(max(t, 1e-3)):+.2f} EV)",
                                "réglage volontaire du préréglage pour coller à la lumière du décor"))
    if len(lip_src) > 10:
        x, y = np.array(lip_src), np.array(lip_out)
        lag, corr = _xcorr_lag(x, y, 5)
        ok = lag == 0 and corr >= 0.97
        checks.append(Check("Synchro labiale (mouvement des lèvres)", "PASS" if ok else "FAIL",
                            f"décalage {lag} image(s) · corrélation {corr:.4f}",
                            "énergie de mouvement de la bouche source vs export"))
    else:
        checks.append(Check("Synchro labiale", "WARN", "visage non détecté — contrôle visuel requis"))

    stats_p = workdir / "matte_stats.json"
    stats = json.loads(stats_p.read_text()) if stats_p.exists() else {}
    if flicker:
        p95, med = float(np.percentile(flicker, 95)), float(np.median(flicker))
        st = "PASS" if p95 < 0.08 else ("WARN" if p95 < 0.15 else "FAIL")
        checks.append(Check("Stabilité du détourage (bords)", st,
                            f"scintillement médian {med:.3f} · p95 {p95:.3f}",
                            "variation d'alpha sur les contours là où l'image est immobile "
                            "(heuristique ; seuils PASS < 0,08 / WARN < 0,15 — les pics isolés viennent souvent d'un mouvement rapide)"))
    cov = np.array(coverage)
    jumps = int((np.abs(np.diff(cov)) / np.maximum(cov[:-1], 1e-3) > 0.05).sum()) if len(cov) > 1 else 0
    checks.append(Check("Sauts de silhouette", "PASS" if jumps == 0 else "WARN",
                        f"{jumps} image(s) avec variation de surface > 5 %"))
    rep = stats.get("face_repaired_frames", 0)
    checks.append(Check("Trous dans le visage corrigés", "PASS" if rep == 0 else "WARN",
                        f"{rep} image(s)", "le masque de protection du visage a dû intervenir"))

    if boxes:
        bx = np.median(np.array(boxes), axis=0)
        x, y, w, h = bx
        safe = (y >= IG_SAFE["top"] and y + h <= H - IG_SAFE["bottom"]
                and x >= IG_SAFE["left"] and x + w <= W - IG_SAFE["right"])
        checks.append(Check("Visage dans la zone sûre Instagram", "PASS" if safe else "WARN",
                            f"visage x={int(x)} y={int(y)} {int(w)}x{int(h)}",
                            f"marges : haut {IG_SAFE['top']} / bas {IG_SAFE['bottom']} / droite {IG_SAFE['right']} px"))

    # Contact sheet: source / matte / export at 4 moments ---------------------
    if sheet:
        cw, ch = 270, 480
        cols = []
        for src, al, out in sheet:
            col = [cv2.resize(src, (cw, ch)), cv2.resize(cv2.cvtColor(al, cv2.COLOR_GRAY2RGB), (cw, ch)),
                   cv2.resize(out, (cw, ch))]
            cols.append(np.vstack(col))
        grid = np.hstack(cols)
        cv2.imwrite(str(report_dir / f"{label}_planche.jpg"), cv2.cvtColor(grid, cv2.COLOR_RGB2BGR),
                    [cv2.IMWRITE_JPEG_QUALITY, 88])

    worst = "PASS"
    for c in checks:
        if c.status == "FAIL":
            worst = "FAIL"
        elif c.status == "WARN" and worst == "PASS":
            worst = "WARN"
    result = {"label": label, "verdict": worst, "checks": [c.__dict__ for c in checks],
              "lip_motion_series": {"source": lip_src[:400], "export": lip_out[:400]}}
    (report_dir / f"{label}_qc.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    md = [f"# Contrôle qualité — {label}", "", f"**Verdict : {worst}**", "",
          "| Contrôle | Statut | Mesure | Détail |", "|---|---|---|---|"]
    md += [f"| {c.name} | {c.status} | {c.value} | {c.detail} |" for c in checks]
    md += ["", f"Planche contact : `{label}_planche.jpg` (lignes : source, masque, export)."]
    (report_dir / f"{label}_qc.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return result
