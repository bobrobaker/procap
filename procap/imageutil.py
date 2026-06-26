"""Image comparison primitives shared by stage 1 (extract) and stage 2 (golden).

Screen UIs are flat, high-contrast, and change in small localized regions (a value field,
a status light). Global SSIM is insensitive to exactly those changes, so the primary
metric here is the *changed-pixel fraction*: the fraction of pixels whose intensity moved
more than `pix_thresh`. A value field flipping registers; a few pixels of cursor jitter
does not. This is why extract uses it for keyframe boundaries and golden uses it for
revert-detection (returning to a prior state == near-zero changed fraction).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image

COMPARE_W = 640          # higher than SSIM needs: small text must survive the downscale
PIX_THRESH = 28          # 0..255 intensity delta that counts as "changed"


@lru_cache(maxsize=512)
def load_gray(path: str, width: int = COMPARE_W) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"could not read image: {path}")
    h, w = img.shape
    scale = width / w
    return cv2.resize(img, (width, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)


def changed_fraction(a: np.ndarray, b: np.ndarray, pix_thresh: int = PIX_THRESH) -> float:
    """Fraction (0..1) of pixels that changed by more than `pix_thresh` between a and b."""
    diff = cv2.absdiff(a, b)
    return float(np.count_nonzero(diff > pix_thresh)) / diff.size


def changed_fraction_paths(path_a: str, path_b: str, pix_thresh: int = PIX_THRESH) -> float:
    return changed_fraction(load_gray(path_a), load_gray(path_b), pix_thresh)


def phash(path: str | Path) -> str:
    return str(imagehash.phash(Image.open(path)))
