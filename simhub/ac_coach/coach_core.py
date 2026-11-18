# -*- coding: utf-8 -*-
"""
Nucleo de analise do AC Coach (compativel com o Python 3.3 embutido no Assetto Corsa).
Grava voltas, monta a volta ideal por mini-setores, detecta zonas de frenagem e
compara ponto de freio, pressao, soltura, travamento e velocidade minima.
"""
import bisect
import json
import math
import os
import random
import re
import time

ON = 0.10    # pressao a partir da qual conta como "freando"
OFF = 0.03   # pressao abaixo da qual conta como "freio solto"
MAX_SAVED_LAPS = 8
LOCK_RATIO = 0.12   # roda girando 12% mais devagar que o carro = comecando a travar
SPEED_RED = 3.0     # km/h abaixo da referencia que deixa a janela toda vermelha
WHEELS = ("diant. esq.", "diant. dir.", "tras. esq.", "tras. dir.")



class Snapshot:
    __slots__ = ("status", "completed", "lap_ms", "last_ms", "pos", "speed",
                 "brake", "gas", "in_pit", "car", "track", "track_len",
                 "dist_total", "slip", "invalid", "wx", "wz", "gear")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))



class DemoSource:
    """Pista ficticia de 3,2 km com 6 curvas e um piloto com variacao natural."""
    L = 3200
    VTOP = 265.0
    CORNERS = [(420, 85), (980, 150), (1420, 70), (1900, 115), (2380, 95), (2800, 175)]

    def __init__(self, speedup=1.0, clock=None, seed=None):
        self.rng = random.Random(seed)
        self.clock = clock or time.monotonic
        self.speedup = speedup
        self.t0 = self.clock()
        self.lap_start = 0.0
        self.completed = 0
        self.last_ms = 0
        self.dist_total = 0.0
        self.v_start = 200 / 3.6
        self._make_geometry()
        self._make_lap()

    def _make_geometry(self):
        L = self.L
        kap = [0.0] * (L + 1)
        for n, (dc, vc) in enumerate(self.CORNERS):
            sign = 1 if n % 2 == 0 else -1
            v2 = (vc / 3.6) ** 2
            for d in range(max(0, dc - 90), min(L, dc + 90) + 1):
                taper = min(1.0, max(0.0, (90 - abs(d - dc)) / 40.0))
                kap[d] += sign * 12.0 / (v2 + 2 * 11.0 * abs(d - dc)) * taper   # curva no limite de ~1,2 g
        self.kap = [abs(k) for k in kap]
        th, x, z = 0.0, 0.0, 0.0
        self.gx, self.gz = [0.0] * (L + 1), [0.0] * (L + 1)
        for d in range(1, L + 1):
            th += kap[d]
            x += math.cos(th)
            z += math.sin(th)
            self.gx[d], self.gz[d] = x, z

    def _make_lap(self):
        """Piloto de teste que respeita o circulo de atrito (frenagem + curva dividem a aderencia)."""
        rng, L = self.rng, self.L
        vtop = self.VTOP / 3.6
        lat = [12.0] * (L + 1)
        dec = [11.0] * (L + 1)
        press = [0.0] * (L + 1)
        cid = [-1] * (L + 1)
        style, pc = [], []
        for c, (dc, _) in enumerate(self.CORNERS):
            p = min(1.0, max(0.55, 0.9 + rng.gauss(0, 0.08)))   # pressao que o "piloto" usa
            pc.append(p)
            early = rng.uniform(15, 45) if rng.random() < 0.3 else 0.0      # solta antes do apice
            style.append((rng.uniform(0.15, 0.5), rng.uniform(0.3, 2.4), early))  # (pressao final, formato, soltura)
            lc = 12.0 * (1 + rng.gauss(0, 0.04))
            prev = self.CORNERS[c - 1][0] if c > 0 else 0
            for d in range(prev + 1 if c > 0 else 0, min(L, dc + 90) + 1):
                lat[d], dec[d], press[d], cid[d] = lc, 12.5 * p, p, c
        kap = self.kap

        def ell(v, i):
            return max(0.12, math.sqrt(max(0.0, 1 - (v * v * kap[i] / lat[i]) ** 2)))

        vcap = [min(vtop, math.sqrt(lat[i] / kap[i])) if kap[i] > 1e-6 else vtop for i in range(L + 1)]
        v = vcap[:]
        v[0] = min(vcap[0], self.v_start)
        for d in range(1, L + 1):
            acc = max(1.2, 8.5 * (1 - v[d - 1] / vtop)) * ell(v[d - 1], d - 1)
            v[d] = min(vcap[d], math.sqrt(v[d - 1] ** 2 + 2 * acc))
        for d in range(L - 1, -1, -1):
            v[d] = min(v[d], math.sqrt(v[d + 1] ** 2 + 2 * dec[d] * ell(v[d + 1], d)))
        t = [0.0] * (L + 1)
        b = [0.0] * (L + 1)
        g = [0.0] * (L + 1)
        sl = [(0.0, 0.0, 0.0, 0.0)] * (L + 1)
        bstart = {}
        for d in range(1, L + 1):
            if v[d] < v[d - 1] - 1e-6 and cid[d] >= 0 and cid[d] not in bstart:
                bstart[cid[d]] = d
        for d in range(1, L + 1):
            t[d] = t[d - 1] + 2.0 / (v[d - 1] + v[d])
            if v[d] < v[d - 1] - 1e-6:
                c = cid[d]
                x = press[d]
                if c >= 0 and c in bstart:
                    dc = self.CORNERS[c][0]
                    fr = max(0.0, min(1.0, (dc - d) / max(1, dc - bstart[c])))
                    r0, k, early = style[c]
                    x = 0.0 if dc - d < early else press[d] * (r0 + (1 - r0) * fr ** k)
                x = max(0.0, min(1.0, x + rng.gauss(0, 0.02))) if x > 0 else 0.0
                b[d] = x
                kmh = v[d] * 3.6
                grip = 0.70 + 0.30 * max(0.0, min(1.0, (kmh - 60) / 160))
                if x > grip + 0.02:
                    r = -min(1.0, 0.15 + (x - grip) * 4)
                    sl[d] = (r, r * 0.7, 0.0, 0.0)
            elif v[d] > v[d - 1] + 1e-6:
                g[d] = 1.0
            else:
                g[d] = 0.35
        self.v_start = v[-1]
        self.pt, self.pv, self.pb, self.pg, self.ps = t, v, b, g, sl

    def read(self):
        sim = (self.clock() - self.t0) * self.speedup
        lt = sim - self.lap_start
        while lt >= self.pt[-1]:
            T = self.pt[-1]
            self.completed += 1
            self.last_ms = int(T * 1000)
            self.lap_start += T
            self.dist_total += self.L
            self._make_lap()
            lt = sim - self.lap_start
        t = self.pt
        j = max(0, min(len(t) - 2, bisect.bisect_right(t, lt) - 1))
        f = (lt - t[j]) / (t[j + 1] - t[j]) if t[j + 1] > t[j] else 0.0
        d = j + f
        lerp = lambda a: a[j] + (a[j + 1] - a[j]) * f
        return Snapshot(status=2, completed=self.completed, lap_ms=int(lt * 1000),
                        last_ms=self.last_ms, pos=d / self.L, speed=lerp(self.pv) * 3.6,
                        brake=self.pb[j + 1] if f > 0 else self.pb[j], gas=lerp(self.pg),
                        in_pit=False, car="demo_car", track="demo_track",
                        track_len=float(self.L), dist_total=self.dist_total + d,
                        slip=self.ps[j + 1] if f > 0 else self.ps[j],
                        wx=self.gx[j] + (self.gx[j + 1] - self.gx[j]) * f,
                        wz=self.gz[j] + (self.gz[j + 1] - self.gz[j]) * f)


# ---------------------------------------------------------------------------
# Analise
# ---------------------------------------------------------------------------
def fmt_time(s):
    if s is None or s <= 0:
        return "--:--.---"
    m = int(s // 60)
    return "{}:{:06.3f}".format(m, s - 60 * m)


def resample(raw, n, lap_time):
    """raw: lista de (pos, t, v, b, g[, x, z]) com pos crescente -> grade uniforme de n+1 pontos."""
    nch = min(len(p) for p in raw)
    first, last = raw[0], raw[-1]
    pts = [tuple(p[:nch]) for p in raw]
    if first[0] > 0:
        pts.insert(0, (0.0, 0.0) + tuple(first[2:nch]))
    if last[0] < 1:
        pts.append((1.0, max(lap_time, last[1] + 0.001)) + tuple(last[2:nch]))
    ps = [p[0] for p in pts]
    keys = [("t", 4), ("v", 2), ("b", 3), ("g", 3), ("x", 2), ("z", 2), ("gear", 0)][:nch - 1]
    out = dict((k, []) for k, _ in keys)
    for i in range(n + 1):
        x = i / n
        j = bisect.bisect_left(ps, x)
        if j <= 0:
            r = pts[0]
        elif j >= len(pts):
            r = pts[-1]
        else:
            a, c = pts[j - 1], pts[j]
            f = (x - a[0]) / (c[0] - a[0]) if c[0] > a[0] else 0.0
            r = tuple(a[k] + (c[k] - a[k]) * f for k in range(nch))
        for k, (name, dec) in enumerate(keys):
            out[name].append(round(r[k + 1], dec))
    out["time"] = lap_time
    return out


# ---------------------------------------------------------------------------
# Niveis prontos: voltas simuladas a partir do tracado e do limite do carro
# ---------------------------------------------------------------------------
# (nome, % da aderencia em curva, % da frenagem maxima, % da aceleracao)
LEVELS = [
    ("Iniciante", 0.78, 0.55, 0.85),
    ("Intermediario", 0.85, 0.68, 0.90),
    ("Avancado", 0.91, 0.80, 0.95),
    ("Expert", 0.96, 0.90, 0.98),
    ("Profissional", 1.00, 1.00, 1.00),
]
LIMIT_MARGIN = 1.05   # sem tempo-alvo: o "profissional" usa 5% alem do maximo que voce ja mostrou

# Com tempo-alvo do profissional (alvos.json): cada nivel fica X% mais lento que ele.
# Ex. Exige V6 Cup @ Silverstone GP, pro 2:18.1 -> Expert 2:20.9, Avancado 2:24.1 (~IA),
#     Intermediario 2:28.5, Iniciante 2:33.3
LEVEL_GAPS = [0.11, 0.075, 0.042, 0.02, 0.0]


def parse_time(txt):
    """'2:18.122' ou '138.122' -> segundos."""
    txt = str(txt).strip().replace(",", ".")
    if ":" in txt:
        m, sec = txt.split(":", 1)
        return int(m) * 60 + float(sec)
    return float(txt)


def _norm(txt):
    txt = str(txt).lower()
    for a, b in (("\u00e1", "a"), ("\u00e3", "a"), ("\u00e2", "a"), ("\u00e7", "c"), ("\u00e9", "e"),
                 ("\u00ea", "e"), ("\u00ed", "i"), ("\u00f3", "o"), ("\u00f4", "o"), ("\u00fa", "u")):
        txt = txt.replace(a, b)
    return txt.strip()


def _tokens(v):
    return [_norm(t) for t in (v if isinstance(v, list) else [v]) if str(t).strip()]


def load_targets(path):
    """Le o arquivo de referencias (referencias.json). Cada item:
    carro / pista: pedaco(s) do nome interno do AC; lista = todos precisam aparecer;
                   item comecando com "!" = NAO pode aparecer.
    tempo_profissional: "m:ss.mmm"
    tempos_niveis (opcional): {"Avancado": "2:24.0", ...} fixa o tempo de um nivel."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (IOError, OSError, ValueError):
        return []
    out = []
    for a in data.get("referencias", data.get("alvos", [])):
        try:
            over = {}
            for k, v in (a.get("tempos_niveis") or {}).items():
                over[_norm(k)] = parse_time(v)
            if not a.get("tempo_profissional"):
                continue                        # combinacao sem tempo: niveis usam a estimativa
            out.append({"carro": _tokens(a["carro"]), "pista": _tokens(a["pista"]),
                        "pro": parse_time(a["tempo_profissional"]), "fonte": str(a.get("fonte", "")),
                        "niveis": over})
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return out


def _match(tokens, name):
    exact = [t[1:] for t in tokens if t.startswith("=")]
    if exact:                                   # "=nome" -> tem que ser exatamente esse nome
        return name in exact, 100
    pos = [t for t in tokens if not t.startswith("!")]
    neg = [t[1:] for t in tokens if t.startswith("!")]
    return all(t in name for t in pos) and not any(t in name for t in neg), len(pos)


def find_target(targets, car, track):
    """Referencia mais especifica que casa com o carro e a pista (None se nao houver)."""
    car, track = _norm(car or ""), _norm(track or "")
    best, score = None, -1
    for t in targets:
        okc, nc = _match(t["carro"], car)
        okt, nt = _match(t["pista"], track)
        if okc and okt and nc + nt > score:
            best, score = t, nc + nt
    if best is None:
        return None
    return best["pro"], best["fonte"], best["niveis"]


def _pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * (len(s) - 1)))]


def track_curvature(x, z, step_m):
    """Curvatura (1/raio) em cada ponto do tracado, a partir das posicoes x/z gravadas."""
    n = len(x) - 1
    k = max(2, int(round(12.0 / step_m)))
    kap = [0.0] * n
    for i in range(n):
        a, c = (i - k) % n, (i + k) % n
        ax, az, bx, bz, cx, cz = x[a], z[a], x[i], z[i], x[c], z[c]
        ab = math.hypot(bx - ax, bz - az)
        bc = math.hypot(cx - bx, cz - bz)
        ca = math.hypot(ax - cx, az - cz)
        den = ab * bc * ca
        area2 = abs((bx - ax) * (cz - az) - (bz - az) * (cx - ax))
        kap[i] = 2.0 * area2 / den if den > 1e-6 else 0.0
    w = max(1, int(round(8.0 / step_m)))
    sm = []
    for i in range(n):
        tot = 0.0
        for d in range(-w, w + 1):
            tot += kap[(i + d) % n]
        sm.append(tot / (2 * w + 1))
    return sm


def car_limits(laps, kap, step):
    """Estima o limite do carro pelo que voce ja fez: aderencia lateral, frenagem, aceleracao."""
    n = len(kap)
    dec, bins, vmax = [], {}, 0.0
    best_corner = {}                       # apice -> (v^2, g lateral) mais alto que voce ja fez
    win = max(2, int(round(15.0 / step)))
    for lap in laps:
        v = [x / 3.6 for x in lap["v"]]
        t = lap["t"]
        vmax = max(vmax, max(v))
        for zn in find_zones(lap["v"], lap["b"], step):
            ap = zn["apex"]
            v2 = v[ap] ** 2
            a_lat = v2 * max(kap[j % n] for j in range(ap - win, ap + win + 1))
            key = int(ap * step // 60)
            if a_lat > best_corner.get(key, (0, 0))[0]:
                best_corner[key] = (a_lat, v2)
        for i in range(1, n - 1):
            dt = t[i + 1] - t[i - 1]
            if dt <= 0:
                continue
            a = (v[i + 1] - v[i - 1]) / dt
            if a < -0.5:
                dec.append(-a)
            elif a > 0.2 and lap["g"][i] > 0.9:
                bins.setdefault(int(v[i] * 3.6 // 10), []).append(a)
    dec_max = _pct(dec, 0.98)
    pts = [(v2, a) for a, v2 in best_corner.values() if a > 2]
    if not pts or not dec_max or dec_max < 3 or not bins:
        return None
    # aderencia lateral = a0 + c * v^2  (c > 0 quando o carro tem downforce)
    c = 0.0
    if len(pts) >= 3:
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        sxx = sum((p[0] - mx) ** 2 for p in pts)
        if sxx > 1e-9:
            c = max(0.0, sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx)
    a0 = max(p[1] - c * p[0] for p in pts)
    top = int(vmax * 3.6 // 10) + 2
    acc = [None] * (top + 1)
    for b, vals in bins.items():
        if b <= top:
            acc[b] = _pct(vals, 0.95)
    last = None
    for b in range(top + 1):              # preenche buracos
        if acc[b] is None:
            acc[b] = last
        last = acc[b]
    first = next(a for a in acc if a is not None)
    acc = [first if a is None else a for a in acc]
    for b in range(top - 1, -1, -1):       # envelope: em baixa velocidade acelera pelo menos igual a alta
        acc[b] = max(acc[b], acc[b + 1])
    return {"a0": a0 * LIMIT_MARGIN, "c": c * LIMIT_MARGIN, "dec": dec_max * LIMIT_MARGIN,
            "dec_user": dec_max, "acc": acc, "vtop": vmax * 1.02}


def _params(f):
    """Um unico 'fator de desempenho' -> (curva, frenagem, aceleracao). Frenagem varia mais entre niveis."""
    return f, f ** 1.6, min(1.0, 0.7 + 0.3 * f)


CAL_STEP = 4   # calibracao roda numa grade 4x mais grossa; so a volta final e na grade cheia


def calibrate(kap, lim, step, p_brake, target):
    """Acha o fator que faz a volta simulada bater o tempo-alvo (busca binaria).

    Cada simulate_lap passa ~6 vezes por todos os pontos da pista, com raiz quadrada em
    cada um; no Python do AC isso custa dezenas de milissegundos. Fazer 22 delas vezes 5
    niveis trava o jogo por segundos. Por isso a busca comeca numa grade 4x mais grossa,
    que da praticamente o mesmo tempo de volta por uma fracao do custo, e so o ajuste
    fino e a volta devolvida rodam na grade cheia.
    """
    ckap, cstep = kap[::CAL_STEP], step * CAL_STEP
    lo, hi = 0.3, 2.0
    for _ in range(10):                                   # grosso: acha a faixa certa
        mid = (lo + hi) / 2
        if simulate_lap(ckap, lim, *(_params(mid) + (cstep, p_brake)))["time"] > target:
            lo = mid
        else:
            hi = mid
    lo, hi = max(0.3, lo - 0.06), min(2.0, hi + 0.06)     # folga para o erro da grade grossa
    lap_hi = simulate_lap(kap, lim, *(_params(hi) + (step, p_brake)))
    if lap_hi["time"] > target:
        return lap_hi, False          # alvo inalcancavel com o que sabemos do carro
    lap = lap_hi
    for _ in range(5):                                    # fino: na grade cheia
        mid = (lo + hi) / 2
        lap = simulate_lap(kap, lim, *(_params(mid) + (step, p_brake)))
        if lap["time"] > target:
            lo = mid
        else:
            hi = mid
        if abs(lap["time"] - target) < 0.02:
            break
    return lap, True


def simulate_lap(kap, lim, fl, fb, fa, step, p_brake):
    """Volta de referencia: v = min(limite da curva, aceleracao, frenagem), com circulo de atrito."""
    n = len(kap)
    a0, cc, blim, vtop, acc = fl * lim["a0"], fl * lim["c"], fb * lim["dec"], lim["vtop"], lim["acc"]

    def acc_at(v):
        return fa * acc[min(len(acc) - 1, int(v * 3.6 // 10))]

    def lat_at(v):
        return a0 + cc * v * v

    vcap = [min(vtop, math.sqrt(a0 / (k - cc))) if k > cc + 1e-9 else vtop for k in kap]
    fw = vcap[:]
    cur = vcap[0]
    for _ in range(2):
        for i in range(n):
            j = (i + 1) % n
            ell = math.sqrt(max(0.0, 1 - (cur * cur * kap[i] / lat_at(cur)) ** 2))
            cur = min(vcap[j], math.sqrt(cur * cur + 2 * acc_at(cur) * max(0.15, ell) * step))
            fw[j] = cur
    bw = fw[:]
    for _ in range(2):
        for i in range(n - 1, -1, -1):
            vj = bw[(i + 1) % n]
            ell = math.sqrt(max(0.0, 1 - (vj * vj * kap[i] / lat_at(vj)) ** 2))
            bw[i] = min(fw[i], math.sqrt(vj * vj + 2 * blim * max(0.15, ell) * step))
    v = bw + [bw[0]]
    t, b, g = [0.0] * (n + 1), [0.0] * (n + 1), [0.0] * (n + 1)
    for i in range(n):
        t[i + 1] = t[i] + 2 * step / (v[i] + v[i + 1])
        if v[i + 1] < v[i] - 1e-4 and v[i] < fw[i] + 1e-6 and bw[i] < fw[i] - 1e-4:
            d = (v[i] ** 2 - v[i + 1] ** 2) / (2 * step)
            b[i] = min(1.0, p_brake * d / lim["dec_user"])
        elif v[i + 1] > v[i] + 1e-4:
            g[i] = 1.0
        else:
            g[i] = 0.5
    b[n], g[n] = b[0], g[0]
    return {"t": t, "v": [x * 3.6 for x in v], "b": b, "g": g, "time": t[n]}


def build_levels(laps, n, L, target=None, overrides=None):
    geo = [lp for lp in laps if "x" in lp and len(lp["x"]) == n + 1]
    if not geo or L <= 0:
        return None
    step = L / n
    base = geo[0]
    kap = track_curvature(base["x"], base["z"], step)
    lim = car_limits(laps, kap, step)
    if lim is None:
        return None
    peaks = [z["peak"] for z in find_zones(base["v"], base["b"], step)]
    p_brake = sorted(peaks)[len(peaks) // 2] if peaks else 0.9
    out = []
    if target:
        base = dict(lim)
        for k in ("a0", "c", "dec"):
            base[k] = lim[k] / LIMIT_MARGIN
        overrides = overrides or {}
        for (name, _, _, _), gap in zip(LEVELS, LEVEL_GAPS):
            goal = overrides.get(_norm(name), target * (1 + gap))
            lap, ok = calibrate(kap, base, step, p_brake, goal)
            lap["name"], lap["calibrated"] = name, ok
            out.append(lap)
        return out
    for name, fl, fb, fa in LEVELS:
        lap = simulate_lap(kap, lim, fl, fb, fa, step, p_brake)
        lap["name"] = name
        out.append(lap)
    return out


def build_ideal(laps, n, nseg):
    """Volta ideal: para cada mini-setor usa o trecho da volta mais rapida naquele setor."""
    bounds = [round(k * n / nseg) for k in range(nseg + 1)]
    t, v, b, g = [0.0] * (n + 1), [0.0] * (n + 1), [0.0] * (n + 1), [0.0] * (n + 1)
    gear, has_gear = [0.0] * (n + 1), True
    cum = 0.0
    for k in range(nseg):
        a, e = bounds[k], bounds[k + 1]
        best = min(laps, key=lambda lp: lp["t"][e] - lp["t"][a])
        base = best["t"][a]
        bg = best.get("gear")
        if not bg:
            has_gear = False
        for i in range(a, e + 1):
            t[i] = cum + best["t"][i] - base
            v[i], b[i], g[i] = best["v"][i], best["b"][i], best["g"][i]
            if bg:
                gear[i] = bg[i]
        cum += best["t"][e] - base
    out = {"t": t, "v": v, "b": b, "g": g, "time": cum}
    if has_gear:
        out["gear"] = gear
    return out


def find_zones(v, b, step_m):
    n = len(b)
    raw, i = [], 0
    while i < n:
        if b[i] >= ON:
            s = i
            while s > 0 and b[s - 1] > OFF:
                s -= 1
            e = i
            while e < n - 1 and b[e + 1] > OFF:
                e += 1
            raw.append([s, e])
            i = e + 1
        else:
            i += 1
    gap = max(1, int(40 / step_m))
    merged = []
    for s, e in raw:
        if merged and s - merged[-1][1] <= gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    zones = []
    for s, e in merged:
        vmin, ap, k = v[s], s, s
        while k < n:
            if v[k] < vmin:
                vmin, ap = v[k], k
            if k > e and v[k] > vmin + 3:
                break
            k += 1
        peak = max(b[s:e + 1])
        if v[s] - vmin < 8 and peak < 0.3:
            continue  # toque leve no freio, nao e uma curva
        zones.append({"start": s, "end": e, "apex": ap, "peak": peak,
                      "vmin": vmin, "ventry": v[s]})
    back, fwd, pad = int(150 / step_m), int(80 / step_m), max(1, int(10 / step_m))
    for k, z in enumerate(zones):
        prev_ap = zones[k - 1]["apex"] if k > 0 else -10 ** 9
        nxt = zones[k + 1]["start"] if k + 1 < len(zones) else 10 ** 9
        z["win_a"] = min(z["start"], max(0, z["start"] - back, prev_ap + pad))
        z["win_b"] = min(n - 1, max(z["apex"] + pad, min(z["apex"] + fwd, nxt - pad)))
        z["n"] = k + 1
    return zones


def coach_tip(r):
    tips = []
    bp, dv, rel = r["bp_m"], r["vmin"] - r["ref_vmin"], r.get("rel_m")
    if r.get("lock"):
        tips.append("travou ({}) a {:.0f} km/h - alivie o pedal".format(r['lock'], r['lock_kmh']))
    if bp is None:
        tips.append("nao freou - so aliviou")
    elif bp < -10:
        tips.append("frear ~{:.0f} m mais tarde".format(abs(bp)))
    elif bp > 10 and dv < -2:
        tips.append("freou tarde demais e matou a saida")
    if r["peak"] < r["ref_peak"] - 0.10:
        tips.append("pise mais forte no inicio da frenagem")
    elif r["peak"] > r["ref_peak"] + 0.12 and not r.get("lock"):
        tips.append("pressao alta demais, cuidado com o travamento")
    if rel is not None and bp is not None:
        if rel < -15:
            tips.append("soltou o freio {:.0f} m cedo demais - leve um pouco ate a tangencia".format(abs(rel)))
        elif rel > 15:
            tips.append("segurou o freio {:.0f} m a mais - solte antes".format(rel))
        elif r["trail"] > r["ref_trail"] + 0.15:
            tips.append("alivie mais o pedal no fim da frenagem")
        elif r["trail"] < r["ref_trail"] - 0.15:
            tips.append("mantenha mais freio na entrada (trail braking)")
    if dv < -3 and not (bp is not None and bp > 10) and len(tips) < 3:
        tips.append("carregue +{:.0f} km/h no apice".format(abs(dv)))
    if not tips:
        tips.append("boa, igual a referencia" if abs(r["dt"]) < 0.05 else
                    ("ganhou tempo aqui" if r["dt"] < 0 else "perdeu tempo na saida"))
    return "; ".join(tips[:2])


def synth_pedals(lap, step):
    """Carros da IA: se o jogo nao informar freio/acelerador, estima pela desaceleracao."""
    v = [x / 3.6 for x in lap["v"]]
    t = lap["t"]
    n = len(v)
    if max(lap["b"]) > 0.05 and max(lap["g"]) > 0.05:
        return lap
    acc = [0.0] * n
    for i in range(1, n - 1):
        dt = t[i + 1] - t[i - 1]
        if dt > 0:
            acc[i] = (v[i + 1] - v[i - 1]) / dt
    dec = _pct([-a for a in acc if a < -1.0], 0.98) or 10.0
    b, g = [0.0] * n, [0.0] * n
    for i in range(n):
        a = acc[i]
        if a < -3.5:                      # desaceleracao forte = freando (calibrado: 11/11 frenagens, erro <= 2 m)
            b[i] = min(1.0, 0.9 * (-a) / dec)
        elif a > 0.3:
            g[i] = 1.0
        else:
            g[i] = 0.4
    lap["b"], lap["g"], lap["pedais_estimados"] = b, g, True
    return lap


OFFTRACK_M = 14.0      # desvio maximo aceitavel do tracado comum (a pista tem ~13 m de largura)
OFFTRACK_MIN = 2       # so testa quando ja existem pelo menos 2 voltas guardadas


def path_offtrack(lap, laps, limit=OFFTRACK_M):
    """Detecta a volta que passou POR FORA da pista, pela geometria do tracado.

    Por que isto existe: o canal de rodas fora do jogo nao pega todos os casos. Uma
    volta que alarga para a area de escape ou corta uma curva pode marcar zero rodas
    fora e ainda assim virar a "melhor volta", e ai o app passa a cobrar um ponto de
    freada e uma velocidade que nao da para repetir dentro da pista.

    Como funciona: todas as voltas sao reamostradas na MESMA grade de posicao do
    spline, entao o ponto i de duas voltas e o mesmo lugar da pista e a distancia
    entre elas e direta. Duas voltas legitimas ficam a poucos metros uma da outra;
    uma que saiu fica a dezenas de metros. Compara com a mediana das voltas ja
    guardadas, para uma unica volta ruim no conjunto nao estragar a conta.

    Retorna o desvio em metros se a volta estiver fora, ou None se estiver limpa.
    """
    x = lap.get("x")
    if not x:
        return None
    ref = [l for l in laps if l.get("x") and len(l["x"]) == len(x)]
    if len(ref) < OFFTRACK_MIN:
        return None
    z = lap["z"]
    dev = []
    for i in range(0, len(x), 3):              # 1 ponto a cada 3 ja basta e custa 3x menos
        ds = sorted(math.hypot(x[i] - r["x"][i], z[i] - r["z"][i]) for r in ref)
        dev.append(ds[len(ds) // 2])           # mediana das distancias
    dev.sort()
    worst = dev[int(0.98 * (len(dev) - 1))]    # p98: ignora 2% de ruido de GPS/zebra
    return worst if worst > limit else None


class LapRecorder:
    """Separa as voltas de um carro (seu ou da IA) e grava os pontos de cada uma.

    Cuida dos casos em que o carro cruza a linha sem o jogo contar volta: largada com o
    grid atras da linha e saida do box. Nesses casos o tempo da volta passa a contar a
    partir da linha (offset), e a volta anterior (incompleta) e ignorada."""

    def __init__(self):
        self.raw = []
        self.last_completed = None
        self.valid = False
        self.armed = False        # so grava depois de cruzar a linha
        self.offset = 0.0         # tempo do cronometro do jogo quando a volta comecou na linha
        self.prev_pos = None
        self.pending = None       # volta que acabou na linha um instante antes do jogo contar

    def _start(self, t_abs, valid):
        self.raw = []
        self.offset = t_abs
        self.valid = valid
        self.armed = True

    def _finish(self, ev, raw, lap_time, valid):
        ok = (valid and lap_time > 10 and len(raw) > 50 and raw[0][0] < 0.03 and raw[-1][0] > 0.97)
        if ok:
            ev["finished"] = (raw, lap_time)
        else:
            ev["discarded"] = lap_time

    def feed(self, s):
        ev = {"finished": None, "discarded": None, "new_lap": False, "sample": None}
        pos, t_abs = s.pos, s.lap_ms / 1000.0
        if self.last_completed is None:
            self.last_completed = s.completed
            if pos < 0.05 and s.lap_ms < 3000:          # comecou parado na linha
                self._start(t_abs, not s.in_pit)
                ev["new_lap"] = True
        elif s.completed != self.last_completed:          # o jogo contou uma volta
            crossed = s.completed == self.last_completed + 1
            self.last_completed = s.completed
            if self.pending is not None:
                raw, off, valid = self.pending            # a posicao virou um instante antes
            else:
                raw, off, valid = self.raw, self.offset, self.valid and self.armed
            self.pending = None
            if crossed and raw:
                self._finish(ev, raw, s.last_ms / 1000.0 - off, valid)
            self._start(t_abs, crossed and not s.in_pit)
            ev["new_lap"] = True
        elif self.prev_pos is not None and self.prev_pos > 0.9 and pos < 0.1:
            # cruzou a linha sem o jogo contar volta (largada, saida do box)
            self.pending = (self.raw, self.offset, self.valid and self.armed) if self.armed else None
            self._start(t_abs, not s.in_pit)
            ev["new_lap"] = True
        if self.pending is not None and pos > 0.05:
            self.pending = None                           # o jogo nao contou: era volta de saida
        self.prev_pos = pos
        if not self.armed:
            return ev
        if s.in_pit or getattr(s, "invalid", False):
            self.valid = False
        if self.raw:
            lp = self.raw[-1][0]
            if pos <= lp:
                return ev                                 # parado, de re
            if pos - lp > 0.05:
                self.valid = False                        # pulo (voltou ao box etc.)
        elif pos > 0.5:
            return ev                                     # ainda do lado de la da linha
        t = max(0.0, t_abs - self.offset)
        gear = getattr(s, "gear", None)
        if s.wx is None or s.wz is None:
            smp = (pos, t, s.speed, s.brake or 0.0, s.gas or 0.0)
        elif gear is None:
            smp = (pos, t, s.speed, s.brake or 0.0, s.gas or 0.0, s.wx, s.wz)
        else:
            smp = (pos, t, s.speed, s.brake or 0.0, s.gas or 0.0, s.wx, s.wz, float(gear))
        self.raw.append(smp)
        ev["sample"] = smp
        return ev


class Coach:
    def __init__(self, store_dir, targets_file=None):
        self.store_dir = store_dir
        self.targets_file = targets_file
        self.target = None            # (segundos do profissional, fonte, {nivel: segundos})
        os.makedirs(store_dir, exist_ok=True)
        self.key = None
        self.car = self.track = ""
        self.L = 0.0
        self.N = 2000
        self.laps = []
        self.mode = "ideal"          # "ideal", "melhor" ou 0..4 (nivel pronto)
        self.levels = None
        self.levels_stale = False    # ha volta nova melhor; refaz os niveis no menu
        self.level_msg = ""
        self.ref = self.best = self.ideal = None
        self.zones = []
        self.ref_vmax = 1.0
        self.state = "aguardando"
        self.snap = None
        self.last_completed = None
        self.lap_start_dist = 0.0
        self.cur_idx = None
        self.results = {}
        self.last_result = None
        self.lap_msg = ""
        self.rec = LapRecorder()
        self.lock_now = None      # (intensidade 0-1, "diant. esq. + diant. dir.")
        self.lock_hold = 0
        self._new_lap(False)

    # ---- propriedades -----------------------------------------------------
    @property
    def step_m(self):
        return (self.L / self.N) if self.L > 0 else None

    @property
    def path(self):
        return os.path.join(self.store_dir, re.sub(r"[^\w.-]+", "_", self.key) + ".json")

    # ---- dados salvos -----------------------------------------------------
    def _setup(self, key, s):
        self.key, self.car, self.track = key, s.car, s.track
        self.L = float(s.track_len or 0)
        self.N = 2000 if self.L <= 0 else int(min(5000, max(1000, self.L / 2)))
        self.laps, self.results, self.last_result = [], {}, None
        self.ai_lap = None
        self.target = find_target(load_targets(self.targets_file), s.car, s.track) if self.targets_file else None
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("N") == self.N:
                self.laps = data["laps"]
                self.ai_lap = data.get("ia")
                self.L = self.L or data.get("L", 0)
        except (OSError, ValueError, KeyError):
            pass
        self._load_mode()
        self.last_completed = None
        self.rec = LapRecorder()
        self._new_lap()
        self._rebuild_ref()

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"car": self.car, "track": self.track, "L": self.L,
                           "N": self.N, "laps": self.laps, "ia": self.ai_lap}, f)
        except OSError:
            pass

    # ---- referencia escolhida (arquivo proprio, pequeno) ------------------
    @property
    def mode_path(self):
        return os.path.join(self.store_dir, "modo.json")

    def _load_mode(self):
        """Volta a abrir na mesma referencia que estava escolhida neste carro/pista."""
        try:
            with open(self.mode_path, encoding="utf-8") as f:
                m = json.load(f).get(self.key)
        except (OSError, ValueError, AttributeError):
            return
        if m in ("ideal", "melhor", "ia") or (isinstance(m, int) and 0 <= m < len(LEVELS)):
            self.mode = m

    def _save_mode(self):
        """Arquivo separado e minusculo: gravar no clique do menu nao segura o jogo."""
        if not self.key:
            return
        try:
            try:
                with open(self.mode_path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = {}
            data[self.key] = self.mode
            with open(self.mode_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

    def clear_data(self):
        self.laps, self.results, self.last_result, self.ai_lap = [], {}, None, None
        try:
            os.remove(self.path)
        except (OSError, TypeError):
            pass
        self._rebuild_ref()

    def add_ai_lap(self, raw, lap_time):
        """Guarda a volta da IA se for a mais rapida ja vista neste carro/pista."""
        if self.key is None or (self.ai_lap and self.ai_lap["time"] <= lap_time):
            return False
        lap = synth_pedals(resample(raw, self.N, lap_time), self.step_m or 1.0)
        lap["date"] = time.strftime("%Y-%m-%d %H:%M")
        self.ai_lap = lap
        self._save()
        if self.mode == "ia":
            self._rebuild_ref(keep_levels=True)
        self.lap_msg = "IA: nova volta de referencia {}".format(fmt_time(lap_time))
        return True

    def toggle_mode(self):
        self.set_mode("melhor" if self.mode == "ideal" else "ideal")

    def set_mode(self, mode):
        self.mode = mode
        self._rebuild_ref(keep_levels=True)
        self._save_mode()         # a escolha volta igual quando o jogo reabrir

    def mode_name(self, mode=None):
        mode = self.mode if mode is None else mode
        if mode == "ideal":
            return "Minha ideal"
        if mode == "melhor":
            return "Minha melhor"
        if mode == "ia":
            return "IA (gravada)"
        return LEVELS[mode][0]

    def ensure_levels(self):
        if self.levels is None and self.laps:
            self.levels = build_levels(self.laps, self.N, self.L,
                                       self.target[0] if self.target else None,
                                       self.target[2] if self.target else None) or []
        return self.levels or []

    def mode_time(self, mode):
        if mode == "ideal":
            return self.ideal["time"] if self.ideal else None
        if mode == "melhor":
            return self.best["time"] if self.best else None
        if mode == "ia":
            return self.ai_lap["time"] if self.ai_lap else None
        lv = self.ensure_levels()
        return lv[mode]["time"] if lv else None

    def _rebuild_ref(self, keep_levels=False):
        if not keep_levels:
            self.levels = None
        self.level_msg = ""
        if not self.laps:
            self.best = self.ideal = None
            self.ref, self.zones = None, []
            if isinstance(self.mode, int):
                self.level_msg = "nivel disponivel apos 1 volta valida"
            if self.mode == "ia":
                if self.ai_lap:
                    self._use_ref(self.ai_lap)
                else:
                    self.level_msg = "IA ainda sem volta gravada (corra com IA do mesmo carro)"
            return
        self.best = self.laps[0]
        if len(self.laps) >= 2 and self.L > 0:
            nseg = max(8, min(self.N // 10, int(self.L / 150)))
            self.ideal = build_ideal(self.laps, self.N, nseg)
        else:
            self.ideal = self.best
        self.ref = self.ideal if self.mode == "ideal" else self.best
        if self.mode == "ia":
            if self.ai_lap:
                self.ref = self.ai_lap
            else:
                self.ref = self.ideal
                self.level_msg = "IA ainda sem volta gravada - usando sua ideal"
        if isinstance(self.mode, int):
            lv = self.ensure_levels()
            if lv:
                self.ref = lv[self.mode]
                if not lv[self.mode].get("calibrated", True):
                    self.level_msg = "tempo-alvo alto demais p/ o que o app sabe do carro"
            else:
                self.level_msg = "nivel indisponivel: grave 1 volta nova - usando sua ideal"
        self._use_ref(self.ref)

    def _gear_track(self):
        """Marcha ponto a ponto, tirada da volta mais rapida que tenha marcha gravada.

        Os niveis prontos sao voltas simuladas e nunca tem marcha; a volta da IA e as
        voltas gravadas antes da versao com marcha tambem nao. Como a marcha num ponto
        da pista e praticamente a mesma em qualquer volta sua, a mesma lista serve de
        marcha para qualquer referencia.
        """
        n = self.N + 1
        for lap in self.laps:                  # self.laps ja esta ordenada por tempo
            g = lap.get("gear")
            if g and len(g) == n:
                return g
        g = (self.ai_lap or {}).get("gear")
        return g if g and len(g) == n else None

    def _use_ref(self, ref):
        if ref is not None and not ref.get("gear"):
            g = self._gear_track()             # nivel/IA/volta antiga: empresta a marcha
            if g:
                ref = dict(ref)
                ref["gear"] = g
        self.ref = ref
        self.ref_vmax = max(self.ref["v"]) or 1.0
        self.zones = find_zones(self.ref["v"], self.ref["b"], self.step_m or 1.0)
        idx = self.cur_idx or 0
        self.next_zone = next((k for k, z in enumerate(self.zones) if z["win_a"] >= idx),
                              len(self.zones))

    # ---- voltas -----------------------------------------------------------
    def _new_lap(self, valid=None):
        n = self.N + 1
        self.lt, self.lv, self.lb, self.lg = [None] * n, [None] * n, [None] * n, [None] * n
        self.lk, self.lkw = [0.0] * n, [""] * n
        self.live_idx = -1
        self.cur_idx = None
        self.next_zone = 0

    def _store_lap(self, raw, lap_time):
        lap = resample(raw, self.N, lap_time)
        lap["date"] = time.strftime("%Y-%m-%d %H:%M")
        off = path_offtrack(lap, self.laps)
        if off is not None:
            self.lap_msg = "Volta {} passou {:.0f} m fora do tracado - nao vira referencia".format(
                fmt_time(lap_time), off)
            return
        prev_best = self.laps[0]["time"] if self.laps else None
        self.laps.append(lap)
        self.laps.sort(key=lambda x: x["time"])
        if len(self.laps) > MAX_SAVED_LAPS:
            keep = self.laps[:MAX_SAVED_LAPS]
            if not any(l.get("gear") for l in keep):
                # segura sempre uma volta com marcha, senao o indicador fica em "-"
                g = next((l for l in self.laps if l.get("gear")), None)
                if g is not None:
                    keep[-1] = g
                    keep.sort(key=lambda x: x["time"])
            self.laps = keep
        kept = any(l is lap for l in self.laps)
        new_best = kept and (prev_best is None or lap_time < prev_best)
        if new_best:
            self.levels_stale = True
        # keep_levels=True de proposito: recalcular os 5 niveis aqui trava o jogo por
        # segundos a cada volta fechada. Eles sao refeitos quando voce abre o menu Ref.
        self._rebuild_ref(keep_levels=True)
        if kept:
            self._save()             # so grava se a volta entrou mesmo no conjunto
            tag = "NOVA MELHOR VOLTA! " if new_best else ""
            self.lap_msg = "{}Volta {} salva".format(tag, fmt_time(lap_time))
        else:
            self.lap_msg = "Volta {} (fora das {} melhores)".format(
                fmt_time(lap_time), MAX_SAVED_LAPS)

    # ---- ciclo principal --------------------------------------------------
    def process(self, s):
        self.snap = s
        if s is None or s.status != 2:
            self.state = {1: "replay", 3: "pausado"}.get(getattr(s, "status", 0), "aguardando o jogo")
            return
        self.state = "ao vivo"
        key = "{}__{}__{}".format(s.car, s.track, int(s.track_len or 0))
        if key != self.key:
            self._setup(key, s)

        ev = self.rec.feed(s)
        if ev["finished"]:
            raw, lap_time = ev["finished"]
            if self.L <= 0:
                self.L = max(0.0, s.dist_total - self.lap_start_dist)
            self._store_lap(raw, lap_time)
        elif ev["discarded"] is not None and ev["discarded"] > 10:
            self.lap_msg = "Volta {} descartada (incompleta, box ou saida de pista)".format(fmt_time(ev["discarded"]))
        if ev["new_lap"]:
            self._new_lap()
            self.lap_start_dist = s.dist_total

        lock = self._detect_lock(s)
        if lock:
            self.lock_now, self.lock_hold = lock, 20
        elif self.lock_hold > 0:
            self.lock_hold -= 1
            if self.lock_hold == 0:
                self.lock_now = None

        smp = ev["sample"]
        if smp is None:
            # antes de cruzar a linha (saida do box/grid): mostra a proxima freada, sem delta
            if not self.rec.armed and self.N:
                self.cur_idx = min(self.N, max(0, int(round(s.pos * self.N))))
                self.next_zone = len(self.zones)
            return
        pos, t = smp[0], smp[1]
        idx = min(self.N, max(0, int(round(pos * self.N))))
        prev = self.live_idx
        self._live_fill(idx, t, s.speed, s.brake, s.gas)
        if lock:
            for i in range(max(0, prev + 1 if prev >= 0 else idx), idx + 1):
                if lock[0] > self.lk[i]:
                    self.lk[i], self.lkw[i] = lock[0], lock[1]
        self.cur_idx = idx

        while self.next_zone < len(self.zones) and idx >= self.zones[self.next_zone]["win_b"]:
            z = self.zones[self.next_zone]
            self.next_zone += 1
            r = self._eval_zone(z)
            if r:
                self.results[z["n"]] = r
                self.last_result = r

    @staticmethod
    def _detect_lock(s):
        slip = getattr(s, "slip", None)
        if not slip or s.brake < 0.05 or s.speed < 25:
            return None
        bad = [i for i in range(4) if slip[i] < -LOCK_RATIO]
        if not bad:
            return None
        sev = max(0.0, min(1.0, (-min(slip) - LOCK_RATIO) / 0.4 + 0.2))
        if bad == [0, 1]:
            names = "dianteiras"
        elif bad == [2, 3]:
            names = "traseiras"
        elif len(bad) == 4:
            names = "todas"
        else:
            names = " + ".join(WHEELS[i] for i in bad)
        return sev, names

    def _live_fill(self, idx, t, v, b, g):
        a = self.live_idx
        if a < 0 or idx <= a:
            self.lt[idx], self.lv[idx], self.lb[idx], self.lg[idx] = t, v, b, g
            self.live_idx = max(a, idx)
            return
        t0, v0, b0, g0 = self.lt[a], self.lv[a], self.lb[a], self.lg[a]
        span = idx - a
        for i in range(a + 1, idx + 1):
            f = (i - a) / span
            self.lt[i] = t0 + (t - t0) * f
            self.lv[i] = v0 + (v - v0) * f
            self.lb[i] = b if f == 1 else max(b0, b) if span <= 2 else b0 + (b - b0) * f
            self.lg[i] = g0 + (g - g0) * f
        self.live_idx = idx

    def _eval_zone(self, z):
        a, e = z["win_a"], z["win_b"]
        if self.lt[a] is None or self.lt[e] is None or self.ref is None:
            return None
        lb, lv = self.lb[a:e + 1], self.lv[a:e + 1]
        if any(x is None for x in lb):
            return None
        bp = None
        for k, x in enumerate(lb):
            if x >= ON:
                while k > 0 and lb[k - 1] > OFF:
                    k -= 1
                bp = a + k
                break
        step = self.step_m or 1.0
        rt, rb = self.ref["t"], self.ref["b"]
        rel = None
        if bp is not None:
            k = bp - a
            while k < len(lb) - 1 and lb[k + 1] > OFF:
                k += 1
            rel = (a + k - z["end"]) * step           # + = soltou o freio depois da referencia
        mid = (z["start"] + z["apex"]) // 2
        seg = range(mid, max(mid + 1, z["apex"]))
        trail = sum(self.lb[i] or 0 for i in seg) / len(seg)
        ref_trail = sum(rb[i] for i in seg) / len(seg)
        lk = self.lk[a:e + 1]
        kmax = max(lk) if lk else 0.0
        ki = a + lk.index(kmax) if kmax > 0 else None
        r = {
            "n": z["n"],
            "bp_m": None if bp is None else (bp - z["start"]) * step,   # + = freou mais tarde
            "peak": max(lb), "ref_peak": z["peak"],
            "vmin": min(lv), "ref_vmin": z["vmin"],
            "dt": (self.lt[e] - rt[e]) - (self.lt[a] - rt[a]),           # + = perdeu tempo
            "rel_m": rel, "trail": trail, "ref_trail": ref_trail,
            "lock": self.lkw[ki] if ki is not None else "",
            "lock_kmh": self.lv[ki] if ki is not None else 0.0,
        }
        r["tip"] = coach_tip(r)
        return r

    # ---- consultas para a interface --------------------------------------
    def live_delta(self):
        i = self.cur_idx
        if self.ref is None or i is None or self.lt[i] is None:
            return None
        return self.lt[i] - self.ref["t"][i]

    def speed_gap(self):
        """km/h acima (+) ou abaixo (-) da referencia, no ponto da pista onde voce esta."""
        i = self.cur_idx
        if self.ref is None or i is None or self.snap is None:
            return None
        return self.snap.speed - self.ref["v"][i]

    def ref_gear(self):
        """Marcha que a referencia usa aqui (None se a referencia nao tiver marcha gravada)."""
        i = self.cur_idx
        if self.ref is None or i is None:
            return None
        g = self.ref.get("gear")
        if not g:
            return None
        return int(round(g[i]))

    def next_brake(self):
        """(zona, metros ate o ponto de frenagem) da proxima curva."""
        if not self.zones or self.cur_idx is None or not self.step_m:
            return None, None
        i = self.cur_idx
        for z in self.zones:
            if z["end"] >= i:
                return z, (z["start"] - i) * self.step_m
        z = self.zones[0]
        return z, (self.N - i + z["start"]) * self.step_m
