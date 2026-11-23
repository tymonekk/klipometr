"""Wykrywanie baneru (ruletka skrzynek z niebieską albo złotą ramką) na pojedynczej klatce.

Baner to szeroki prostokąt z grubą, nasyconą ramką (niebieską, a przy wygranej złotą),
zajmujący większość szerokości filmu. Szukamy więc w klatce obrysu w tym kolorze, który:
  - jest szeroki (>= MIN_WIDTH szerokości klatki),
  - ma proporcje mniej więcej 2:1 do 4,5:1,
  - jest "pusty w środku": kolor leży głównie na krawędziach prostokąta, a nie wypełnia go.
"""
import cv2
import numpy as np

WORK_WIDTH = 360          # klatki skalujemy do tej szerokości
MIN_WIDTH = 0.55          # minimalna szerokość baneru jako część szerokości klatki
MIN_ASPECT, MAX_ASPECT = 1.9, 4.8
MIN_EDGE = 0.55           # jaka część każdej z 4 krawędzi musi mieć kolor ramki
MAX_FILL = 0.45           # ile najwyżej wnętrza może mieć kolor ramki

# zakresy HSV w OpenCV (H: 0-179)
COLORS = {
    "niebieski": ((85, 110, 140), (108, 255, 255)),
    "zloty": ((14, 110, 140), (34, 255, 255)),
}


def _edge_cover(mask, x, y, w, h, t):
    """Udział pikseli ramki na każdej z czterech krawędzi prostokąta (pomija narożniki)."""
    cx0, cx1 = x + w // 8, x + w - w // 8
    cy0, cy1 = y + h // 6, y + h - h // 6

    def frac(region):
        return float(region.mean()) / 255.0 if region.size else 0.0

    top = frac(mask[y:y + t, cx0:cx1].max(axis=0, keepdims=True))
    bottom = frac(mask[y + h - t:y + h, cx0:cx1].max(axis=0, keepdims=True))
    left = frac(mask[cy0:cy1, x:x + t].max(axis=1, keepdims=True))
    right = frac(mask[cy0:cy1, x + w - t:x + w].max(axis=1, keepdims=True))
    return min(top, bottom, left, right)


def find_banner(frame_bgr):
    """Zwraca słownik z opisem znalezionego baneru albo None."""
    h0, w0 = frame_bgr.shape[:2]
    if w0 != WORK_WIDTH:
        frame_bgr = cv2.resize(frame_bgr, (WORK_WIDTH, round(h0 * WORK_WIDTH / w0)), interpolation=cv2.INTER_AREA)
    H, W = frame_bgr.shape[:2]
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    best = None
    for name, (lo, hi) in COLORS.items():
        mask = cv2.inRange(hsv, np.array(lo), np.array(hi))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w < MIN_WIDTH * W or h < 8:
                continue
            aspect = w / h
            if not (MIN_ASPECT <= aspect <= MAX_ASPECT):
                continue
            t = max(3, round(w * 0.025))
            edge = _edge_cover(mask, x, y, w, h, t)
            if edge < MIN_EDGE:
                continue
            inner = mask[y + 2 * t:y + h - 2 * t, x + 2 * t:x + w - 2 * t]
            fill = float(inner.mean()) / 255.0 if inner.size else 1.0
            if fill > MAX_FILL:
                continue
            score = edge * (1 - fill)
            if best is None or score > best["score"]:
                best = {"color": name, "score": round(score, 3), "edge": round(edge, 3),
                        "fill": round(fill, 3), "box": [x, y, w, h]}
    return best
