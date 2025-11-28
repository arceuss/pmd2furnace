import pmd2furnace

pmd = pmd2furnace.PMDParser('TH4 - LLS/Th04_07.M')
pmd.parse()
print(f'Title: [{pmd.header.title}]')
print(f'Title repr: {repr(pmd.header.title)}')

# Check what pack_string produces
title = pmd.header.title or 'PMD Import'
result = pmd2furnace.pack_string(title)
print(f'pack_string output: {result.hex()}')
print(f'pack_string len: {len(result)}')

