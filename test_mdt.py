import mdt2furnace

mdt = mdt2furnace.MDTParser('REIMU.MDT')
print(f'Raw title: {repr(mdt.title)}')

# Check channels and find actual notes
for ch in mdt.channels[:2]:
    print(f'\n{ch.name} (fur ch {ch.fur_channel}):')
    notes_found = 0
    for i, ev in enumerate(ch.events):
        if isinstance(ev, mdt2furnace.MDTNote) and not ev.is_rest:
            print(f'  Event {i}: note={ev.note}, oct={ev.octave}, len={ev.length}')
            notes_found += 1
            if notes_found >= 10:
                break
    print(f'  Total events: {len(ch.events)}')

