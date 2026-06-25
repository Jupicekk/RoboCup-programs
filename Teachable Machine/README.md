# Detektor výstrelu katapultu (RoboCup) – obraz + AI

Náhrada za Teachable Machine s nižšou chybovosťou. Mobil je kamera (cez **Iriun Webcam**),
notebook vyhodnocuje obraz, **lokálna detekcia pohybu** spustí udalosť a **Gemini** ju potvrdí,
potom sa pošle signál do **micro:bitu** (USB), ktorý ho rádiom rozošle ostatným.

```
Mobil (Iriun, USB)  ->  Notebook: detekcia pohybu  ->  Gemini potvrdenie  ->  micro:bit (USB) -> rádio -> ostatné micro:bity
                         (hlavný spúšťač)              (filter falošných poplachov)
```

Ak vypadne internet/Gemini, systém **automaticky beží len na lokálnej detekcii** (fail-safe).

---

## ✅ Čo už je hotové (spravené za teba)
- Nainštalovaný **Python 3.12** + všetky knižnice (opencv, google-genai, pyserial, numpy).
- **Gemini API kľúč je zapojený** (súbor `gemini_key.txt`) a **otestovaný – funguje**.
- Model nastavený na **`gemini-2.5-flash`** (overený, odpovedá).
- Spúšťací súbor **`spustit.bat`** – stačí dvojklik.

## Čo musíš spraviť ešte ty (fyzické veci)

### 1. Mobil ako kamera
1. Nainštaluj **Iriun Webcam** do mobilu aj do notebooku (iriun.com).
2. Mobil pripoj k notebooku **USB káblom** (stabilnejšie než WiFi v hale).
3. V Iriun na notebooku skontroluj, že vidíš obraz z mobilu.

### 2. micro:bit
1. Otvor `microbit_makecode.md` a nahraj **vysielací** program do micro:bitu, ktorý
   bude v notebooku cez USB. Do ostatných micro:bitov nahraj **prijímací** program.
2. Zisti **COM port** vysielacieho micro:bitu (Windows → Správca zariadení → Porty COM).
3. Zapíš ho do `SERIAL_PORT` v `detektor_katapult.py` (napr. `"COM3"`).

> Zatiaľ nemáš micro:bit zapojený a chceš len otestovať detekciu? V `detektor_katapult.py`
> nastav `USE_MICROBIT = False` a skript pobeží aj bez neho (len ukáže „VYSTREL!" v okne).

### 3. Spustenie
**Dvojklik na `spustit.bat`** (alebo v PowerShelli:
`& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" detektor_katapult.py`).

1. **Pri každom spustení** myšou **nakresli obdĺžnik TESNE okolo ramena katapultu** (ťahaj ľavým
   tlačidlom) a stlač **ENTER**. Výber sa robí zakaždým nanovo, lebo kamera nemusí byť rovnako.
   (Ak by si mal kameru pevne fixovanú, daj v skripte `ALWAYS_SELECT_ROI = False` na použitie uloženého výrezu.)
2. **Kým je katapult v pokoji, stlač `r`** → uloží sa referenčný snímok. Gemini potom
   porovnáva „pokoj vs. teraz" a presnejšie rozozná výstrel od iného pohybu.
3. Skús katapult vystreliť → v okne sa ukáže „VYSTREL!" a micro:bity dostanú signál.
4. Ukončenie: stlač **q**.

**Klávesy v okne:** `q` = koniec · `r` = ulož referenciu (katapult v pokoji)

> ⚠️ **Bezpečnosť:** kľúč v `gemini_key.txt` bol zdieľaný v chate – ber ho ako kompromitovaný.
> Po súťaži (alebo hneď) si vygeneruj nový na https://ai.google.dev a starý zruš.
> Súbor `gemini_key.txt` nikdy nikam nenahrávaj (je v `.gitignore`).

---

## Ladenie spoľahlivosti (ak máš falošné poplachy alebo nezachytí výstrel)

V `detektor_katapult.py` hore v sekcii KONFIGURÁCIA:

| Problém | Čo zmeniť |
|---|---|
| Zachytáva aj iný pohyb (falošné poplachy) | zmenši ROI len na rameno, zväčši `MOTION_RATIO`, ulož referenciu klávesou `r` |
| Pohyb človeka stále spúšťa | zmenši `SPIKE_MAX_FRAMES` (napr. 6) – kratší „záblesk" = prísnejšie na dlhý pohyb |
| Nezachytí slabý/rýchly výstrel | zmenši `MOTION_RATIO` (napr. 0.05) alebo `MOG2_VARTHRESH` (napr. 16) |
| Zo začiatku to nereaguje | ~1 s sa „učí pozadie" (`WARMUP_FRAMES`) – počkaj, kým zmizne nápis |
| Dvojité spustenia za sebou | zväčši `COOLDOWN_S` |
| Gemini je pomalý v hale | zmenši `GEMINI_TIMEOUT_S`, alebo daj internet z mobilného hotspotu |
| Zlá kamera | skús iný `CAM_INDEX` (`CAM_POCITAC` / `CAM_IRIUN`) |
| Chcem vystreliť aj keď vypadne internet | nastav `FALLBACK_ON_ERROR = True` (predvolene `False` = pri výpadku radšej nevystrelí) |

> **Detekcia:** MOG2 (učí sa pozadie) + „záblesk" (len krátky prudký pohyb; dlhý súvislý pohyb
> ako prechádzajúci človek sa ignoruje – v okne svieti „ignorujem (dlhý pohyb)").
> Potom **vždy** potvrdí Gemini. Ukazovateľ `max` v okne pomáha naladiť `MOTION_RATIO`.

> **Plynulosť obrazu:** Gemini kontrola beží na pozadí, takže živý obraz sa už **neseká**.
> Keď prebieha kontrola, v okne svieti žlté „kontrolujem (Gemini)…", obraz medzitým beží ďalej.

**Tip na súťaž:** video z mobilu cez **USB**, internet pre Gemini z **mobilného hotspotu** –
nezáviseť od preťaženej WiFi v hale. Mobil pevne uchyť, aby bol záber stále rovnaký.

---

## Prečo je to lepšie než Teachable Machine
- Sleduje **konkrétny výrez okolo ramena**, nie celý rozmazaný snímok → menej chýb na svetlo/pozadie.
- **Lokálna detekcia** je okamžitá a nepotrebuje internet → spoľahlivý základ.
- **Veľký AI model (Gemini)** potvrdzuje udalosť → odfiltruje falošné poplachy.
- Silný **inovačný príbeh**: vlastná počítačová vízia + multimodálne AI.
