# -*- coding: utf-8 -*-
"""
Detektor vystrelu katapultu pre RoboCup.

DVA REZIMY (prepinac TEST_MODE):
  * TEST_MODE = True  -> vystrel na HOCIJAKY pohyb vo vyreze (bez Gemini). Na otestovanie celej
                        cesty kamera -> detekcia -> micro:bit -> pocitadlo (chyti aj tvoju ruku).
  * TEST_MODE = False -> OSTRY rezim: porovna obraz PRED a PO pohybe; len TRVALA zmena (katapult
                        ostal v inej polohe) je kandidat, potom Gemini potvrdi. Ignoruje ludi.

Kamera bezi stale, vyrez (ROI) kreslis mysou NAŽIVO. micro:bit: COM port sa najde automaticky,
pocita vystrely (F=+1, R=reset).

Ovladanie:  mys = vyrez | q = koniec | r = referencia (NABITY) | x = nuluj pocitadlo
Spustenie:  dvojklik na spustit.bat
"""

import os
import sys
import time
import threading
import concurrent.futures

import cv2
import numpy as np

# ----------------------------------------------------------------------------
# KONFIGURACIA
# ----------------------------------------------------------------------------
CAM_IRIUN     = 0
CAM_POCITAC   = 1
CAM_INDEX     = CAM_POCITAC

CAM_W    = 1280
CAM_H    = 720
CAM_FPS  = 30
USE_MJPG = True
DISPLAY_MAX_W = 1280

# --- REZIM ---
TEST_MODE = True          # <<< True = vystrel na hocijaky pohyb (test rukou); False = ostry rezim

# --- detekcia ---
MOTION_RATIO        = 0.02   # citlivost na pohyb (nizsie = citlivejsie). V test rezime = prah vystrelu.
DIFF_THRESH         = 25     # citlivost na zmenu jasu pixelu
SETTLE_S            = 0.30   # (ostry rezim) ako dlho pokoj, aby bol pohyb dobehnuty
PERMANENT_THRESHOLD = 0.04   # (ostry rezim) aka cast vyrezu sa musi TRVALO zmenit = vystrel
COOLDOWN_S          = 2.0    # po vystrele ignoruj dalsi tolko sekund
CHECK_COOLDOWN_S    = 1.0    # (ostry rezim) min rozostup medzi Gemini kontrolami
GEMINI_MAX_WAIT     = 8.0    # ak Gemini kontrola trva dlhsie, zahod ju (aby sa nezasekla)

# --- Gemini potvrdenie (len ostry rezim) ---
USE_GEMINI        = True
GEMINI_MODEL      = "gemini-2.5-flash"
FALLBACK_ON_ERROR = True

# --- micro:bit ---
USE_MICROBIT   = True
SERIAL_PORT    = "COM3"    # zaloha, ak sa port nenajde automaticky
SERIAL_BAUD    = 115200
FIRE_CMD       = b"F\n"    # micro:bit: pocitadlo += 1 a radio send number
RESET_CMD      = b"R\n"    # micro:bit: pocitadlo = 0
RESET_ON_START = True

ALWAYS_SELECT_ROI = True
ROI_FILE = "roi.txt"
REF_FILE = "referencia_pokoj.jpg"


def _load_key(env_name, filename):
    k = os.environ.get(env_name, "").strip()
    if k:
        return k
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    return ""


GEMINI_KEY = _load_key("GEMINI_API_KEY", "gemini_key.txt")
# ----------------------------------------------------------------------------


# ---------- micro:bit ----------
def find_microbit_port():
    """Automaticky najde COM port micro:bitu (podla VID 0x0D28 alebo popisu)."""
    try:
        from serial.tools import list_ports
    except Exception:
        return None
    for p in list_ports.comports():
        if getattr(p, "vid", None) == 0x0D28:
            return p.device
        info = ((p.description or "") + " " + (p.hwid or "")).lower()
        if "micro:bit" in info or "microbit" in info or "mbed" in info:
            return p.device
    return None


def init_serial():
    if not USE_MICROBIT:
        return None
    port = find_microbit_port() or SERIAL_PORT
    try:
        import serial
        ser = serial.Serial(port, SERIAL_BAUD, timeout=0.1)
        auto = " (automaticky najdeny)" if port != SERIAL_PORT else ""
        print(f"[micro:bit] pripojeny na {port}{auto}")
        return ser
    except Exception as e:
        print(f"[micro:bit] CHYBA pripojenia na {port} ({e}). Bezim bez micro:bitu.")
        return None


def send_cmd(ser, cmd):
    if ser is None:
        return
    try:
        ser.write(cmd)
    except Exception as e:
        print(f"[micro:bit] chyba posielania: {e}")


def send_fire(ser):
    send_cmd(ser, FIRE_CMD)


def send_reset(ser):
    if ser is None:
        return
    send_cmd(ser, RESET_CMD)
    print("[micro:bit] reset pocitadla")


# ---------- Gemini (len ostry rezim, na pozadi) ----------
_gemini_client = None
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
_ref_jpg = None

PROMPT_REF = (
    "Prvy obrazok = katapult NABITY (referencia). Druhy obrazok = aktualny zaber po pohybe. "
    "Vystrelil prave KATAPULT (rameno sa vymrstilo / zmenilo polohu oproti referencii)? "
    "Ignoruj ludi a pozadie. Odpovedz IBA jednym slovom: ANO alebo NIE."
)
PROMPT_NOREF = (
    "Na obrazku je katapult tesne po pohybe. Vystrelil prave KATAPULT (rameno sa vymrstilo)? "
    "Ak je to len clovek/predmet, odpovedz NIE. Odpovedz IBA jednym slovom: ANO alebo NIE."
)


def init_gemini():
    global _gemini_client
    if not USE_GEMINI:
        return
    if not GEMINI_KEY:
        print("[Gemini] CHYBA: nie je nastaveny kluc.")
        return
    try:
        from google import genai
        _gemini_client = genai.Client(api_key=GEMINI_KEY)
        print(f"[Gemini] pripraveny, model {GEMINI_MODEL}")
    except Exception as e:
        print(f"[Gemini] CHYBA inicializacie ({e}).")


def confirm_job(roi_bgr):
    """Bezi NA POZADI. Vrati True ak Gemini potvrdi vystrel."""
    if _gemini_client is None:
        return FALLBACK_ON_ERROR
    try:
        from google.genai import types
        from google.genai import errors as gerr
    except Exception:
        return FALLBACK_ON_ERROR
    ok, buf = cv2.imencode(".jpg", roi_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        return FALLBACK_ON_ERROR
    cur = types.Part.from_bytes(data=buf.tobytes(), mime_type="image/jpeg")
    if _ref_jpg is not None:
        contents = [types.Part.from_bytes(data=_ref_jpg, mime_type="image/jpeg"), cur, PROMPT_REF]
    else:
        contents = [cur, PROMPT_NOREF]
    for _ in range(2):
        try:
            r = _gemini_client.models.generate_content(model=GEMINI_MODEL, contents=contents)
            ans = (r.text or "").strip().upper()
            print(f"[Gemini] {ans}")
            return "ANO" in ans
        except gerr.ServerError:
            time.sleep(0.3)
        except gerr.ClientError as e:
            if "429" in repr(e) or "RESOURCE_EXHAUSTED" in repr(e):
                print(f"[Gemini] LIMIT 429 -> fallback {FALLBACK_ON_ERROR}")
            else:
                print(f"[Gemini] chyba ({repr(e)[:80]})")
            return FALLBACK_ON_ERROR
        except Exception as e:
            print(f"[Gemini] chyba ({repr(e)[:80]})")
            return FALLBACK_ON_ERROR
    return FALLBACK_ON_ERROR


# ---------- referencia ----------
def load_reference():
    global _ref_jpg
    if os.path.exists(REF_FILE):
        with open(REF_FILE, "rb") as f:
            _ref_jpg = f.read()
        print(f"[referencia] nacitana zo suboru {REF_FILE}")


def save_reference(roi_bgr):
    global _ref_jpg
    ok, buf = cv2.imencode(".jpg", roi_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if ok:
        _ref_jpg = buf.tobytes()
        with open(REF_FILE, "wb") as f:
            f.write(_ref_jpg)
        print("[referencia] ULOZENA (NABITY katapult)")


# ---------- ROI subor ----------
def save_roi(box):
    with open(ROI_FILE, "w") as f:
        f.write(f"{box[0]},{box[1]},{box[2]},{box[3]}")


def load_saved_roi(W, H):
    if os.path.exists(ROI_FILE):
        try:
            with open(ROI_FILE) as f:
                x, y, w, h = (int(v) for v in f.read().split(","))
            if w > 15 and h > 15 and w * h <= 0.85 * W * H:
                return (x, y, w, h)
        except Exception:
            pass
    return None


def _fit_display(img, max_w=DISPLAY_MAX_W):
    h, w = img.shape[:2]
    if w <= max_w:
        return img, 1.0
    s = max_w / float(w)
    return cv2.resize(img, (int(w * s), int(h * s))), s


# ---------- kamera vo vlastnom vlakne ----------
class CameraStream:
    def __init__(self, index):
        self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if USE_MJPG:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_W)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_H)
        self.cap.set(cv2.CAP_PROP_FPS, CAM_FPS)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.lock = threading.Lock()
        self.frame = None
        self.running = self.cap.isOpened()
        if self.running:
            self.t = threading.Thread(target=self._loop, daemon=True)
            self.t.start()

    def _loop(self):
        while self.running:
            ok, f = self.cap.read()
            if ok:
                with self.lock:
                    self.frame = f
            else:
                time.sleep(0.005)

    def read(self):
        with self.lock:
            if self.frame is None:
                return False, None
            return True, self.frame.copy()

    def isOpened(self):
        return self.cap.isOpened()

    def get(self, prop):
        return self.cap.get(prop)

    def release(self):
        self.running = False
        try:
            self.t.join(timeout=1.0)
        except Exception:
            pass
        self.cap.release()


# ---------- hlavna slucka ----------
def main():
    init_gemini()
    ser = init_serial()
    if RESET_ON_START:
        send_reset(ser)

    cap = CameraStream(CAM_INDEX)
    if not cap.isOpened():
        print(f"[kamera] Nepodarilo sa otvorit kameru index {CAM_INDEX}. Skus iny CAM_INDEX.")
        sys.exit(1)
    aw, ah = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[kamera] realne rozlisenie {aw}x{ah} @ {cap.get(cv2.CAP_PROP_FPS):.0f} fps")

    print("[kamera] nabieham, moment...")
    frame = None
    t0 = time.time()
    while time.time() - t0 < 6.0:
        ok, f = cap.read()
        if ok and f is not None:
            frame = f
            if time.time() - t0 > 1.0:
                break
        time.sleep(0.03)
    if frame is None:
        print("[kamera] necitam snimky (skus iny CAM_INDEX).")
        cap.release()
        sys.exit(1)

    display_scale = (DISPLAY_MAX_W / aw) if aw > DISPLAY_MAX_W else 1.0
    test_mode = TEST_MODE
    rezim = "TEST (hocijaky pohyb)" if test_mode else "OSTRY (trvala zmena + Gemini)"
    win = "Detektor katapultu (mys=vyrez, q=koniec, r=referencia, x=nuluj)"
    cv2.namedWindow(win)

    # klikacie tlacitka (vpravo hore): rezim a pauza
    btn_x0, btn_y0 = aw - 300, 12
    btn_x1, btn_y1 = aw - 12, 50
    btn2_x0, btn2_y0 = aw - 300, 56
    btn2_x1, btn2_y1 = aw - 12, 94

    sel = {"drawing": False, "start": None, "cur": None, "roi": None,
           "toggle_request": False, "pause_request": False}
    if not ALWAYS_SELECT_ROI:
        saved = load_saved_roi(aw, ah)
        if saved:
            sel["roi"] = saved
            load_reference()

    def on_mouse(event, x, y, flags, param):
        fx = max(0, min(aw - 1, int(x / display_scale)))
        fy = max(0, min(ah - 1, int(y / display_scale)))
        if event == cv2.EVENT_LBUTTONDOWN:
            if btn_x0 <= fx <= btn_x1 and btn_y0 <= fy <= btn_y1:
                param["toggle_request"] = True       # klik -> prepni rezim
                return
            if btn2_x0 <= fx <= btn2_x1 and btn2_y0 <= fy <= btn2_y1:
                param["pause_request"] = True         # klik -> pauza
                return
            param["drawing"] = True
            param["start"] = (fx, fy)
            param["cur"] = (fx, fy)
        elif event == cv2.EVENT_MOUSEMOVE and param["drawing"]:
            param["cur"] = (fx, fy)
        elif event == cv2.EVENT_LBUTTONUP and param["drawing"]:
            param["drawing"] = False
            param["cur"] = (fx, fy)
            x0, y0 = param["start"]
            bx, by = min(x0, fx), min(y0, fy)
            bw, bh = abs(fx - x0), abs(fy - y0)
            if bw >= 15 and bh >= 15:
                param["roi"] = (bx, by, bw, bh)

    cv2.setMouseCallback(win, on_mouse, sel)

    active_roi = None
    roi_area = 1.0
    prev_gray = None
    stable_gray = None
    pre_motion_gray = None
    motion_active = False
    settle_pending = False
    settle_at = 0.0
    last_perm = 0.0
    last_fire = 0.0
    last_check = 0.0
    fire_flash_until = 0.0
    pending = None
    pending_started = 0.0
    paused = False

    print(f"[OK] Bezim, rezim: {rezim}. Nakresli mysou obdlznik okolo katapultu. q/r/x.")

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            if cv2.waitKey(10) & 0xFF == ord("q"):
                break
            continue

        roi_box = sel["roi"]
        detect = roi_box is not None and not sel["drawing"]

        if sel["toggle_request"]:
            sel["toggle_request"] = False
            test_mode = not test_mode
            rezim = "TEST (hocijaky pohyb)" if test_mode else "OSTRY (trvala zmena + Gemini)"
            prev_gray = stable_gray = pre_motion_gray = None
            motion_active = False
            settle_pending = False
            pending = None
            print(f"[REZIM] prepnute na: {rezim}")

        if sel["pause_request"]:
            sel["pause_request"] = False
            paused = not paused
            print("[PAUZA] " + ("ZAPNUTA - signaly do micro:bitu sa NEPOSIELAJU" if paused else "vypnuta"))

        if roi_box is not None and roi_box != active_roi:
            active_roi = roi_box
            roi_area = float(roi_box[2] * roi_box[3])
            prev_gray = None
            stable_gray = None
            pre_motion_gray = None
            motion_active = False
            settle_pending = False
            last_perm = 0.0
            pending = None
            save_roi(roi_box)
            print(f"[ROI] novy vyrez: {roi_box}")

        now = time.time()
        motion_ratio = 0.0

        if detect:
            rx, ry, rw, rh = active_roi
            roi = frame[ry:ry + rh, rx:rx + rw]
            if roi.size > 0:
                gray = cv2.GaussianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (11, 11), 0)
                if stable_gray is None:
                    stable_gray = gray
                if prev_gray is not None and prev_gray.shape == gray.shape:
                    d = cv2.absdiff(prev_gray, gray)
                    _, m = cv2.threshold(d, DIFF_THRESH, 255, cv2.THRESH_BINARY)
                    motion_ratio = cv2.countNonZero(m) / roi_area
                prev_gray = gray

                if test_mode:
                    # --- TEST: vystrel na hocijaky dostatocny pohyb ---
                    if motion_ratio >= MOTION_RATIO and (now - last_fire) > COOLDOWN_S:
                        last_fire = now
                        fire_flash_until = now + 1.0
                        if not paused:
                            send_fire(ser)
                        print(">>> VYSTREL (test)" + (" [POZASTAVENE]" if paused else " -> micro:bit"))
                else:
                    # --- OSTRY: trvala zmena (pred vs po) + Gemini ---
                    moving = motion_ratio > MOTION_RATIO
                    if moving:
                        if not motion_active:
                            motion_active = True
                            pre_motion_gray = stable_gray
                    else:
                        if motion_active:
                            motion_active = False
                            settle_pending = True
                            settle_at = now
                        elif not settle_pending:
                            stable_gray = gray

                    # watchdog proti zaseknutej kontrole
                    if pending is not None and not pending.done() and (now - pending_started) > GEMINI_MAX_WAIT:
                        print("[Gemini] kontrola trva privelmi dlho -> zahadzujem")
                        pending = None

                    if pending is not None and pending.done():
                        try:
                            confirmed = pending.result()
                        except Exception:
                            confirmed = FALLBACK_ON_ERROR
                        pending = None
                        if confirmed and (now - last_fire) > COOLDOWN_S:
                            last_fire = now
                            fire_flash_until = now + 1.0
                            if not paused:
                                send_fire(ser)
                            print(">>> VYSTREL POTVRDENY" + (" [POZASTAVENE]" if paused else " -> micro:bit"))

                    if settle_pending and (now - settle_at) >= SETTLE_S and not moving:
                        settle_pending = False
                        last_perm = 0.0
                        if pre_motion_gray is not None and pre_motion_gray.shape == gray.shape:
                            d2 = cv2.absdiff(pre_motion_gray, gray)
                            _, m2 = cv2.threshold(d2, DIFF_THRESH, 255, cv2.THRESH_BINARY)
                            last_perm = cv2.countNonZero(m2) / roi_area
                        stable_gray = gray
                        if (last_perm >= PERMANENT_THRESHOLD and pending is None
                                and (now - last_fire) > COOLDOWN_S
                                and (now - last_check) > CHECK_COOLDOWN_S):
                            last_check = now
                            if USE_GEMINI and _gemini_client is not None:
                                pending = _pool.submit(confirm_job, roi.copy())
                                pending_started = now
                            else:
                                last_fire = now
                                fire_flash_until = now + 1.0
                                if not paused:
                                    send_fire(ser)
                                print(">>> VYSTREL (lokalne)" + (" [POZASTAVENE]" if paused else " -> micro:bit"))

        # ---------- vykreslenie ----------
        if sel["drawing"] and sel["start"] and sel["cur"]:
            cv2.rectangle(frame, sel["start"], sel["cur"], (0, 255, 255), 2)
            cv2.putText(frame, "kresli vyrez...", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        elif active_roi is not None:
            rx, ry, rw, rh = active_roi
            if now < fire_flash_until:
                if paused:
                    status, color = "VYSTREL (pozastavene)", (0, 140, 255)
                else:
                    status, color = "VYSTREL!", (0, 0, 255)
            elif (not test_mode) and pending is not None:
                status, color = "kontrolujem (Gemini)...", (0, 200, 255)
            elif test_mode:
                status = f"TEST  pohyb:{motion_ratio:0.2f}  prah:{MOTION_RATIO:0.2f}"
                color = (0, 255, 0)
            else:
                status = f"pohyb:{motion_ratio:0.2f}  trvala-zmena:{last_perm:0.2f}  prah:{PERMANENT_THRESHOLD:0.2f}"
                color = (0, 255, 0)
            cv2.rectangle(frame, (rx, ry), (rx + rw, ry + rh), color, 2)
            cv2.putText(frame, status, (rx, max(ry - 10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        else:
            cv2.putText(frame, "Nakresli mysou obdlznik okolo katapultu", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

        # klikacie tlacitko rezimu (vpravo hore)
        btn_color = (0, 130, 0) if test_mode else (0, 0, 150)
        cv2.rectangle(frame, (btn_x0, btn_y0), (btn_x1, btn_y1), btn_color, -1)
        cv2.rectangle(frame, (btn_x0, btn_y0), (btn_x1, btn_y1), (255, 255, 255), 1)
        btn_label = ("REZIM: TEST" if test_mode else "REZIM: OSTRY") + "  (klik / M)"
        cv2.putText(frame, btn_label, (btn_x0 + 10, btn_y0 + 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        # klikacie tlacitko pauzy
        btn2_color = (120, 90, 0) if paused else (0, 110, 0)
        cv2.rectangle(frame, (btn2_x0, btn2_y0), (btn2_x1, btn2_y1), btn2_color, -1)
        cv2.rectangle(frame, (btn2_x0, btn2_y0), (btn2_x1, btn2_y1), (255, 255, 255), 1)
        btn2_label = ("POZASTAVENE" if paused else "BEZI") + "  (klik / P)"
        cv2.putText(frame, btn2_label, (btn2_x0 + 10, btn2_y0 + 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        ref_txt = "referencia: ULOZENA" if _ref_jpg is not None else "referencia: ziadna (nepovinna, 'r')"
        cv2.putText(frame, ref_txt, (10, ah - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

        disp, _ = _fit_display(frame)
        cv2.imshow(win, disp)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("r") and active_roi is not None:
            rx, ry, rw, rh = active_roi
            save_reference(frame[ry:ry + rh, rx:rx + rw].copy())
        elif key == ord("x"):
            send_reset(ser)
        elif key == ord("m"):
            sel["toggle_request"] = True
        elif key == ord("p"):
            sel["pause_request"] = True

    cap.release()
    cv2.destroyAllWindows()
    if ser is not None:
        ser.close()


if __name__ == "__main__":
    main()
