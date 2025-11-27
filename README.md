# PMD to Furnace Converter

Converts compiled PMD (Professional Music Driver) `.M` files to Furnace Tracker `.fur` modules.

## Usage

```bash
python pmd2furnace.py input.M [output.fur]
```

If no output file is specified, it will use the input filename with `.fur` extension.

### Batch Conversion

Use `convert_th4.bat` to convert all `.M` files in the `TH4 - LLS` folder.

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

## References

- [PMD Effects Commands (BotB Lyceum)](https://battleofthebits.com/lyceum/View/Professional%20Music%20Driver%20Effects%20Commands)
- [PMD MML Manual](PMDMML_EN.MAN.htm)
- [Furnace Documentation](furnace/doc/)
- [ValleyBell's PMD Format Documentation](https://raw.githubusercontent.com/ValleyBell/MidiConverters/master/pmd_SeqFormat.txt)

## Tools

- `pmd2mml/` - C tool to decompile .M files back to MML source
- `PMDDotNET/` - C# PMD player/compiler (reference implementation)
- `pmdmini/` - C PMD playback library

## License

MIT

