#!/usr/bin/env python3
"""
MDT to Furnace Converter
Converts MDRV2 .MDT files (Touhou 1) to Furnace .fur modules

Based on MDTParsingTools by Lmocinemod and HertzDevil's decompiler
"""

import struct
import sys
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field

# =============================================================================
# Constants
# =============================================================================

TARGET_FURNACE_VERSION = 227

# MDT Channel IDs
CHANNEL_IDS = {
    0x80: ('FM-A', 'fm', 0),
    0x81: ('FM-B', 'fm', 1),
    0x82: ('FM-C', 'fm', 2),
    0x83: ('FM-D', 'fm', 3),
    0x84: ('FM-E', 'fm', 4),
    0x85: ('FM-F', 'fm', 5),
    0x40: ('SSG-I', 'ssg', 6),
    0x41: ('SSG-J', 'ssg', 7),
    0x42: ('SSG-K', 'ssg', 8),
    0x10: ('Rhythm-L', 'rhythm', 9),
}

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# Ticks per row in Furnace (MDT uses 192 ticks per whole note)
TICKS_PER_ROW = 6  # Allows 32nd notes (6 ticks) to have their own row

# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class MDTNote:
    note: int  # 0-11
    octave: int  # 0-7
    length: int  # in ticks
    is_rest: bool = False

@dataclass
class MDTCommand:
    cmd: int
    params: List[int] = field(default_factory=list)

@dataclass
class MDTChannel:
    id: int
    name: str
    channel_type: str
    fur_channel: int
    location: int
    events: List[Any] = field(default_factory=list)
    loop_pos: int = -1

@dataclass
class MDTFMInstrument:
    id: int
    params: List[List[int]]  # 5 rows: channel + 4 operators

@dataclass
class MDTSSGEnvelope:
    id: int
    params: List[int]  # 6 parameters

# =============================================================================
# Helper Functions
# =============================================================================

def pack_byte(val: int) -> bytes:
    return struct.pack('<B', val & 0xFF)

def pack_short(val: int) -> bytes:
    return struct.pack('<H', val & 0xFFFF)

def pack_long(val: int) -> bytes:
    return struct.pack('<I', val & 0xFFFFFFFF)

def pack_string(s: str) -> bytes:
    encoded = s.encode('utf-8')
    return pack_short(len(encoded)) + encoded

def bl_length(parts) -> int:
    return sum(len(p) if isinstance(p, bytes) else len(p) for p in parts)

# =============================================================================
# MDT Parser
# =============================================================================

class MDTParser:
    def __init__(self, filename: str):
        self.filename = filename
        with open(filename, 'rb') as f:
            self.data = f.read()
        
        self.channels: List[MDTChannel] = []
        self.fm_instruments: List[MDTFMInstrument] = []
        self.ssg_envelopes: List[MDTSSGEnvelope] = []
        self.title = ""
        self.tempo = 120
        
        self._parse()
    
    def _uint8(self, offset: int) -> int:
        return self.data[offset]
    
    def _int8(self, offset: int) -> int:
        val = self.data[offset]
        return val - 0x100 if val >= 0x80 else val
    
    def _uint16(self, offset: int) -> int:
        return self.data[offset] | (self.data[offset + 1] << 8)
    
    def _int16(self, offset: int) -> int:
        val = self._uint16(offset)
        return val - 0x10000 if val >= 0x8000 else val
    
    def _parse(self):
        # Skip magic bytes (02 03)
        offset = 2
        
        # Channel count and chip type
        channel_count = self._uint16(offset)
        offset += 2
        self.chip = self._uint16(offset)
        offset += 2
        
        # Read channel entries
        for _ in range(channel_count):
            location = self._uint16(offset)
            offset += 2
            ch_id = self._uint16(offset)
            offset += 2
            
            if ch_id != 0 and ch_id in CHANNEL_IDS:
                name, ch_type, fur_ch = CHANNEL_IDS[ch_id]
                self.channels.append(MDTChannel(
                    id=ch_id,
                    name=name,
                    channel_type=ch_type,
                    fur_channel=fur_ch,
                    location=location
                ))
        
        # File locations
        fm_def_loc = self._uint16(offset)
        offset += 2
        ssg_def_loc = self._uint16(offset)
        offset += 2
        title_loc = self._uint16(offset)
        offset += 2
        
        # Parse title
        self._parse_title(title_loc)
        
        # Parse channels
        for channel in self.channels:
            self._parse_channel(channel)
        
        # Parse FM instruments
        self._parse_fm_instruments(fm_def_loc, ssg_def_loc)
        
        # Parse SSG envelopes
        self._parse_ssg_envelopes(ssg_def_loc)
    
    def _parse_title(self, title_loc: int):
        title_bytes = []
        offset = title_loc
        while offset < len(self.data):
            b = self.data[offset]
            if b == ord('$'):
                break
            title_bytes.append(b)
            offset += 1
        try:
            self.title = bytes(title_bytes).decode('shift-jis', errors='replace')
        except:
            self.title = "Unknown"
    
    def _parse_channel(self, channel: MDTChannel):
        offset = channel.location
        octave = 4
        
        while offset < len(self.data):
            cmd = self.data[offset]
            offset += 1
            
            if cmd == 0xFF:  # End
                break
            elif cmd < 0x80:
                # Note
                note_val = cmd & 0x0F
                note_octave = (cmd >> 4) & 0x07
                length = self.data[offset]
                offset += 1
                
                channel.events.append(MDTNote(
                    note=note_val,
                    octave=note_octave,
                    length=length
                ))
                octave = note_octave
            elif cmd == 0x90:
                # Rest
                length = self.data[offset]
                offset += 1
                channel.events.append(MDTNote(
                    note=0, octave=octave, length=length, is_rest=True
                ))
            elif cmd == 0x91:
                # Tie
                channel.events.append(MDTCommand(cmd=0x91))
            elif cmd == 0xE0:
                # Loop start |:
                count = self.data[offset]
                offset += 1
                channel.events.append(MDTCommand(cmd=0xE0, params=[count]))
            elif cmd == 0xE1:
                # Loop break :
                channel.events.append(MDTCommand(cmd=0xE1))
            elif cmd == 0xE2:
                # Loop end :|
                channel.events.append(MDTCommand(cmd=0xE2))
            elif cmd == 0xE3:
                # Force note-off
                channel.events.append(MDTCommand(cmd=0xE3))
            elif cmd == 0xE4:
                # Loop start [
                count = self.data[offset]
                offset += 1
                channel.events.append(MDTCommand(cmd=0xE4, params=[count]))
            elif cmd == 0xE5:
                # Loop end ]
                channel.events.append(MDTCommand(cmd=0xE5))
            elif cmd == 0xE6:
                # Detune
                val = self._int8(offset)
                offset += 1
                channel.events.append(MDTCommand(cmd=0xE6, params=[val]))
            elif cmd == 0xE7:
                # Transpose
                val = self._int8(offset)
                offset += 1
                channel.events.append(MDTCommand(cmd=0xE7, params=[val]))
            elif cmd == 0xE8:
                # Amplitude LFO
                params = list(self.data[offset:offset+4])
                offset += 4
                channel.events.append(MDTCommand(cmd=0xE8, params=params))
            elif cmd == 0xE9:
                # Tempo
                tempo = self.data[offset]
                offset += 1
                self.tempo = tempo
                channel.events.append(MDTCommand(cmd=0xE9, params=[tempo]))
            elif cmd == 0xEA:
                # Articulation/gate time
                val = self.data[offset]
                offset += 1
                channel.events.append(MDTCommand(cmd=0xEA, params=[val]))
            elif cmd == 0xEB:
                # Instrument change
                inst = self.data[offset]
                offset += 1
                channel.events.append(MDTCommand(cmd=0xEB, params=[inst]))
            elif cmd == 0xEC:
                # Volume
                if channel.id == 0x10:  # Rhythm
                    first = self.data[offset]
                    offset += 1
                    if first & 0x80:
                        second = self.data[offset]
                        offset += 1
                        channel.events.append(MDTCommand(cmd=0xEC, params=[first, second]))
                    else:
                        params = [first] + list(self.data[offset:offset+6])
                        offset += 6
                        channel.events.append(MDTCommand(cmd=0xEC, params=params))
                else:
                    val = self.data[offset]
                    offset += 1
                    channel.events.append(MDTCommand(cmd=0xEC, params=[val]))
            elif cmd == 0xED:
                # Pitch LFO
                params = list(self.data[offset:offset+4])
                offset += 4
                channel.events.append(MDTCommand(cmd=0xED, params=params))
            elif cmd == 0xEE:
                # Register move
                params = list(self.data[offset:offset+2])
                offset += 2
                channel.events.append(MDTCommand(cmd=0xEE, params=params))
            elif cmd == 0xEF:
                # LFO delay / Noise freq
                val = self.data[offset]
                offset += 1
                channel.events.append(MDTCommand(cmd=0xEF, params=[val]))
            elif cmd == 0xF0:
                # Fade
                val = self.data[offset]
                offset += 1
                channel.events.append(MDTCommand(cmd=0xF0, params=[val]))
            elif cmd == 0xF1:
                # Pan
                if channel.id == 0x10:
                    params = list(self.data[offset:offset+2])
                    offset += 2
                else:
                    params = [self.data[offset]]
                    offset += 1
                channel.events.append(MDTCommand(cmd=0xF1, params=params))
            elif cmd == 0xF2:
                # Portamento
                params = list(self.data[offset:offset+4])
                offset += 4
                channel.events.append(MDTCommand(cmd=0xF2, params=params))
            elif cmd == 0xF3:
                # Infinite loop point
                channel.loop_pos = len(channel.events)
                offset += 2
                channel.events.append(MDTCommand(cmd=0xF3))
            elif cmd == 0xF4:
                # Volume up
                val = self.data[offset]
                offset += 1
                channel.events.append(MDTCommand(cmd=0xF4, params=[val]))
            elif cmd == 0xF5:
                # Volume down
                val = self.data[offset]
                offset += 1
                channel.events.append(MDTCommand(cmd=0xF5, params=[val]))
            elif cmd == 0xF6:
                # Loop start [:
                count = self.data[offset]
                offset += 3  # count + 2 byte pointer
                channel.events.append(MDTCommand(cmd=0xF6, params=[count]))
            elif cmd == 0xF7:
                # Loop end :]
                offset += 3
                channel.events.append(MDTCommand(cmd=0xF7))
            elif cmd == 0xF8:
                # Sync value
                val = self.data[offset]
                offset += 1
                channel.events.append(MDTCommand(cmd=0xF8, params=[val]))
            elif cmd == 0xF9:
                # Loop break |
                offset += 2
                channel.events.append(MDTCommand(cmd=0xF9))
            elif cmd == 0xFA:
                # Macro call
                offset += 2
                channel.events.append(MDTCommand(cmd=0xFA))
            elif cmd == 0xFB:
                # Pitch LFO sawtooth
                params = list(self.data[offset:offset+4])
                offset += 4
                channel.events.append(MDTCommand(cmd=0xFB, params=params))
            elif cmd == 0xFC:
                # Amplitude LFO sawtooth
                params = list(self.data[offset:offset+4])
                offset += 4
                channel.events.append(MDTCommand(cmd=0xFC, params=params))
            elif cmd == 0xFD:
                # Hardware LFO
                params = list(self.data[offset:offset+4])
                offset += 4
                channel.events.append(MDTCommand(cmd=0xFD, params=params))
            else:
                # Unknown command, skip
                pass
    
    def _parse_fm_instruments(self, fm_loc: int, ssg_loc: int):
        offset = fm_loc
        inst_id = 0
        while offset + 32 <= ssg_loc:
            params_raw = list(self.data[offset:offset+32])
            offset += 32
            
            # Parse into channel + 4 operator format
            params = self._parse_fm_params(params_raw)
            self.fm_instruments.append(MDTFMInstrument(id=inst_id, params=params))
            inst_id += 1
    
    def _parse_fm_params(self, raw: List[int]) -> List[List[int]]:
        """Parse 32-byte FM instrument into channel + 4 operators"""
        # Channel parameters
        ch = [0] * 11
        ch[3], ch[10] = raw[0] % 0x40, raw[0] >> 6  # SY, NOI
        ch[4] = raw[1]  # SP
        ch[6] = raw[2]  # AMD
        ch[2], ch[1] = raw[3] % 0x08, raw[3] >> 3  # WF, OM
        ch[0], ch[9] = raw[4] % 0x40, raw[4] >> 6  # AF, PAN
        ch[8], ch[7] = raw[5] % 0x10, raw[5] >> 4  # AMS, PMS
        ch[5] = raw[30] % 0x80  # PMD
        
        result = [ch]
        
        # Operators in OPNA register order (1, 3, 2, 4)
        op_offsets = [0, 2, 1, 3]
        for j in op_offsets:
            op = [0] * 11
            op[7] = raw[j + 6] % 0x10  # ML
            dt1_raw = raw[j + 6]
            op[8] = (0x140 - dt1_raw if dt1_raw >= 0x80 else dt1_raw) >> 4  # DT1
            op[5] = raw[j + 10]  # OL (TL)
            op[0], op[6] = raw[j + 14] % 0x40, raw[j + 14] >> 6  # AR, KS
            op[1], op[10] = raw[j + 18] % 0x80, raw[j + 18] >> 7  # DR, AME
            op[2], op[9] = raw[j + 22] % 0x40, raw[j + 22] >> 6  # SR, DT2
            op[3], op[4] = raw[j + 26] % 0x10, raw[j + 26] >> 4  # RR, SL
            result.append(op)
        
        return result
    
    def _parse_ssg_envelopes(self, ssg_loc: int):
        offset = ssg_loc
        env_id = 0
        while offset + 6 <= len(self.data):
            params = list(self.data[offset:offset+6])
            offset += 6
            self.ssg_envelopes.append(MDTSSGEnvelope(id=env_id, params=params))
            env_id += 1
    
    def get_info(self) -> str:
        lines = [
            "=" * 60,
            "MDT File Info:",
            f"  Title: {self.title}",
            f"  Chip: {'OPM' if self.chip == 0 else 'OPN' if self.chip == 1 else 'OPLL'}",
            f"  Channels: {len(self.channels)}",
            f"  FM Instruments: {len(self.fm_instruments)}",
            f"  SSG Envelopes: {len(self.ssg_envelopes)}",
        ]
        for ch in self.channels:
            lines.append(f"    {ch.name}: {len(ch.events)} events")
        return '\n'.join(lines)


# =============================================================================
# Furnace Builder
# =============================================================================

class FurnaceBuilder:
    def __init__(self, mdt: MDTParser):
        self.mdt = mdt
        self.channel_count = 10  # 6 FM + 3 SSG + 1 Rhythm
        self.pattern_length = 64
        self.patterns: List[List[bytes]] = [[] for _ in range(self.channel_count)]
        self.order_count = 0
        self.effects_count = [2] * self.channel_count
        self.instruments: List[bytes] = []
        self.tempo = mdt.tempo
        self.loop_point_order = None
    
    def build(self) -> bytes:
        """Build the complete .fur file"""
        print("\nBuilding Furnace module...")
        
        # Create FM instruments
        for fm_ins in self.mdt.fm_instruments:
            self._make_fm_instrument(fm_ins)
        
        # Convert channels to patterns
        for channel in self.mdt.channels:
            self._channel_to_patterns(channel)
        
        # Ensure all channels have same number of patterns
        max_patterns = max(len(p) for p in self.patterns) if any(self.patterns) else 1
        self.order_count = max_patterns
        
        for ch_idx in range(self.channel_count):
            while len(self.patterns[ch_idx]) < max_patterns:
                empty_pattern = self._make_empty_pattern(ch_idx, len(self.patterns[ch_idx]))
                self.patterns[ch_idx].append(empty_pattern)
        
        # Build file structure
        return self._build_file()
    
    def _make_fm_instrument(self, fm_ins: MDTFMInstrument) -> bytes:
        """Create Furnace FM instrument from MDT format"""
        params = fm_ins.params
        ch = params[0]
        
        # Map MDT params to Furnace
        alg = ch[0] & 0x07
        fb = (ch[0] >> 3) & 0x07
        
        flags = (4 & 0x0F) | ((0x0F) << 4)
        alg_fb = (fb & 0x07) | ((alg & 0x07) << 4)
        
        feature_fm = [
            b'FM',
            pack_short(0),
            pack_byte(flags),
            pack_byte(alg_fb),
            pack_byte(0),  # AMS/PMS
            pack_byte(0),
        ]
        
        # Operators
        for i in [0, 2, 1, 3]:  # Furnace order
            op = params[i + 1]
            dt_mult = (op[7] & 0x0F) | (((3 + op[8]) & 0x07) << 4)
            tl = op[5] & 0x7F
            rs_ar = (op[0] & 0x1F) | ((op[6] & 0x03) << 6)
            am_dr = (op[1] & 0x1F) | ((op[10] & 0x01) << 7)
            kvs_sr = (op[2] & 0x1F) | (2 << 5)
            sl_rr = (op[3] & 0x0F) | ((op[4] & 0x0F) << 4)
            ssg_eg = 0
            
            feature_fm.extend([
                pack_byte(dt_mult), pack_byte(tl), pack_byte(rs_ar),
                pack_byte(am_dr), pack_byte(kvs_sr), pack_byte(sl_rr),
                pack_byte(ssg_eg), pack_byte(0)
            ])
        
        feature_fm[1] = pack_short(bl_length(feature_fm[2:]))
        
        # Name feature
        name = f"FM {fm_ins.id}"
        feature_name = [b'NA', pack_short(0), pack_string(name)]
        feature_name[1] = pack_short(bl_length(feature_name[2:]))
        
        ins_block = [
            b'INS2',
            pack_long(0),
            pack_short(TARGET_FURNACE_VERSION),
            pack_short(1),  # FM (OPN)
            b''.join(feature_name),
            b''.join(feature_fm),
            b'EN'
        ]
        ins_block[1] = pack_long(bl_length(ins_block[2:]))
        
        self.instruments.append(b''.join(ins_block))
        return b''.join(ins_block)
    
    def _channel_to_patterns(self, channel: MDTChannel):
        """Convert MDT channel events to Furnace patterns"""
        fur_channel = channel.fur_channel
        
        # Expand loops and collect events with tick positions
        events_with_ticks = []
        tick_pos = 0
        
        # Simple loop expansion
        loop_stack = []
        event_index = 0
        
        while event_index < len(channel.events):
            event = channel.events[event_index]
            
            if isinstance(event, MDTNote):
                events_with_ticks.append((tick_pos, event))
                tick_pos += event.length
                event_index += 1
            elif isinstance(event, MDTCommand):
                if event.cmd in (0xE0, 0xE4, 0xF6):  # Loop starts
                    count = event.params[0] if event.params else 2
                    loop_stack.append({
                        'start': event_index + 1,
                        'count': count,
                        'remaining': count
                    })
                    event_index += 1
                elif event.cmd in (0xE2, 0xE5, 0xF7):  # Loop ends
                    if loop_stack:
                        loop = loop_stack[-1]
                        loop['remaining'] -= 1
                        if loop['remaining'] > 0:
                            event_index = loop['start']
                        else:
                            loop_stack.pop()
                            event_index += 1
                    else:
                        event_index += 1
                elif event.cmd == 0xF3:  # Infinite loop point
                    # Mark for later
                    events_with_ticks.append((tick_pos, event))
                    event_index += 1
                else:
                    events_with_ticks.append((tick_pos, event))
                    event_index += 1
            else:
                event_index += 1
        
        if not events_with_ticks:
            return
        
        # Convert to patterns
        current_pattern_data = bytearray()
        pattern_index = 0
        current_row_in_pattern = 0
        
        current_vol = None
        current_ins = None
        
        for tick, event in events_with_ticks:
            row = tick // TICKS_PER_ROW
            target_pattern = row // self.pattern_length
            
            # Create new patterns as needed
            while target_pattern > pattern_index:
                rows_left = self.pattern_length - current_row_in_pattern
                if rows_left > 0:
                    self._write_skip(current_pattern_data, rows_left)
                current_pattern_data += b'\xFF'
                self.patterns[fur_channel].append(
                    self._make_pattern(fur_channel, pattern_index, bytes(current_pattern_data))
                )
                pattern_index += 1
                current_pattern_data = bytearray()
                current_row_in_pattern = 0
            
            row_in_pattern = row - (pattern_index * self.pattern_length)
            skip_rows = row_in_pattern - current_row_in_pattern
            
            if skip_rows > 0:
                self._write_skip(current_pattern_data, skip_rows)
                current_row_in_pattern += skip_rows
            
            if isinstance(event, MDTNote):
                if event.is_rest:
                    # Skip for rests
                    pass
                else:
                    # Convert note
                    fur_note = (event.octave + 1) * 12 + event.note
                    entry = self._make_entry(note=fur_note, ins=current_ins)
                    current_pattern_data += entry
                    current_row_in_pattern += 1
                    current_ins = None  # Only write once
            elif isinstance(event, MDTCommand):
                if event.cmd == 0xEB:  # Instrument
                    current_ins = event.params[0] if event.params else 0
                elif event.cmd == 0xEC:  # Volume
                    if event.params:
                        vol = event.params[0]
                        if channel.channel_type == 'ssg':
                            current_vol = vol
                        else:
                            current_vol = 127 - vol if vol < 128 else 0
        
        # Finalize last pattern
        if current_pattern_data or pattern_index < 1:
            rows_left = self.pattern_length - current_row_in_pattern
            if rows_left > 0:
                self._write_skip(current_pattern_data, rows_left)
            current_pattern_data += b'\xFF'
            self.patterns[fur_channel].append(
                self._make_pattern(fur_channel, pattern_index, bytes(current_pattern_data))
            )
    
    def _write_skip(self, data: bytearray, rows: int):
        """Write skip command to pattern data"""
        while rows > 0:
            skip = min(rows, 255)
            data += bytes([0x01, skip - 1])
            rows -= skip
    
    def _make_entry(self, note=None, ins=None, vol=None, fx=None) -> bytes:
        """Create a pattern row entry"""
        flags = 0
        data = bytearray()
        
        if note is not None:
            flags |= 0x01
        if ins is not None:
            flags |= 0x02
        if vol is not None:
            flags |= 0x04
        if fx:
            flags |= (len(fx) << 4)
        
        data.append(flags)
        
        if note is not None:
            data.append(note & 0xFF)
        if ins is not None:
            data.append(ins & 0xFF)
        if vol is not None:
            data.append(vol & 0xFF)
        if fx:
            for effect, value in fx:
                data.append(effect & 0xFF)
                data.append(value & 0xFF)
        
        return bytes(data)
    
    def _make_pattern(self, channel: int, index: int, data: bytes) -> bytes:
        """Create a PATN block"""
        block = [
            b'PATN',
            pack_long(0),
            pack_byte(0),  # subsong
            pack_byte(channel),
            pack_short(index),
            pack_string(""),
            data
        ]
        block[1] = pack_long(bl_length(block[2:]))
        return b''.join(block)
    
    def _make_empty_pattern(self, channel: int, index: int) -> bytes:
        """Create an empty pattern"""
        data = bytes([0x01, self.pattern_length - 1, 0xFF])
        return self._make_pattern(channel, index, data)
    
    def _build_file(self) -> bytes:
        """Build the complete .fur file"""
        # Song info block
        info_block = self._build_info_block()
        
        # Collect all blocks
        blocks = [info_block]
        
        # Add instruments
        for ins_data in self.instruments:
            blocks.append(ins_data)
        
        # Add patterns
        for ch_patterns in self.patterns:
            for pattern in ch_patterns:
                blocks.append(pattern)
        
        # Build header
        header = b'-Furnace module-' + pack_short(TARGET_FURNACE_VERSION)
        header += pack_short(0)  # reserved
        header += pack_long(32)  # song info offset
        header += pack_long(0) * 2  # reserved
        
        return header + b''.join(blocks)
    
    def _build_info_block(self) -> bytes:
        """Build the INFO block"""
        # Orders
        orders = bytearray()
        for order_idx in range(self.order_count):
            for ch in range(self.channel_count):
                orders.append(order_idx & 0xFF)
        
        # Build INFO content
        info_data = [
            pack_byte(1),  # time base
            pack_byte(6),  # speed 1
            pack_byte(6),  # speed 2
            pack_byte(1),  # arpeggio speed
            struct.pack('<f', self.tempo * 2.5),  # Furnace tempo
            pack_short(self.order_count),
            pack_short(0),  # highlight A
            pack_short(0),  # highlight B
            pack_short(len(self.instruments)),
            pack_short(0),  # wavetables
            pack_short(0),  # samples
            pack_long(self.pattern_length),
            pack_short(1),  # sound chip count
            
            # Sound chip: YM2608
            pack_byte(0x06),  # YM2608
            pack_byte(1),  # volume
            pack_byte(0),  # panning
            pack_long(0),  # flags
            
            pack_string(self.mdt.title),  # title
            pack_string(""),  # author
            pack_string("1.0"),  # tuning tag
            
            pack_string("Converted from MDT"),
            
            pack_byte(self.channel_count),
        ]
        
        info_2 = [
            bytes(orders),
            b''.join(pack_byte(min(8, n)) for n in self.effects_count),
            pack_byte(3) * self.channel_count,
            pack_byte(0) * self.channel_count,
            pack_string('') * self.channel_count,
            pack_string('') * self.channel_count,
            pack_string(''),
            struct.pack('<f', 440.0),
            pack_byte(0) * 19,
            pack_long(0),
            pack_byte(0),
            pack_short(1),
            pack_byte(0) * 5,
            pack_byte(255),
            pack_byte(0) * 8,
        ]
        
        info_content = b''.join(b if isinstance(b, bytes) else b for b in info_data)
        info_content += b''.join(b if isinstance(b, bytes) else b for b in info_2)
        
        block = b'INFO' + pack_long(len(info_content)) + info_content
        return block


# =============================================================================
# Main
# =============================================================================

def main():
    if len(sys.argv) < 3:
        print("Usage: python mdt2furnace.py <input.mdt> <output.fur>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    print(f"Parsing: {input_file}")
    
    try:
        mdt = MDTParser(input_file)
        print(mdt.get_info())
        
        builder = FurnaceBuilder(mdt)
        fur_data = builder.build()
        
        with open(output_file, 'wb') as f:
            f.write(fur_data)
        
        print(f"\nWritten: {output_file} ({len(fur_data)} bytes)")
        print("\nDone! Open the .fur file in Furnace Tracker.")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()

