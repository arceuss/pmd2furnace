#!/usr/bin/env python3
"""
PMD (Professional Music Driver) to Furnace Tracker Converter

Converts compiled PMD .M files to Furnace .fur modules.
Based on pmd2mml for parsing and vgm2fur for .fur file generation.

PMD Channel Layout (YM2608/OPNA):
- FM A-F: 6 FM synthesis channels
- SSG G-I: 3 SSG (PSG) channels  
- ADPCM J: 1 ADPCM sample channel
- Rhythm K: 6 rhythm sounds (BD, SD, HH, TOM, CYM, RIM)
"""

import struct
import zlib
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path


# =============================================================================
# Binary Builder Helpers (from vgm2fur)
# =============================================================================

def pack_byte(x): return struct.pack('<B', x)
def pack_short(x): return struct.pack('<H', x)
def pack_long(x): return struct.pack('<L', x)
def pack_qlong(x): return struct.pack('<Q', x)
def pack_float(x): return struct.pack('<f', x)
def pack_string(x): return x.encode('utf-8') + b'\0'

def bl_length(byteslist):
    return sum(len(x) for x in byteslist)


# =============================================================================
# Constants
# =============================================================================

TARGET_FURNACE_VERSION = 228  # Furnace v0.6.8

# YM2608 (OPNA) chip ID in Furnace
CHIP_YM2608 = 0x8E  # PC98 mode, 16 channels

# PMD note names (low nibble 0-11)
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# Special PMD note values (low nibble)
PMD_NOTE_REST = 0x0F  # Rest

# Furnace special note values
FUR_NOTE_OFF = 180
FUR_NOTE_REL = 181


# =============================================================================
# PMD Data Structures
# =============================================================================

@dataclass
class PMDHeader:
    """PMD file header"""
    version: int = 0
    fm_pointers: List[int] = field(default_factory=list)
    ssg_pointers: List[int] = field(default_factory=list)
    adpcm_pointer: int = 0
    rhythm_pointer: int = 0
    rhythm_table_pointer: int = 0
    instrument_pointer: int = 0
    # Metadata
    title: str = ""
    composer: str = ""
    arranger: str = ""
    memo: List[str] = field(default_factory=list)


@dataclass
class PMDNote:
    """A single note event"""
    note: int       # 0-11 (C to B)
    octave: int     # 0-7 (internal), maps to MML o1-o8
    length: int     # In ticks (96 = whole note by default)
    is_rest: bool = False
    
    def to_furnace_note(self, channel_type: str = 'fm') -> int:
        """Convert to Furnace note value (0-179)
        
        PMD internal octave 0-7 corresponds to MML octave 1-8.
        Furnace: C-5=0, C-4=12, C-3=24, C-2=36, C-1=48, C0=60, C1=72, C2=84, C3=96, C4=108, etc.
        
        FM channels: octave + 5 (per user feedback - down 1 octave)
        SSG channels: octave + 6 (user wants SSG an octave higher)
        """
        if self.is_rest:
            return None
        if channel_type == 'ssg':
            return (self.octave + 6) * 12 + self.note
        else:
            return (self.octave + 5) * 12 + self.note


@dataclass
class PMDCommand:
    """A command event"""
    cmd: int
    params: List[int] = field(default_factory=list)


@dataclass
class PMDChannel:
    """Parsed channel data"""
    name: str
    channel_type: str  # 'fm', 'ssg', 'adpcm', 'rhythm'
    events: List[Any] = field(default_factory=list)
    current_instrument: int = 0
    current_volume: int = 127


@dataclass
class PMDInstrument:
    """FM instrument definition (25 bytes)"""
    id: int
    alg: int = 0
    fb: int = 0
    # Operators: DT/MUL, TL, KS/AR, AM/DR, SR, SL/RR
    ops: List[Dict] = field(default_factory=list)


# =============================================================================
# PMD Parser
# =============================================================================

class PMDParser:
    """Parser for compiled PMD .M files"""
    
    # Command sizes based on pmd2mml source
    CMD_SIZES = {
        0xFF: 1,   # instrument
        0xFE: 1,   # gate time (can be 1 or 3 bytes)
        0xFD: 1,   # volume
        0xFC: 1,   # tempo (can have sub-commands)
        0xFB: 0,   # tie
        0xFA: 2,   # detune
        0xF9: 2,   # loop start
        0xF8: 4,   # loop end
        0xF7: 2,   # loop escape
        0xF6: 0,   # loop point
        0xF5: 1,   # transpose
        0xF4: 0,   # volume up 4
        0xF3: 0,   # volume down 4
        0xF2: 4,   # LFO set
        0xF1: 1,   # LFO switch
        0xF0: 4,   # PSG envelope
        0xEF: 2,   # direct register
        0xEE: 1,   # PSG noise
        0xED: 1,   # PSG mix
        0xEC: 1,   # pan
        0xEB: 1,   # rhythm key
        0xEA: 1,   # rhythm volume
        0xE9: 1,   # rhythm pan
        0xE8: 1,   # rhythm master
        0xE7: 1,   # relative transpose
        0xE6: 1,   # rhythm volume add
        0xE5: 2,   # rhythm volume add 2
        0xE4: 1,   # HLFO delay
        0xE3: 1,   # volume up N
        0xE2: 1,   # volume down N
        0xE1: 1,   # HLFO set
        0xE0: 1,   # port 22h
        0xDF: 1,   # zenlen
        0xDE: 1,   # vol up fine
        0xDD: 1,   # vol down fine
        0xDC: 1,   # status
        0xDB: 1,   # status add
        0xDA: 3,   # portamento
        0xD9: 1,   # HLFO waveform
        0xD8: 1,   # HLFO AMD/PMD
        0xD7: 1,   # HLFO frequency
        0xD6: 2,   # MD set
        0xD5: 2,   # detune add
        0xD4: 1,   # SSG effect
        0xD3: 1,   # FM effect
        0xD2: 1,   # fadeout
        0xD1: 1,   # unused
        0xD0: 1,   # noise freq add
        0xCF: 1,   # slotmask
        0xCE: 6,   # ???
        0xCD: 5,   # SSG env extended
        0xCC: 1,   # detune extend
        0xCB: 1,   # LFO wave
        0xCA: 1,   # extend mode
        0xC9: 1,   # envelope extend
        0xC8: 3,   # slot detune
        0xC7: 3,   # slot detune 2
        0xC6: 6,   # FM3 extend
        0xC5: 1,   # volmask
        0xC4: 1,   # gate time Q
        0xC3: 2,   # pan ex
        0xC2: 1,   # LFO delay
        0xC1: 0,   # slur
        0xC0: 1,   # part mask (can have sub-commands)
        0xBF: 4,   # LFO set B
        0xBE: 1,   # LFO switch B
        0xBD: 2,   # MD set B
        0xBC: 1,   # LFO wave B
        0xBB: 1,   # extend mode B
        0xBA: 1,   # volmask B
        0xB9: 1,   # LFO delay B
        0xB8: 2,   # TL set
        0xB7: 1,   # MD count
        0xB6: 1,   # FB set
        0xB5: 2,   # slot delay
        0xB4: 16,  # PPZ extend
        0xB3: 1,   # gate min
        0xB2: 1,   # transpose def
    }
    
    CHANNEL_NAMES = ['FM-A', 'FM-B', 'FM-C', 'FM-D', 'FM-E', 'FM-F',
                     'SSG-G', 'SSG-H', 'SSG-I', 'ADPCM-J', 'Rhythm-K']
    
    def __init__(self, file_path: str):
        with open(file_path, 'rb') as f:
            self.data = f.read()
        self.header = self._parse_header()
        self.channels: List[PMDChannel] = []
        self.instruments: Dict[int, PMDInstrument] = {}
        self.tempo = 75  # Default PMD tempo (= 150 BPM)
        
    def _parse_header(self) -> PMDHeader:
        """Parse the PMD file header
        
        PMD pointers are relative to byte 1, so we add 1 to get absolute file offsets.
        See pmd2mml.c: ftell(fp) < pointer + 1
        """
        header = PMDHeader()
        header.version = self.data[0]
        
        offset = 1
        pointers = []
        for i in range(12):
            ptr = struct.unpack('<H', self.data[offset:offset+2])[0]
            # Add 1 to convert to absolute file offset
            pointers.append(ptr + 1)
            offset += 2
        
        header.fm_pointers = pointers[0:6]
        header.ssg_pointers = pointers[6:9]
        header.adpcm_pointer = pointers[9]
        header.rhythm_pointer = pointers[10]
        header.rhythm_table_pointer = pointers[11]
        
        # Instrument pointer follows channel pointers
        header.instrument_pointer = struct.unpack('<H', self.data[offset:offset+2])[0] + 1
        
        # Parse metadata from end of file (null-terminated Shift-JIS strings)
        self._parse_metadata(header)
        
        return header
    
    def _parse_metadata(self, header: PMDHeader):
        """Parse metadata strings from end of file
        
        Metadata is stored as null-terminated Shift-JIS strings at the end of the file.
        Order is: Title, Composer, Arranger, Memo, Memo...
        """
        # Find the last non-null byte
        pos = len(self.data) - 1
        while pos > 0 and self.data[pos] == 0:
            pos -= 1
        
        # Work backwards to find null-terminated strings
        strings = []
        end = pos + 1
        while end > len(self.data) - 500 and len(strings) < 10:
            start = end - 1
            while start > 0 and self.data[start-1] != 0:
                start -= 1
            if start < end - 1:
                try:
                    s = self.data[start:end].decode('shift-jis').strip()
                    # Filter out junk - valid metadata should have readable content
                    # and not be too short or have too many control characters
                    if len(s) >= 2 and sum(1 for c in s if c.isprintable()) > len(s) * 0.7:
                        strings.insert(0, s)
                except:
                    pass
            end = start - 1 if start > 0 else 0
        
        # Filter to only keep good metadata strings (typically the last 5)
        # Good strings are ones that look like actual text, not instrument data
        good_strings = [s for s in strings if not any(c in s for c in '=<>{}[]')]
        
        # Assign to header fields based on position
        if len(good_strings) >= 5:
            header.title = good_strings[-5]
            header.composer = good_strings[-4]
            header.arranger = good_strings[-3]
            header.memo = good_strings[-2:]
        elif len(good_strings) >= 3:
            header.title = good_strings[-3]
            header.composer = good_strings[-2]
            header.arranger = good_strings[-1]
        elif len(good_strings) >= 1:
            header.title = good_strings[-1]
    
    def parse(self):
        """Parse the entire PMD file"""
        # Collect channel data
        all_pointers = []
        
        for i, ptr in enumerate(self.header.fm_pointers):
            if ptr > 0:
                all_pointers.append((ptr, self.CHANNEL_NAMES[i], 'fm'))
        
        for i, ptr in enumerate(self.header.ssg_pointers):
            if ptr > 0:
                all_pointers.append((ptr, self.CHANNEL_NAMES[6+i], 'ssg'))
        
        if self.header.adpcm_pointer > 0:
            all_pointers.append((self.header.adpcm_pointer, 'ADPCM-J', 'adpcm'))
        
        if self.header.rhythm_pointer > 0:
            all_pointers.append((self.header.rhythm_pointer, 'Rhythm-K', 'rhythm'))
        
        # Sort by offset
        all_pointers.sort(key=lambda x: x[0])
        
        # Parse each channel
        for i, (ptr, name, ch_type) in enumerate(all_pointers):
            if i + 1 < len(all_pointers):
                end_offset = all_pointers[i + 1][0]
            else:
                end_offset = self.header.rhythm_table_pointer
            
            channel = PMDChannel(name=name, channel_type=ch_type)
            self._parse_channel(channel, ptr, end_offset)
            self.channels.append(channel)
        
        # Parse instruments
        self._parse_instruments()
        
        return self
    
    def _parse_channel(self, channel: PMDChannel, start: int, end: int):
        """Parse channel data between start and end offsets
        
        Based on pmd2mml's pmdReadSequenceFM function.
        Many commands have variable length depending on sub-commands.
        """
        offset = start
        
        while offset < end:
            byte = self.data[offset]
            
            # Track end marker
            if byte == 0x80:
                break
            
            # Note data: 0x00-0x7F (but not 0x0F, 0x1F, etc. which are rests)
            if byte < 0x80:
                note_val = byte & 0x0F
                octave = (byte >> 4) & 0x07
                
                if offset + 1 >= end:
                    break
                length = self.data[offset + 1]
                offset += 2
                
                if note_val == PMD_NOTE_REST:  # 0x0F, 0x1F, etc = rest
                    channel.events.append(PMDNote(
                        note=0, octave=octave, length=length, is_rest=True
                    ))
                elif note_val < 12:
                    channel.events.append(PMDNote(
                        note=note_val, octave=octave, length=length
                    ))
            
            # Commands: 0x80-0xFF
            else:
                cmd = byte
                offset += 1
                params = []
                
                # Determine parameter size based on command and sub-commands
                # This is based on pmd2mml's pmdReadSequenceFM switch statement
                if cmd == 0xFF:  # Instrument
                    params = [self.data[offset]]
                    offset += 1
                elif cmd == 0xFE:  # Gate time q
                    params = [self.data[offset]]
                    offset += 1
                    if params[0] == 0xB1 and offset + 1 < end:
                        params.extend([self.data[offset], self.data[offset+1]])
                        offset += 2
                elif cmd == 0xFD:  # Volume V
                    params = [self.data[offset]]
                    offset += 1
                elif cmd == 0xFC:  # Tempo t/T
                    first = self.data[offset]
                    offset += 1
                    if first >= 0xFD:  # Sub-command (0xFF, 0xFE, 0xFD)
                        params = [first, self.data[offset]]
                        offset += 1
                    else:
                        params = [first]
                elif cmd == 0xFB:  # Tie &
                    pass  # No params
                elif cmd == 0xFA:  # Detune D (2 bytes, signed short)
                    params = list(self.data[offset:offset+2])
                    offset += 2
                elif cmd == 0xF9:  # Loop start [
                    params = list(self.data[offset:offset+2])
                    offset += 2
                elif cmd == 0xF8:  # Loop end ]
                    params = list(self.data[offset:offset+4])
                    offset += 4
                elif cmd == 0xF7:  # Loop escape :
                    params = list(self.data[offset:offset+2])
                    offset += 2
                elif cmd == 0xF6:  # Loop point L
                    pass  # No params
                elif cmd == 0xF5:  # Transpose _
                    params = [self.data[offset]]
                    offset += 1
                elif cmd == 0xF4:  # Volume up )
                    pass  # No params
                elif cmd == 0xF3:  # Volume down (
                    pass  # No params
                elif cmd == 0xF2:  # LFO M
                    params = list(self.data[offset:offset+4])
                    offset += 4
                elif cmd == 0xF1:  # LFO switch *
                    params = [self.data[offset]]
                    offset += 1
                elif cmd == 0xF0:  # PSG envelope E
                    params = list(self.data[offset:offset+4])
                    offset += 4
                elif cmd == 0xEF:  # Direct register y
                    params = list(self.data[offset:offset+2])
                    offset += 2
                elif cmd == 0xEC:  # Pan p
                    params = [self.data[offset]]
                    offset += 1
                elif cmd == 0xEB:  # Rhythm key
                    params = [self.data[offset]]
                    offset += 1
                elif cmd == 0xDA:  # Portamento
                    params = list(self.data[offset:offset+3])
                    offset += 3
                elif cmd == 0xC6:  # FM3 extend
                    params = list(self.data[offset:offset+6])
                    offset += 6
                elif cmd == 0xC0:  # Part mask / volume down settings
                    first = self.data[offset]
                    offset += 1
                    if first >= 0xF5:  # Has sub-command
                        params = [first, self.data[offset]]
                        offset += 1
                    else:
                        params = [first]
                elif cmd == 0xB4:  # PPZ extend
                    params = list(self.data[offset:offset+16])
                    offset += 16
                else:
                    # Use default sizes for other commands
                    param_size = self.CMD_SIZES.get(cmd, 0)
                    if param_size > 0 and offset + param_size <= end:
                        params = list(self.data[offset:offset + param_size])
                        offset += param_size
                
                channel.events.append(PMDCommand(cmd=cmd, params=params))
                
                # Track state changes
                if cmd == 0xFF and params:  # Instrument
                    channel.current_instrument = params[0]
                elif cmd == 0xFD and params:  # Volume
                    channel.current_volume = params[0]
                elif cmd == 0xFC and params:  # Tempo
                    if len(params) >= 2 and params[0] >= 0xFD:
                        self.tempo = params[1]
                    else:
                        self.tempo = params[0]
    
    def _parse_instruments(self):
        """Parse FM instrument definitions"""
        offset = self.header.instrument_pointer
        if offset == 0 or offset >= len(self.data):
            return
        
        while offset + 26 <= len(self.data):
            # Check for end marker
            marker = struct.unpack('<H', self.data[offset:offset+2])[0]
            if marker == 0xFF00:
                break
            
            ins_id = self.data[offset]
            ins_data = self.data[offset+1:offset+26]
            
            # Parse instrument (based on pmd2mml pmdReadInstrumentSection)
            slot_order = [0, 2, 1, 3]  # PMD operator order
            ops = []
            for i in range(4):
                s = slot_order[i]
                op = {
                    'dt': (ins_data[s] >> 4) & 0x07,
                    'mul': ins_data[s] & 0x0F,
                    'tl': ins_data[4+s] & 0x7F,
                    'ks': (ins_data[8+s] >> 6) & 0x03,
                    'ar': ins_data[8+s] & 0x1F,
                    'am': (ins_data[12+s] >> 7) & 0x01,
                    'dr': ins_data[12+s] & 0x1F,
                    'sr': ins_data[16+s] & 0x1F,
                    'sl': (ins_data[20+s] >> 4) & 0x0F,
                    'rr': ins_data[20+s] & 0x0F,
                }
                ops.append(op)
            
            alg = ins_data[24] & 0x07
            fb = (ins_data[24] >> 3) & 0x07
            
            self.instruments[ins_id] = PMDInstrument(
                id=ins_id, alg=alg, fb=fb, ops=ops
            )
            
            offset += 26
    
    def get_info(self) -> str:
        """Get summary info"""
        lines = [
            f"PMD File Info:",
            f"  Version: 0x{self.header.version:02X}",
        ]
        if self.header.title:
            lines.append(f"  Title: {self.header.title}")
        if self.header.composer:
            lines.append(f"  Composer: {self.header.composer}")
        if self.header.arranger:
            lines.append(f"  Arranger: {self.header.arranger}")
        lines.extend([
            f"  Channels: {len(self.channels)}",
            f"  Instruments: {len(self.instruments)}",
        ])
        for ch in self.channels:
            notes = sum(1 for e in ch.events if isinstance(e, PMDNote) and not e.is_rest)
            lines.append(f"    {ch.name}: {notes} notes")
        return '\n'.join(lines)


# =============================================================================
# Furnace Module Builder
# =============================================================================

class FurnaceBuilder:
    """Builds Furnace .fur files from PMD data"""
    
    # PMD drum instrument values -> drum name
    PMD_DRUM_MAP = {
        1: 'Bass Drum',      # @1
        2: 'Snare Drum 1',   # @2
        4: 'Low Tom',        # @4
        8: 'Middle Tom',     # @8
        16: 'High Tom',      # @16
        32: 'Rim Shot',      # @32
        64: 'Snare Drum 2',  # @64
        128: 'Hi-Hat Close', # @128
        256: 'Hi-Hat Open',  # @256
        512: 'Crash Cymbal', # @512
        1024: 'Ride Cymbal', # @1024
    }
    
    def __init__(self, pmd: PMDParser):
        self.pmd = pmd
        self.pattern_length = 64
        self.channel_count = 16  # YM2608: 6 FM + 3 SSG + 1 ADPCM + 6 Rhythm
        self.ticks_per_second = 60.0
        self.patterns: List[List[bytes]] = [[] for _ in range(self.channel_count)]
        self.order_count = 0
        # Start with 2 effect columns for all channels to support multiple effects
        self.effects_count = [2] * self.channel_count
        self.instruments: List[bytes] = []
        self.tempo = pmd.tempo  # PMD tempo (half-note BPM)
        self.loop_point_order = None  # Pattern order for loop point (L command)
        self.ssg_envelope_instruments = {}  # Maps (al, dd, sr, rr) -> instrument index
        self.drum_instruments = {}  # Maps drum value -> instrument index
        self.adpcma_instrument_idx = None  # Index of dummy ADPCM-A instrument for rhythm
    
    def _make_entry(self, note=None, ins=None, vol=None, fx=None) -> bytes:
        """Create a pattern entry"""
        mask = 0
        masklen = 1
        payload = b''
        
        if note is not None:
            mask |= 1
            payload += pack_byte(note)
        if ins is not None:
            mask |= 2
            payload += pack_byte(ins)
        if vol is not None:
            mask |= 4
            payload += pack_byte(vol)
        if fx and len(fx) > 0:
            if len(fx) == 1:
                mask |= 8 | 16
                fxtype, fxval = fx[0]
                payload += pack_byte(fxtype) + pack_byte(fxval)
            else:
                if len(fx) > 8: fx = fx[:8]
                if len(fx) <= 4:
                    mask |= 8 | 16 | 32
                    masklen = 2
                else:
                    mask |= 8 | 16 | 32 | 64
                    masklen = 3
                m = 256 | 512
                for fxtype, fxval in fx:
                    payload += pack_byte(fxtype) + pack_byte(fxval)
                    mask |= m
                    m <<= 2
        
        return mask.to_bytes(masklen, 'little') + payload
    
    def _make_pattern(self, channel: int, index: int, data: bytes) -> bytes:
        """Create a PATN block"""
        pat = [
            b'PATN',
            pack_long(0),
            pack_byte(0),  # subsong
            pack_byte(channel),
            pack_short(index),
            pack_string(''),
            data + b'\xFF'
        ]
        pat[1] = pack_long(bl_length(pat[2:]))
        return b''.join(pat)
    
    def _make_fm_instrument(self, ins: PMDInstrument) -> bytes:
        """Create an INS2 block for FM instrument"""
        # Flags: 4 ops, all active
        flags = (4 & 0x0F) | ((0x0F) << 4)
        alg_fb = (ins.fb & 0x07) | ((ins.alg & 0x07) << 4)
        
        feature_fm = [
            b'FM',
            pack_short(0),
            pack_byte(flags),
            pack_byte(alg_fb),
            pack_byte(0),  # AMS/PMS
            pack_byte(0),
            pack_byte(0),
        ]
        
        # Operators in order: 1, 3, 2, 4 (Furnace order)
        for i in [0, 2, 1, 3]:
            op = ins.ops[i]
            dt_mult = (op['mul'] & 0x0F) | (((3 + op['dt']) & 0x07) << 4)
            tl = op['tl'] & 0x7F
            rs_ar = (op['ar'] & 0x1F) | ((op['ks'] & 0x03) << 6)
            am_dr = (op['dr'] & 0x1F) | ((op['am'] & 0x01) << 7)
            kvs_sr = (op['sr'] & 0x1F) | (2 << 5)  # KVS = 2
            sl_rr = (op['rr'] & 0x0F) | ((op['sl'] & 0x0F) << 4)
            
            feature_fm.extend([
                pack_byte(dt_mult),
                pack_byte(tl),
                pack_byte(rs_ar),
                pack_byte(am_dr),
                pack_byte(kvs_sr),
                pack_byte(sl_rr),
                pack_byte(0),  # SSG-EG
                pack_byte(0),
            ])
        
        feature_fm[1] = pack_short(bl_length(feature_fm[2:]))
        
        # Name feature
        feature_name = [
            b'NA',
            pack_short(0),
            pack_string(f'FM {ins.id:02X}')
        ]
        feature_name[1] = pack_short(bl_length(feature_name[2:]))
        
        # Full instrument
        ins_block = [
            b'INS2',
            pack_long(0),
            pack_short(TARGET_FURNACE_VERSION),
            pack_short(1),  # FM instrument type
            b''.join(feature_name),
            b''.join(feature_fm),
            b'EN'
        ]
        ins_block[1] = pack_long(bl_length(ins_block[2:]))
        return b''.join(ins_block)
    
    def _make_ssg_envelope_instrument(self, al: int, dd: int, sr: int, rr: int, name: str = '') -> bytes:
        """Create an SSG instrument with volume macro for software envelope
        
        PMD E command: E <al>, <dd>, <sr>, <rr>
        
        Volume sequence from docs: V'al  V-dd'sr  V-dd-1'sr  V-dd-2'sr ... / R'rr R-1'rr ...
        
        - AL = Attack Length: hold at max volume for AL ticks
        - DD = Decay Depth: initial volume drop after attack (signed, -15 to 15)
        - SR = Sustain Rate: ticks between each -1 volume step during decay
        - RR = Release Rate: ticks between each -1 after note release
        
        Furnace macro release point (/): when note is released, macro jumps here.
        We build: [attack + decay portion] / [release portion with RR timing]
        """
        max_vol = 15
        vol_sequence = []
        
        # === ATTACK PHASE ===
        # Hold at max volume for AL ticks
        attack_steps = max(1, al) if al > 0 else 1
        for _ in range(attack_steps):
            vol_sequence.append(max_vol)
        
        # === DECAY PHASE ===
        # After attack, DD is ADDED to volume, then volume decreases by 1 every SR ticks
        # DD is signed: negative = decay down, positive = swell up
        # Example: E1,-2,2,1 v13 → 13 + (-2) = 11
        current_vol = max_vol + dd  # DD is added (negative DD = decrease)
        current_vol = max(0, min(15, current_vol))
        
        # SR=0 means NO decay (sustain at current level forever)
        if sr == 0:
            # Sustain at current volume until release
            for _ in range(30):  # Hold for a while
                vol_sequence.append(current_vol)
        else:
            # Decay down to 0: decrease by 1 every SR ticks
            decay_speed = sr
            while current_vol > 0 and len(vol_sequence) < 60:
                # Hold this volume for SR ticks
                for _ in range(decay_speed):
                    vol_sequence.append(current_vol)
                    if len(vol_sequence) >= 60:
                        break
                current_vol -= 1
        
        # Add sustain at 0 (or loop point for sustained notes)
        # For sustained notes, we want to hold at the end of decay
        # Add a few 0s as sustain floor
        sustain_vol = 0
        for _ in range(4):
            vol_sequence.append(sustain_vol)
        
        # Mark where release portion starts
        release_point = len(vol_sequence)
        
        # === RELEASE PHASE ===
        # When note is released, decay from current volume down to 0
        # Furnace jumps TO the release point, so we need a full decay sequence here
        
        # RR=0 means instant drop to 0
        if rr == 0:
            vol_sequence.append(0)
        else:
            # Release: decay from max volume down to 0 at RR rate
            # This handles the case where note is released during attack
            for vol in range(max_vol, -1, -1):
                for _ in range(rr):
                    vol_sequence.append(vol)
                    if len(vol_sequence) >= 127:
                        break
                if len(vol_sequence) >= 127:
                    break
        
        # Ensure we end at 0
        if vol_sequence[-1] != 0:
            vol_sequence.append(0)
        
        # Limit sequence length
        if len(vol_sequence) > 127:
            vol_sequence = vol_sequence[:127]
            release_point = min(release_point, 126)
        
        # Build volume macro
        # Format from Furnace instrument.cpp writeMacro():
        #   1 byte: macroType & 31 (0 = volume)
        #   1 byte: len
        #   1 byte: loop (255 = no loop)
        #   1 byte: rel (release point index, 255 = no release)
        #   1 byte: mode (0 = sequence)
        #   1 byte: (open & 0x3f) | wordSize  
        #   1 byte: delay
        #   1 byte: speed (1 = every tick)
        #   N bytes: data
        
        macro_vol = bytes([
            0,  # macroType: 0 = volume
            len(vol_sequence),  # length
            255,  # loop (255 = no loop) 
            release_point,  # release point - macro jumps here on note release
            0,  # mode (0 = sequence)
            0x01,  # open=1, wordSize=0 (8-bit unsigned)
            0,  # delay
            1,  # speed = 1 tick per step
        ]) + bytes(vol_sequence)
        
        # MA feature format from Furnace writeFeatureMA():
        #   2 bytes: "MA"
        #   2 bytes: feature block length
        #   2 bytes: macro header size (always 8)
        #   [macro data for each non-empty macro...]
        #   1 byte: 255 (end marker)
        
        ma_content = pack_short(8) + macro_vol + bytes([255])
        
        feature_ma = [
            b'MA',
            pack_short(len(ma_content)),
            ma_content
        ]
        
        # Name feature
        if not name:
            name = f'SSG E{al},{dd},{sr},{rr}'
        feature_name = [
            b'NA',
            pack_short(0),
            pack_string(name)
        ]
        feature_name[1] = pack_short(bl_length(feature_name[2:]))
        
        # Full instrument - type 6 = AY-3-8910 (what YM2608 SSG uses)
        ins_block = [
            b'INS2',
            pack_long(0),
            pack_short(TARGET_FURNACE_VERSION),
            pack_short(6),  # instrument type 6 = AY-3-8910
            b''.join(feature_name),
            b''.join(feature_ma),
            b'EN'
        ]
        ins_block[1] = pack_long(bl_length(ins_block[2:]))
        return b''.join(ins_block)
    
    def _make_adpcma_instrument(self, name: str = 'ADPCM-A Drums') -> bytes:
        """Create a dummy ADPCM-A instrument for rhythm channels"""
        # Name feature
        feature_name = [
            b'NA',
            pack_short(0),
            pack_string(name)
        ]
        feature_name[1] = pack_short(bl_length(feature_name[2:]))
        
        # Full instrument - type 37 = ADPCM-A
        ins_block = [
            b'INS2',
            pack_long(0),
            pack_short(TARGET_FURNACE_VERSION),
            pack_short(37),  # instrument type 37 = ADPCM-A
            b''.join(feature_name),
            b'EN'
        ]
        ins_block[1] = pack_long(bl_length(ins_block[2:]))
        return b''.join(ins_block)
    
    def _load_fui_file(self, filepath: str) -> Optional[bytes]:
        """Load a .fui instrument file and return its raw bytes"""
        try:
            with open(filepath, 'rb') as f:
                data = f.read()
            # .fui files are just instrument data without the module wrapper
            # They should already be in the correct format
            return data
        except Exception as e:
            print(f"Warning: Could not load {filepath}: {e}")
            return None
    
    def _load_drum_instruments(self, drum_folder: str = 'PMD Drums'):
        """Load drum instruments from .fui files"""
        drum_path = Path(drum_folder)
        if not drum_path.exists():
            print(f"Warning: Drum folder '{drum_folder}' not found")
            return
        
        # Map file names to PMD drum values
        file_to_drum = {
            '00_Bass Drum.fui': 1,
            '01_Snare Drum 1.fui': 2,
            '02_Snare Drum 2.fui': 64,
            '03_Crash Cymbal.fui': 512,
            '04_Closed HiHat.fui': 128,
            '05_Open HiHat (G-7).fui': 256,
            '06_TomTom (A-#).fui': 4,  # We'll use this for all toms
        }
        
        for filename, drum_val in file_to_drum.items():
            filepath = drum_path / filename
            if filepath.exists():
                fui_data = self._load_fui_file(str(filepath))
                if fui_data:
                    ins_idx = len(self.instruments)
                    self.instruments.append(fui_data)
                    self.drum_instruments[drum_val] = ins_idx
                    # Also map tom variants to the same instrument
                    if drum_val == 4:  # Low Tom
                        self.drum_instruments[8] = ins_idx   # Middle Tom
                        self.drum_instruments[16] = ins_idx  # High Tom
    
    def _parse_rhythm_patterns(self) -> List[List[Tuple[int, int, int]]]:
        """Parse R pattern definitions from the rhythm table.
        
        R patterns contain SSG drum sequences using @<value> instruments.
        Format in compiled .M file:
        - 0x00-0x7F: rest with that length
        - 0x80-0xBF: drum note (high bits in cmd, low bits in next byte, length after)
        - 0xC0-0xFF: commands (0xFF = end)
        
        Returns list of patterns, each pattern is list of (tick, drum_instrument, length)
        """
        patterns = []
        data = self.pmd.data
        table_ptr = self.pmd.header.rhythm_table_pointer
        
        if table_ptr >= len(data) or table_ptr == 0:
            return patterns
        
        # Read up to 128 pattern pointers (R0-R127)
        for pattern_idx in range(128):
            ptr_offset = table_ptr + pattern_idx * 2
            if ptr_offset + 1 >= len(data):
                break
            
            pattern_ptr = data[ptr_offset] | (data[ptr_offset + 1] << 8)
            if pattern_ptr == 0:
                patterns.append([])
                continue
            
            # Convert to absolute offset (add 1 for PMD's addressing)
            abs_ptr = pattern_ptr + 1
            
            # Validate pointer
            if abs_ptr >= len(data) or abs_ptr < 1:
                patterns.append([])
                continue
            
            # Parse R pattern events
            pattern_events = []
            tick = 0
            offset = abs_ptr
            max_iterations = 500
            iterations = 0
            
            while offset < len(data) and iterations < max_iterations:
                iterations += 1
                cmd = data[offset]
                offset += 1
                
                if cmd == 0xFF:  # End of pattern
                    break
                elif cmd >= 0x80 and cmd <= 0xBF:
                    # Drum note: 0x80 + (drum_high << 0), next = drum_low, then length
                    if offset + 1 >= len(data):
                        break
                    drum_low = data[offset]
                    length = data[offset + 1]
                    offset += 2
                    
                    if length == 0:
                        length = 1
                    
                    # Calculate drum instrument: low 8 bits + high 6 bits
                    # cmd & 0x3F gives high bits (0-63), shifted left 8
                    drum_val = drum_low | ((cmd & 0x3F) << 8)
                    
                    pattern_events.append((tick, drum_val, length))
                    tick += length
                elif cmd < 0x80:
                    # Rest with length = cmd
                    if cmd > 0:
                        tick += cmd
                elif cmd >= 0xC0:
                    # Commands - handle the ones we know about
                    if cmd == 0xE8:  # \V rhythm master volume
                        if offset < len(data):
                            offset += 1
                    elif cmd == 0xEA:  # \v rhythm channel volume
                        if offset < len(data):
                            offset += 1
                    elif cmd == 0xE9:  # \<pan> rhythm pan
                        if offset < len(data):
                            offset += 1
                    elif cmd == 0xFD:  # v volume (shouldn't appear but handle it)
                        if offset < len(data):
                            offset += 1
                    elif cmd == 0xF9:  # [ loop start
                        if offset + 1 < len(data):
                            offset += 2
                    elif cmd == 0xF8:  # ] loop end
                        if offset + 3 < len(data):
                            offset += 4
                    elif cmd == 0xF7:  # : loop break
                        if offset + 1 < len(data):
                            offset += 2
                    elif cmd == 0xDF:  # C zenlen
                        if offset < len(data):
                            offset += 1
                    elif cmd == 0xFC:  # t tempo - variable length
                        if offset < len(data):
                            sub = data[offset]
                            offset += 1
                            if sub >= 0xFB:
                                offset += 1
                    else:
                        # Unknown command, try to continue
                        pass
            
            patterns.append(pattern_events)
        
        return patterns
    
    def _rhythm_to_patterns(self):
        """Convert K/R rhythm channel to Furnace SSG-I channel (8)
        
        PMD K/R system:
        - K channel contains R pattern references (R0, R1, etc.)
        - R patterns contain SSG drum definitions with @<value> instruments
        - Drums play on SSG channel 3 (SSG-I in Furnace = channel 8)
        
        Drum instrument values (binary flags):
        - @1 = Bass Drum
        - @2 = Snare 1
        - @4/@8/@16 = Toms (different panning)
        - @32 = Rim Shot
        - @64 = Snare 2
        - @128 = Hi-Hat closed
        - @256/@512/@1024 = Cymbals
        
        Values can be combined: @129 = @1 + @128 = Bass + Hi-Hat
        """
        # Parse R pattern definitions from the rhythm table
        r_patterns = self._parse_rhythm_patterns()
        
        if not r_patterns:
            return
        
        # Find the Rhythm-K channel
        rhythm_ch = None
        for ch in self.pmd.channels:
            if ch.name == 'Rhythm-K':
                rhythm_ch = ch
                break
        
        if not rhythm_ch or not rhythm_ch.events:
            return
        
        # Expand K channel's R pattern references into drum events
        # Format: (tick, drum_value, length)
        all_events = []
        tick = 0
        max_tick = 100000
        
        for event in rhythm_ch.events:
            if tick > max_tick:
                break
            
            if isinstance(event, PMDNote):
                if event.is_rest:
                    tick += event.length
                else:
                    # Note value is the R pattern index
                    pattern_idx = event.note + (event.octave * 12)
                    
                    if 0 <= pattern_idx < len(r_patterns):
                        r_pattern = r_patterns[pattern_idx]
                        # Add each drum event from the R pattern
                        for pat_tick, drum_val, drum_len in r_pattern:
                            all_events.append((tick + pat_tick, drum_val, drum_len))
                    
                    tick += event.length
        
        if not all_events:
            return
        
        # Limit events
        if len(all_events) > 5000:
            all_events = all_events[:5000]
        
        # Map drum values to notes for SSG-I
        # We'll use different octaves/notes for different drums
        # This is a simplified mapping - user can adjust instruments later
        def drum_to_note(drum_val):
            # Find the primary (lowest) drum in the combined value
            for bit, note in [(1, 36), (2, 38), (4, 41), (8, 43), (16, 45),
                              (32, 37), (64, 40), (128, 42), (256, 46), (512, 49), (1024, 51)]:
                if drum_val & bit:
                    return note  # Return MIDI-style note
            return 60  # Default C-4
        
        # Convert to SSG-I channel (8)
        fur_channel = 8
        ticks_per_row = 6
        max_patterns = 200
        
        current_pattern_data = bytearray()
        pattern_index = 0
        current_row_in_pattern = 0
        
        for tick, drum_val, length in all_events:
            row = tick // ticks_per_row
            target_pattern = row // self.pattern_length
            
            if target_pattern >= max_patterns:
                break
            
            # If we jumped to a new pattern, finalize current one and fill gaps
            while target_pattern > pattern_index:
                # Finish current pattern with skip to end
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
            
            # Write drum note to SSG-I
            note = drum_to_note(drum_val)
            entry = self._make_entry(note=note)
            current_pattern_data.extend(entry)
            current_row_in_pattern += 1
        
        # Save final pattern
        if current_pattern_data or pattern_index < self.order_count:
            rows_left = self.pattern_length - current_row_in_pattern
            if rows_left > 0:
                self._write_skip(current_pattern_data, rows_left)
            current_pattern_data += b'\xFF'
            self.patterns[fur_channel].append(
                self._make_pattern(fur_channel, pattern_index, bytes(current_pattern_data))
            )
    
    def _channel_to_patterns(self, channel: PMDChannel, fur_channel: int):
        """Convert PMD channel events to Furnace patterns
        
        PMD timing: Default ZENLEN=96 (whole note), so:
        - Whole note = 96 ticks
        - Half note = 48 ticks
        - Quarter note = 24 ticks
        - 8th note = 12 ticks
        - 16th note = 6 ticks
        - 32nd note = 3 ticks
        
        Furnace at speed 3: 1 row = 3 ticks (at 60Hz)
        This allows proper handling of 32nd notes.
        """
        TICKS_PER_ROW = 3
        
        current_tick = 0  # Current position in ticks
        current_pattern_data = bytearray()
        pattern_index = 0
        current_row_in_pattern = 0
        
        pending_ins = None
        pending_vol = None
        last_ins = None
        last_vol = None  # Track last written volume to avoid duplicates
        transpose = 0  # Current transpose in semitones (_M + _)
        master_transpose = 0  # Master transpose (_M)
        
        # First pass: expand loops and collect notes with tick positions
        # Loop expansion is critical for correct timing!
        events_with_ticks = []
        tick_pos = 0
        loop_point_tick = None  # Track L command position
        current_transpose = 0
        current_master = 0
        current_volume = None  # Track current volume
        current_detune = 0     # Track detune (D command)
        last_written_detune = None  # Only write detune when it changes
        current_pan = None     # Track panning (p command)
        current_envelope = None  # SSG envelope (E command) config
        pending_ssg_envelope = None  # SSG envelope to apply to next note
        pending_portamento = None  # Portamento: (start_note, end_note, duration)
        pending_effects = []   # Effects to apply to next note
        tie_active = False     # Track if tie (&) is active for portamento
        pitch_slide_active = False  # Track if we need to stop pitch slide
        current_gate_time = 8  # Gate time q (0-8, where 8 = full length, 0 = staccato)
        
        # Volume step for ) and ( commands (v increments)
        # FM: roughly 4 per v step, SSG: 1 per v step
        vol_step = 4 if channel.channel_type == 'fm' else 1
        
        # Stack for nested loops: [(start_index, loop_count, current_iteration)]
        loop_stack = []
        event_index = 0
        max_iterations = 100000  # Safety limit
        iteration_count = 0
        
        while event_index < len(channel.events) and iteration_count < max_iterations:
            iteration_count += 1
            event = channel.events[event_index]
            
            if isinstance(event, PMDNote):
                # Store note with current transpose, volume, and effects
                note_effects = list(pending_effects)  # Copy pending effects
                
                # Stop pitch slide from previous note if active
                if pitch_slide_active and not event.is_rest:
                    note_effects.append((0xE2, 0x00))  # E200 = stop slide
                    pitch_slide_active = False
                
                # Detune is tracked separately - will be added only when it changes
                # Add pan effect if set
                if current_pan is not None:
                    # PMD pan: 0=right, 1=left, 2=left, 3=center
                    # Furnace 08xy: x=left, y=right
                    if current_pan == 0:
                        note_effects.append((0x08, 0x0F))  # Right
                    elif current_pan in [1, 2]:
                        note_effects.append((0x08, 0xF0))  # Left
                    else:
                        note_effects.append((0x08, 0xFF))  # Center
                    current_pan = None  # Only apply once
                # Add detune effect only when it changes
                if current_detune != 0 and current_detune != last_written_detune:
                    # Furnace E5xx: 80 = center, range is roughly +-1 semitone
                    # PMD detune is in chip units, roughly map to Furnace
                    detune_val = max(0, min(255, 0x80 + (current_detune // 4)))
                    note_effects.append((0xE5, detune_val))
                    last_written_detune = current_detune
                elif current_detune == 0 and last_written_detune is not None and last_written_detune != 0:
                    # Reset detune to center
                    note_effects.append((0xE5, 0x80))
                    last_written_detune = 0
                
                # SSG envelope (E command) - apply as pending SSG instrument
                ssg_env_for_note = None
                if current_envelope is not None and channel.channel_type == 'ssg':
                    ssg_env_for_note = current_envelope
                
                # Gate time: add ECxx note cut effect if gate_time < 8
                # q0 = immediate cut, q8 = full length
                # Calculate cut tick: (note_length * gate_time) / 8
                note_gate_time = current_gate_time
                
                events_with_ticks.append((tick_pos, event, current_transpose + current_master, current_volume, note_effects, ssg_env_for_note, note_gate_time))
                tick_pos += event.length
                event_index += 1
                pending_effects = []  # Clear pending effects after note
                # Volume persists until changed - don't reset it
                
            elif isinstance(event, PMDCommand):
                if event.cmd == 0xF9:  # Loop start [
                    # Pre-scan to find matching ] and get loop count
                    depth = 1
                    scan_idx = event_index + 1
                    loop_count = 2  # Default
                    while scan_idx < len(channel.events) and depth > 0:
                        scan_event = channel.events[scan_idx]
                        if isinstance(scan_event, PMDCommand):
                            if scan_event.cmd == 0xF9:
                                depth += 1
                            elif scan_event.cmd == 0xF8:
                                depth -= 1
                                if depth == 0:
                                    # Found matching ], get count from params
                                    loop_count = scan_event.params[0] if scan_event.params else 2
                        scan_idx += 1
                    
                    loop_stack.append({
                        'start': event_index + 1,
                        'count': loop_count,
                        'iteration': 0,
                        'break_index': None
                    })
                    event_index += 1
                    
                elif event.cmd == 0xF8:  # Loop end ]
                    if loop_stack:
                        loop_info = loop_stack[-1]
                        loop_info['iteration'] += 1
                        
                        if loop_info['iteration'] < loop_info['count']:
                            # Go back to start of loop
                            event_index = loop_info['start']
                        else:
                            # Loop done, continue past ]
                            loop_stack.pop()
                            event_index += 1
                    else:
                        event_index += 1
                        
                elif event.cmd == 0xF7:  # Loop break :
                    if loop_stack:
                        loop_info = loop_stack[-1]
                        # On last iteration, skip to end of loop
                        if loop_info['iteration'] >= loop_info['count'] - 1:
                            # Find matching ] and skip there
                            depth = 1
                            skip_idx = event_index + 1
                            while skip_idx < len(channel.events) and depth > 0:
                                cmd = channel.events[skip_idx]
                                if isinstance(cmd, PMDCommand):
                                    if cmd.cmd == 0xF9:
                                        depth += 1
                                    elif cmd.cmd == 0xF8:
                                        depth -= 1
                                skip_idx += 1
                            event_index = skip_idx
                            loop_stack.pop()
                        else:
                            event_index += 1
                    else:
                        event_index += 1
                        
                else:
                    # Track transpose and volume changes
                    if event.cmd == 0xF5 and event.params:  # Transpose _
                        val = event.params[0]
                        current_transpose = val if val < 128 else val - 256
                    elif event.cmd == 0xB2 and event.params:  # Master transpose _M
                        val = event.params[0]
                        current_master = val if val < 128 else val - 256
                    elif event.cmd == 0xFD and event.params:  # Volume V (absolute)
                        current_volume = event.params[0]
                    elif event.cmd == 0xF4:  # Volume up )
                        # v increment: FM ~4, SSG 1
                        if current_volume is None:
                            current_volume = 100  # Default starting point
                        current_volume = min(127, current_volume + vol_step)
                    elif event.cmd == 0xF3:  # Volume down (
                        if current_volume is None:
                            current_volume = 100
                        current_volume = max(0, current_volume - vol_step)
                    elif event.cmd == 0xFA and len(event.params) >= 2:  # Detune D
                        # Signed 16-bit value
                        detune_raw = event.params[0] | (event.params[1] << 8)
                        current_detune = detune_raw if detune_raw < 32768 else detune_raw - 65536
                    elif event.cmd == 0xEC and event.params:  # Pan p
                        current_pan = event.params[0]
                    elif event.cmd == 0xF6:  # Loop point L
                        loop_point_tick = tick_pos
                    elif event.cmd == 0xF0 and len(event.params) >= 4:  # SSG Envelope E
                        # E AL, DD, SR, RR
                        # AL = Attack Length, DD = Decay Depth (signed), SR = Sustain Rate, RR = Release Rate
                        al = event.params[0]
                        dd = event.params[1] if event.params[1] < 128 else event.params[1] - 256  # Signed
                        sr = event.params[2]
                        rr = event.params[3]
                        # Store envelope config - we'll create an instrument for it later
                        current_envelope = (al, dd, sr, rr)
                    elif event.cmd == 0xFB:  # Tie (&)
                        tie_active = True
                    elif event.cmd == 0xFE and event.params:  # Gate time q (0-8)
                        # q command: sets gate time as fraction of 8
                        # q0 = staccato (note cut immediately), q8 = full length
                        current_gate_time = min(8, event.params[0])
                    elif event.cmd == 0xC4 and event.params:  # Gate time Q (alternate)
                        # Q command: similar to q but with different semantics
                        current_gate_time = min(8, event.params[0])
                    elif event.cmd == 0xDA and len(event.params) >= 3:  # Portamento { }
                        # Params: [start_note, end_note, duration]
                        # Each note byte: high nibble = octave, low nibble = note (0-11)
                        start_byte = event.params[0]
                        end_byte = event.params[1]
                        duration = event.params[2]
                        
                        start_octave = (start_byte >> 4) & 0x0F
                        start_note_val = start_byte & 0x0F
                        end_octave = (end_byte >> 4) & 0x0F
                        end_note_val = end_byte & 0x0F
                        
                        # Calculate semitone difference for slide direction
                        start_semitones = start_octave * 12 + start_note_val
                        end_semitones = end_octave * 12 + end_note_val
                        semitone_diff = end_semitones - start_semitones
                        
                        # Calculate slide: use E1xy (slide up) or E2xy (slide down)
                        # x = speed (1-F), y = semitones (1-F)
                        porta_effects = list(pending_effects)
                        semitones = min(15, abs(semitone_diff))  # Max 15 semitones for y nibble
                        # Speed: duration/3 = rows (at TICKS_PER_ROW=3), we want to complete slide in that time
                        # Higher speed = faster. For 4 rows and 3 semitones, speed ~4-8 works
                        rows = max(1, duration // 3)
                        speed = max(1, min(15, 32 // rows))  # Inverse: more rows = slower speed
                        
                        if semitone_diff < 0:
                            # Slide down - use E2xy
                            porta_effects.append((0xE2, (speed << 4) | semitones))
                        elif semitone_diff > 0:
                            # Slide up - use E1xy
                            porta_effects.append((0xE1, (speed << 4) | semitones))
                        
                        if tie_active:
                            # Tied portamento - just add effect without a new note
                            # Create a "command" entry with the slide effect at this tick position
                            # We'll handle this by adding a special marker
                            events_with_ticks.append((tick_pos, 'SLIDE_EFFECT', 0, None, porta_effects))
                            tie_active = False
                        else:
                            # Non-tied portamento - create a note with the slide effect
                            porta_note = PMDNote(note=start_note_val, octave=start_octave, length=duration)
                            events_with_ticks.append((tick_pos, porta_note, current_transpose + current_master, current_volume, porta_effects))
                        
                        pitch_slide_active = True  # Mark that we need to stop slide on next note
                        tick_pos += duration
                        pending_effects = []
                        event_index += 1
                        continue  # Skip adding the command itself
                    events_with_ticks.append((tick_pos, event, 0, current_volume, []))
                    event_index += 1
            else:
                event_index += 1
        
        # Second pass: place notes at correct row positions
        last_row = -1
        for item in events_with_ticks:
            if len(item) == 7:
                tick_pos, event, note_transpose, note_volume, note_effects, note_ssg_env, note_gate_time = item
            elif len(item) == 6:
                tick_pos, event, note_transpose, note_volume, note_effects, note_ssg_env = item
                note_gate_time = 8  # Full length
            elif len(item) == 5:
                tick_pos, event, note_transpose, note_volume, note_effects = item
                note_ssg_env = None
                note_gate_time = 8
            elif len(item) == 4:
                tick_pos, event, note_transpose, note_volume = item
                note_effects = []
                note_ssg_env = None
                note_gate_time = 8
            elif len(item) == 3:
                tick_pos, event, note_transpose = item
                note_volume = None
                note_effects = []
                note_ssg_env = None
                note_gate_time = 8
            else:
                tick_pos, event = item
                note_transpose = 0
                note_volume = None
                note_effects = []
                note_gate_time = 8
            row = tick_pos // TICKS_PER_ROW
            
            if isinstance(event, PMDNote):
                if event.is_rest:
                    continue  # Rests just advance time, no note needed
                
                fur_note = event.to_furnace_note(channel.channel_type)
                if fur_note is None:
                    continue
                
                # Apply transpose
                fur_note += note_transpose
                if fur_note < 0 or fur_note >= 180:
                    continue
                
                # Handle pattern boundaries and skips
                while row >= (pattern_index + 1) * self.pattern_length:
                    # Finish current pattern
                    rows_left = self.pattern_length - current_row_in_pattern
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
                
                # Only set instrument if changed
                ins_to_set = None
                
                # For SSG channels with envelope, use the SSG envelope instrument
                if channel.channel_type == 'ssg' and note_ssg_env is not None:
                    env_key = note_ssg_env
                    if env_key not in self.ssg_envelope_instruments:
                        # Create new SSG envelope instrument
                        al, dd, sr, rr = env_key
                        ins_idx = len(self.instruments)
                        self.instruments.append(self._make_ssg_envelope_instrument(al, dd, sr, rr))
                        self.ssg_envelope_instruments[env_key] = ins_idx
                    
                    ssg_ins = self.ssg_envelope_instruments[env_key]
                    if ssg_ins != last_ins:
                        ins_to_set = ssg_ins
                        last_ins = ssg_ins
                elif pending_ins is not None and pending_ins < 256 and pending_ins != last_ins:
                    if pending_ins in self.ins_id_map:
                        ins_to_set = self.ins_id_map[pending_ins]
                        last_ins = pending_ins
                
                # Use volume from event or pending
                # If there's a volume slide effect, always write volume to reset it
                vol_to_set = None
                vol_value = note_volume if note_volume is not None else pending_vol
                has_vol_slide = any(fx[0] == 0x0A for fx in note_effects) if note_effects else False
                
                if vol_value is not None:
                    # Always write volume if there's a volume slide (to reset each note)
                    # Otherwise only write if changed
                    if has_vol_slide or vol_value != last_vol:
                        if channel.channel_type == 'fm' and 0 <= vol_value <= 127:
                            vol_to_set = vol_value
                            last_vol = vol_value
                        elif channel.channel_type == 'ssg' and 0 <= vol_value <= 15:
                            vol_to_set = vol_value
                            last_vol = vol_value
                
                # Add gate time effect (ECxx - note cut after xx ticks)
                # Gate time: q0 = immediate cut, q8 = full length
                # Calculate cut tick: (note_length * gate_time) / 8
                fx_list = list(note_effects) if note_effects else []
                if note_gate_time < 8 and isinstance(event, PMDNote) and not event.is_rest:
                    # Calculate the cut tick within this note
                    cut_tick = (event.length * note_gate_time) // 8
                    # ECxx: cut after xx ticks (0x00-0xFF)
                    cut_tick = max(0, min(255, cut_tick))
                    if cut_tick > 0:
                        fx_list.append((0xEC, cut_tick))
                
                # Add effects if any
                fx_to_set = fx_list if fx_list else None
                
                # Update effects column count if needed
                if fx_to_set and len(fx_to_set) > 0:
                    self.effects_count[fur_channel] = max(self.effects_count[fur_channel], len(fx_to_set))
                
                entry = self._make_entry(note=fur_note, ins=ins_to_set, vol=vol_to_set, fx=fx_to_set)
                current_pattern_data += entry
                current_row_in_pattern += 1
                pending_ins = None
                pending_vol = None
                last_row = row
            
            elif event == 'SLIDE_EFFECT':
                # Tied portamento - add just the slide effect without a note
                # Handle pattern boundaries and skips
                while row >= (pattern_index + 1) * self.pattern_length:
                    rows_left = self.pattern_length - current_row_in_pattern
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
                
                # Update effects column count
                if note_effects and len(note_effects) > 0:
                    self.effects_count[fur_channel] = max(self.effects_count[fur_channel], len(note_effects))
                
                # Add entry with just the effect (no note, no instrument, no volume)
                entry = self._make_entry(fx=note_effects)
                current_pattern_data += entry
                current_row_in_pattern += 1
                last_row = row
                
            elif isinstance(event, PMDCommand):
                if event.cmd == 0xFF and event.params:  # Instrument
                    pending_ins = event.params[0]
                elif event.cmd == 0xFD and event.params:  # Volume
                    pending_vol = event.params[0]
                # Transpose is already handled in first pass
        
        # Finish final pattern
        if current_row_in_pattern > 0:
            rows_left = self.pattern_length - current_row_in_pattern
            if rows_left > 0:
                self._write_skip(current_pattern_data, rows_left)
            current_pattern_data += b'\xFF'
            self.patterns[fur_channel].append(
                self._make_pattern(fur_channel, pattern_index, bytes(current_pattern_data))
            )
        
        # Calculate loop point order from tick position (only need to do this once)
        if loop_point_tick is not None and self.loop_point_order is None:
            loop_point_row = loop_point_tick // TICKS_PER_ROW
            self.loop_point_order = loop_point_row // self.pattern_length
    
    def _write_skip(self, data: bytearray, count: int):
        """Write skip bytes to pattern data"""
        while count > 0:
            if count >= 128:
                data += b'\xFE'
                count -= 128
            elif count >= 2:
                data += pack_byte((count - 2) | 0x80)
                count = 0
            else:
                data += b'\x00'
                count -= 1
    
    def build(self) -> bytes:
        """Build the complete .fur file"""
        # Convert instruments and build ID map
        self.ins_id_map = {}  # Maps PMD instrument ID to Furnace index
        for idx, ins_id in enumerate(sorted(self.pmd.instruments.keys())):
            self.ins_id_map[ins_id] = idx
            self.instruments.append(self._make_fm_instrument(self.pmd.instruments[ins_id]))
        
        # If no instruments, create a default one
        if not self.instruments:
            default_ins = PMDInstrument(id=0, alg=0, fb=0, ops=[
                {'dt': 0, 'mul': 1, 'tl': 0, 'ks': 0, 'ar': 31, 'am': 0, 'dr': 0, 'sr': 0, 'sl': 0, 'rr': 15}
                for _ in range(4)
            ])
            self.ins_id_map[0] = 0
            self.instruments.append(self._make_fm_instrument(default_ins))
        
        # Create dummy ADPCM-A instrument for rhythm channels
        self.adpcma_instrument_idx = len(self.instruments)
        self.instruments.append(self._make_adpcma_instrument())
        
        # Map PMD channels to Furnace channels
        # YM2608: FM1-6 (0-5), SSG1-3 (6-8), ADPCM (9), Rhythm (10-15)
        # Note: SSG-I (channel 8) is used for SSG drums from Rhythm-K
        channel_map = {
            'FM-A': 0, 'FM-B': 1, 'FM-C': 2, 'FM-D': 3, 'FM-E': 4, 'FM-F': 5,
            'SSG-G': 6, 'SSG-H': 7,
            # 'SSG-I': 8,  # Reserved for SSG drums
            'ADPCM-J': 9
        }
        
        # Convert each channel
        for ch in self.pmd.channels:
            if ch.name in channel_map:
                fur_ch = channel_map[ch.name]
                # Skip channels with no actual notes
                note_count = sum(1 for e in ch.events if isinstance(e, PMDNote) and not e.is_rest)
                if note_count > 0:
                    self._channel_to_patterns(ch, fur_ch)
        
        # Convert K/R rhythm channel to SSG-I (channel 8)
        # Disabled for now - drums need more work
        # self._rhythm_to_patterns()
        
        # Determine order count
        self.order_count = max(len(pats) for pats in self.patterns) if any(self.patterns) else 1
        
        # Fill empty channels with empty patterns
        for ch_idx in range(self.channel_count):
            while len(self.patterns[ch_idx]) < self.order_count:
                idx = len(self.patterns[ch_idx])
                empty_pat = self._make_pattern(ch_idx, idx, b'\xFF')
                self.patterns[ch_idx].append(empty_pat)
        
        # Add loop jump (0Bxx) at the end of the last pattern on channel 0
        if self.loop_point_order is not None and self.order_count > 0 and len(self.patterns[0]) > 0:
            # Modify the last pattern of channel 0 to add a jump effect
            last_pat_idx = self.order_count - 1
            if last_pat_idx < len(self.patterns[0]):
                # Create a new pattern with the loop jump effect on the last row
                loop_jump_fx = [(0x0B, self.loop_point_order)]
                # Build pattern data with just the jump effect on row 0 of last pattern
                loop_data = bytearray()
                # Skip to last row
                if self.pattern_length > 1:
                    self._write_skip(loop_data, self.pattern_length - 1)
                # Add an empty entry with the loop jump effect
                loop_data += self._make_entry(fx=loop_jump_fx)
                loop_data += b'\xFF'
                self.patterns[0][last_pat_idx] = self._make_pattern(0, last_pat_idx, bytes(loop_data))
                # Make sure we have enough effect columns
                self.effects_count[0] = max(self.effects_count[0], 1)
        
        # Count total patterns
        pattern_count = sum(len(pats) for pats in self.patterns)
        
        # Build the file
        return self._build_fur_file(pattern_count)
    
    def _build_fur_file(self, pattern_count: int) -> bytes:
        """Assemble the complete .fur file"""
        file = bytearray()
        
        # Header
        header = [
            b'-Furnace module-',
            pack_short(TARGET_FURNACE_VERSION),
            pack_short(0),
            pack_long(0),  # Song info pointer (will be updated)
            pack_qlong(0),
        ]
        header[3] = pack_long(bl_length(header))
        file += b''.join(header)
        
        # INFO block
        info = [
            b'INFO',
            pack_long(0),
            pack_byte(0),   # time base
            pack_byte(3),   # speed 1 (3 ticks per row for 32nd note support)
            pack_byte(3),   # speed 2
            pack_byte(1),   # arp speed
            pack_float(self.ticks_per_second),
            pack_short(self.pattern_length),
            pack_short(self.order_count),
            pack_byte(4),   # highlight A
            pack_byte(16),  # highlight B
            pack_short(len(self.instruments)),
            pack_short(0),  # wavetable count
            pack_short(0),  # sample count
            pack_long(pattern_count),
            pack_byte(CHIP_YM2608),  # YM2608
            pack_byte(0) * 31,
            pack_byte(0x40) * 32,  # chip volume
            pack_byte(0) * 32,     # chip panning
            pack_long(0) * 32,     # chip flags
            pack_string(self.pmd.header.title or 'PMD Import'),
            pack_string(self.pmd.header.composer or ''),
            pack_float(440.0),
            pack_byte(0) * 20,  # compat flags
        ]
        
        # Instrument pointers (placeholder)
        ins_ptr = [pack_long(0)] * len(self.instruments)
        
        # Pattern pointers (placeholder)
        pat_ptr = [pack_long(0)] * pattern_count
        
        # Orders: simple sequential patterns
        orders = b''.join(
            pack_byte(n) for n in range(self.order_count)
        ) * self.channel_count
        
        info_2 = [
            orders,
            b''.join(pack_byte(n) for n in self.effects_count),
            pack_byte(3) * self.channel_count,  # channel shown
            pack_byte(0) * self.channel_count,  # channel collapsed
            pack_string('') * self.channel_count,  # channel names
            pack_string('') * self.channel_count,  # channel short names
            pack_string(''),  # song comment
            pack_float(1.0),  # master volume
            pack_byte(0) * 28,  # ext compat
            # Virtual tempo: PMD tempo t means t half-notes per minute = t*2 BPM
            # With speed 6 at 60Hz, base is 150 BPM, so we need t/75 multiplier
            pack_short(self.tempo),  # v.tempo num (actual PMD tempo)
            pack_short(75),          # v.tempo denom (base tempo for speed 6 @ 60Hz)
            pack_string(''),  # subsong name
            pack_string(''),  # subsong comment
            pack_byte(0),     # additional subsongs
            pack_byte(0) * 3,
            pack_string('PC-98'),
            pack_string(''),
            pack_string(''),
            pack_string(''),
            pack_string('PC-98'),
            pack_string(''),
            pack_float(1.0),  # chip 1 volume
            pack_float(0.0),  # chip 1 panning
            pack_float(0.0),  # chip 1 surround
            pack_long(0),     # patchbay count
            pack_byte(1),     # auto patchbay
            pack_byte(0) * 8,
            pack_byte(1),     # speed pattern len
            pack_byte(3),     # speed pattern (3 ticks per row)
            pack_byte(0) * 15,
            pack_byte(0),     # groove count
            pack_long(0),     # ins asset dir
            pack_long(0),     # wave asset dir
            pack_long(0),     # sample asset dir
        ]
        
        # Calculate info size
        info_size = bl_length(info[2:]) + bl_length(ins_ptr) + bl_length(pat_ptr) + bl_length(info_2)
        info[1] = pack_long(info_size)
        
        fileptr = len(file) + info_size + 8
        
        # Asset directory for instruments
        ins_adir = self._make_asset_dir(len(self.instruments))
        info_2[-3] = pack_long(fileptr)
        fileptr += len(ins_adir)
        
        # Empty asset dirs for waves and samples
        empty_adir = self._make_asset_dir(0)
        info_2[-2] = pack_long(fileptr)
        fileptr += len(empty_adir)
        info_2[-1] = pack_long(fileptr)
        fileptr += len(empty_adir)
        
        # Instrument pointers
        for i, ins in enumerate(self.instruments):
            ins_ptr[i] = pack_long(fileptr)
            fileptr += len(ins)
        
        # Pattern pointers
        all_patterns = []
        for ch_pats in self.patterns:
            all_patterns.extend(ch_pats)
        
        for i, pat in enumerate(all_patterns):
            pat_ptr[i] = pack_long(fileptr)
            fileptr += len(pat)
        
        # Write INFO block
        file += b''.join(info + ins_ptr + pat_ptr + info_2)
        
        # Write asset directories
        file += ins_adir + empty_adir + empty_adir
        
        # Write instruments
        file += b''.join(self.instruments)
        
        # Write patterns
        file += b''.join(all_patterns)
        
        # Return uncompressed (Furnace accepts both)
        return bytes(file)
    
    def _make_asset_dir(self, count: int) -> bytes:
        """Create an ADIR block"""
        if count == 0:
            adir = [
                b'ADIR',
                pack_long(0),
                pack_long(0),
            ]
        else:
            adir = [
                b'ADIR',
                pack_long(0),
                pack_long(1),
                pack_string(''),
                pack_short(count),
                b''.join(pack_byte(x) for x in range(count))
            ]
        adir[1] = pack_long(bl_length(adir[2:]))
        return b''.join(adir)


# =============================================================================
# Main
# =============================================================================

def main():
    import sys
    
    if len(sys.argv) < 2:
        print("PMD to Furnace Converter")
        print("Usage: python pmd2furnace.py <file.M> [output.fur]")
        print("\nConverts compiled PMD .M files to Furnace .fur modules.")
        sys.exit(1)
    
    input_file = sys.argv[1]
    if len(sys.argv) >= 3:
        output_file = sys.argv[2]
    else:
        output_file = Path(input_file).stem + '.fur'
    
    print(f"Parsing: {input_file}")
    print("=" * 60)
    
    # Parse PMD file
    pmd = PMDParser(input_file)
    pmd.parse()
    print(pmd.get_info())
    
    print("\n" + "=" * 60)
    print("Building Furnace module...")
    
    # Build Furnace module
    builder = FurnaceBuilder(pmd)
    fur_data = builder.build()
    
    # Write output
    with open(output_file, 'wb') as f:
        f.write(fur_data)
    
    print(f"Written: {output_file} ({len(fur_data)} bytes)")
    print("\nDone! Open the .fur file in Furnace Tracker.")


if __name__ == '__main__':
    main()
