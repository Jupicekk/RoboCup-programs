# -*- coding: utf-8 -*-
"""Spravi snimku z indexov 0 a 1, ulozi cam0.jpg / cam1.jpg na identifikaciu."""
import cv2

for i in [0, 1]:
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(f"index {i}: nedostupna")
        continue
    for _ in range(15):   # zahriatie kamery, prve snimky byvaju cierne
        cap.read()
    ok, f = cap.read()
    if ok:
        cv2.imwrite(f"cam{i}.jpg", f)
        print(f"index {i}: ulozene cam{i}.jpg ({f.shape[1]}x{f.shape[0]})")
    else:
        print(f"index {i}: necita")
    cap.release()
