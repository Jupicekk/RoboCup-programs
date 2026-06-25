# MakeCode programy pre micro:bity

Architektúra: **notebook (detektor) → USB → micro:bit (počíta + vysiela) → rádio → ostatné micro:bity.**

Detektor posiela po USB:
- `F` = výstrel → micro:bit **zvýši počítadlo o 1** a pošle ho rádiom
- `R` = reset → micro:bit **vynuluje počítadlo** (detektor to robí pri štarte a klávesou `x`)

---

## 1) VYSIELAČ / POČÍTADLO — micro:bit zapojený do notebooku (USB)

**Bloky (slovne):**
- `na začiatku`:
  - `nastav pocitadlo na 0`
  - `rádio nastav skupinu 1`
  - `sériová linka presmeruj na USB`
  - `zobraz číslo 0`
- `sériová linka pri prijatí dát (oddeľovač: nový riadok)`:
  - prečítaj riadok do `cmd`
  - `ak cmd = "F"`: `zmeň pocitadlo o 1`, `rádio pošli číslo pocitadlo`, `zobraz číslo pocitadlo`
  - `inak ak cmd = "R"`: `nastav pocitadlo na 0`, `rádio pošli číslo 0`, `zobraz číslo 0`

**To isté ako JavaScript (v MakeCode prepni vpravo hore na „JavaScript"):**
```javascript
let pocitadlo = 0
radio.setGroup(1)
serial.redirectToUSB()
basic.showNumber(0)
serial.onDataReceived(serial.delimiters(Delimiters.NewLine), function () {
    let cmd = serial.readUntil(serial.delimiters(Delimiters.NewLine))
    if (cmd == "F") {
        pocitadlo += 1
        radio.sendNumber(pocitadlo)
        basic.showNumber(pocitadlo)
    } else if (cmd == "R") {
        pocitadlo = 0
        radio.sendNumber(0)
        basic.showNumber(0)
    }
})
```

> Pozor: toto je iné než tvoj pôvodný program s `pause 1000` (ten počítal sekundy).
> Tu sa počítadlo zvyšuje **iba pri reálnom výstrele** (príkaz `F` z detektora).
> `serial.redirectToUSB()` + baud **115200** musí sedieť s detektorom (sedí).

---

## 2) PRIJÍMAČ — ostatné micro:bity (bez zmeny, počúvajú číslo na skupine 1)

```javascript
radio.setGroup(1)
radio.onReceivedNumber(function (receivedNumber) {
    basic.showNumber(receivedNumber)
    // alebo svoja akcia podla cisla...
})
```

> Skupina rádia (`setGroup`) musí byť **rovnaká (1)** na vysielači aj prijímačoch.
