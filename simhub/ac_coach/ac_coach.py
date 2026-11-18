# -*- coding: utf-8 -*-
"""
AC Coach - app nativo do Assetto Corsa
=======================================
Instalacao: copie a pasta "ac_coach" para  <Assetto Corsa>/apps/python/
e ative o app em Content Manager > Configuracoes > Assetto Corsa > Python apps.

O que aparece no jogo agora e so o seletor de referencia (Minha ideal, Minha melhor,
IA, niveis). Todo o resto - painel de frenagem, velocidade, delta, dicas - vai para o
dashboard "AC Coach Lovely" do SimHub, pelo arquivo live.txt desta pasta.

Atalhos (de qualquer lugar, com o jogo aberto):
    Alt+1 Minha ideal    Alt+2 Minha melhor   Alt+3 IA (gravada)
    Alt+4 Iniciante      Alt+5 Intermediario  Alt+6 Avancado
    Alt+7 Expert         Alt+8 Profissional   Alt+0 proxima referencia
"""
import os
import platform
import sys
import time
import traceback

import ac
import acsys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import coach_core as core  # noqa: E402

# ctypes (para ler o teclado) nao vem no Python do AC: usa o _ctypes.pyd que o app
# SimHub ja instala em apps/python/SimHub, ou uma copia em ac_coach/stdlib(64)
_LIB = "stdlib64" if platform.architecture()[0] == "64bit" else "stdlib"
for _d in (os.path.join(HERE, _LIB), os.path.join(HERE, "..", "SimHub", _LIB)):
    if os.path.isdir(_d):
        sys.path.insert(0, os.path.abspath(_d))
os.environ["PATH"] = os.environ.get("PATH", "") + ";."
try:
    import ctypes
    _U32 = ctypes.windll.user32
except Exception:
    _U32 = None

APP = "AC Coach"
W = 230
ROW = 24
MENU_MODES = ["ideal", "melhor", "ia", 0, 1, 2, 3, 4]
VK_ALT = 0x12

# cores (r, g, b) - so usadas no arquivo para o dash
PANEL = (0.095, 0.11, 0.135)
PANEL_LOW = (0.22, 0.06, 0.08)
FG = (0.91, 0.92, 0.93)
DIM = (0.54, 0.56, 0.60)
GREEN = (0.24, 0.86, 0.52)
RED = (1.0, 0.32, 0.32)
AMBER = (1.0, 0.70, 0.0)
BLUE = (0.31, 0.76, 0.97)
PURPLE = (0.69, 0.29, 0.82)
PURPLE_D = (0.37, 0.08, 0.44)
BLACK = (0.0, 0.0, 0.0)
WHITE = (1.0, 1.0, 1.0)
DGREEN = (0.12, 0.23, 0.16)
DAMBER = (0.42, 0.29, 0.0)
DBLUE = (0.11, 0.25, 0.36)

S = {}   # estado global do app (AC chama funcoes soltas)


# ---------------------------------------------------------------------------
# leitura do jogo
# ---------------------------------------------------------------------------
class GameSource:
    def __init__(self):
        self.dist = 0.0
        self.static = None
        self.radius = None

    def _wheels(self, key):
        try:
            v = ac.getCarState(0, key)
            if isinstance(v, (tuple, list)) and len(v) >= 4:
                return [float(x) for x in v[:4]]
        except Exception:
            pass
        try:
            return [float(ac.getCarState(0, key, i)) for i in range(4)]
        except Exception:
            return None

    def _static_info(self):
        if self.static is None:
            car = ac.getCarName(0)
            track = ac.getTrackName(0)
            try:
                cfg = ac.getTrackConfiguration(0)
                if cfg and cfg != -1:
                    track = "{}-{}".format(track, cfg)
            except Exception:
                pass
            try:
                length = float(ac.getTrackLength(0))
            except Exception:
                length = 0.0
            self.static = (str(car), str(track), max(0.0, length))
        return self.static

    def _slip(self, speed_kmh):
        v = speed_kmh / 3.6
        if v < 3:
            return None
        if self.radius is None:
            r = self._wheels(acsys.CS.TyreRadius)
            self.radius = r if (r and all(x > 0.05 for x in r)) else False
        if self.radius:
            w = self._wheels(acsys.CS.WheelAngularSpeed)
            if w:
                return tuple((abs(w[i]) * self.radius[i] - v) / v for i in range(4))
        sr = self._wheels(acsys.CS.SlipRatio)
        return tuple(sr) if sr else None

    def cars(self):
        """Ids dos carros da IA com o mesmo modelo do seu carro."""
        self._ai_tick = getattr(self, "_ai_tick", 0) + 1
        if getattr(self, "_ai_ids", None) is None or self._ai_tick % 600 == 0:   # reve a lista a cada ~10 s
            mine = str(ac.getCarName(0))
            try:
                count = int(ac.getCarsCount())
            except Exception:
                count = 32
            ids = []
            for i in range(1, count):
                try:
                    name = ac.getCarName(i)
                except Exception:
                    break
                if name == -1 or name is None:
                    break
                if str(name) == mine:
                    ids.append(i)
            self._ai_ids = ids
            old = getattr(self, "_ai_rec", {})
            self._ai_rec = dict((i, old.get(i) or core.LapRecorder()) for i in ids)
        return self._ai_ids

    def read_ai(self, i):
        cs = acsys.CS

        def st(key, default=0.0):
            try:
                return ac.getCarState(i, key)
            except Exception:
                return default
        try:
            wpos = ac.getCarState(i, cs.WorldPosition)
            wpos = [float(x) for x in wpos] if len(wpos) >= 3 else None
        except Exception:
            wpos = None
        try:
            in_pit = bool(ac.isCarInPitline(i)) or bool(ac.isCarInPit(i))
        except Exception:
            in_pit = False
        return core.Snapshot(
            status=2, completed=int(st(cs.LapCount, 0)), lap_ms=int(st(cs.LapTime, 0)),
            last_ms=int(st(cs.LastLap, 0)), pos=float(st(cs.NormalizedSplinePosition)),
            speed=float(st(cs.SpeedKMH)), brake=float(st(cs.Brake)), gas=float(st(cs.Gas)),
            in_pit=in_pit, invalid=False, car="", track="", track_len=0.0, dist_total=0.0, slip=None,
            wx=wpos[0] if wpos else None, wz=wpos[2] if wpos else None)

    def feed_ai(self, coach):
        """Grava as voltas da IA; entrega ao coach as voltas completas."""
        for i in self.cars():
            ev = self._ai_rec[i].feed(self.read_ai(i))
            if ev["finished"]:
                coach.add_ai_lap(*ev["finished"])

    def read(self, dt):
        car, track, length = self._static_info()
        cs = acsys.CS
        speed = float(ac.getCarState(0, cs.SpeedKMH))
        self.dist += speed / 3.6 * dt
        try:
            invalid = bool(ac.getCarState(0, cs.LapInvalidated))
        except Exception:
            invalid = False
        try:
            in_pit = bool(ac.isCarInPitline(0)) or bool(ac.isCarInPit(0))
        except Exception:
            in_pit = False
        try:
            wpos = ac.getCarState(0, cs.WorldPosition)
            wpos = [float(x) for x in wpos] if len(wpos) >= 3 else None
        except Exception:
            wpos = None
        try:
            gear = int(ac.getCarState(0, cs.Gear)) - 1   # AC: 0=re, 1=neutro, 2=1a marcha
        except Exception:
            gear = None
        return core.Snapshot(
            status=2,
            completed=int(ac.getCarState(0, cs.LapCount)),
            lap_ms=int(ac.getCarState(0, cs.LapTime)),
            last_ms=int(ac.getCarState(0, cs.LastLap)),
            pos=float(ac.getCarState(0, cs.NormalizedSplinePosition)),
            speed=speed,
            brake=float(ac.getCarState(0, cs.Brake)),
            gas=float(ac.getCarState(0, cs.Gas)),
            in_pit=in_pit, invalid=invalid,
            car=car, track=track, track_len=length,
            dist_total=self.dist,
            slip=self._slip(speed),
            wx=wpos[0] if wpos else None, wz=wpos[2] if wpos else None,
            gear=gear)


# ---------------------------------------------------------------------------
# ciclo do AC
# ---------------------------------------------------------------------------
def acMain(ac_version):
    app = ac.newApp(APP)
    S["app"] = app
    S["frame"] = 0
    S["err"] = ""
    S["low"] = False
    S["menu_open"] = False
    S["btn_txt"] = None

    ac.setTitle(app, "")
    ac.setIconPosition(app, 0, -10000)
    ac.drawBorder(app, 0)
    ac.setSize(app, W, ROW + 4)
    ac.setBackgroundOpacity(app, 0.6)

    btn = ac.addButton(app, "Ref: --  v")
    ac.setPosition(btn, 2, 2)
    ac.setSize(btn, W - 4, ROW - 2)
    ac.setFontSize(btn, 13)
    ac.addOnClickedListener(btn, on_menu)
    S["btn_ref"] = btn
    S["menu"], S["menu_cb"] = [], []
    for k, mode in enumerate(MENU_MODES):
        b = ac.addButton(app, "")
        ac.setPosition(b, 2, ROW + 2 + k * ROW)
        ac.setSize(b, W - 4, ROW - 2)
        ac.setFontSize(b, 12)
        cb = _menu_cb(mode)
        S["menu_cb"].append(cb)
        ac.addOnClickedListener(b, cb)
        ac.setVisible(b, 0)
        S["menu"].append(b)

    S["coach"] = core.Coach(os.path.join(HERE, "data"), os.path.join(HERE, "referencias.json"))
    S["src"] = GameSource()
    S["live_path"] = os.path.join(HERE, "live.txt")   # lido pelo dash do SimHub
    S["keys"] = {}
    _poll_keys()            # zera o "apertou desde a ultima leitura" das teclas
    return APP


def _fail(where):
    """Registra o erro no py_log (uma vez por tipo), sem travar o resto."""
    msg = traceback.format_exc()
    key = where + msg.strip().splitlines()[-1]
    seen = S.setdefault("seen_err", set())
    if key not in seen:
        seen.add(key)
        ac.log("[AC Coach] erro em {}:\n{}".format(where, msg))
        ac.console("[AC Coach] erro em {}: {}".format(where, msg.strip().splitlines()[-1]))
    S["err"] = "{}: {}".format(where, msg.strip().splitlines()[-1])[:60]


def acUpdate(deltaT):
    S["frame"] += 1
    S["err"] = ""
    try:
        _poll_keys()
    except Exception:
        _fail("teclado")
    try:
        S["coach"].process(S["src"].read(deltaT))
    except Exception:
        _fail("leitura")
    try:
        S["src"].feed_ai(S["coach"])
    except Exception:
        _fail("ia")
    try:
        gap = S["coach"].speed_gap()
        S["low"] = gap is not None and gap < -core.SPEED_RED
    except Exception:
        _fail("velocidade")
    if S["frame"] % 3 == 0:
        try:
            _update_button()
        except Exception:
            _fail("botao")
        write_live()


def acShutdown():
    try:
        with open(S["live_path"], "w") as f:
            f.write("ts=0\n#end\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# referencia: menu e atalhos
# ---------------------------------------------------------------------------
def _choose(mode):
    c = S["coach"]
    if isinstance(mode, int) and c.levels_stale:
        # niveis sao refeitos so quando alguem pede um nivel (a conta e pesada)
        c.levels, c.levels_stale = None, False
    c.set_mode(mode)
    S["btn_txt"] = None
    if S["menu_open"]:
        _fill_menu()


def _key_hit(vk):
    """Tecla apertada agora ou desde a ultima leitura (pega o toque rapido do SimHub)."""
    return bool(_U32.GetAsyncKeyState(vk) & 0x8001)


def _poll_keys():
    if _U32 is None:
        return
    alt = _key_hit(VK_ALT)
    for n in range(9):
        hit = _key_hit(0x30 + n) or _key_hit(0x60 + n)   # numero de cima ou do teclado numerico
        was = S["keys"].get(n, False)
        S["keys"][n] = hit
        if hit and not was and alt:
            if n == 0:
                c = S["coach"]
                i = MENU_MODES.index(c.mode) if c.mode in MENU_MODES else -1
                _choose(MENU_MODES[(i + 1) % len(MENU_MODES)])
            elif n <= len(MENU_MODES):
                _choose(MENU_MODES[n - 1])


def _fill_menu():
    c = S["coach"]
    for k, (b, mode) in enumerate(zip(S["menu"], MENU_MODES)):
        t = c.mode_time(mode)
        mark = "> " if mode == c.mode else ""
        ac.setText(b, "{}{}. {}  {}".format(mark, k + 1, c.mode_name(mode),
                                           core.fmt_time(t) if t else "(sem dados)"))


def on_menu(*args):
    S["menu_open"] = not S["menu_open"]
    c = S["coach"]
    if S["menu_open"]:
        if c.levels_stale:
            c.levels, c.levels_stale = None, False
        _fill_menu()
    for b in S["menu"]:
        ac.setVisible(b, 1 if S["menu_open"] else 0)
    ac.setSize(S["app"], W, ROW + 4 + (len(MENU_MODES) * ROW if S["menu_open"] else 0))


def _menu_cb(mode):
    def cb(*args):
        _choose(mode)
        on_menu()          # fecha o menu
    return cb


def _update_button():
    c = S["coach"]
    if S["err"]:
        txt = "erro: " + S["err"][:28]
    else:
        t = c.ref["time"] if c.ref else None
        txt = "Ref: {}  {}  v".format(c.mode_name(), core.fmt_time(t)[:-2] if t else "")
    if txt != S["btn_txt"]:
        ac.setText(S["btn_ref"], txt)
        S["btn_txt"] = txt


# ---------------------------------------------------------------------------
# o que vai para o dash
# ---------------------------------------------------------------------------
def _panel(c):
    """(fundo, texto, titulo, numero grande, alvo, barra 0-1, cor da barra, marca)"""
    if c.lock_now:
        sev, wheels = c.lock_now
        blink = PURPLE if (S["frame"] // 12) % 2 == 0 else PURPLE_D
        return blink, WHITE, "RODA TRAVANDO", "ALIVIE!", wheels, sev, PURPLE, None
    z, dist = c.next_brake()
    low = S.get("low")
    base = PANEL_LOW if low else PANEL
    if z is None:
        return base, DIM, "PROXIMA FRENAGEM", "--", "complete uma volta para marcar os pontos", 0, BLUE, None
    braking = c.snap is not None and c.snap.brake >= core.ON
    target = "alvo {:.0f}% de freio  -  min {:.0f} km/h".format(z["peak"] * 100, z["vmin"])
    head = "CURVA {}".format(z["n"])
    frac = max(0.0, min(1.0, 1 - dist / 300.0))
    if dist > 30:
        return base, FG, head + "  -  FREAR EM", "{:.0f} m".format(dist), target, frac, BLUE, None
    if dist > 0:
        return AMBER, BLACK, head + "  -  PREPARE", "{:.0f} m".format(dist), target, frac, AMBER, None
    if not braking and dist > -40:
        return RED, WHITE, head, "FREIE!", target, 1.0, RED, None
    you = c.snap.brake if c.snap else 0.0
    i = c.cur_idx
    refb = c.ref["b"][i] if (c.ref is not None and i is not None) else you
    if you > refb + 0.12:
        cue, bg = "ALIVIE", DAMBER
    elif you < refb - 0.12 and refb > core.OFF:
        cue, bg = "MAIS FREIO", DBLUE
    elif refb <= core.OFF and you > 0.1:
        cue, bg = "SOLTE O FREIO", DAMBER
    else:
        cue, bg = "no ponto", DGREEN
    return (bg, FG, "{}  -  {}".format(head, cue),
            "{:.0f}% / {:.0f}%".format(you * 100, refb * 100),
            "seu freio / ref  -  min {:.0f} km/h no apice".format(z["vmin"]), you, RED, refb)


def _hex(c):
    return "#FF{:02X}{:02X}{:02X}".format(*[int(max(0.0, min(1.0, x)) * 255) for x in c])


def _clean(t):
    return str(t).replace("\n", " ").replace("\r", " ")


def write_live():
    """Grava o estado do coach num txt pequeno que o dash do SimHub le a cada quadro."""
    try:
        c = S["coach"]
        bg, fg, head, big, tgt, frac, barc, mark = _panel(c)
        s = c.snap
        v = s.speed if s else 0.0
        i = c.cur_idx
        rv = c.ref["v"][i] if (c.ref is not None and i is not None) else None
        d = c.live_delta()
        rg = c.ref_gear()
        mine = getattr(s, "gear", None) if s else None
        wrong = rg is not None and mine is not None and mine > 0 and mine != rg
        r = c.last_result
        t = c.ref["time"] if c.ref else None
        lines = [
            "ts={:.2f}".format(time.time()),
            "bg=" + _hex(bg), "fg=" + _hex(fg),
            "head=" + _clean(head), "big=" + _clean(big), "tgt=" + _clean(tgt),
            "frac={:.3f}".format(max(0.0, min(1.0, frac or 0.0))), "barc=" + _hex(barc),
            "mark={:.3f}".format(-1 if mark is None else max(0.0, min(1.0, mark))),
            "spd={:.0f}".format(v),
            "ref=" + ("" if rv is None else "{:.0f}".format(rv)),
            "gap=" + ("" if rv is None else "{:+.0f}".format(v - rv)),
            "low=" + ("1" if S.get("low") else "0"),
            "delta=" + ("--" if d is None else "{:+.3f}".format(d)),
            "dcol=" + _hex(DIM if d is None else (RED if d > 0 else GREEN)),
            "mode=" + _clean(c.mode_name()),
            "modei=" + str(MENU_MODES.index(c.mode) if c.mode in MENU_MODES else -1),
            "modet=" + (core.fmt_time(t) if t else ""),
            "msg=" + _clean(c.level_msg or ""),
            "rgear=" + ("" if rg is None else ("N" if rg <= 0 else str(rg))),
            "wrong=" + ("1" if wrong else "0"),
            "tip=" + (_clean("Curva {}: {}".format(r["n"], r["tip"])) if r else ""),
            "tipc=" + _hex(GREEN if (r and r["dt"] <= 0) else RED),
            "#end", ""]
        with open(S["live_path"], "w") as f:
            f.write("\n".join(lines))
    except Exception:
        pass   # SimHub lendo o arquivo nesse instante: grava no proximo ciclo
