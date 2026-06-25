# -*- coding: utf-8 -*-
"""Prehlada dostupne kamery (index 0-4) a ukaze, ktore funguju."""
import cv2

for i in range(5):
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if cap.isOpened():
        ok, f = cap.read()
        if ok:
            print(f"index {i}: OK  rozlisenie {f.shape[1]}x{f.shape[0]}")
        else:
            print(f"index {i}: otvorena, ale necita snimku")
        cap.release()
    else:
        print(f"index {i}: nedostupna")
