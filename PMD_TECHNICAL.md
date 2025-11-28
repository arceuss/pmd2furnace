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

## PMD Predefined SSG Envelopes

**Source:** PMDDotNET/PMDDotNETCompiler/mml_seg.cs `psgenvdat`

These are the built-in SSG envelope presets (format: `{ AL, DD, SR, RR }`):

| Preset | Values | Name | Description |
|--------|--------|------|-------------|
| @0 | 0, 0, 0, 0 | Standard | No envelope (flat) |
| @1 | 2, 255, 0, 1 | Synth 1 | DD=255 is -1 signed |
| @2 | 2, 254, 0, 1 | Synth 2 | DD=254 is -2 signed |
| @3 | 2, 254, 0, 8 | Synth 3 | Slower release |
| @4 | 2, 255, 24, 1 | E.Piano 1 | Slow decay (SR=24) |
| @5 | 2, 254, 24, 1 | E.Piano 2 | |
| @6 | 2, 254, 4, 1 | Glocken/Marimba | Fast decay (SR=4) |
| @7 | 2, 1, 0, 1 | Strings | DD=1 (slight swell), SR=0 (sustain) |
| @8 | 1, 2, 0, 1 | Brass 1 | Quick attack |
| @9 | 1, 2, 24, 1 | Brass 2 | Quick attack, slow decay |

**Note:** DD values 128-255 are negative when treated as signed bytes.

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

**Source:** PMDMML_EN.MAN.htm, PMDDotNET source code

### How Gate Time Actually Works (from PMDDotNET)

PMD's gate time is **NOT** "play for q/8 of the note". Instead:

- `qdat` = **ticks before note end to keyoff**
- Keyoff happens when `remaining_length <= qdat`
- `qdat = 0` means full note length (no early keyoff)
- Higher qdat = more staccato

**Variables in PMDDotNET:**
```
qdata  - q command value (direct ticks)
qdatb  - Q command value (percentage 0-8)
qdat   - calculated gate time for current note
qdat2  - minimum gate guarantee
qdat3  - random gate variation
```

**Calculation:**
```
qdat = qdata + (note_length * qdatb / 8)
release_tick = note_start + note_length - qdat
```

### q Command (0xFE)
Sets direct tick value to cut from end of note.

- `q0` = Full length (no early keyoff) - DEFAULT
- `q2` = Keyoff 2 ticks before note ends
- Higher values = more staccato

### Q Command (0xC4)
Sets percentage-based gate time (0-8 range).

- `Q0` = Full length
- `Q4` = Keyoff at 50% of note length (length * 4 / 8)
- `Q8` = Keyoff at 100% (effectively immediate)

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

## PMD Tempo System

**Source:** PMDWin/pmdwincore/pmdwincore.cpp `comt()`, `calc_tb_tempo()`, `calc_tempo_tb()`

### Two Tempo Values

PMD maintains two related tempo values:

| Variable | Name | Range | Description |
|----------|------|-------|-------------|
| `tempo_d` | Timer B | 0-255 | Raw YM2608 Timer B register value |
| `tempo_48` | Tempo | 18-255 | Human-friendly tempo value (higher = faster) |

### Conversion Formulas

```
tempo_48 = 0x112C / (256 - tempo_d)    // Timer B → Tempo
tempo_d  = 256 - (0x112C / tempo_48)   // Tempo → Timer B
```

Where `0x112C` = 4396 decimal.

**Example calculations:**
- Default `tempo_d = 200` → `tempo_48 = 4396 / 56 ≈ 78`
- `tempo_d = 250` → `tempo_48 = 4396 / 6 ≈ 733` (very fast)
- `tempo_d = 100` → `tempo_48 = 4396 / 156 ≈ 28` (very slow)

### Tempo Commands (0xFC)

| Format | MML | Effect | Function Called |
|--------|-----|--------|-----------------|
| `FC tt` (tt < 251) | `T<value>` | Set Timer B directly | `calc_tb_tempo()` |
| `FC FF tt` | `t<value>` | Set tempo_48 directly | `calc_tempo_tb()` |
| `FC FE tt` (signed) | `T±<value>` | Add to Timer B | `calc_tb_tempo()` |
| `FC FD tt` (signed) | `t±<value>` | Add to tempo_48 | `calc_tempo_tb()` |

**Important:** 
- `T` commands work on raw Timer B (hardware-level)
- `t` commands work on tempo_48 (user-friendly)
- **Higher tempo_48 = FASTER playback** (more ticks per second)

### Gradual Tempo Changes

The `FC FD` command allows gradual tempo changes by adding/subtracting from tempo_48:

```mml
t72      ; Set tempo to 72 (FC FF 48)
t+3      ; Add 3 → 75, slightly faster (FC FD 03)
t-12     ; Subtract 12 → 63, slower (FC FD F4, where F4 = -12 signed)
t-5      ; Subtract 5 → 58, even slower (FC FD FB, where FB = -5 signed)
```

### Furnace Virtual Tempo Mapping

Furnace uses virtual tempo effects to scale playback speed:
- `FDxx` = numerator (current tempo_48)
- `FExx` = denominator (fixed baseline = 75)
- Effective speed = base × (numerator / denominator)

**Mapping strategy:**
```
FD = current tempo_48   (numerator - changes with tempo commands)
FE = 75                  (denominator - fixed baseline for BPM calculation)
```

**Why 75?** This baseline gives correct BPM. For example, Bad Apple:
- tempo_48 = 80 (from `t80` command)
- Ratio = 80/75 = 1.067
- Base Furnace tempo × 1.067 = correct 160 BPM (320 half-note)

**Example sequence (Th02_04):**
| Tick | Command | tempo_48 | Furnace FD/FE | Ratio |
|------|---------|----------|---------------|-------|
| 0 | `FC FF 72` | 72 | FD48 FE4B | 0.96 (4% slower) |
| 1896 | `FC FD +3` | 75 | FD4B FE4B | 1.00 (base) |
| 1920 | `FC FD -12` | 63 | FD3F FE4B | 0.84 (16% slower) |
| 2088 | `FC FD -5` | 58 | FD3A FE4B | 0.77 (23% slower) |
| 2208 | `FC FF 76` | 76 | FD4C FE4B | 1.01 (back to normal) |

### Timer B Hardware Details

The YM2608 Timer B generates interrupts at a rate determined by:
```
Timer B Frequency = 2MHz / (6 × 12 × (256 - tempo_d) × 16)
                  = ~17.36Hz × (256 - tempo_d)⁻¹
```

At default tempo_d=200: ~310 Hz tick rate

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

## PMD Rhythm K/R Channel

**Source:** https://mml-guide.readthedocs.io/pmd/rhythm/

### K Channel
The K channel sequences rhythm **definitions** using `R<n>`:
```
K    l2 R0 R1
```

### R Definitions
R definitions use `@<value>` to select drums. Values are **bit flags**:

| Bit | @Value | SSG Drum |
|-----|--------|----------|
| 0 | @1 | Bass Drum |
| 1 | @2 | Snare Drum 1 |
| 2 | @4 | Low Tom |
| 3 | @8 | Middle Tom |
| 4 | @16 | High Tom |
| 5 | @32 | Rim Shot |
| 6 | @64 | Snare Drum 2 |
| 7 | @128 | Closed Hi-hat |
| 8 | @256 | Open Hi-hat |
| 9 | @512 | Crash Cymbal |
| 10 | @1024 | Ride Cymbal |

**Combining drums:** Add values to play multiple: `@130 = @128 + @2` = Hi-hat + Snare

**SSG Priority:** SSG channel only plays ONE drum - lowest bit takes priority.

**RSS Mapping:**
- @1-@64 trigger RSS drums (BD, SD, Tom, RimShot)
- @4, @8, @16 all trigger Tom RSS with different panning
- @256, @512, @1024 trigger Cymbal RSS with different panning

### Commands Supported by K/R
- `[ : ]` - Loop
- `l` - Default Length
- `L` - Channel Loop
- `t` - Tempo
- `C` - Zenlen
- `T` - Timer

Notes and rests are NOT supported in K channel (only R definitions).

### Rhythm Sequence Data Format

**Source:** pmd_SeqFormat.txt

The K channel references rhythm subroutines (R patterns). Here's how the binary works:

**K Channel (Rhythm Sequence):**
```
00-7F    - Execute rhythm subroutine N (R0, R1, R2...)
80-FF    - Sequence commands (loops, etc.)
```

**R Pattern (Rhythm Subroutine):**
```
00-7F ll - Rest, length ll ticks
80-BF bb ll - Drum note: value = ((cmd << 8) | bb) & 0x3FFF, length ll
C0-FE    - Sequence commands
FF       - Subroutine return
```

### K Channel Binary Format

In compiled .M files, the K channel uses a special format:
- **0x00-0x7F**: R pattern index (R0, R1, R2... R127) - NOT rests!
- **0x80-0xBF**: NOT USED (these would be drum notes in R patterns)
- **0xC0-0xFF**: Commands (same as other channels)

Example: `00 01 F9...` = Play R0, Play R1, then loop start...

### R Pattern Binary Format

Inside R patterns, drum notes use this format:
- **Byte 0**: 0x80-0xBF (drum marker)
- **Byte 1**: Low bits of drum value
- **Byte 2**: Note length in ticks

Drum value calculation: `((byte0 << 8) | byte1) & 0x3FFF`

Example: `81 01 06` = drum @257 (0x0101), length 6 ticks

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

**Source:** pmd_SeqFormat.txt (PMDWin 0.36)

### Implemented Commands ✅

| Byte | MML | Params | Description | Furnace Mapping |
|------|-----|--------|-------------|-----------------|
| 0xFF | @ | 1 | Set instrument | Instrument change |
| 0xFE | q | 1+ | Gate time (ticks before keyoff) | FCxx effect |
| 0xFD | V | 1 | Set volume (00=silent, 7F/0F=max) | Volume column |
| 0xFC | t/T | 1+ | Set tempo (sub-cmds: FD/FE/FF) | Tempo (global) |
| 0xFB | & | 0 | Tie/Hold | Note continuation |
| 0xFA | D | 2 | Set detune (signed 16-bit) | E5xx (detune) |
| 0xF9 | [ | 2 | Loop start | Pattern loop |
| 0xF8 | ] | 4 | Loop end (tt cc oooo) | Pattern loop |
| 0xF7 | : | 2 | Loop escape/break | Pattern loop exit |
| 0xF6 | L | 0 | Master loop start | 0Bxx (song loop) |
| 0xF5 | _ | 1 | Set transposition | Note transpose |
| 0xF4 | ) | 0 | Volume up (+3dB) | Volume change |
| 0xF3 | ( | 0 | Volume down (-3dB) | Volume change |
| 0xF0 | E | 4 | PSG envelope (AR,DR,SR,RR) | SSG volume macro |
| 0xEC | p | 1 | Set panning (0=off,1=R,2=L,3=C) | 08xy (pan) |
| 0xEB | \r | 1 | OPNA rhythm key on | ADPCM-A notes |
| 0xE9 | \< | 1 | OPNA rhythm panning | 08xy on ADPCM-A |
| 0xE7 | _~ | 1 | Relative transpose (add) | Note transpose |
| 0xDA | {} | 3 | Portamento (from,to,len) | E1xy/E2xy (slide) |
| 0xC4 | Q | 1 | Gate time % (0-8, len*Q/8) | FCxx effect |

### Parsed but Not Converted ⚠️

| Byte | MML | Params | Description | Notes |
|------|-----|--------|-------------|-------|
| 0xF2 | M | 4 | LFO/Vibrato (delay,speed,depthA,depthB) | Parsed, not output |
| 0xF1 | * | 1 | LFO switch (enable/disable) | Parsed, not output |
| 0xEF | y | 2 | Direct OPN register write | Parsed, not output |
| 0xEE | w | 1 | PSG noise frequency | Parsed, not output |
| 0xED | P | 1 | PSG tone/noise mask | Parsed, not output |
| 0xEA | \v | 1 | OPNA rhythm volume | Parsed, not output |
| 0xE8 | \V | 1 | OPNA rhythm master volume | Parsed, not output |
| 0xE6 | | 1 | OPNA rhythm volume add | Parsed, not output |
| 0xE5 | | 2 | OPNA rhythm volume add 2 | Parsed, not output |
| 0xE4 | H | 1 | Hardware LFO delay | Parsed, not output |
| 0xE3 | )n | 1 | Volume up by N | Parsed, not output |
| 0xE2 | (n | 1 | Volume down by N | Parsed, not output |
| 0xE1 | | 1 | Hardware LFO set | Parsed, not output |
| 0xE0 | | 1 | Hardware LFO speed (reg 22) | Parsed, not output |
| 0xDF | C | 1 | Set ZENLEN | Parsed, affects timing |
| 0xDE | | 1 | Fine volume up (next note) | Parsed, not output |
| 0xDD | | 1 | Fine volume down (next note) | Parsed, not output |
| 0xDC | | 1 | Set status byte | Parsed, not output |
| 0xDB | | 1 | Add to status byte | Parsed, not output |
| 0xD9 | | 1 | HLFO waveform | Parsed, not output |
| 0xD8 | | 1 | HLFO AMD/PMD | Parsed, not output |
| 0xD7 | | 1 | HLFO frequency | Parsed, not output |
| 0xD6 | | 2 | MD set | Parsed, not output |
| 0xD5 | | 2 | Detune add | Parsed, not output |
| 0xD4 | | 1 | SSG effect | Parsed, not output |
| 0xD3 | | 1 | FM effect | Parsed, not output |
| 0xD2 | | 1 | Fade out | Parsed, not output |
| 0xD1 | | 1 | (Unused) | Parsed, ignored |
| 0xD0 | | 1 | Noise freq add | Parsed, not output |
| 0xCF | s | 1 | FM slot mask | Parsed, not output |
| 0xCE | | 6 | Unknown | Parsed, not output |
| 0xCD | | 5 | SSG envelope extended | Parsed, not output |
| 0xCC | | 1 | Detune extend mode | Parsed, not output |
| 0xCB | MW | 1 | LFO waveform | Parsed, not output |
| 0xCA | | 1 | Extend mode bit 1 | Parsed, not output |
| 0xC9 | | 1 | Extend mode bit 2 | Parsed, not output |
| 0xC8 | | 3 | Slot detune | Parsed, not output |
| 0xC7 | | 3 | Slot detune 2 | Parsed, not output |
| 0xC6 | | 6 | FM3 extended mode init | Parsed, not output |
| 0xC5 | | 1 | Volume mask | Parsed, not output |
| 0xC3 | | 2 | Pan extended | Parsed, not output |
| 0xC2 | MD | 1 | LFO delay | Parsed, not output |
| 0xC1 | | 0 | Slur/legato ignore keyoff | Parsed, not output |
| 0xC0 | | 1+ | Part mask | Parsed, not output |
| 0xBF | MB | 4 | LFO set B | Parsed, not output |
| 0xBE | | 1 | LFO switch B | Parsed, not output |
| 0xBD | | 2 | MD set B | Parsed, not output |
| 0xBC | | 1 | LFO wave B | Parsed, not output |
| 0xBB | | 1 | Extend mode bit 5 | Parsed, not output |
| 0xBA | | 1 | Volume mask B | Parsed, not output |
| 0xB9 | | 1 | LFO delay B | Parsed, not output |
| 0xB8 | | 2 | TL set | Parsed, not output |
| 0xB7 | | 1 | MD count | Parsed, not output |
| 0xB6 | | 1 | FB set | Parsed, not output |
| 0xB5 | | 2 | Slot delay | Parsed, not output |
| 0xB4 | | 16 | PPZ extend | Parsed, not output |
| 0xB3 | q2 | 1 | Gate minimum | Parsed, not output |
| 0xB2 | | 1 | Secondary transpose | Parsed, not output |
| 0xB1 | q3 | 1 | Gate randomizer range | Parsed, not output |

### Special Values

| Byte | Description |
|------|-------------|
| 0x00-0x7F | Note (octave<<4 \| note), followed by length byte |
| 0x0F | Rest (note=F within any octave) |
| 0x80 | Track end |

### Notes on Notes

- High nibble (0-7) = octave
- Low nibble (0-B) = note (C, C#, D, D#, E, F, F#, G, G#, A, A#, B)
- Low nibble 0xC = hold/tie marker
- Low nibble 0xF = rest
- Always followed by 1 length byte

---

## Furnace Note Types

**Source:** furnace/doc/3-pattern/README.md

| Value | Display | Name | Description |
|-------|---------|------|-------------|
| 180 | `OFF` | Note Off | Key off for FM/hardware envelope; note cut otherwise |
| 181 | `===` | Note Release | Triggers macro release AND key off for FM |
| 182 | `REL` | Macro Release | Triggers macro release only, NO key off for FM |

**For PMD conversion:**
- FM channels: Use `OFF` (180) for gate time note cuts
- SSG channels: Use `REL` (182) to trigger software envelope release phase

---

## Furnace ADSR Macro Mode

**Source:** furnace/src/engine/macroInt.cpp

Furnace macros can use ADSR mode instead of sequences. Set `macro.open & 6 == 2`.

**ADSR Parameters (stored in val[0-8]):**

| Index | Name | Description |
|-------|------|-------------|
| val[0] | LOW | Minimum output level |
| val[1] | HIGH | Maximum output level |
| val[2] | AR | Attack Rate (position increases by AR per tick) |
| val[3] | HT | Hold Time (ticks to hold at peak before decay) |
| val[4] | DR | Decay Rate (position decreases by DR per tick) |
| val[5] | SL | Sustain Level (0-255, position to sustain at) |
| val[6] | ST | Sustain Time (ticks before sustain decay starts) |
| val[7] | SR | Sustain Rate (position decreases by SR per tick) |
| val[8] | RR | Release Rate (position decreases by RR on note release) |

**ADSR Flow:**
1. Attack: `pos += AR` each tick until `pos >= 255`
2. Hold: Wait `HT` ticks at peak
3. Decay: `pos -= DR` each tick until `pos <= SL`
4. Sustain: `pos -= SR` each tick (or hold if SR=0)
5. Release (on note off): `pos -= RR` each tick until `pos <= 0`

**Output scaling:** Final value is interpolated between LOW and HIGH based on position (0-255).

---

## Furnace AY-3-8910 Instrument Macros

**Source:** furnace/doc/4-instrument/ay8910.md

The AY-3-8910 (SSG) instrument in Furnace has these macros:

| Macro | Type | Description |
|-------|------|-------------|
| Volume | 0 | Volume sequence (0-15) |
| Arpeggio | 1 | Pitch sequence |
| Duty/Noise Freq | 2 | Noise generator frequency (0-31, **global!**) |
| Waveform | 3 | Sound type selector |
| Pitch | 4 | Fine pitch |
| Phase Reset | 5 | Trigger envelope restart |
| Ex1 (Envelope) | 6 | Hardware envelope settings |
| Ex2 (AutoEnv Num) | 7 | Envelope freq = channel freq × Num |
| Ex3 (AutoEnv Den) | 8 | Envelope freq = channel freq × Den |

**Waveform values:**
- Bit 0: Tone enabled
- Bit 1: Noise enabled  
- Bit 2: Envelope enabled

Common combinations:
- 1 = Tone only
- 2 = Noise only
- 3 = Tone + Noise
- 4 = Envelope only
- 5 = Tone + Envelope
- 6 = Noise + Envelope
- 7 = All

**Envelope settings (Ex1):**
- Bit 0: Enable
- Bit 1: Direction (0=down, 1=up)
- Bit 2: Alternate
- Bit 3: Hold

**Note:** Noise frequency is GLOBAL - affects all SSG channels!

### Working SSG Drum Definitions (from drums.txt)

These are known-working Furnace instruments for SSG drums:

| # | Name | Volume | Arp (Fixed) | Duty (Noise) | Wave | Pattern Note |
|---|------|--------|-------------|--------------|------|--------------|
| 0 | Bass Drum | 15→0 (9 frames) | +28→+19 | 0 | 3→1 | C_2 (36) |
| 1 | Snare Drum | 15→0 (14 frames) | +51→+27 | 24→31 | 3 | C_2 (36) |
| 2 | Low Tom | 15→0 (7 frames) | - | 31 | 3, pitch -16 | E-3 (88) |
| 3 | Mid Tom | 15→0 (7 frames) | - | 31 | 3, pitch -8 | A-3 (93) |
| 4 | High Tom | 15→0 (7 frames) | - | 31 | 3, pitch -4 | F#4 (102) |
| 5 | Rim Shot | 15→0 (9 frames) | - | - | -, pitch -2 | C_2 (36) |
| 6 | Snare 2 | 15→0 (16 frames) | - | 16→31 | 2 (noise only) | C_2 (36) |
| 7 | Hi-Hat Closed | 15→0 (4 frames) | +91 | 31,30 | 3 | G-7 (151) |
| 8 | Hi-Hat Open | 15→0 (17 frames) | +91 | 31,30 | 3 | G-7 (151) |
| 9 | Crash Cymbal | 15→0 (17 frames) | +91 | 0→31 | 3 | G-7 (151) |
| 10 | Ride Cymbal | 15→0 (17 frames) | +91 | 31,30 | 3 | G-7 (151) |

**Arpeggio "Fixed" mode:** Values with 0x40000000 flag are absolute semitones from C-0.
- +28 = E-2, +51 = D#4, +91 = G-7

**Waveform values:** 1=tone, 2=noise, 3=tone+noise

---

## Furnace Note Numbering

**Source:** furnace/src/gui/guiConst.cpp

Furnace supports 180 notes (0-179), including **negative octaves**:

| Note Range | Octave | Display Format | Example |
|------------|--------|----------------|---------|
| 0-11 | -5 | lowercase + underscore | `c_5` |
| 12-23 | -4 | lowercase + underscore | `c_4` |
| 24-35 | -3 | lowercase + underscore | `c_3` |
| 36-47 | -2 | lowercase + underscore | `c_2` = note 36 |
| 48-59 | -1 | lowercase + underscore | `c_1` |
| 60-71 | 0 | uppercase + dash | `C-0` = note 60 |
| 72-83 | 1 | uppercase + dash | `C-1` |
| ... | ... | ... | ... |
| 144-155 | 7 | uppercase + dash | `G-7` = note 151 |
| 156-167 | 8 | uppercase + dash | `C-8` |
| 168-179 | 9 | uppercase + dash | `C-9` |

**Key conversions:**
- MIDI note 60 (middle C) ≈ Furnace `C-4` (note 108)
- `c_2` (note 36) is a very low "trigger note" for drums
- `G-7` (note 151) is used for cymbal/hi-hat high noise

### PMDWin Tom Frequencies

From PMDWin `table.cpp`, the SSG drum definitions use period values:

| Drum | Period (initial) | Approx. Frequency | Sweep |
|------|------------------|-------------------|-------|
| Low Tom | 700 | ~160 Hz (E-3) | Down to ~124 Hz |
| Mid Tom | 500 | ~224 Hz (A-3) | Down to ~167 Hz |
| High Tom | 300 | ~373 Hz (F#4) | Down to ~250 Hz |

SSG frequency formula: `Freq = 1789772.5 / (16 × Period)`

For drums in Furnace, the pattern note is just a "trigger" - the instrument's arpeggio/pitch macros control the actual sound.

---

## Furnace Effect Mapping

| PMD Feature | Furnace Effect | Notes |
|-------------|----------------|-------|
| Detune (D) | E5xx | 80=center, range ±1 semitone |
| Pan (p) | 08xy | x=left, y=right (FF=center, 0F=right, F0=left) |
| Gate time (q/Q) | FCxx | Note release after xx ticks in current row |
| Gate time (different row) | OFF note | OFF (180) placed at release tick |
| SSG envelope release | REL note | REL (182) for macro release |
| Portamento ({}) | E1xy/E2xy | Note slide up/down |
| Stop slide | E200 | Stop any active pitch slide |
| Song loop (L) | 0Bxx | Jump to order xx |
| Note cut | ECxx | Hard cut after xx ticks (not used) |
| Note release | FCxx | Release envelope after xx ticks |

### Pan Value Conversion

| PMD | Furnace 08xy | Description |
|-----|--------------|-------------|
| 0x00 | 0x00 | Off (mute) |
| 0x01 | 0x0F | Right only |
| 0x02 | 0xF0 | Left only |
| 0x03 | 0xFF | Center (both) |

---

## Features NOT Converted

The following PMD features are parsed but not converted to Furnace:

| Feature | Reason |
|---------|--------|
| LFO/Vibrato (M, MA, MB) | Furnace has different vibrato system |
| Hardware LFO (H, port 22h) | YM2608-specific, not directly mappable |
| FM slot mask (s) | FM operator control not in patterns |
| PSG tone/noise mix (P) | Would need per-note instrument changes |
| PSG noise frequency (w) | Global setting, complex to track |
| Direct register writes (y) | Too low-level for pattern conversion |
| Fade out | Furnace handles this differently |
| Extended FM3 mode | Special 4-op mode not supported |
| Part masking | Channel mute, not relevant |
| PPZ/PPS samples | Separate sample system |
| Status bytes | Internal driver state |

These features would require significant effort to implement and are rarely critical for accurate playback.

---

## Resources

- [PMDMML_EN.MAN](https://pigu-a.github.io/pmddocs/pmdmml.htm) - Full MML documentation
- [BotB Lyceum - PMD Effects](https://battleofthebits.com/lyceum/View/Professional%20Music%20Driver%20Effects%20Commands) - Quick reference
- [pedipanol's MML Guide](https://mml-guide.readthedocs.io/pmd/resources/) - Resource links
- pmd_SeqFormat.txt (in readthisforpmdstuff/) - Binary format documentation
- PMDWin source (in readthisforpmdstuff/PMDWin/) - Reference implementation

