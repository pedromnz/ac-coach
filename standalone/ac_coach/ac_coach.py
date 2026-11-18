# -*- coding: utf-8 -*-
"""
AC Coach (standalone) - app nativo do Assetto Corsa, SEM SimHub
===============================================================
Instalacao: copie a pasta "ac_coach" para  <Assetto Corsa>/apps/python/
e ative o app em Content Manager > Configuracoes > Assetto Corsa > Python apps.

Tudo aparece dentro do jogo, em duas janelas:
  "AC Coach"        - referencia, marcha, delta, velocidade e painel de frenagem
  "AC Coach - Dica" - resultado da ultima curva e a dica (arraste para onde quiser)

Atalhos (opcionais - precisam do _ctypes.pyd, ver LEIA-ME):
    Alt+1 Minha ideal    Alt+2 Minha melhor   Alt+3 IA (gravada)
    Alt+4 Iniciante      Alt+5 Intermediario  Alt+6 Avancado
    Alt+7 Expert         Alt+8 Profissional   Alt+0 proxima referencia
"""
import os
import platform
import sys
import traceback

import ac
import acsys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import coach_core as core  # noqa: E402

# ctypes (para ler o teclado) nao vem no Python do AC. Procura o _ctypes.pyd em
# ac_coach/stdlib(64) ou no app SimHub, se existir. Sem ele, so o menu funciona.
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
APP_TIP = "AC Coach - Dica"
W = 320
ROW = 24
MENU_MODES = ["ideal", "melhor", "ia", 0, 1, 2, 3, 4]
VK_ALT = 0x12

# layout da janela principal (y de cada bloco)
Y_REF = 2
Y_TOP = 30          # marcha / delta / velocidade
Y_PANEL = 104       # painel de frenagem
H_PANEL = 112
Y_STATUS = Y_PANEL + H_PANEL + 4
H_MAIN = Y_STATUS + 22

# cores (r, g, b)
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
BAR_BG = (0.20, 0.22, 0.26)

S = {}   # estado global do app (AC chama funcoes soltas)


# ---------------------------------------------------------------------------
# leitura do jogo (igual a versao SimHub)
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
        if getattr(self, "_ai_ids", None) is None or self._ai_tick % 600 == 0:
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
# helpers de interface
# ---------------------------------------------------------------------------
def label(app, x, y, w, h, size, align="left", text=""):
    lb = ac.addLabel(app, text)
    ac.setPosition(lb, x, y)
    ac.setSize(lb, w, h)
    ac.setFontSize(lb, size)
    ac.setFontAlignment(lb, align)
    return lb


def rect(app, x, y, w, h, color):
    """Retangulo colorido = label vazio com fundo desenhado."""
    lb = ac.addLabel(app, "")
    ac.setPosition(lb, x, y)
    ac.setSize(lb, w, h)
    ac.drawBackground(lb, 1)
    ac.setBackgroundOpacity(lb, 1)
    ac.setBackgroundColor(lb, *color)
    return lb


def set_text(ctrl, txt, color=None):
    """So chama a API do AC quando algo muda (evita custo a cada quadro)."""
    key = ("t", ctrl)
    if S["cache"].get(key) != txt:
        ac.setText(ctrl, txt)
        S["cache"][key] = txt
    if color is not None:
        set_color(ctrl, color)


def set_color(ctrl, color):
    key = ("c", ctrl)
    if S["cache"].get(key) != color:
        ac.setFontColor(ctrl, color[0], color[1], color[2], 1)
        S["cache"][key] = color


def set_bg(ctrl, color):
    key = ("b", ctrl)
    if S["cache"].get(key) != color:
        ac.setBackgroundColor(ctrl, *color)
        S["cache"][key] = color


def set_rect(ctrl, x, y, w, h):
    key = ("r", ctrl)
    v = (int(x), int(y), int(max(0, w)), int(h))
    if S["cache"].get(key) != v:
        ac.setPosition(ctrl, v[0], v[1])
        ac.setSize(ctrl, v[2], v[3])
        S["cache"][key] = v


def set_visible(ctrl, on):
    key = ("v", ctrl)
    if S["cache"].get(key) != on:
        ac.setVisible(ctrl, 1 if on else 0)
        S["cache"][key] = on


# ---------------------------------------------------------------------------
# ciclo do AC
# ---------------------------------------------------------------------------
def acMain(ac_version):
    S["frame"] = 0
    S["err"] = ""
    S["low"] = False
    S["menu_open"] = False
    S["cache"] = {}
    S["clear_armed"] = 0

    app = ac.newApp(APP)
    S["app"] = app
    ac.setTitle(app, "")
    ac.setIconPosition(app, 0, -10000)
    ac.drawBorder(app, 0)
    ac.setSize(app, W, H_MAIN)

    # referencia (abre o menu)
    btn = ac.addButton(app, "Ref: --  v")
    ac.setPosition(btn, 2, Y_REF)
    ac.setSize(btn, W - 4, ROW - 2)
    ac.setFontSize(btn, 13)
    ac.addOnClickedListener(btn, on_menu)
    S["btn_ref"] = btn

    # linha de cima: marcha que deveria estar | delta | velocidade
    S["gear_ring"] = rect(app, 8, Y_TOP + 2, 52, 52, AMBER)
    S["gear_in"] = rect(app, 11, Y_TOP + 5, 46, 46, PANEL)
    S["gear"] = label(app, 8, Y_TOP + 6, 52, 40, 30, "center", "-")
    S["gear_you"] = label(app, 4, Y_TOP + 56, 60, 16, 11, "center")
    S["delta"] = label(app, 70, Y_TOP + 4, 120, 40, 30, "center", "--")
    S["delta_cap"] = label(app, 70, Y_TOP + 46, 120, 16, 11, "center", "delta")
    S["spd"] = label(app, 196, Y_TOP + 2, 118, 34, 26, "right", "0")
    S["spd_ref"] = label(app, 196, Y_TOP + 38, 118, 16, 12, "right")
    S["spd_bar_bg"] = rect(app, 196, Y_TOP + 58, 118, 6, BAR_BG)
    S["spd_bar"] = rect(app, 255, Y_TOP + 58, 0, 6, GREEN)

    # painel de frenagem
    S["p_bg"] = rect(app, 4, Y_PANEL, W - 8, H_PANEL, PANEL)
    S["p_head"] = label(app, 4, Y_PANEL + 6, W - 8, 18, 13, "center")
    S["p_big"] = label(app, 4, Y_PANEL + 24, W - 8, 44, 36, "center")
    S["p_tgt"] = label(app, 4, Y_PANEL + 70, W - 8, 16, 11, "center")
    S["p_bar_bg"] = rect(app, 20, Y_PANEL + 92, W - 40, 10, BAR_BG)
    S["p_bar"] = rect(app, 20, Y_PANEL + 92, 0, 10, BLUE)
    S["p_mark"] = rect(app, 20, Y_PANEL + 88, 3, 18, WHITE)

    # rodape: mensagens / erro + limpar pista
    S["status"] = label(app, 6, Y_STATUS, W - 100, 18, 11)
    clr = ac.addButton(app, "Limpar pista")
    ac.setPosition(clr, W - 92, Y_STATUS - 1)
    ac.setSize(clr, 88, 18)
    ac.setFontSize(clr, 10)
    ac.addOnClickedListener(clr, on_clear)
    S["btn_clear"] = clr

    # menu de referencias (aparece abaixo de tudo)
    S["menu"], S["menu_cb"] = [], []
    for k, mode in enumerate(MENU_MODES):
        b = ac.addButton(app, "")
        ac.setPosition(b, 2, H_MAIN + k * ROW)
        ac.setSize(b, W - 4, ROW - 2)
        ac.setFontSize(b, 12)
        cb = _menu_cb(mode)
        S["menu_cb"].append(cb)
        ac.addOnClickedListener(b, cb)
        ac.setVisible(b, 0)
        S["menu"].append(b)

    # janela da dica
    tip = ac.newApp(APP_TIP)
    S["tip_app"] = tip
    ac.setTitle(tip, "")
    ac.setIconPosition(tip, 0, -10000)
    ac.drawBorder(tip, 0)
    ac.setSize(tip, W, 26)
    S["tip"] = label(tip, 8, 4, W - 16, 20, 13)

    S["coach"] = core.Coach(os.path.join(HERE, "data"), os.path.join(HERE, "referencias.json"))
    S["src"] = GameSource()
    S["keys"] = {}
    _poll_keys()
    return APP


def _fail(where):
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
    # o AC volta a opacidade da janela ao passar o mouse; reaplica sempre
    ac.setBackgroundOpacity(S["app"], 0.75)
    ac.setBackgroundOpacity(S["tip_app"], 0.75)
    if S["frame"] % 2 == 0:
        try:
            render()
        except Exception:
            _fail("tela")


def acShutdown():
    pass


# ---------------------------------------------------------------------------
# referencia: menu e atalhos
# ---------------------------------------------------------------------------
def _choose(mode):
    c = S["coach"]
    if isinstance(mode, int) and c.levels_stale:
        c.levels, c.levels_stale = None, False
    c.set_mode(mode)
    if S["menu_open"]:
        _fill_menu()


def _key_hit(vk):
    return bool(_U32.GetAsyncKeyState(vk) & 0x8001)


def _poll_keys():
    if _U32 is None:
        return
    alt = _key_hit(VK_ALT)
    for n in range(9):
        hit = _key_hit(0x30 + n) or _key_hit(0x60 + n)
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
    ac.setSize(S["app"], W, H_MAIN + (len(MENU_MODES) * ROW if S["menu_open"] else 0))


def _menu_cb(mode):
    def cb(*args):
        _choose(mode)
        on_menu()
    return cb


def on_clear(*args):
    """Dois cliques em ate 3 s para apagar as voltas deste carro/pista."""
    if S["frame"] - S["clear_armed"] < 180:
        S["coach"].clear_data()
        S["clear_armed"] = 0
        S["coach"].lap_msg = "Voltas deste carro/pista apagadas"
        ac.setText(S["btn_clear"], "Limpar pista")
    else:
        S["clear_armed"] = S["frame"]
        ac.setText(S["btn_clear"], "Confirmar?")


# ---------------------------------------------------------------------------
# desenho
# ---------------------------------------------------------------------------
def _panel(c):
    """(fundo, texto, titulo, numero grande, alvo, barra 0-1, cor da barra, marca)"""
    if c.lock_now:
        sev, wheels = c.lock_now
        blink = PURPLE if (S["frame"] // 12) % 2 == 0 else PURPLE_D
        return blink, WHITE, "RODA TRAVANDO", "ALIVIE!", wheels, sev, PURPLE, None
    z, dist = c.next_brake()
    base = PANEL_LOW if S.get("low") else PANEL
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


def _wrap(text, width):
    """Labels do AC nao quebram linha sozinhos."""
    out, line = [], ""
    for word in str(text).replace("\n", " ").split(" "):
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word) if line else word
    out.append(line)
    return "\n".join(out)


def render():
    c = S["coach"]
    s = c.snap
    low = S.get("low")
    ac.setBackgroundColor(S["app"], *(PANEL_LOW if low else BLACK))

    # botao da referencia
    if S["err"]:
        txt = "erro: " + S["err"][:34]
    else:
        t = c.ref["time"] if c.ref else None
        txt = "Ref: {}  {}  v".format(c.mode_name(), core.fmt_time(t)[:-2] if t else "")
    set_text(S["btn_ref"], txt)

    # marcha que a referencia usa aqui
    rg = c.ref_gear()
    mine = getattr(s, "gear", None) if s else None
    wrong = rg is not None and mine is not None and mine > 0 and mine != rg
    set_text(S["gear"], "-" if rg is None else ("N" if rg <= 0 else str(rg)), RED if low else FG)
    set_visible(S["gear_ring"], wrong)
    set_bg(S["gear_in"], PANEL_LOW if low else PANEL)
    set_text(S["gear_you"], "voce: {}".format(mine) if wrong else "", AMBER)

    # delta
    d = c.live_delta()
    set_text(S["delta"], "--" if d is None else "{:+.2f}".format(d),
             DIM if d is None else (RED if d > 0 else GREEN))

    # velocidade vs referencia
    v = s.speed if s else 0.0
    i = c.cur_idx
    rv = c.ref["v"][i] if (c.ref is not None and i is not None) else None
    set_text(S["spd"], "{:.0f}".format(v), RED if low else FG)
    if rv is None:
        set_text(S["spd_ref"], "km/h", DIM)
        set_rect(S["spd_bar"], 255, Y_TOP + 58, 0, 6)
    else:
        gap = v - rv
        set_text(S["spd_ref"], "ref {:.0f}  ({:+.0f})".format(rv, gap), RED if low else DIM)
        half = 59
        w = min(half, abs(gap) / 15.0 * half)       # +-15 km/h enche a meia barra
        x = 255 if gap >= 0 else 255 - w
        set_rect(S["spd_bar"], x, Y_TOP + 58, w, 6)
        set_bg(S["spd_bar"], GREEN if gap >= 0 else RED)

    # painel de frenagem
    bg, fg, head, big, tgt, frac, barc, mark = _panel(c)
    set_bg(S["p_bg"], bg)
    set_text(S["p_head"], head, fg)
    set_text(S["p_big"], big, fg)
    set_text(S["p_tgt"], tgt, fg if fg != FG else DIM)
    bw = W - 40
    set_rect(S["p_bar"], 20, Y_PANEL + 92, bw * max(0.0, min(1.0, frac or 0.0)), 10)
    set_bg(S["p_bar"], barc)
    if mark is None:
        set_visible(S["p_mark"], False)
    else:
        set_visible(S["p_mark"], True)
        set_rect(S["p_mark"], 20 + bw * max(0.0, min(1.0, mark)) - 1, Y_PANEL + 88, 3, 18)

    # rodape
    if S["clear_armed"] and S["frame"] - S["clear_armed"] >= 180:
        S["clear_armed"] = 0
        ac.setText(S["btn_clear"], "Limpar pista")
    msg = c.level_msg or c.lap_msg or ("{} @ {}".format(c.car, c.track) if c.car else "aguardando...")
    set_text(S["status"], msg[:48], DIM)

    # janela da dica: so uma tira fina quando nao ha nada a dizer
    r = c.last_result
    if r:
        tip = _wrap("Curva {}: {}".format(r["n"], r["tip"]), 44)
        set_text(S["tip"], tip, GREEN if r["dt"] <= 0 else RED)
        h = 10 + 18 * (tip.count("\n") + 1)
    else:
        set_text(S["tip"], "")
        h = 8
    if S["cache"].get("tip_h") != h:
        ac.setSize(S["tip"], W - 16, h - 6)
        ac.setSize(S["tip_app"], W, h)
        S["cache"]["tip_h"] = h
