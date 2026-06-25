# -*- coding: utf-8 -*-
"""Test vizie s opakovanim: posli realny obrazok a najdi model ktory odpoveda."""
import os, time
import cv2
import numpy as np
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

def _load_key():
    k = os.environ.get("GEMINI_API_KEY", "").strip()
    if k:
        return k
    kf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini_key.txt")
    return open(kf, encoding="utf-8").read().strip() if os.path.exists(kf) else ""

client = genai.Client(api_key=_load_key())

# vyrobime jednoduchy testovaci obrazok
img = np.zeros((200, 320, 3), dtype=np.uint8)
cv2.putText(img, "TEST", (60, 120), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (255, 255, 255), 4)
ok, buf = cv2.imencode(".jpg", img)
jpg = buf.tobytes()

models = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.5-flash",
          "gemini-flash-latest", "gemini-2.0-flash-lite"]

prompt = "Aky text vidis na obrazku? Odpovedz jednym slovom."

for model in models:
    for attempt in range(1, 4):
        try:
            r = client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=jpg, mime_type="image/jpeg"), prompt],
            )
            print(f"[OK] model='{model}' (pokus {attempt}) -> odpoved: {r.text!r}")
            raise SystemExit(0)
        except genai_errors.ServerError as e:
            print(f"[503/server] {model} pokus {attempt}: {repr(e)[:90]} ... skusam znova")
            time.sleep(2)
        except Exception as e:
            print(f"[chyba] {model}: {repr(e)[:150]}")
            break

print("Ziadny model neodpovedal (zrejme docasne preťazenie). Skus o chvilu znova.")
