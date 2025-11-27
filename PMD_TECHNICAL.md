# PMD Technical Documentation

This file documents PMD internals discovered during development of pmd2furnace.

## SSG Software Envelope (E Command)

**Source:** PMDMML_EN.MAN.htm §8.1

### Format 1 (PMD-style): `E al, dd, sr, rr`

PMD's unique simplified envelope specification method.

| Parameter | Name | Range | Description |
|-----------|------|-------|-------------|
| number1 | AL | 0-255 | Attack Length |
| number2 | DD | -15 to +15 | Decay Depth |
| number3 | SR | 0-255 | Sustain Rate |
| number4 | RR | 0-255 | Release Rate |

**Envelope Behavior:**

1. After keyon, wait for **AL clocks** at the set volume, then add/subtract **DD** to/from volume
2. For each **SR clocks**, decrease the volume by -1
3. When keyoff is done, decrease the volume by -1 for each **RR clocks**

**Special Cases:**
- When SR = 0: At step 2 the volume will NOT decay (sustain forever)
- When RR = 0: At step 3 the volume will instantly drop to 0

**Example:** `E1,-2,2,1 v13 l16 gr`

Volume changes: `13 → 11 → 11 → 9 → 9 → 7 → 7 → 6 → 5 → 4 → 3 → 2`
```
13 11 11 9 9 7 7 6 5 4 3 2
↑keyon         ↑keyoff
```

Breakdown:
- Start at v13
- After 1 clock (AL=1), add DD=-2: volume becomes 11
- Every 2 clocks (SR=2), decrease by 1: 11→11→9→9→7→7→...
- On keyoff, decrease by 1 every 1 clock (RR=1): 6→5→4→3→2

### Format 2 (FM-style): `E ar, dr, sr, rr, sl [,al]`

Nearly identical to FM volume envelope.

| Parameter | Name | Range | Description |
|-----------|------|-------|-------------|
| number1 | AR | 0-31 | Attack Rate |
| number2 | DR | 0-31 | Decay Rate |
| number3 | SR | 0-31 | Sustain Rate |
| number4 | RR | 0-15 | Release Rate |
| number5 | SL | 0-15 | Sustain Level |
| number6 | AL | 0-15 | Attack Level (default 0) |

**Example:** `E31,18,4,15,2` - Piano-like envelope

---

## Envelope Speed (EX Command)

**Source:** PMDMML_EN.MAN.htm §8.2

`EX0` - Envelope speed depends on tempo (slower tempo = slower envelope)
`EX1` - Envelope speed is fixed at ~54.17 Hz

**Clock Rates:**
- EX0 (Normal): 1 clock = 1 internal clock (tempo-dependent)
- EX1 (Extend): 1 clock = 54.17 Hz (fixed)

The internal clock rate for EX0 is approximately: `tempo * 48 / 60` Hz

---

## Common E Command Values in TH4

From Bad Apple (Th04_07):
- `E2,255,24,1` → AL=2, DD=-1 (255 signed), SR=24, RR=1
  - Wait 2 clocks at volume, then +1 volume (DD=-1 means add 1)
  - Decay by 1 every 24 clocks
  - Release: decay by 1 every 1 clock

From Th04_04:
- `E1,2,24,1` → AL=1, DD=2, SR=24, RR=1
  - Wait 1 clock, subtract 2 from volume
  - Decay by 1 every 24 clocks
  
- `E2,1,0,1` → AL=2, DD=1, SR=0, RR=1
  - Wait 2 clocks, subtract 1
  - SR=0 means NO decay (sustain at that level)
  - Release at 1 clock per step

From Th14_13:
- `E1,0,0,0` → No envelope (all zeros = flat)
- `E2,-1,24,1` → Swell up by 1, then decay
- `E2,-2,4,1` → Swell up by 2, fast decay (4 clocks)

---

## Gate Time (q/Q Commands)

**Source:** PMDMML_EN.MAN.htm

### q Command (0xFE)
`q0` to `q8` - Sets gate time as fraction of 8

- `q0` = Staccato (note cuts immediately after keyon)
- `q8` = Full length (default)
- `q6` = Note plays for 6/8 (75%) of duration

**Calculation:** `actual_length = note_length * q / 8`

### Q Command (0xC4)
Similar to q but specifies minimum gate time in ticks.

---

## Timing / Internal Clock

**ZENLEN (Whole Note Length):** Default 96 ticks

| Note Value | Ticks |
|------------|-------|
| Whole | 96 |
| Half | 48 |
| Quarter | 24 |
| 8th | 12 |
| 16th | 6 |
| 32nd | 3 |

**Internal Clock Rate:** 
- Normal mode: `tempo * 48 / 60` Hz
- At tempo 120: 96 Hz
- At tempo 150: 120 Hz

---

## LFO / Vibrato (M Command)

**Source:** PMDMML_EN.MAN.htm §9.1

`M delay, speed, depthA, depthB`
`MA delay, speed, depthA, depthB` (LFO1)
`MB delay, speed, depthA, depthB` (LFO2)

| Parameter | Range | Description |
|-----------|-------|-------------|
| delay | 0-255 | Ticks before LFO starts |
| speed | 0-255 | LFO speed |
| depthA | -128 to +127 | Primary depth |
| depthB | 0-255 | Secondary depth |

**Waveforms (MW command):**
- MW0: Triangle Wave 1
- MW1: Sawtooth Wave
- MW2: Square Wave
- MW3: Random Wave
- MW4: Triangle Wave 2
- MW5: Triangle Wave 3
- MW6: One-shot

**Speed Modes (MX command):**
- MX0: Speed depends on tempo
- MX1: Fixed speed (~54.17 Hz)

---

## Channel Mapping

| PMD | Name | Type | Furnace Ch |
|-----|------|------|------------|
| A | FM-A | FM | 0 |
| B | FM-B | FM | 1 |
| C | FM-C | FM | 2 |
| D | FM-D | FM | 3 |
| E | FM-E | FM | 4 |
| F | FM-F | FM | 5 |
| G | SSG-G | SSG | 6 |
| H | SSG-H | SSG | 7 |
| I | SSG-I | SSG | 8 |
| J | ADPCM-J | ADPCM-B | 9 |
| K | Rhythm-K | Rhythm | 10+ |

---

## Binary Command Reference

| Byte | Command | Params | Description |
|------|---------|--------|-------------|
| 0xFF | @ | 1 | Instrument |
| 0xFE | q | 1+ | Gate time |
| 0xFD | V | 1 | Volume |
| 0xFC | t/T | 1+ | Tempo |
| 0xFB | & | 0 | Tie |
| 0xFA | D | 2 | Detune |
| 0xF9 | [ | 2 | Loop start |
| 0xF8 | ] | 4 | Loop end |
| 0xF7 | : | 2 | Loop break |
| 0xF6 | L | 0 | Loop point |
| 0xF5 | _ | 1 | Transpose |
| 0xF4 | ) | 0 | Volume up |
| 0xF3 | ( | 0 | Volume down |
| 0xF2 | M | 4 | LFO set |
| 0xF1 | * | 1 | LFO switch |
| 0xF0 | E | 4 | SSG envelope |
| 0xEC | p | 1 | Pan |
| 0xDA | {} | 3 | Portamento |
| 0xC4 | Q | 1 | Gate time alt |

---

## Furnace Effect Mapping

| PMD Feature | Furnace Effect | Notes |
|-------------|----------------|-------|
| Detune (D) | E5xx | 80=center |
| Pan (p) | 08xy | x=left, y=right |
| Gate time (q) | ECxx | Note cut after xx ticks |
| Portamento ({}) | E1xy/E2xy | Note slide up/down |
| Volume slide | 0Axy / F3xx/F4xx | |
| Song loop (L) | 0Bxx | Jump to order |

---

## Resources

- [PMDMML_EN.MAN](https://pigu-a.github.io/pmddocs/pmdmml.htm) - Full MML documentation
- [BotB Lyceum - PMD Effects](https://battleofthebits.com/lyceum/View/Professional%20Music%20Driver%20Effects%20Commands) - Quick reference
- [pedipanol's MML Guide](https://mml-guide.readthedocs.io/pmd/resources/) - Resource links

