import sys
sys.path.insert(0, 'MDTParsingTools')
from mdt_decomp_rip import parse_mdt

song = parse_mdt('REIMU.MDT', cut_time=True)

print(f"Title: {song.title}")
print(f"Channels: {len(song.channels)}")

# Look at first channel's events
ch = song.channels[0]
print(f"\nChannel {ch.id} location: {ch.location}")
print(f"Number of events: {len(ch.events)}")
print(f"\nFirst 20 events:")
for i, ev in enumerate(ch.events[:20]):
    print(f"  {i}: {type(ev).__name__} = {ev}")

