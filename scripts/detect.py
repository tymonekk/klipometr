"""Wykrywanie baneru (ruletka skrzynek) na pojedynczej klatce.

Baner ma zawsze ten sam kształt, zmienia się tylko jego wielkość i miejsce:
  - prostokąt o proporcjach ~2,58:1 z cienką ramką (niebieską, a przy wygranej złotą),
  - na środku górnej i dolnej krawędzi trójkąty skierowane do środka,
  - nieprzezroczysty, ciemny środek (kafelki z broniami).

Dla każdej wielkości porównujemy klatkę z wzorcem tego kształtu (cv2.matchTemplate):
  plus  = jaka część ramki i trójkątów ma kolor ramki (powinno być ~1),
  minus = jaka część wnętrza ma kolor ramki (powinno być ~0).
Dzięki "minus" nie myli nas tło w podobnym kolorze (żółte ściany na Dust2), a dzięki temu,
że patrzymy tylko na ramkę i wnętrze, nie przeszkadza niebieskie niebo dookoła baneru.
"""
import cv2
import numpy as np

WORK_WIDTH = 360            # klatki skalujemy do tej szerokości
ASPECT = 2.58               # szerokość / wysokość baneru
MIN_W, MAX_W = 0.45, 0.97   # szerokość baneru jako część szerokości klatki
SCALE_STEP = 1.03           # kolejne sprawdzane szerokości różnią się o 3%
MIN_SCORE = 0.80            # plus - minus (prawdziwe banery: >= 0.9, inne rzeczy: <= 0.81)
MIN_PLUS = 0.85
MAX_MINUS = 0.15
NEAR_SCORE = 0.5            # od tego wyniku zapamiętujemy "prawie trafienia" (tylko do kontroli)
MAX_INNER_V = 110           # mediana jasności wnętrza (baner: 30-60, ściany mapy itp.: 85-255)

# zakresy HSV w OpenCV (H: 0-179)
COLORS = {
    "niebieski": ((85, 110, 140), (108, 255, 255)),
    "zloty": ((14, 110, 140), (34, 255, 255)),
}

_templates = {}


def _template(w):
    """Wzorce (plus, minus) dla baneru szerokości w pikseli."""
    if w in _templates:
        return _templates[w]
    h = round(w / ASPECT)
    bt = max(2, round(w * 0.015))           # grubość ramki
    rc = max(3, round(w * 0.05))            # pomijany narożnik (zaokrąglony)
    plus = np.zeros((h, w), np.float32)
    plus[0:bt, rc:w - rc] = 1
    plus[h - bt:h, rc:w - rc] = 1
    plus[rc:h - rc, 0:bt] = 1
    plus[rc:h - rc, w - bt:w] = 1
    # trójkąty: podstawa ~8% szerokości, wysokość ~17% wysokości (licząc od ramki)
    tb, th = w * 0.08, h * 0.17
    cx = (w - 1) / 2
    tri = np.zeros((h, w), np.uint8)
    top = np.array([[cx - tb / 2, bt], [cx + tb / 2, bt], [cx, bt + th]], np.int32)
    bot = np.array([[cx - tb / 2, h - 1 - bt], [cx + tb / 2, h - 1 - bt], [cx, h - 1 - bt - th]], np.int32)
    cv2.fillPoly(tri, [top, bot], 1)
    core = cv2.erode(tri, np.ones((3, 3), np.uint8))   # bez brzegów, które bywają rozmyte
    plus[core > 0] = 1
    # wnętrze (bez trójkątów z zapasem i bez pasa tuż przy ramce)
    m = bt + max(2, round(w * 0.012))
    minus = np.zeros((h, w), np.float32)
    minus[m:h - m, m:w - m] = 1
    minus[cv2.dilate(tri, np.ones((7, 7), np.uint8)) > 0] = 0
    plus /= plus.sum()
    minus /= minus.sum()
    _templates[w] = (plus, minus)
    return plus, minus


def _widths(W):
    w = MIN_W * W
    out = []
    while w <= MAX_W * W:
        out.append(int(round(w)))
        w *= SCALE_STEP
    return out


def find_banner(frame_bgr, near=None):
    """Zwraca słownik z opisem znalezionego baneru albo None.

    Jeśli podasz słownik `near`, trafi do niego najlepszy kandydat z ciemnym środkiem także poniżej progów
    (do kontroli, czy jakiś baner nie umyka)."""
    h0, w0 = frame_bgr.shape[:2]
    if w0 != WORK_WIDTH:
        frame_bgr = cv2.resize(frame_bgr, (WORK_WIDTH, round(h0 * WORK_WIDTH / w0)), interpolation=cv2.INTER_AREA)
    H, W = frame_bgr.shape[:2]
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    best = None
    for name, (lo, hi) in COLORS.items():
        mask = cv2.inRange(hsv, np.array(lo), np.array(hi))
        # ramka ma ~5 px przy pełnej szerokości; jeśli w klatce prawie nie ma tego koloru, szkoda czasu
        if cv2.countNonZero(mask) < MIN_W * W * 4 * 2:
            continue
        m_in = (mask > 0).astype(np.float32)
        m_plus = cv2.dilate(m_in, np.ones((3, 3), np.uint8))   # tolerancja na skalę i rozmycie
        for w in _widths(W):
            plus, minus = _template(w)
            ph, pw = plus.shape
            if ph > H or pw > W:
                continue
            sp = cv2.matchTemplate(m_plus, plus, cv2.TM_CCORR)
            sm = cv2.matchTemplate(m_in, minus, cv2.TM_CCORR)
            score = sp - sm
            _, val, _, (x, y) = cv2.minMaxLoc(score)
            if val < NEAR_SCORE:
                continue
            p, n = float(sp[y, x]), float(sm[y, x])
            # wnętrze baneru jest ciemne (kafelki z broniami na ciemnym tle)
            inner = hsv[y + ph // 5:y + ph - ph // 5, x + pw // 10:x + pw - pw // 10, 2]
            dark = float(np.median(inner))
            if dark > MAX_INNER_V:
                continue
            cand = {"color": name, "score": round(float(val), 3), "plus": round(p, 3),
                    "minus": round(n, 3), "v": round(dark), "box": [int(x), int(y), pw, ph]}
            ok = val >= MIN_SCORE and p >= MIN_PLUS and n <= MAX_MINUS
            if ok and (best is None or val > best["score"]):
                best = cand
            if near is not None and (near.get("score", 0) < val):
                near.clear()
                near.update(cand)
    return best
