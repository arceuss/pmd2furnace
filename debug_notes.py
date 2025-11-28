import sys
sys.path.insert(0, 'MDTParsingTools')
from mdt_decomp_rip import parse_mdt

song = parse_mdt('REIMU.MDT', cut_time=True)

# Check channel A (FM)
ch = song.channels[0]
print(f"Channel A (id={ch.id}):")
print(f"Total events: {len(ch.events)}")

# Find first actual notes
note_count = 0
for i, ev in enumerate(ch.events):
    if ev[0][0] in 'abcdefg' or (len(ev[0]) > 1 and ev[0][1] in 'abcdefg'):
        print(f"  Note {note_count}: {ev}")
        note_count += 1
        if note_count >= 20:
            break

