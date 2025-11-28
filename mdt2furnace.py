#!/usr/bin/env python3
"""
MDT to Furnace Converter
Converts MDRV2 .MDT files (Touhou 1) to Furnace .fur modules
"""

import sys
import struct

# Add MDTParsingTools to path
sys.path.insert(0, 'MDTParsingTools')
from mdt_decomp_rip import parse_mdt, Song, Channel, FMInstrument

# =============================================================================
# Constants
# =============================================================================

TARGET_FURNACE_VERSION = 228
CHIP_YM2608 = 0x8E

# Channel mapping
CHANNEL_MAP = {
    0x80: 0,   # FM-A
    0x81: 1,   # FM-B
    0x82: 2,   # FM-C
    0x83: 3,   # FM-D
    0x84: 4,   # FM-E
    0x85: 5,   # FM-F
    0x40: 6,   # SSG-I
    0x41: 7,   # SSG-J
    0x42: 8,   # SSG-K
    0x10: 9,   # Rhythm
}

NOTE_MAP = {'c': 0, 'c+': 1, 'd': 2, 'd+': 3, 'e': 4, 'f': 5, 
            'f+': 6, 'g': 7, 'g+': 8, 'a': 9, 'a+': 10, 'b': 11}

# =============================================================================
# Packing helpers
# =============================================================================

def pack_byte(v): return struct.pack('<B', v & 0xFF)
def pack_short(v): return struct.pack('<H', v & 0xFFFF)
def pack_long(v): return struct.pack('<I', v & 0xFFFFFFFF)
def pack_qlong(v): return struct.pack('<Q', v & 0xFFFFFFFFFFFFFFFF)
def pack_float(v): return struct.pack('<f', v)
def pack_string(s): return s.encode('utf-8') + b'\0'
def bl_length(parts): return sum(len(p) for p in parts)

# =============================================================================
# Note/Length Parsing
# =============================================================================

def parse_note_event(cmd: str, octave: int, is_ssg: bool) -> tuple:
    """Parse a note string like 'd16', '>c8', '<a+4.' 
    Returns (note_value, length_ticks, new_octave) or None if not a note"""
    
    s = cmd
    new_oct = octave
    
    # Handle octave prefix
    while s and s[0] in '<>':
        if s[0] == '>':
            new_oct += 1
        else:
            new_oct -= 1
        s = s[1:]
    
    if not s or s[0] not in 'abcdefg':
        return None
    
    # Parse note name
    if len(s) > 1 and s[1] == '+':
        note_name = s[:2]
        length_str = s[2:]
    else:
        note_name = s[0]
        length_str = s[1:]
    
    note_val = NOTE_MAP.get(note_name, 0)
    
    # SSG plays one octave higher
    final_oct = new_oct + 1 if is_ssg else new_oct
    
    # Parse length
    ticks = parse_length(length_str)
    
    return (note_val, final_oct, ticks, new_oct)


def parse_length(s: str) -> int:
    """Parse length string to ticks. MDT uses 192 ticks/whole, cut time doubles."""
    if not s:
        return 96  # Default half note in cut time
    
    s = s.strip()
    
    # Raw ticks with %
    if s.startswith('%'):
        try:
            return int(s[1:]) * 2  # Cut time
        except:
            return 96
    
    # Standard fraction
    try:
        dot = 1.5 if s.endswith('.') else 1.0
        if s.endswith('.'):
            s = s[:-1]
        base = int(s)
        ticks = int((192 / base) * dot) * 2  # Cut time doubles
        return ticks
    except:
        return 96

# =============================================================================
# Main Converter
# =============================================================================

class MDT2Furnace:
    def __init__(self, song: Song):
        self.song = song
        self.channel_count = 16
        self.pattern_length = 64
        self.ticks_per_row = 12  # 192 ticks/whole * 2 (cut time) / 32 rows per whole = 12
        self.instruments = []
        self.patterns = [[] for _ in range(self.channel_count)]
        self.order_count = 1
        self.effects_count = [2] * self.channel_count
        self.tempo = 150
        
    def convert(self) -> bytes:
        """Convert MDT to Furnace"""
        print("\nBuilding Furnace module...")
        
        # Create FM instruments
        for i, fm in enumerate(self.song.fm):
            self.instruments.append(self._make_fm_instrument(fm, i))
        
        # Convert each channel
        for ch in self.song.channels:
            self._convert_channel(ch)
        
        # Ensure all channels have same pattern count
        max_pats = max(len(p) for p in self.patterns) if any(self.patterns) else 1
        self.order_count = max_pats
        
        for ch_idx in range(self.channel_count):
            while len(self.patterns[ch_idx]) < max_pats:
                self.patterns[ch_idx].append(self._empty_pattern(ch_idx, len(self.patterns[ch_idx])))
        
        return self._build_file()
    
    def _convert_channel(self, channel: Channel):
        """Convert a single MDT channel to Furnace patterns"""
        if channel.id not in CHANNEL_MAP:
            return
        
        fur_ch = CHANNEL_MAP[channel.id]
        is_ssg = bool(channel.id & 0x40)
        
        # Collect notes with their tick positions
        notes = []
        tick = 0
        octave = 4
        current_ins = None
        
        # Loop handling
        loop_stack = []
        return_stack = []
        octave_stack = []
        
        i = 0
        events = channel.events
        
        while i < len(events):
            ev = events[i]
            if not isinstance(ev, list) or not ev:
                i += 1
                continue
            
            cmd = ev[0]
            if not isinstance(cmd, str):
                i += 1
                continue
            
            # Note event
            if cmd[0] in '<>abcdefg':
                result = parse_note_event(cmd, octave, is_ssg)
                if result:
                    note_val, note_oct, length, octave = result
                    fur_note = note_oct * 12 + note_val
                    notes.append({
                        'tick': tick,
                        'note': fur_note,
                        'ins': current_ins,
                        'len': length
                    })
                    current_ins = None
                    tick += length
                i += 1
                
            # Rest
            elif cmd[0] == 'r':
                length = parse_length(cmd[1:])
                tick += length
                i += 1
                
            # Octave
            elif cmd == 'O':
                octave = ev[1] if len(ev) > 1 else 4
                i += 1
                
            # Tempo
            elif cmd in ('t', '@T'):
                if len(ev) > 1:
                    self.tempo = ev[1]
                i += 1
                
            # Instrument
            elif cmd == '@':
                current_ins = ev[1] if len(ev) > 1 else 0
                i += 1
                
            # Loop start
            elif cmd in ('[', '|:', '[:'):
                count = ev[1] if len(ev) > 1 else 2
                loop_stack.append(count - 1)
                return_stack.append(i + 1)
                octave_stack.append(octave)
                i += 1
                
            # Loop end
            elif cmd in (']', ':|', ':]'):
                if loop_stack:
                    if loop_stack[-1] > 0:
                        loop_stack[-1] -= 1
                        i = return_stack[-1]
                        octave = octave_stack[-1]
                        continue
                    else:
                        loop_stack.pop()
                        return_stack.pop()
                        octave_stack.pop()
                i += 1
                
            # Tie (extend previous note)
            elif cmd == '&':
                i += 1
                
            else:
                i += 1
        
        # Convert notes to patterns
        self._notes_to_patterns(notes, fur_ch)
    
    def _notes_to_patterns(self, notes, fur_ch):
        """Convert note list to Furnace patterns"""
        if not notes:
            return
        
        pat_data = bytearray()
        pat_idx = 0
        row = 0
        
        for note in notes:
            target_row = note['tick'] // self.ticks_per_row
            target_pat = target_row // self.pattern_length
            
            # Create patterns as needed
            while target_pat > pat_idx:
                # Fill rest of current pattern
                skip = self.pattern_length - row
                if skip > 0:
                    self._write_skip(pat_data, skip)
                pat_data.append(0xFF)
                self.patterns[fur_ch].append(self._make_pattern(fur_ch, pat_idx, bytes(pat_data)))
                pat_idx += 1
                pat_data = bytearray()
                row = 0
            
            # Skip to note position
            row_in_pat = target_row - (pat_idx * self.pattern_length)
            skip = row_in_pat - row
            if skip > 0:
                self._write_skip(pat_data, skip)
                row += skip
            
            # Write note
            flags = 0x01  # Has note
            if note['ins'] is not None:
                flags |= 0x02
            
            pat_data.append(flags)
            pat_data.append(note['note'] & 0xFF)
            if note['ins'] is not None:
                pat_data.append(note['ins'] & 0xFF)
            row += 1
        
        # Finish last pattern
        skip = self.pattern_length - row
        if skip > 0:
            self._write_skip(pat_data, skip)
        pat_data.append(0xFF)
        self.patterns[fur_ch].append(self._make_pattern(fur_ch, pat_idx, bytes(pat_data)))
    
    def _write_skip(self, data, count):
        while count > 0:
            s = min(count, 255)
            data.append(0x01)
            data.append(s - 1)
            count -= s
    
    def _make_pattern(self, ch, idx, data):
        block = [b'PATN', pack_long(0), pack_byte(0), pack_byte(ch), 
                 pack_short(idx), pack_string(""), data]
        block[1] = pack_long(bl_length(block[2:]))
        return b''.join(block)
    
    def _empty_pattern(self, ch, idx):
        data = bytes([0x01, self.pattern_length - 1, 0xFF])
        return self._make_pattern(ch, idx, data)
    
    def _make_fm_instrument(self, fm: FMInstrument, idx: int):
        """Create Furnace FM instrument"""
        p = fm.params
        ch = p[0]
        
        alg = ch[0] & 0x07
        fb = (ch[0] >> 3) & 0x07
        
        fm_feat = [b'FM', pack_short(0), pack_byte(0x4F), pack_byte((fb & 7) | ((alg & 7) << 4)),
                   pack_byte(0), pack_byte(0)]
        
        for oi in [0, 2, 1, 3]:
            op = p[oi + 1]
            fm_feat.extend([
                pack_byte((op[7] & 0x0F) | (((3 + op[8]) & 7) << 4)),
                pack_byte(op[5] & 0x7F),
                pack_byte((op[0] & 0x1F) | ((op[6] & 3) << 6)),
                pack_byte((op[1] & 0x1F) | ((op[10] & 1) << 7)),
                pack_byte((op[2] & 0x1F) | (2 << 5)),
                pack_byte((op[3] & 0x0F) | ((op[4] & 0x0F) << 4)),
                pack_byte(0), pack_byte(0)
            ])
        
        fm_feat[1] = pack_short(bl_length(fm_feat[2:]))
        
        name_feat = [b'NA', pack_short(0), pack_string(f"FM {idx}")]
        name_feat[1] = pack_short(bl_length(name_feat[2:]))
        
        ins = [b'INS2', pack_long(0), pack_short(TARGET_FURNACE_VERSION), pack_short(1),
               b''.join(name_feat), b''.join(fm_feat), b'EN']
        ins[1] = pack_long(bl_length(ins[2:]))
        return b''.join(ins)
    
    def _build_file(self):
        """Build complete .fur file"""
        file = bytearray()
        
        # Header
        hdr = [b'-Furnace module-', pack_short(TARGET_FURNACE_VERSION), pack_short(0),
               pack_long(0), pack_qlong(0)]
        hdr[3] = pack_long(bl_length(hdr))
        file += b''.join(hdr)
        
        pat_count = sum(len(p) for p in self.patterns)
        
        # Clean title
        title = self.song.title.strip() if self.song.title else 'MDT'
        if '  ' in title:
            title = title.split('  ')[0].strip()
        
        # INFO block
        info = [b'INFO', pack_long(0),
                pack_byte(0), pack_byte(6), pack_byte(6), pack_byte(1),
                pack_float(60.0),
                pack_short(self.pattern_length), pack_short(self.order_count),
                pack_byte(4), pack_byte(16),
                pack_short(len(self.instruments)), pack_short(0), pack_short(0),
                pack_long(pat_count),
                pack_byte(CHIP_YM2608), pack_byte(0) * 31,
                pack_byte(0x40) * 32, pack_byte(0) * 32, pack_long(0) * 32,
                pack_string(title), pack_string(''),
                pack_float(440.0), pack_byte(0) * 20]
        
        ins_ptr = [pack_long(0)] * len(self.instruments)
        pat_ptr = [pack_long(0)] * pat_count
        
        orders = b''.join(pack_byte(n) for n in range(self.order_count)) * self.channel_count
        
        info2 = [orders,
                 b''.join(pack_byte(n) for n in self.effects_count),
                 pack_byte(3) * self.channel_count,
                 pack_byte(0) * self.channel_count,
                 pack_string('') * self.channel_count,
                 pack_string('') * self.channel_count,
                 pack_string(''), pack_float(1.0), pack_byte(0) * 28,
                 pack_short(self.tempo), pack_short(75),
                 pack_string(''), pack_string(''),
                 pack_byte(0), pack_byte(0) * 3,
                 pack_string('PC-98'), pack_string(''), pack_string(''), pack_string(''),
                 pack_string('PC-98'), pack_string(''),
                 pack_float(1.0), pack_float(0.0), pack_float(0.0),
                 pack_long(0), pack_byte(1), pack_byte(0) * 8,
                 pack_byte(1), pack_byte(6), pack_byte(0) * 15, pack_byte(0),
                 pack_long(0), pack_long(0), pack_long(0)]
        
        info_size = bl_length(info[2:]) + bl_length(ins_ptr) + bl_length(pat_ptr) + bl_length(info2)
        info[1] = pack_long(info_size)
        
        fptr = len(file) + info_size + 8
        
        # Asset dirs
        def make_adir(cnt):
            if cnt == 0:
                a = [b'ADIR', pack_long(0), pack_long(0)]
            else:
                a = [b'ADIR', pack_long(0), pack_long(1), pack_string(''),
                     pack_short(cnt), b''.join(pack_byte(x) for x in range(cnt))]
            a[1] = pack_long(bl_length(a[2:]))
            return b''.join(a)
        
        ins_adir = make_adir(len(self.instruments))
        info2[-3] = pack_long(fptr); fptr += len(ins_adir)
        
        empty_adir = make_adir(0)
        info2[-2] = pack_long(fptr); fptr += len(empty_adir)
        info2[-1] = pack_long(fptr); fptr += len(empty_adir)
        
        for i, ins in enumerate(self.instruments):
            ins_ptr[i] = pack_long(fptr)
            fptr += len(ins)
        
        all_pats = []
        for ch_pats in self.patterns:
            all_pats.extend(ch_pats)
        
        for i, pat in enumerate(all_pats):
            pat_ptr[i] = pack_long(fptr)
            fptr += len(pat)
        
        file += b''.join(info + ins_ptr + pat_ptr + info2)
        file += ins_adir + empty_adir + empty_adir
        file += b''.join(self.instruments)
        file += b''.join(all_pats)
        
        return bytes(file)


def main():
    if len(sys.argv) < 2:
        print("Usage: python mdt2furnace.py <input.mdt> [output.fur]")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file.rsplit('.', 1)[0] + '.fur'
    
    print(f"Parsing: {input_file}")
    
    try:
        song = parse_mdt(input_file, cut_time=True)
        
        title = song.title.strip().split('  ')[0] if song.title else 'Unknown'
        print(f"Title: {title}")
        print(f"Channels: {len(song.channels)}")
        print(f"FM Instruments: {len(song.fm)}")
        
        converter = MDT2Furnace(song)
        fur_data = converter.convert()
        
        with open(output_file, 'wb') as f:
            f.write(fur_data)
        
        print(f"\nWritten: {output_file} ({len(fur_data)} bytes)")
        print("Done!")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
