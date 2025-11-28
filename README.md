# PMD/MDT to Furnace Converter

Converts compiled PMD (Professional Music Driver) `.M` files and MDT (MDRV2) `.MDT` files to Furnace Tracker `.fur` modules.

## Usage

### PMD Conversion
```bash
python pmd2furnace.py input.M [output.fur]
```

### MDT Conversion (Touhou 1)
```bash
python mdt2furnace.py input.MDT [output.fur]
```

If no output file is specified, it will use the input filename with `.fur` extension.

## Supported Features

### ✅ Fully Implemented

| Feature | PMD Command | Furnace Equivalent |
|---------|-------------|-------------------|
| FM Notes | `cdefgab` | Note data |
| SSG Notes | `cdefgab` | Note data (octave +1) |
| FM Instruments | `@` | FM (OPN) instruments with all 4 operators |
| Volume | `V` | Volume column |
| Tempo | `t` | Tick rate |
| Transpose | `_`, `_M` | Applied to note values |
| Detune | `D` | `E5xx` pitch effect |
| Pan | `p` | `08xy` panning effect |
| Loops | `[ ]` | Expanded during conversion |
| Loop Break | `:` | Handled correctly |
| Song Loop | `L` | `0Bxx` jump to order |
| Ties | `&` | Extended note duration |
| Portamento | `{ }` | `E1xy`/`E2xy` note slide |
| Gate Time | `q`, `Q` | `ECxx` note cut effect |
| Rest | `r` | Empty rows |

### ⚠️ Partially Implemented

| Feature | PMD Command | Status |
|---------|-------------|--------|
| SSG Envelope | `E al,dd,sr,rr` | Creates instrument with volume macro, but timing may be inaccurate |
| LFO/Vibrato | `M`, `MW` | Parsed but not converted to Furnace `04xy` vibrato |
| Rhythm (K channel) | `R` patterns | Disabled - needs ADPCM-A samples |

### ❌ Not Implemented

| Feature | PMD Command | Notes |
|---------|-------------|-------|
| ADPCM samples | `J` channel | Requires sample data |
| FM3 Extended Mode | | |
| Hardware LFO | `H` commands | |
| SSG Noise | `w`, `P` | |
| PCM86 | | |
| PPZ8 | | |

## Technical Details

### Timing

- PMD uses ZENLEN=96 (96 ticks per whole note)
- Converter uses **TICKS_PER_ROW = 3** and **speed = 3**
- This allows proper handling of 32nd notes (3 ticks each)
- 16th note = 2 rows, 8th note = 4 rows, quarter = 8 rows

### Channel Mapping

| PMD Channel | Furnace Channel | Type |
|-------------|-----------------|------|
| A | 0 | FM |
| B | 1 | FM |
| C | 2 | FM |
| D | 3 | FM |
| E | 4 | FM |
| F | 5 | FM |
| G | 6 | SSG |
| H | 7 | SSG |
| I | 8 | SSG |
| J | 9 | ADPCM-B |
| K | 10+ | Rhythm |

### Octave Mapping

- FM channels: PMD octave + 5 → Furnace note
- SSG channels: PMD octave + 6 → Furnace note (one octave higher)

### Gate Time (q command)

PMD's `q` command controls note duration:
- `q0` = staccato (note cuts immediately)
- `q8` = full length (default)
- `q6` = note plays for 6/8 (75%) of its duration

Converted to Furnace `ECxx` effect where `xx = (note_length * gate_time) / 8`

### SSG Envelope (E command)

PMD format: `E al, dd, sr, rr`
- `al` = Attack length (ticks at full volume)
- `dd` = Decay depth (volume decrease per step, -15 to 15)
- `sr` = Sustain rate (ticks per decay step)
- `rr` = Release rate (ticks per release step)

Currently creates Furnace instruments with volume macros, but the timing calculation needs refinement to match PMD's internal clock.

## Known Issues

1. **SSG envelope timing** - The volume macro speed doesn't perfectly match PMD's envelope behavior
2. **LFO/Vibrato** - Not converted, songs with heavy vibrato will sound flat
3. **Rhythm channel** - Disabled, requires ADPCM-A instrument setup
4. **Very fast notes** - Notes shorter than 3 ticks may still overlap

## Dependencies

- Python 3.6+
- No external packages required

---

# MDT (MDRV2) Support

> ⚠️ **MDT support is experimental and largely broken.** The MDT format (used in Touhou 1) is very poorly documented compared to PMD, and the converter produces incorrect output for most files. This is unlikely to be fixed unless better documentation surfaces.

MDT is the music driver used in Touhou 1 (Highly Responsive to Prayers).

### Status: ❌ Broken

The MDT converter (`mdt2furnace.py`) exists but does not produce usable output. Issues include:
- Incorrect note parsing
- Broken timing/tempo
- Missing instrument data
- Garbled pattern data

If you need to work with Touhou 1 music, consider using [MDTParsingTools](https://github.com/OPNA2608/MDTParsingTools) to decompile to MML and manually recreate in Furnace.

## References

- [PMD Effects Commands (BotB Lyceum)](https://battleofthebits.com/lyceum/View/Professional%20Music%20Driver%20Effects%20Commands)
- [PMD MML Manual (pigu-a)](https://pigu-a.github.io/pmddocs/pmdmml.htm)
- [PMDWin Source Code (C60)](http://c60.la.coocan.jp/download.html) - Official PMDWin player/DLL source
- [MDRV2 Documentation](https://en.touhouwiki.net/wiki/User:Mami/Music_Dev/Mdrv2/Md2mml)
- [Furnace Tracker](https://github.com/tildearrow/furnace)
- [ValleyBell's PMD Format Documentation](https://raw.githubusercontent.com/ValleyBell/MidiConverters/master/pmd_SeqFormat.txt)

## Useful External Tools

- [pmd2mml](https://github.com/loveemu/pmd2mml) - Decompile .M files back to MML source
- [PMDDotNET](https://github.com/kuma4649/PMDDotNET) - C# PMD player/compiler
- [MDTParsingTools](https://github.com/OPNA2608/MDTParsingTools) - Python MDT parser/decompiler

## License

MIT

