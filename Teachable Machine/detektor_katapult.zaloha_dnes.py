# -*- coding: utf-8 -*-
"""
Detektor vystrelu katapultu pre RoboCup.

Princip (setrny na API): LOKALNE sa zachyti mozny vystrel (pohyb vo vyreze), a AI len POTVRDI
"je to realny vystrel? ANO/NIE". Cize 1 dotaz na jeden mozny vystrel = minimum volani.

AI sa da prepnut: AI_PROVIDER = "gemini" alebo "claude".

Kamera bezi stale, vyrez (ROI) okolo katapultu kreslis mysou NAŽIVO (drz lave tlacidlo a tahaj).
Referencia (klaves 'r' ked je katapult NABITY) je nepovinna - pomoze presnosti.

Ovladanie:  mys = vyrez   |   q = koniec   |   r = uloz referenciu (NABITY)

Spustenie: dvojklik na spustit.bat
"""

import os
import sys
import time
import base64
import threading
import concurrent.futures

import cv2
import numpy as np

# ----------------------------------------------------------------------------
# KONFIGURACIA
# ----------------------------------------------------------------------------
# --- vyber kamery ---
CAM_IRIUN     = 0
CAM_POCITAC   = 1
CAM_INDEX     = CAM_POCITAC   # <<< prepinas: CAM_POCITAC alebo CAM_IRIUN

# --- kvalita kamery ---
CAM_W    = 1280
CAM_H    = 720
CAM_FPS  = 30
USE_MJPG = True
DISPLAY_MAX_W = 1280

# --- ktora AI potvrdzuje vystrel ---
AI_PROVIDER  = "gemini"                       # <<< "gemini"  alebo  "claude"
GEMINI_MODEL = "gemini-2.5-flash"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"    # rychly+lacny; pre vyssiu presnost: "claude-sonnet-4-6"

# --- lokalna detekcia mozneho vystrelu (len rozhoduje KEDY sa spytat AI) ---
CHANGE_THRESHOLD = 0.02   # aka cast vyrezu sa musi zmenit = "nieco sa hyblo" (nizsie = citlivejsie)
DIFF_THRESH      = 25     # citlivost na zmenu jasu pixelu
SETTLE_S         = 0.25   # po skonceni pohybu pockaj tolko a posli AI cisty (ustaleny) zaber
CHECK_COOLDOWN_S = 1.0    # min rozostup medzi AI dotazmi
COOLDOWN_S       = 2.0    # po potvrdenom vystrele ignoruj dalsi tolko sekund
BACKOFF_S        = 15.0   # po limite (429) pockaj tolko

# --- micro:bit cez USB serial (ak nenajde port, bezi bez neho) ---
USE_MICROBIT  = True
SERIAL_PORT   = "COM3"
SERIAL_BAUD   = 115200
FIRE_CMD      = b"F\n"

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
CLAUDE_KEY = _load_key("ANTHROPIC_API_KEY", "claude_key.txt")
# ----------------------------------------------------------------------------


# ---------- micro:bit serial ----------
def init_serial():
    if not USE_MICROBIT:
        return None
    try:
        import serial
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.1)
        print(f"[micro:bit] pripojeny na {SERIAL_PORT}")
        return ser
    except Exception as e:
        print(f"[micro:bit] CHYBA pripojenia ({e}). Bezim bez micro:bitu.")
        return None


def send_fire(ser):
    if ser is None:
        return
    try:
        ser.write(FIRE_CMD)
    except Exception as e:
        print(f"[micro:bit] chyba posielania: {e}")


# ---------- AI potvrdenie vystrelu ----------
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_gemini_client = None
_claude_client = None
_ref_jpg = None

PROMPT_FIRE = (
    "Na obrazku je katapult tesne po nejakom pohybe. Vystrelil PRAVE TERAZ katapult "
    "(rameno sa vymrstilo dopredu/hore, je uvolnene)? Ak je rameno stale stiahnute/nabite, "
    "alebo sa hyblo nieco ine (clovek, pozadie), odpovedz NIE. "
    "Odpovedz IBA jednym slovom: ANO alebo NIE."
)
PROMPT_FIRE_REF = (
    "Prvy obrazok = katapult NABITY (referencia). Druhy obrazok = aktualny zaber po pohybe. "
    "Vystrelil katapult (rameno sa vymrstilo / zmenilo polohu oproti referencii)? "
    "Ignoruj pohyb ludi a pozadia. Odpovedz IBA jednym slovom: ANO alebo NIE."
)


def _jpeg(roi_bgr, q=85):
    ok, buf = cv2.imencode(".jpg", roi_bgr, [cv2.IMWRITE_JPEG_QUALITY, q])
    return buf.tobytes() if ok else None


def init_ai():
    global _gemini_client, _claude_client
    if AI_PROVIDER == "claude":
        if not CLAUDE_KEY:
            print("[Claude] CHYBA: ziadny kluc (claude_key.txt alebo ANTHROPIC_API_KEY).")
            return
        try:
            import anthropic
            _claude_client = anthropic.Anthropic(api_key=CLAUDE_KEY)
            print(f"[Claude] pripraveny, model {CLAUDE_MODEL}")
        except Exception as e:
            print(f"[Claude] CHYBA inicializacie ({e}).")
    else:
        if not GEMINI_KEY:
            print("[Gemini] CHYBA: ziadny kluc (gemini_key.txt alebo GEMINI_API_KEY).")
            return
        try:
            from google import genai
            _gemini_client = genai.Client(api_key=GEMINI_KEY)
            print(f"[Gemini] pripraveny, model {GEMINI_MODEL}")
        except Exception as e:
            print(f"[Gemini] CHYBA inicializacie ({e}).")


def ai_ready():
    return _claude_client is not None if AI_PROVIDER == "claude" else _gemini_client is not None


def _gemini_confirm(jpg):
    try:
        from google.genai import types
        from google.genai import errors as gerr
    except Exception:
        return "UNSURE"
    cur = types.Part.from_bytes(data=jpg, mime_type="image/jpeg")
    if _ref_jpg is not None:
        contents = [types.Part.from_bytes(data=_ref_jpg, mime_type="image/jpeg"), cur, PROMPT_FIRE_REF]
    else:
        contents = [cur, PROMPT_FIRE]
    for _ in range(2):
        try:
            r = _gemini_client.models.generate_content(model=GEMINI_MODEL, contents=contents)
            ans = (r.text or "").strip().upper()
            print(f"[Gemini] {ans}")
            return "YES" if "ANO" in ans else ("NO" if "NIE" in ans else "UNSURE")
        except gerr.ServerError:
            time.sleep(0.3)
        except gerr.ClientError as e:
            if "429" in repr(e) or "RESOURCE_EXHAUSTED" in repr(e):
                print("[Gemini] LIMIT 429")
                return "RATE_LIMIT"
            print(f"[Gemini] chyba ({repr(e)[:80]})")
            return "UNSURE"
        except Exception as e:
            print(f"[Gemini] chyba ({repr(e)[:80]})")
            return "UNSURE"
    return "UNSURE"


def _claude_confirm(jpg):
    import anthropic
    content = []
    if _ref_jpg is not None:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                        "data": base64.b64encode(_ref_jpg).decode()}})
        prompt = PROMPT_FIRE_REF
    else:
        prompt = PROMPT_FIRE
    content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                    "data": base64.b64encode(jpg).decode()}})
    content.append({"type": "text", "text": prompt})
    try:
        msg = _claude_client.messages.create(
            model=CLAUDE_MODEL, max_tokens=10,
            messages=[{"role": "user", "content": content}])
        ans = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip().upper()
        print(f"[Claude] {ans}")
        return "YES" if "ANO" in ans else ("NO" if "NIE" in ans else "UNSURE")
    except anthropic.RateLimitError:
        print("[Claude] LIMIT 429")
        return "RATE_LIMIT"
    except Exception as e:
        print(f"[Claude] chyba ({repr(e)[:80]})")
        return "UNSURE"


def confirm_fire(roi_bgr):
    """Bezi NA POZADI. Vrati 'YES' / 'NO' / 'UNSURE' / 'RATE_LIMIT'."""
    jpg = _jpeg(roi_bgr)
    if jpg is None:
        return "UNSURE"
    if AI_PROVIDER == "claude":
        if _claude_client is None:
            return "UNSURE"
        return _claude_confirm(jpg)
    if _gemini_client is None:
        return "UNSURE"
    return _gemini_confirm(jpg)


# ---------- referencia ----------
def load_reference():
    global _ref_jpg
    if os.path.exists(REF_FILE):
        with open(REF_FILE, "rb") as f:
            _ref_jpg = f.read()
        print(f"[referencia] nacitana zo suboru {REF_FILE}")


def save_reference(roi_bgr):
    global _ref_jpg
    jpg = _jpeg(roi_bgr)
    if jpg is not None:
        _ref_jpg = jpg
        with open(REF_FILE, "wb") as f:
            f.write(jpg)
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


RESULT_TXT = {"YES": "VYSTREL", "NO": "ok (nie vystrel)", "UNSURE": "neiste",
              "RATE_LIMIT": "LIMIT API", "—": "—"}


# ---------- hlavna slucka ----------
def main():
    init_ai()
    ser = init_serial()

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
    win = "Detektor katapultu (mys=vyrez, q=koniec, r=referencia)"
    cv2.namedWindow(win)

    sel = {"drawing": False, "start": None, "cur": None, "roi": None}
    if not ALWAYS_SELECT_ROI:
        saved = load_saved_roi(aw, ah)
        if saved:
            sel["roi"] = saved
            load_reference()

    def on_mouse(event, x, y, flags, param):
        fx = max(0, min(aw - 1, int(x / display_scale)))
        fy = max(0, min(ah - 1, int(y / display_scale)))
        if event == cv2.EVENT_LBUTTONDOWN:
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
    prev_gray = None
    motion_active = False
    motion_stop_time = 0.0
    settle_pending = False
    last_fire = 0.0
    last_check = 0.0
    backoff_until = 0.0
    fire_flash_until = 0.0
    pending = None
    last_result = "—"

    print(f"[OK] Bezim ({AI_PROVIDER}). Nakresli mysou obdlznik okolo katapultu. q=koniec, r=referencia.")

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            if cv2.waitKey(10) & 0xFF == ord("q"):
                break
            continue

        roi_box = sel["roi"]
        detect = roi_box is not None and not sel["drawing"]

        if roi_box is not None and roi_box != active_roi:
            active_roi = roi_box
            prev_gray = None
            motion_active = False
            settle_pending = False
            last_result = "—"
            pending = None
            save_roi(roi_box)
            print(f"[ROI] novy vyrez: {roi_box}")

        now = time.time()
        motion_ratio = 0.0

        if detect and ai_ready():
            rx, ry, rw, rh = active_roi
            roi = frame[ry:ry + rh, rx:rx + rw]

            # lacna lokalna detekcia ZMENY (rozhoduje len KEDY sa spytat AI)
            if roi.size > 0:
                gray = cv2.GaussianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (11, 11), 0)
                if prev_gray is not None and prev_gray.shape == gray.shape:
                    diff = cv2.absdiff(prev_gray, gray)
                    _, mask = cv2.threshold(diff, DIFF_THRESH, 255, cv2.THRESH_BINARY)
                    motion_ratio = cv2.countNonZero(mask) / float(roi.shape[0] * roi.shape[1])
                prev_gray = gray

            # vyhodnot dobehnutu AI kontrolu
            if pending is not None and pending.done():
                try:
                    res = pending.result()
                except Exception:
                    res = "UNSURE"
                pending = None
                last_result = res
                if res == "RATE_LIMIT":
                    backoff_until = now + BACKOFF_S
                elif res == "YES" and (now - last_fire) > COOLDOWN_S:
                    send_fire(ser)
                    last_fire = now
                    fire_flash_until = now + 1.0
                    print(">>> VYSTREL POTVRDENY -> poslane micro:bitu")

            # mozny vystrel = pohyb prave dobehol -> posli Ana potvrdenie
            if motion_ratio > CHANGE_THRESHOLD:
                motion_active = True
            elif motion_active:
                motion_active = False
                motion_stop_time = now
                settle_pending = True

            if (settle_pending and (now - motion_stop_time) >= SETTLE_S
                    and pending is None and now >= backoff_until
                    and roi.size > 0 and (now - last_check) >= CHECK_COOLDOWN_S):
                last_check = now
                settle_pending = False
                pending = _pool.submit(confirm_fire, roi.copy())

        # ---------- vykreslenie ----------
        if sel["drawing"] and sel["start"] and sel["cur"]:
            cv2.rectangle(frame, sel["start"], sel["cur"], (0, 255, 255), 2)
            cv2.putText(frame, "kresli vyrez...", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        elif active_roi is not None:
            rx, ry, rw, rh = active_roi
            if now < fire_flash_until:
                status, color = "VYSTREL!", (0, 0, 255)
            elif not ai_ready():
                status, color = f"{AI_PROVIDER} nedostupne (skontroluj kluc)", (0, 0, 255)
            else:
                human = RESULT_TXT.get(last_result, last_result)
                color = (0, 0, 255) if last_result == "RATE_LIMIT" else (0, 255, 0)
                dots = " ." * (int(now * 2) % 4) if pending is not None else ""
                status = f"{AI_PROVIDER}: {human}{dots}   pohyb:{motion_ratio:0.2f}"
            cv2.rectangle(frame, (rx, ry), (rx + rw, ry + rh), color, 2)
            cv2.putText(frame, status, (rx, max(ry - 10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        else:
            cv2.putText(frame, "Nakresli mysou obdlznik okolo katapultu", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

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

    cap.release()
    cv2.destroyAllWindows()
    if ser is not None:
        ser.close()


if __name__ == "__main__":
    main()
