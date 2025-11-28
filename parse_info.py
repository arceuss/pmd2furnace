import struct
import pmd2furnace

# Generate fresh PMD fur  
pmd = pmd2furnace.PMDParser('TH4 - LLS/Th04_07.M')
pmd.parse()
pmd_builder = pmd2furnace.FurnaceBuilder(pmd)
data = pmd_builder.build()

o = data.find(b'INFO')
print(f'INFO at: {o}')
o += 4

# Size
size = int.from_bytes(data[o:o+4], 'little')
print(f'Block size: {size}')
o += 4

# Now at content
print(f'Offset now: {o} (should be 40 if INFO at 32)')

# Parse field by field
print(f'time_base: {data[o]}'); o += 1
print(f'speed1: {data[o]}'); o += 1
print(f'speed2: {data[o]}'); o += 1
print(f'arp: {data[o]}'); o += 1
ticks = struct.unpack('<f', data[o:o+4])[0]
print(f'ticks: {ticks}'); o += 4
print(f'patlen: {int.from_bytes(data[o:o+2], "little")}'); o += 2
print(f'ordcnt: {int.from_bytes(data[o:o+2], "little")}'); o += 2
print(f'hlA: {data[o]}'); o += 1
print(f'hlB: {data[o]}'); o += 1
print(f'inscnt: {int.from_bytes(data[o:o+2], "little")}'); o += 2
print(f'wavcnt: {int.from_bytes(data[o:o+2], "little")}'); o += 2
print(f'smpcnt: {int.from_bytes(data[o:o+2], "little")}'); o += 2
print(f'patcnt: {int.from_bytes(data[o:o+4], "little")}'); o += 4

print(f'Offset before chips: {o}')
print(f'Chip bytes: {data[o:o+5].hex()}')
o += 32  # chip IDs
print(f'Volume bytes: {data[o:o+5].hex()}')
o += 32  # volumes  
print(f'Pan bytes: {data[o:o+5].hex()}')
o += 32  # panning
print(f'Flag bytes: {data[o:o+8].hex()}')
o += 128  # flags

print(f'Offset before title: {o}')
print(f'Bytes before title (offset {o-10} to {o+30}):')
print(data[o-10:o+30].hex())
print()
# Try reading as length-prefixed string
title_len = int.from_bytes(data[o:o+2], 'little')
print(f'If title_len at {o}: {title_len}')

# What if there's something before?
for test_offset in [o-4, o-2, o]:
    test_len = int.from_bytes(data[test_offset:test_offset+2], 'little')
    if test_len < 100:
        print(f'At offset {test_offset}: len={test_len}, string={data[test_offset+2:test_offset+2+min(test_len,50)]}')

