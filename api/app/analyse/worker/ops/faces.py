"""faces: gezichten vinden in previews (fase 6, X2 "De keten" stap 3–8, Keuze 60; X2a).

Alleen in het kind werk. Per preview (een fd van de app, alleen lezen):

3. **Decoderen.** De bytes met pread vanaf 0 (de positie van de fd is gedeeld met de app),
   hooguit MAX_BYTES. Alleen WebP (`Image.open(…, formats=["WEBP"])`), MAX_IMAGE_PIXELS 4 MP en
   DecompressionBombWarning als fout, zoals app/render.py: te groot is too_big, geen WebP
   unreadable, een kapot beeld image_bad.
4. **YuNet** op de preview in BGR (0–255, NCHW), rechts en onder met nullen opgevuld tot een
   veelvoud van 32 (K2-05: zonder faalt YuNet 2026may op 1024 × 683). De coördinaten blijven gelijk.
5. **De uitvoer decoderen en NMS**, zoals OpenCV (FaceDetectorYN): per stride 8, 16 en 32 een
   cel per anker (rij × kolommen + kolom), score = √(cls · obj), het midden (kolom + b0) · stride,
   de breedte exp(b2) · stride, de vijf punten (k + kolom) · stride; dan NMS met IoU 0,3 op de
   gezichten met score ≥ args.score. Een gezicht met een korte kant (binnen de preview) onder
   args.min_px valt af; hooguit args.max, de grootste eerst.
6. **Uitlijnen** op de vijf punten (Umeyama, een gelijkvormigheid) naar het sjabloon van SFace,
   112 × 112, bilineair, buiten de preview nul (zoals cv2.warpAffine).
7. **SFace** in RGB (OpenCV: blobFromImage met swapRB): 128 × float32, L2-genormaliseerd.
8. **Het antwoord.** De vectoren als ruwe bytes in de memfd uit (n × 512 B, in de volgorde van
   de gezichten en de previews); in het JSON alleen de kaders (0–1 van de preview), de score,
   px (de korte kant) en n. Een vector staat nooit in een bericht.

De kleurvolgorde (BGR naar YuNet, RGB naar SFace) legt laag 2 vast; laag 4 beslist of dat de
juiste is (bouwontwerp X2, "De tests").
"""

from __future__ import annotations

import io
import math
import os
import stat
import warnings

import numpy as np
from PIL import Image, UnidentifiedImageError

from app.analyse import wire
from app.analyse.wire import Code
from app.analyse.worker.child import Job, OpFailed, Stopped
from app.analyse.worker.models import SFACE, YUNET

MAX_PIXELS = 4_000_000  # Keuze 60, K1-07: een preview tussen 4 en 8 MP is ook too_big
MAX_BYTES = 16 * 1024 * 1024  # een 1024.webp is ±0,1–0,3 MB
PAD = 32
STRIDES = (8, 16, 32)
NMS_IOU = 0.3  # OpenCV FaceDetectorYN
TOP_K = 5000
SIZE = 112
# Het sjabloon van SFace (OpenCV face_recognize.cpp): rechteroog, linkeroog, neus, mondhoeken
TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float64,
)


class PhotoError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- 3. decoderen ---------------------------------------------------------------------------


def read_preview(fd: int) -> bytes:
    try:
        info = os.fstat(fd)
    except OSError:
        raise PhotoError("unreadable") from None
    if not stat.S_ISREG(info.st_mode):
        raise PhotoError("unreadable")
    if info.st_size > MAX_BYTES:
        raise PhotoError("too_big")
    out = bytearray()
    try:
        while len(out) < info.st_size:
            chunk = os.pread(fd, info.st_size - len(out), len(out))
            if not chunk:
                break
            out += chunk
    except OSError:
        raise PhotoError("unreadable") from None
    return bytes(out)


def decode(data: bytes) -> np.ndarray:
    """De preview als RGB, uint8, (h, w, 3)."""
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data), formats=["WEBP"]) as img:
                img.load()
                rgb = img.convert("RGB")
        except (Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise PhotoError("too_big") from None
        except UnidentifiedImageError:
            raise PhotoError("unreadable") from None
        except (OSError, ValueError, SyntaxError, EOFError):
            raise PhotoError("image_bad") from None
    arr = np.asarray(rgb, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] != 3 or not arr.shape[0] or not arr.shape[1]:
        raise PhotoError("image_bad")
    return arr


# --- 4 en 5. YuNet --------------------------------------------------------------------------


def padded(n: int) -> int:
    return ((n - 1) // PAD + 1) * PAD


def yunet_input(rgb: np.ndarray) -> np.ndarray:
    """BGR, float32 0–255, NCHW, rechts en onder opgevuld met nullen tot een veelvoud van 32."""
    h, w = rgb.shape[:2]
    blob = np.zeros((1, 3, padded(h), padded(w)), dtype=np.float32)
    blob[0, :, :h, :w] = rgb[:, :, ::-1].transpose(2, 0, 1)
    return blob


def decode_yunet(
    outputs: dict[str, np.ndarray], pad_h: int, pad_w: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(kaders x, y, w, h), scores, punten (5 × 2) voor elk anker van de drie strides."""
    boxes, scores, points = [], [], []
    for s in STRIDES:
        rows, cols = pad_h // s, pad_w // s
        cls = outputs[f"cls_{s}"].reshape(-1)
        obj = outputs[f"obj_{s}"].reshape(-1)
        box = outputs[f"bbox_{s}"].reshape(-1, 4)
        kps = outputs[f"kps_{s}"].reshape(-1, 10)
        if not (cls.size == obj.size == box.shape[0] == kps.shape[0] == rows * cols):
            raise OpFailed(Code.INTERNAL, "de uitvoer van YuNet past niet bij de invoer")
        idx = np.arange(rows * cols)
        r, c = (idx // cols).astype(np.float64), (idx % cols).astype(np.float64)
        score = np.sqrt(np.clip(cls, 0, 1).astype(np.float64) * np.clip(obj, 0, 1))
        cx = (c + box[:, 0]) * s
        cy = (r + box[:, 1]) * s
        bw = np.exp(box[:, 2].astype(np.float64)) * s
        bh = np.exp(box[:, 3].astype(np.float64)) * s
        boxes.append(np.stack([cx - bw / 2, cy - bh / 2, bw, bh], axis=1))
        scores.append(score)
        pts = kps.astype(np.float64).reshape(-1, 5, 2).copy()
        pts[:, :, 0] = (pts[:, :, 0] + c[:, None]) * s
        pts[:, :, 1] = (pts[:, :, 1] + r[:, None]) * s
        points.append(pts)
    return np.concatenate(boxes), np.concatenate(scores), np.concatenate(points)


def nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    """cv2.dnn.NMSBoxes: score ≥ threshold, hoogste eerst (hooguit TOP_K), en een kader dat
    meer dan NMS_IOU overlapt met een gekozen kader valt af."""
    keep_idx = np.flatnonzero(np.isfinite(scores) & (scores >= threshold))
    order = keep_idx[np.argsort(-scores[keep_idx], kind="stable")][:TOP_K]
    chosen: list[int] = []
    x1, y1 = boxes[:, 0], boxes[:, 1]
    x2, y2 = x1 + boxes[:, 2], y1 + boxes[:, 3]
    area = np.maximum(boxes[:, 2], 0) * np.maximum(boxes[:, 3], 0)
    for i in order:
        if not np.all(np.isfinite(boxes[i])):
            continue
        ok = True
        for j in chosen:
            iw = min(x2[i], x2[j]) - max(x1[i], x1[j])
            ih = min(y2[i], y2[j]) - max(y1[i], y1[j])
            if iw <= 0 or ih <= 0:
                continue
            inter = iw * ih
            union = area[i] + area[j] - inter
            if union > 0 and inter / union > NMS_IOU:
                ok = False
                break
        if ok:
            chosen.append(int(i))
    return chosen


# --- 6. uitlijnen ---------------------------------------------------------------------------


def similarity(src: np.ndarray, dst: np.ndarray = TEMPLATE) -> np.ndarray:
    """Umeyama (1991): de gelijkvormigheid (2 × 3) die src het best op dst legt."""
    n = src.shape[0]
    src_mean, dst_mean = src.mean(axis=0), dst.mean(axis=0)
    src_d, dst_d = src - src_mean, dst - dst_mean
    a = dst_d.T @ src_d / n
    d = np.ones(2)
    if np.linalg.det(a) < 0:
        d[1] = -1
    u, s, vt = np.linalg.svd(a)
    t = np.eye(3)
    rank = np.linalg.matrix_rank(a)
    if rank == 0:
        raise ValueError("de punten liggen op elkaar")
    if rank == 1:
        if np.linalg.det(u) * np.linalg.det(vt) > 0:
            t[:2, :2] = u @ vt
        else:
            keep = d[1]
            d[1] = -1
            t[:2, :2] = u @ np.diag(d) @ vt
            d[1] = keep
    else:
        t[:2, :2] = u @ np.diag(d) @ vt
    var = src_d.var(axis=0).sum()
    scale = (s @ d) / var
    t[:2, 2] = dst_mean - scale * (t[:2, :2] @ src_mean)
    t[:2, :2] *= scale
    return t[:2]


def warp(img: np.ndarray, m: np.ndarray, size: int = SIZE) -> np.ndarray:
    """cv2.warpAffine(img, m, (size, size)), bilineair, buiten het beeld 0. float32 (h, w, 3)."""
    full = np.vstack([m, [0.0, 0.0, 1.0]])
    inv = np.linalg.inv(full)[:2]
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float64)
    sx = inv[0, 0] * xs + inv[0, 1] * ys + inv[0, 2]
    sy = inv[1, 0] * xs + inv[1, 1] * ys + inv[1, 2]
    x0, y0 = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
    fx, fy = (sx - x0)[..., None], (sy - y0)[..., None]
    h, w = img.shape[:2]
    src = img.astype(np.float32)

    def at(yy: np.ndarray, xx: np.ndarray) -> np.ndarray:
        inside = (yy >= 0) & (yy < h) & (xx >= 0) & (xx < w)
        out = np.zeros((size, size, 3), dtype=np.float32)
        out[inside] = src[yy[inside], xx[inside]]
        return out

    top = at(y0, x0) * (1 - fx) + at(y0, x0 + 1) * fx
    bottom = at(y0 + 1, x0) * (1 - fx) + at(y0 + 1, x0 + 1) * fx
    return (top * (1 - fy) + bottom * fy).astype(np.float32)


# --- 7. SFace -------------------------------------------------------------------------------


def sface_input(aligned_rgb: np.ndarray) -> np.ndarray:
    """RGB, float32 0–255, NCHW (OpenCV: blobFromImage met swapRB op het BGR-kader)."""
    return np.ascontiguousarray(aligned_rgb.transpose(2, 0, 1)[None], dtype=np.float32)


def normalized(vec: np.ndarray) -> np.ndarray:
    v = vec.astype(np.float64).reshape(-1)
    norm = float(np.linalg.norm(v))
    if v.size != wire.FACE_DIM or not math.isfinite(norm) or norm <= 0:
        raise ValueError("geen bruikbare vector")
    out = (v / norm).astype(np.float32)
    if not np.all(np.isfinite(out)):
        raise ValueError("geen bruikbare vector")
    return out


# --- de opdracht ----------------------------------------------------------------------------


def _run(sess, feeds: dict, job: Job) -> list[np.ndarray]:
    import onnxruntime

    options = onnxruntime.RunOptions()
    options.log_severity_level = 3
    job.stop.run_options = options
    job.stop.check()
    try:
        return sess.run(None, feeds, options)
    except Exception:
        if job.stop.set:
            raise Stopped() from None
        raise
    finally:
        job.stop.run_options = None


def _unit(value: float) -> float:
    return float(min(max(float(value), 0.0), 1.0))


def faces_in(
    rgb: np.ndarray, args: wire.FacesArgs, job: Job
) -> tuple[list[dict], list[np.ndarray]]:
    yunet = job.models.session(YUNET)
    sface = job.models.session(SFACE)
    h, w = rgb.shape[:2]
    blob = yunet_input(rgb)
    names = [o.name for o in yunet.get_outputs()]
    outs = dict(zip(names, _run(yunet, {yunet.get_inputs()[0].name: blob}, job), strict=True))
    boxes, scores, points = decode_yunet(outs, blob.shape[2], blob.shape[3])
    found = []
    for i in nms(boxes, scores, args.score):
        x, y, bw, bh = boxes[i]
        x1, y1 = max(x, 0.0), max(y, 0.0)
        x2, y2 = min(x + bw, float(w)), min(y + bh, float(h))
        if x2 <= x1 or y2 <= y1:
            continue
        px = min(x2 - x1, y2 - y1)
        if px < args.min_px:
            continue
        found.append((float((x2 - x1) * (y2 - y1)), i, (x1, y1, x2, y2), px))
    found.sort(key=lambda f: (-f[0], f[1]))
    rows, vectors = [], []
    sface_in = sface.get_inputs()[0].name
    for _area, i, (x1, y1, x2, y2), px in found[: args.max]:
        job.stop.check()
        try:
            aligned = warp(rgb, similarity(points[i]))
        except ValueError:  # de punten zijn niet uit te lijnen (ook LinAlgError)
            continue
        (out,) = _run(sface, {sface_in: sface_input(aligned)}, job)[:1]
        try:
            vectors.append(normalized(out))
        except ValueError:
            continue
        rows.append(
            {
                "x": _unit(x1 / w),
                "y": _unit(y1 / h),
                "w": _unit((x2 - x1) / w),
                "h": _unit((y2 - y1) / h),
                "score": _unit(scores[i]),
                "px": int(round(px)),
            }
        )
    return rows, vectors


def _write_all(fd: int, data: bytes, offset: int) -> int:
    view = memoryview(data)
    while view:
        done = os.pwrite(fd, view, offset)
        view, offset = view[done:], offset + done
    return offset


def run(job: Job) -> dict:
    try:
        args = wire.FacesArgs.model_validate(job.args)
    except ValueError:
        raise OpFailed(Code.BAD_REQUEST, "args van faces") from None
    if len(job.fds) < 2:
        raise OpFailed(Code.BAD_REQUEST, "faces vraagt previews en uit")
    previews, out = job.fds[:-1], job.fds[-1]
    try:
        os.ftruncate(out, 0)
    except OSError:
        raise OpFailed(Code.BAD_REQUEST, "uit is geen memfd om in te schrijven") from None
    # De modellen eerst: ontbreekt er een, dan model_missing voor de hele opdracht
    job.models.session(YUNET)
    job.models.session(SFACE)
    photos = []
    offset = 0
    for fd in previews:
        job.stop.check()
        try:
            rgb = decode(read_preview(fd))
        except PhotoError as exc:
            photos.append({"code": exc.code})
            continue
        h, w = rgb.shape[:2]
        rows, vectors = faces_in(rgb, args, job)
        del rgb
        for vec in vectors:
            offset = _write_all(out, vec.tobytes(), offset)
        photos.append({"code": "ok", "w": w, "h": h, "faces": rows, "n": len(rows)})
    return wire.FacesData.model_validate({"photos": photos}).model_dump(mode="json")
