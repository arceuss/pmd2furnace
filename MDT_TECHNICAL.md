# MDT (MDRV2) Technical Documentation

This file documents the MDRV2 MDT format used in Touhou 1 (Highly Responsive to Prayers).

**Source:** MDTParsingTools by Lmocinemod, HertzDevil's decompiler

## Overview

MDRV2 is a music driver for FM Towns and PC-98 that uses the YM2608 (OPNA) chip.
Unlike PMD, MDT does NOT have SSG drums - rhythm is handled by the OPNA's built-in
rhythm sound source (RSS).

## File Structure

```
Offset  Size  Description
0x00    2     Magic bytes (02 03)
0x02    2     Channel count
0x04    2     Chip type (0=OPM, 1=OPN, 2=OPLL)
0x06    2*n   Channel entries (location, id) for each channel
...     2     FM instrument definitions location
...     2     SSG envelope definitions location
...     2     Title location
```

## Channel IDs

| ID | Channel | Type |
|----|---------|------|
| 0x80 | A | FM |
| 0x81 | B | FM |
| 0x82 | C | FM |
| 0x83 | D | FM |
| 0x84 | E | FM |
| 0x85 | F | FM |
| 0x40 | I | SSG |
| 0x41 | J | SSG |
| 0x42 | K | SSG |
| 0x10 | L | Rhythm |

## Binary Commands

### Notes and Rests
| Byte | Command | Description |
|------|---------|-------------|
| 0x00-0x7F | Note | High nibble = octave, low nibble = note (0-11) |
| 0x90 | Rest | Followed by length byte |
| 0x91 | Tie | `&` - tie to next note |

### Loops
| Byte | Command | Params | Description |
|------|---------|--------|-------------|
| 0xE0 | `\|:` | count | Pipe-colon loop start |
| 0xE1 | `:` | - | Skip to loop end on last iteration |
| 0xE2 | `:\|` | - | Pipe-colon loop end |
| 0xE4 | `[` | count | Bracket loop start |
| 0xE5 | `]` | - | Bracket loop end |
| 0xF6 | `[:` | count, ptr | Bracket-colon loop start |
| 0xF7 | `:]` | ptr | Bracket-colon loop end |
| 0xF9 | `\|` | ptr | Skip to loop end (bracket-colon) |
| 0xF3 | `\` | ptr | Infinite loop point |

### Control Commands
| Byte | Command | Params | Description |
|------|---------|--------|-------------|
| 0xE3 | `/` | - | Force note-off |
| 0xE6 | `^` | int8 | Detune |
| 0xE7 | `@^` | int8 | Transpose |
| 0xE9 | `t` | tempo | Tempo (BPM) |
| 0xEA | `Q` | value | Articulation/gate time |
| 0xEB | `@` | inst | Instrument change |
| 0xEC | `V`/`@V` | vol | Volume |
| 0xEF | `W` | value | LFO delay (FM) / Noise freq (SSG) |
| 0xF0 | `_` | time | Fade in/out |
| 0xF1 | `P` | pan | Panning |
| 0xF4 | `@V+` | delta | Volume increase |
| 0xF5 | `@V-` | delta | Volume decrease |
| 0xF8 | `Z` | value | Sync-work value |
| 0xFA | `U` | ptr | Macro/subroutine call |
| 0xFF | - | - | End of channel |

### LFO Commands
| Byte | Command | Params | Description |
|------|---------|--------|-------------|
| 0xE8 | `SA` | a,b,c,d | Amplitude LFO (triangle) |
| 0xED | `S` | a,b,c,d | Pitch LFO (triangle) |
| 0xFB | `SP` | a,b,c,d | Pitch LFO (sawtooth) |
| 0xFC | `SA` | a,b,c,d | Amplitude LFO (sawtooth) |
| 0xFD | `SH` | a,b,c,d | Hardware LFO |

### Portamento
| Byte | Command | Params | Description |
|------|---------|--------|-------------|
| 0xF2 | `()` | start, duration, change | Portamento/glide |

## SSG Instrument Format (@)

For SSG channels, the @ command sets both noise mix and envelope:
- Bits 7-6: Noise mix (bit 7 = noise off, bit 6 = tone off)
- Bits 5-0: Envelope number (0-63)

Noise mix values:
- 0 = Neither (silent)
- 1 = Tone only
- 2 = Noise only
- 3 = Tone + Noise

## FM Instrument Definition (32 bytes)

```
Offset  Description
0-5     Channel parameters (SY, SP, AMD, WF/OM, AF/PAN, AMS/PMS)
6-9     Operator 1 (M1) - ML/DT1
10-13   Operator 3 (C1) - ML/DT1
14-17   Operator 2 (M2) - ML/DT1
18-21   Operator 4 (C2) - ML/DT1
...     (continues for OL, AR/KS, DR/AME, SR/DT2, RR/SL)
30      PMD
31      (unused/garbage)
```

## SSG Envelope Definition (6 bytes)

Format: `P<n> = param1, param2, param3, param4, param5, param6`

## Timing

- ZENLEN (whole note): 192 ticks
- Note lengths follow standard musical divisions

| Ticks | Note |
|-------|------|
| 192 | Whole (1) |
| 96 | Half (2) |
| 48 | Quarter (4) |
| 24 | 8th (8) |
| 12 | 16th (16) |
| 6 | 32nd (32) |

Dotted notes are supported (e.g., 72 = dotted quarter)

## Key Differences from PMD

| Feature | PMD | MDT |
|---------|-----|-----|
| SSG Drums | Yes (K/R channels) | No |
| Rhythm | ADPCM-A or SSG drums | OPNA RSS only |
| File extension | .M, .M2 | .MDT |
| Whole note | 96 ticks | 192 ticks |
| Loop syntax | `[]` only | `[]`, `\|:\|`, `[:]` |
| Macros | Limited | Full subroutine support |

## Furnace Mapping

| MDT Channel | Furnace Channel | Type |
|-------------|-----------------|------|
| A (0x80) | 0 | FM |
| B (0x81) | 1 | FM |
| C (0x82) | 2 | FM |
| D (0x83) | 3 | FM |
| E (0x84) | 4 | FM |
| F (0x85) | 5 | FM |
| I (0x40) | 6 | SSG |
| J (0x41) | 7 | SSG |
| K (0x42) | 8 | SSG |
| L (0x10) | 9 | ADPCM-A (Rhythm) |

## Resources

- [MDRV2 Documentation (English)](https://en.touhouwiki.net/wiki/User:Mami/Music_Dev/Mdrv2/Md2mml)
- [MDTParsingTools](https://github.com/Lmocinemod/MDTParsingTools)
- [HertzDevil's MDT Decompiler](https://gist.github.com/HertzDevil/036304c692b0f26b7a9d7cfe1126a0ac)

