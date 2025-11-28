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
FUR_NOTE_OFF = 180      # OFF - note off (key off for FM, note cut otherwise)
FUR_NOTE_RELEASE = 181  # === - note release (macro release + key off for FM)
FUR_NOTE_REL = 182      # REL - macro release only (no key off for FM)


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
        
        Rhythm channel (K) is special:
        - 0x00-0x7F: R pattern index (single byte, no length!)
        - 0x80+: commands like other channels
        """
        offset = start
        is_rhythm = (channel.channel_type == 'rhythm')
        
        while offset < end:
            byte = self.data[offset]
            
            # Track end marker
            if byte == 0x80:
                break
            
            # Rhythm channel special handling: 0x00-0x7F = R pattern index
            if is_rhythm and byte < 0x80:
                # Store R pattern index as a PMDNote with special encoding:
                # We use octave and note to reconstruct the byte value later
                # The R pattern's actual duration will be calculated when expanding
                offset += 1
                channel.events.append(PMDNote(
                    note=byte & 0x0F, octave=(byte >> 4) & 0x07, 
                    length=0, is_rest=False  # Length=0 means "use R pattern duration"
                ))
                continue
            
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
        self.tempo_changes = []  # List of (tick, [(effect, value), ...]) for tempo changes
        self.last_content_tick = 0  # Track the last tick with actual content
    
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
        """Create an SSG instrument with ADSR volume macro for software envelope
        
        PMD E command: E <al>, <dd>, <sr>, <rr>
        
        PMD predefined envelopes (from PMDDotNET mml_seg.cs psgenvdat):
          @0: { 0, 0, 0, 0 }     - Standard (no envelope)
          @1: { 2, 255, 0, 1 }   - Synth 1 (DD=255 is -1 signed)
          @2: { 2, 254, 0, 1 }   - Synth 2 (DD=254 is -2 signed)
          @3: { 2, 254, 0, 8 }   - Synth 3
          @4: { 2, 255, 24, 1 }  - E.Piano 1
          @5: { 2, 254, 24, 1 }  - E.Piano 2
          @6: { 2, 254, 4, 1 }   - Glocken/Marimba
          @7: { 2, 1, 0, 1 }     - Strings (DD=1 means +1, slight swell then sustain)
          @8: { 1, 2, 0, 1 }     - Brass 1
          @9: { 1, 2, 24, 1 }    - Brass 2
        
        PMD Envelope Flow:
        1. Attack: Hold at max volume (15) for AL ticks
        2. After attack: volume = 15 + DD (DD is signed byte, usually negative)
        3. Decay: volume decreases by 1 every SR ticks (SR=0 means sustain forever)
        4. Release: volume decreases by 1 every RR ticks (RR=0 means instant cut)
        
        Furnace ADSR mode (macro.open & 6 == 2):
        - val[0] = LOW (minimum level)
        - val[1] = HIGH (maximum level)
        - val[2] = AR (attack rate: position increases by AR per tick until 255)
        - val[3] = HT (hold time at peak)
        - val[4] = DR (decay rate: position decreases by DR per tick to SL)
        - val[5] = SL (sustain level, 0-255)
        - val[6] = ST (sustain time before SR kicks in)
        - val[7] = SR (sustain rate: position decreases by SR per tick)
        - val[8] = RR (release rate: position decreases by RR on note off)
        """
        # Convert DD from unsigned byte to signed
        dd_signed = dd if dd < 128 else dd - 256
        
        # Calculate sustain level after attack
        # PMD: volume after attack = 15 + dd_signed, then decays from there
        sustain_vol = max(0, min(15, 15 + dd_signed))
        
        # Scale SSG volume (0-15) to Furnace ADSR range (0-255)
        # LOW = 0, HIGH = 15 for SSG
        adsr_low = 0
        adsr_high = 15
        
        # Furnace SL is in position space (0-255), scale from SSG volume
        # sustain_vol / 15 * 255
        adsr_sl = int(sustain_vol * 255 / 15) if sustain_vol > 0 else 0
        
        # AR: PMD attack is "hold at max for AL ticks"
        # In Furnace, AR is rate to reach 255. We want instant attack then hold.
        # Set AR=255 (instant attack) and HT=AL (hold time)
        adsr_ar = 255  # Instant attack
        adsr_ht = al   # Hold at peak for AL ticks
        
        # DR: Rate to decay from 255 to SL
        # In PMD, after hold, volume jumps to sustain level
        # Set high DR for quick decay to sustain level
        adsr_dr = 128  # Moderately fast decay
        
        # ST: Time before sustain decay starts (0 for PMD behavior)
        adsr_st = 0
        
        # SR: Sustain decay rate
        # PMD: volume decreases by 1 every SR ticks, so ~17 position units per SR ticks
        # Furnace SR = rate per tick
        # If PMD SR=0, sustain forever (Furnace SR=0)
        # Otherwise, Furnace SR ≈ 17/PMD_SR (decrease ~17 units to match 1 SSG level drop)
        if sr == 0:
            adsr_sr = 0  # No decay during sustain
        else:
            # 17 position units = 1 SSG volume level
            # To decay 17 units over SR ticks: rate = 17/SR
            adsr_sr = max(1, min(255, 17 // sr))
        
        # RR: Release rate
        # Same logic as SR
        if rr == 0:
            adsr_rr = 255  # Instant cut (max rate)
        else:
            adsr_rr = max(1, min(255, 17 // rr))
        
        # Build ADSR macro for volume
        # Format from Furnace instrument.cpp writeMacro():
        #   1 byte: macroType & 31 (0 = volume)
        #   1 byte: len (18 for ADSR mode to hold all params)
        #   1 byte: loop (255 = no loop)
        #   1 byte: rel (255 = no release point, ADSR handles release via RR)
        #   1 byte: mode (1 = ADSR according to ftm.cpp)
        #   1 byte: (open & 0x3f) | wordSize  -- open=3 (bit 1 set = ADSR type, bit 0 = expanded)
        #   1 byte: delay
        #   1 byte: speed (1 = every tick)
        #   N bytes: data (val array with ADSR params)
        
        # ADSR params stored in val[0-8]
        adsr_vals = [
            adsr_low,   # val[0] = LOW
            adsr_high,  # val[1] = HIGH
            adsr_ar,    # val[2] = AR
            adsr_ht,    # val[3] = HT
            adsr_dr,    # val[4] = DR
            adsr_sl,    # val[5] = SL
            adsr_st,    # val[6] = ST
            adsr_sr,    # val[7] = SR
            adsr_rr,    # val[8] = RR
        ]
        # Pad to 16 values (val array extends beyond ADSR params)
        while len(adsr_vals) < 16:
            adsr_vals.append(0)
        
        macro_vol = bytes([
            0,    # macroType: 0 = volume
            16,   # length: need space for ADSR params
            255,  # loop (255 = no loop)
            255,  # rel (255 = ADSR handles release via RR)
            1,    # mode (1 for ADSR according to some imports)
            0x03, # open = 3 (bit 1 = ADSR type, bit 0 = open/expanded)
            0,    # delay
            1,    # speed = 1 tick per step
        ]) + bytes(adsr_vals)
        
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
            name = f'SSG E{al},{dd_signed},{sr},{rr}'
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
    
    def _create_adpcma_drum_kit(self) -> dict:
        """Create 6 ADPCM-A instruments for YM2608 hardware rhythm.
        
        Returns dict mapping Furnace channel (10-15) to instrument index.
        
        YM2608 ADPCM-A Rhythm channels:
        - Channel 10: BD (Bass Drum)
        - Channel 11: SD (Snare Drum) 
        - Channel 12: TOP (Top Cymbal/Crash)
        - Channel 13: HH (Hi-Hat)
        - Channel 14: TOM (Tom)
        - Channel 15: RIM (Rim Shot)
        """
        # YM2608 ADPCM-A channels in Furnace (PC-98 mode, 16 channels):
        # 0-5: FM, 6-8: SSG, 9-14: ADPCM-A, 15: ADPCM-B
        # Testing shows drums were off by 1, so adjust:
        ADPCMA_DRUMS = [
            ('BD', 9),    # Bass Drum - ADPCM-A ch 0
            ('SD', 10),   # Snare Drum - ADPCM-A ch 1
            ('TOP', 11),  # Top Cymbal - ADPCM-A ch 2
            ('HH', 12),   # Hi-Hat - ADPCM-A ch 3
            ('TOM', 13),  # Tom - ADPCM-A ch 4
            ('RIM', 14),  # Rim Shot - ADPCM-A ch 5
        ]
        
        # Create instruments and return channel -> instrument mapping
        adpcma_ins = {}  # channel -> instrument index
        for name, channel in ADPCMA_DRUMS:
            ins_idx = len(self.instruments)
            self.instruments.append(self._make_adpcma_instrument(f'ADPCM-A {name}'))
            adpcma_ins[channel] = ins_idx
        
        return adpcma_ins
    
    def _rhythm_to_adpcma(self, drum_map: dict):
        """Convert K/R rhythm channel to ADPCM-A channels (10-15)
        
        Uses proper YM2608 hardware rhythm instead of SSG drums.
        """
        # Parse R pattern definitions
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
        all_events = []  # (tick, drum_val, length)
        tick = 0
        max_tick = 100000
        
        loop_stack = []
        event_index = 0
        events = rhythm_ch.events
        
        while event_index < len(events) and tick < max_tick:
            event = events[event_index]
            
            if isinstance(event, PMDNote):
                if event.is_rest:
                    tick += event.length
                else:
                    raw_byte = (event.octave << 4) | event.note
                    pattern_idx = raw_byte
                    
                    if 0 <= pattern_idx < len(r_patterns):
                        r_pattern_events, r_duration = r_patterns[pattern_idx]
                        
                        for pat_tick, drum_val, drum_len in r_pattern_events:
                            all_events.append((tick + pat_tick, drum_val, drum_len))
                        
                        if r_duration > 0:
                            tick += r_duration
                event_index += 1
                
            elif isinstance(event, PMDCommand):
                if event.cmd == 0xF9:  # Loop start
                    depth = 1
                    scan_idx = event_index + 1
                    loop_count = 2
                    while scan_idx < len(events) and depth > 0:
                        scan_event = events[scan_idx]
                        if isinstance(scan_event, PMDCommand):
                            if scan_event.cmd == 0xF9:
                                depth += 1
                            elif scan_event.cmd == 0xF8:
                                depth -= 1
                                if depth == 0:
                                    loop_count = scan_event.params[0] if scan_event.params else 2
                        scan_idx += 1
                    
                    loop_stack.append({'start': event_index + 1, 'count': loop_count, 'iteration': 0})
                    event_index += 1
                    
                elif event.cmd == 0xF8:  # Loop end
                    if loop_stack:
                        loop_info = loop_stack[-1]
                        loop_info['iteration'] += 1
                        
                        if loop_info['count'] == 0:
                            if loop_info['iteration'] < 2:
                                event_index = loop_info['start']
                            else:
                                loop_stack.pop()
                                event_index += 1
                        elif loop_info['iteration'] < loop_info['count']:
                            event_index = loop_info['start']
                        else:
                            loop_stack.pop()
                            event_index += 1
                    else:
                        event_index += 1
                        
                elif event.cmd == 0xF7:  # Loop escape
                    if loop_stack:
                        loop_info = loop_stack[-1]
                        if loop_info['iteration'] == loop_info['count'] - 1:
                            depth = 1
                            skip_idx = event_index + 1
                            while skip_idx < len(events) and depth > 0:
                                cmd = events[skip_idx]
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
                    event_index += 1
            else:
                event_index += 1
        
        if not all_events:
            return
        
        # Limit events
        if len(all_events) > 5000:
            all_events = all_events[:5000]
        
        # Group events by ADPCM-A channel
        channel_events = {9: [], 10: [], 11: [], 12: [], 13: [], 14: []}
        
        for tick_pos, drum_val, length in all_events:
            # Check each bit and route to appropriate channel
            for bit_idx in range(11):
                if drum_val & (1 << bit_idx):
                    if bit_idx in drum_map:
                        fur_ch, ins_idx = drum_map[bit_idx]
                        channel_events[fur_ch].append((tick_pos, ins_idx))
        
        # Convert each ADPCM-A channel's events to patterns
        TICKS_PER_ROW = 3
        
        for fur_ch in range(9, 15):
            events_for_ch = channel_events.get(fur_ch, [])
            if not events_for_ch:
                continue
            
            # Sort by tick
            events_for_ch.sort(key=lambda x: x[0])
            
            current_pattern_data = bytearray()
            pattern_index = 0
            current_row_in_pattern = 0
            last_ins = None
            
            for tick_pos, ins_idx in events_for_ch:
                row = tick_pos // TICKS_PER_ROW
                target_pattern = row // self.pattern_length
                
                if target_pattern >= 200:
                    break
                
                # Fill patterns to reach target
                while target_pattern > pattern_index:
                    rows_left = self.pattern_length - current_row_in_pattern
                    if rows_left > 0:
                        self._write_skip(current_pattern_data, rows_left)
                    current_pattern_data += b'\xFF'
                    self.patterns[fur_ch].append(
                        self._make_pattern(fur_ch, pattern_index, bytes(current_pattern_data))
                    )
                    pattern_index += 1
                    current_pattern_data = bytearray()
                    current_row_in_pattern = 0
                
                row_in_pattern = row - (pattern_index * self.pattern_length)
                skip_rows = row_in_pattern - current_row_in_pattern
                
                if skip_rows > 0:
                    self._write_skip(current_pattern_data, skip_rows)
                    current_row_in_pattern += skip_rows
                
                # Write drum note
                ins_to_write = ins_idx if ins_idx != last_ins else None
                note = 60  # C-4 for ADPCM-A
                
                flags = 0x01  # Has note
                if ins_to_write is not None:
                    flags |= 0x02
                    last_ins = ins_idx
                
                current_pattern_data.append(flags)
                current_pattern_data.append(note)
                if ins_to_write is not None:
                    current_pattern_data.append(ins_to_write)
                
                current_row_in_pattern += 1
            
            # Finish last pattern
            if current_row_in_pattern > 0:
                rows_left = self.pattern_length - current_row_in_pattern
                if rows_left > 0:
                    self._write_skip(current_pattern_data, rows_left)
                current_pattern_data += b'\xFF'
                self.patterns[fur_ch].append(
                    self._make_pattern(fur_ch, pattern_index, bytes(current_pattern_data))
                )
    
    def _opna_rhythm_to_adpcma(self):
        """Extract OPNA rhythm commands (0xEB) from all channels and output to ADPCM-A.
        
        The 0xEB command triggers the YM2608 hardware rhythm unit:
        - Bit 0: BD (Bass Drum) -> Channel 9
        - Bit 1: SD (Snare Drum) -> Channel 10
        - Bit 2: TOP (Top Cymbal) -> Channel 11
        - Bit 3: HH (Hi-Hat) -> Channel 12
        - Bit 4: TOM (Tom) -> Channel 13
        - Bit 5: RIM (Rim Shot) -> Channel 14
        - Bit 7: Key Off flag
        
        Also handles 0xE9 for rhythm panning:
        - Format: (drum_index << 5) | pan_bits
        - pan_bits: 0x01=Right, 0x02=Left, 0x03=Center
        """
        # OPNA rhythm bit -> Furnace channel
        opna_rhythm_map = {0: 9, 1: 10, 2: 11, 3: 12, 4: 13, 5: 14}
        
        # PMD pan bits to Furnace 08xy effect
        # 0x01=Right -> 08 0F, 0x02=Left -> 08 F0, 0x03=Center -> 08 FF
        pan_to_furnace = {
            0x01: (0x08, 0x0F),  # Right only
            0x02: (0x08, 0xF0),  # Left only
            0x03: (0x08, 0xFF),  # Center (both)
            0x00: (0x08, 0xFF),  # Default to center
        }
        
        # Collect rhythm events from all channels: (tick, pan_effect or None)
        channel_events = {9: [], 10: [], 11: [], 12: [], 13: [], 14: []}
        
        # Track current pan state for each drum (0-5)
        rhythm_pan = {0: None, 1: None, 2: None, 3: None, 4: None, 5: None}
        
        # Scan all channels for 0xEB (rhythm keyon) and 0xE9 (rhythm pan) commands
        for ch in self.pmd.channels:
            
            tick = 0
            for event in ch.events:
                if isinstance(event, PMDNote):
                    tick += event.length
                elif isinstance(event, PMDCommand):
                    if event.cmd == 0xE9 and event.params:
                        # Rhythm panning: (drum_index << 5) | pan_bits
                        pan_byte = event.params[0]
                        drum_idx = (pan_byte >> 5) - 1  # 1-6 -> 0-5
                        pan_bits = pan_byte & 0x03
                        if 0 <= drum_idx <= 5:
                            rhythm_pan[drum_idx] = pan_to_furnace.get(pan_bits, (0x08, 0xFF))
                    
                    elif event.cmd == 0xEB and event.params:
                        rhythm_byte = event.params[0]
                        is_keyoff = bool(rhythm_byte & 0x80)
                        
                        for bit_idx in range(6):
                            if rhythm_byte & (1 << bit_idx):
                                fur_ch = opna_rhythm_map[bit_idx]
                                if not is_keyoff:
                                    # Include current pan state with the event
                                    pan_fx = rhythm_pan.get(bit_idx)
                                    channel_events[fur_ch].append((tick, pan_fx))
        
        # Convert to patterns
        TICKS_PER_ROW = 3
        
        for fur_ch in range(9, 15):
            events_for_ch = channel_events.get(fur_ch, [])
            if not events_for_ch:
                continue
            
            # Get instrument for this channel
            ins_idx = self.adpcma_drum_map.get(fur_ch)
            
            # Sort by tick and deduplicate (keep first pan value for each tick)
            events_for_ch.sort(key=lambda x: x[0])
            seen_ticks = set()
            unique_events = []
            for tick, pan_fx in events_for_ch:
                if tick not in seen_ticks:
                    seen_ticks.add(tick)
                    unique_events.append((tick, pan_fx))
            events_for_ch = unique_events
            
            current_pattern_data = bytearray()
            pattern_index = 0
            current_row_in_pattern = 0
            ins_written = False
            last_pan = None
            
            for tick_pos, pan_fx in events_for_ch:
                row = tick_pos // TICKS_PER_ROW
                target_pattern = row // self.pattern_length
                
                if target_pattern >= 200:
                    break
                
                while target_pattern > pattern_index:
                    rows_left = self.pattern_length - current_row_in_pattern
                    if rows_left > 0:
                        self._write_skip(current_pattern_data, rows_left)
                    current_pattern_data += b'\xFF'
                    self.patterns[fur_ch].append(
                        self._make_pattern(fur_ch, pattern_index, bytes(current_pattern_data))
                    )
                    pattern_index += 1
                    current_pattern_data = bytearray()
                    current_row_in_pattern = 0
                
                row_in_pattern = row - (pattern_index * self.pattern_length)
                skip_rows = row_in_pattern - current_row_in_pattern
                
                if skip_rows > 0:
                    self._write_skip(current_pattern_data, skip_rows)
                    current_row_in_pattern += skip_rows
                
                # Build effect list (pan effect if changed)
                fx_list = []
                if pan_fx is not None and pan_fx != last_pan:
                    fx_list.append(pan_fx)
                    last_pan = pan_fx
                    self.effects_count[fur_ch] = max(self.effects_count[fur_ch], 1)
                
                # Write drum note with optional pan effect
                note = 60  # C-4 for ADPCM-A
                ins_to_write = ins_idx if not ins_written else None
                if ins_to_write is not None:
                    ins_written = True
                
                entry = self._make_entry(note=note, ins=ins_to_write, fx=fx_list if fx_list else None)
                current_pattern_data += entry
                current_row_in_pattern += 1
            
            # Finish last pattern
            if current_row_in_pattern > 0:
                rows_left = self.pattern_length - current_row_in_pattern
                if rows_left > 0:
                    self._write_skip(current_pattern_data, rows_left)
                current_pattern_data += b'\xFF'
                self.patterns[fur_ch].append(
                    self._make_pattern(fur_ch, pattern_index, bytes(current_pattern_data))
                )
    
    def _collect_tempo_changes(self):
        """Pre-collect ALL tempo changes from all channels
        
        Tempo is GLOBAL in PMD, so we need to process tempo commands from all channels
        in chronological order to track the current TPQ correctly.
        
        Virtual tempo uses FDxx (numerator) and FExx (denominator).
        Effective tempo = base * (numerator / denominator)
        We use initial TPQ as numerator and current TPQ as denominator.
        """
        # Collect all tempo events from all channels with their tick positions
        all_tempo_events = []
        
        for ch in self.pmd.channels:
            tick_pos = 0
            for event in ch.events:
                if isinstance(event, PMDCommand) and event.cmd == 0xFC and event.params:
                    all_tempo_events.append((tick_pos, event.params))
                elif isinstance(event, PMDNote):
                    tick_pos += event.length
        
        if not all_tempo_events:
            return
        
        # Sort by tick position
        all_tempo_events.sort(key=lambda x: x[0])
        
        # Process in order with global tempo_48 tracking
        # tempo_48 is PMD's friendly tempo value (higher = faster)
        # Formula: tempo_48 = 0x112C / (256 - TimerB)
        # Baseline denominator = 75 (standard reference for BPM calculation)
        current_tempo_48 = 75  # Default
        BASELINE_TEMPO = 75    # Fixed denominator for virtual tempo ratio
        
        for tick_pos, params in all_tempo_events:
            tempo_fx = []
            
            if len(params) >= 2 and params[0] == 0xFF:
                # Set tempo_48 (t command) - higher value = faster
                current_tempo_48 = params[1]
                # Virtual tempo: current/baseline ratio
                # Higher current = faster, so numerator = current, denominator = baseline
                tempo_fx.append((0xFD, current_tempo_48))
                tempo_fx.append((0xFE, BASELINE_TEMPO))
            elif len(params) >= 2 and params[0] == 0xFD:
                # Add to tempo_48 (t± command) - gradual tempo change
                delta = params[1]
                delta = delta if delta < 128 else delta - 256  # Signed
                current_tempo_48 = max(18, min(255, current_tempo_48 + delta))
                tempo_fx.append((0xFD, current_tempo_48))
                tempo_fx.append((0xFE, BASELINE_TEMPO))
            elif len(params) >= 2 and params[0] == 0xFE:
                # Add to tempo - relative change (skip for now)
                pass
            elif len(params) == 1:
                # Raw tempo value (half-note BPM)
                raw_tempo = params[0]
                tempo_fx.append((0xFD, raw_tempo))
            
            if tempo_fx:
                # Check if we already have an entry at this tick
                existing = next((i for i, (t, _) in enumerate(self.tempo_changes) if t == tick_pos), None)
                if existing is not None:
                    # Merge with existing entry
                    self.tempo_changes[existing] = (tick_pos, tempo_fx)
                else:
                    self.tempo_changes.append((tick_pos, tempo_fx))
        
        # Sort final tempo changes by tick
        self.tempo_changes.sort(key=lambda x: x[0])
    
    def _tempo_to_adpcmb(self):
        """Output tempo changes on ADPCM-B channel (15)
        
        Tempo changes collected by _collect_tempo_changes are output as
        FDxx (numerator) and FExx (denominator) effects on channel 15.
        """
        if not self.tempo_changes:
            return
        
        fur_ch = 15  # ADPCM-B channel
        TICKS_PER_ROW = 3
        
        current_pattern_data = bytearray()
        pattern_index = 0
        current_row_in_pattern = 0
        
        for tick_pos, fx_list in sorted(self.tempo_changes, key=lambda x: x[0]):
            row = tick_pos // TICKS_PER_ROW
            target_pattern = row // self.pattern_length
            
            if target_pattern >= 200:
                break
            
            # Handle pattern boundaries
            while target_pattern > pattern_index:
                rows_left = self.pattern_length - current_row_in_pattern
                if rows_left > 0:
                    self._write_skip(current_pattern_data, rows_left)
                current_pattern_data += b'\xFF'
                self.patterns[fur_ch].append(
                    self._make_pattern(fur_ch, pattern_index, bytes(current_pattern_data))
                )
                pattern_index += 1
                current_pattern_data = bytearray()
                current_row_in_pattern = 0
            
            row_in_pattern = row - (pattern_index * self.pattern_length)
            skip_rows = row_in_pattern - current_row_in_pattern
            
            if skip_rows > 0:
                self._write_skip(current_pattern_data, skip_rows)
                current_row_in_pattern += skip_rows
            
            # Write entry with tempo effects
            entry = self._make_entry(fx=fx_list)
            current_pattern_data += entry
            current_row_in_pattern += 1
            
            # Update effects count
            self.effects_count[fur_ch] = max(self.effects_count[fur_ch], len(fx_list))
        
        # Finish last pattern
        if current_row_in_pattern > 0:
            rows_left = self.pattern_length - current_row_in_pattern
            if rows_left > 0:
                self._write_skip(current_pattern_data, rows_left)
            current_pattern_data += b'\xFF'
            self.patterns[fur_ch].append(
                self._make_pattern(fur_ch, pattern_index, bytes(current_pattern_data))
            )
    
    def _create_ssg_drum_kit(self):
        """Create SSG drum instruments based on working Furnace drum kit
        
        These definitions are from drums.txt - using FIXED arpeggio mode.
        Fixed arpeggio values use 0x40000000 | note to play absolute notes.
        Returns dict mapping effect number to instrument index
        """
        # FIXED_FLAG for absolute note values in arpeggio
        FIXED = 0x40000000
        
        # Drum definitions matching drums.txt exactly
        SSG_DRUMS = {
            0: {  # Bass Drum - pitch sweep down from fixed notes
                'name': 'Bass Drum',
                'vol': [15, 13, 12, 11, 10, 9, 8, 7, 0],
                # @28 @26 @25 @24 @23 @22 @21 @20 @19 0
                'arp_fixed': [28, 26, 25, 24, 23, 22, 21, 20, 19],
                'arp_rel': [0],  # ends with relative 0
                'duty': [0, 0],
                'wave': [3, 1],  # tone+noise, then tone only
            },
            1: {  # Snare Drum 1 - noise with pitch sweep
                'name': 'Snare Drum',
                'vol': [15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 0],
                # @51 @47 @44 @42 @40 @38 @36 @34 @33 @32 @30 @29 @28 @27 0
                'arp_fixed': [51, 47, 44, 42, 40, 38, 36, 34, 33, 32, 30, 29, 28, 27],
                'arp_rel': [0],
                'duty': [24, 25, 26, 27, 28, 29, 30, 31],
                'wave': [3, 3, 3, 3, 3, 3, 3, 3],
            },
            6: {  # Snare Drum 2 (PMD effect 6) - noise only, no arpeggio
                'name': 'Snare 2',
                'vol': [15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
                'duty': [16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31],
                'wave': [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2],  # noise only
            },
            9: {  # Crash Cymbal - high pitch G-7 (151)
                'name': 'Crash Cymbal',
                'vol': [15, 15, 14, 14, 13, 13, 12, 12, 11, 11, 10, 10, 9, 9, 8, 8, 0],
                'arp_fixed': [91],
                'arp_rel': [0],
                'duty': list(range(32)),  # 0-31 sweep
                'wave': [3] * 32,
            },
            7: {  # Hi-Hat Closed - short high noise G-7 (151)
                'name': 'Hi-Hat Closed',
                'vol': [15, 10, 5, 0],
                'arp_fixed': [91],
                'arp_rel': [0],
                'duty': [31, 30],
                'wave': [3, 3],
            },
            8: {  # Hi-Hat Open - longer high noise G-7 (151)
                'name': 'Hi-Hat Open',
                'vol': [15, 15, 14, 14, 13, 13, 12, 12, 11, 11, 10, 10, 9, 9, 8, 8, 0],
                'arp_fixed': [91],
                'arp_rel': [0],
                'duty': [31, 30],
                'wave': [3, 3],
            },
            # Toms - note is placed in pattern (like cymbals)
            # Low Tom: period 700 ≈ 160 Hz ≈ E-3 (note 88)
            # Mid Tom: period 500 ≈ 224 Hz ≈ A-3 (note 93)
            # High Tom: period 300 ≈ 373 Hz ≈ F#4 (note 102)
            2: {  # Low Tom
                'name': 'Low Tom',
                'vol': [15, 15, 14, 14, 13, 13, 0],
                'duty': [31],
                'wave': [3],
                'pitch': [-16],
                'pitch_mode': 1,
            },
            3: {  # Mid Tom
                'name': 'Mid Tom',
                'vol': [15, 15, 14, 14, 13, 13, 0],
                'duty': [31],
                'wave': [3],
                'pitch': [-8],
                'pitch_mode': 1,
            },
            4: {  # High Tom
                'name': 'High Tom',
                'vol': [15, 15, 14, 14, 13, 13, 0],
                'duty': [31],
                'wave': [3],
                'pitch': [-4],
                'pitch_mode': 1,
            },
            5: {  # Rim Shot
                'name': 'Rim Shot',
                'vol': [15, 13, 11, 9, 7, 5, 3, 1, 0],
                'pitch': [-2],
                'pitch_mode': 1,
            },
            10: {  # Ride Cymbal - high pitch G-7 (151)
                'name': 'Ride Cymbal',
                'vol': [15, 15, 14, 14, 13, 13, 12, 12, 11, 11, 10, 10, 9, 9, 8, 8, 0],
                'arp_fixed': [151],
                'arp_rel': [0],
                'duty': [31, 30],
                'wave': [3, 3],
            },
        }
        
        drum_map = {}  # effect_num -> instrument_index
        
        for effect_num, drum_def in SSG_DRUMS.items():
            ins_idx = len(self.instruments)
            ins_data = self._make_ssg_drum_from_def(drum_def)
            self.instruments.append(ins_data)
            drum_map[effect_num] = ins_idx
        
        return drum_map
    
    def _make_ssg_drum_from_def(self, drum_def: dict) -> bytes:
        """Create SSG drum instrument from definition dict"""
        FIXED_FLAG = 0x40000000
        
        name = drum_def.get('name', 'SSG Drum')
        vol = drum_def.get('vol', [15, 0])
        arp_fixed = drum_def.get('arp_fixed', None)
        arp_rel = drum_def.get('arp_rel', None)
        duty = drum_def.get('duty', None)
        wave = drum_def.get('wave', None)
        pitch = drum_def.get('pitch', None)
        pitch_mode = drum_def.get('pitch_mode', 0)
        
        macros = []
        
        # Volume macro (type 0)
        macro_vol = bytes([
            0,  # macroType: DIV_MACRO_VOL
            len(vol),
            255,  # loop
            255,  # rel
            0,  # mode
            0x01,  # open
            0,  # delay
            1,  # speed
        ]) + bytes(vol)
        macros.append(macro_vol)
        
        # Arpeggio macro (type 1) - with fixed notes using 32-bit values
        if arp_fixed or arp_rel:
            arp_values = []
            # Add fixed values (with 0x40000000 flag)
            if arp_fixed:
                for note in arp_fixed:
                    arp_values.append(FIXED_FLAG | note)
            # Add relative values
            if arp_rel:
                for rel in arp_rel:
                    arp_values.append(rel)  # No flag = relative
            
            # Pack as 32-bit little-endian values
            arp_bytes = b''
            for val in arp_values:
                arp_bytes += struct.pack('<I', val)  # unsigned 32-bit
            
            macro_arp = bytes([
                1,  # macroType: DIV_MACRO_ARP
                len(arp_values),
                255,  # loop
                255,  # rel
                0,  # mode (0 = sequence)
                0xC1,  # open/type/wordSize: bits 6-7=3 (32-bit signed), bit 0=1 (open)
                0,  # delay
                1,  # speed
            ]) + arp_bytes
            macros.append(macro_arp)
        
        # Duty/Noise freq macro (type 2)
        if duty:
            macro_duty = bytes([
                2,  # macroType: DIV_MACRO_DUTY
                len(duty),
                255,  # loop
                255,  # rel
                0,  # mode
                0x01,  # open
                0,  # delay
                1,  # speed
            ]) + bytes(duty)
            macros.append(macro_duty)
        
        # Waveform macro (type 3)
        if wave:
            macro_wave = bytes([
                3,  # macroType: DIV_MACRO_WAVE
                len(wave),
                255,  # loop
                255,  # rel
                0,  # mode
                0x01,  # open
                0,  # delay
                1,  # speed
            ]) + bytes(wave)
            macros.append(macro_wave)
        
        # Pitch macro (type 4)
        if pitch:
            # Pitch values are signed, pack as signed bytes or words
            pitch_bytes = b''
            for val in pitch:
                if val < -128 or val > 127:
                    pitch_bytes += struct.pack('<h', val)  # 16-bit
                else:
                    pitch_bytes += struct.pack('<b', val)  # 8-bit signed
            
            macro_pitch = bytes([
                4,  # macroType: DIV_MACRO_PITCH
                len(pitch),
                255,  # loop
                255,  # rel
                pitch_mode,  # mode (1 = relative)
                0x01,  # open
                0,  # delay
                1,  # speed
            ]) + pitch_bytes
            macros.append(macro_pitch)
        
        # MA feature
        ma_content = pack_short(8) + b''.join(macros) + bytes([255])
        
        feature_ma = [
            b'MA',
            pack_short(len(ma_content)),
            ma_content
        ]
        
        # Name feature
        feature_name = [
            b'NA',
            pack_short(0),
            pack_string(name)
        ]
        feature_name[1] = pack_short(bl_length(feature_name[2:]))
        
        # Full instrument - type 6 = AY-3-8910
        ins_block = [
            b'INS2',
            pack_long(0),
            pack_short(TARGET_FURNACE_VERSION),
            pack_short(6),  # AY-3-8910
            b''.join(feature_name),
            b''.join(feature_ma),
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
    
    def _parse_rhythm_patterns(self) -> List[Tuple[List[Tuple[int, int, int]], int]]:
        """Parse R pattern definitions from the rhythm table.
        
        R patterns contain SSG drum sequences using @<value> instruments.
        Format in compiled .M file:
        - 0x00-0x7F ll: rest, length in next byte
        - 0x80-0xBF bb ll: drum note (cmd + bb form 14-bit value, ll = length)
        - 0xC0-0xFE: commands
        - 0xFF: end of pattern (return)
        
        Drum value calculation: ((cmd << 8) | bb) & 0x3FFF
        This gives the @value used in MML (bit flags for drum selection)
        
        Returns list of (events, total_duration) where events is list of (tick, drum_value, length)
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
                patterns.append(([], 0))
                continue
            
            # Convert to absolute offset (add 1 for PMD's addressing)
            abs_ptr = pattern_ptr + 1
            
            # Validate pointer
            if abs_ptr >= len(data) or abs_ptr < 1:
                patterns.append(([], 0))
                continue
            
            # Parse R pattern events with loop execution
            pattern_events = []
            tick = 0
            offset = abs_ptr
            max_iterations = 2000  # Allow more iterations for loops
            iterations = 0
            
            # Loop state - we need to track loop counters
            # PMD loops: F9 points to F8's counter position
            # F8 format: [loop_count, counter, jump_lo, jump_hi]
            # We'll use a modified copy of the data to track counters
            loop_counters = {}  # offset -> current count
            
            while offset < len(data) and iterations < max_iterations:
                iterations += 1
                cmd = data[offset]
                offset += 1
                
                if cmd == 0xFF:  # End of pattern (return)
                    break
                elif cmd >= 0x80 and cmd <= 0xBF:
                    # Drum note: cmd (0x80-0xBF), next_byte, length
                    if offset + 1 >= len(data):
                        break
                    next_byte = data[offset]
                    length = data[offset + 1]
                    offset += 2
                    
                    if length == 0:
                        length = 1
                    
                    drum_val = ((cmd << 8) | next_byte) & 0x3FFF
                    pattern_events.append((tick, drum_val, length))
                    tick += length
                elif cmd < 0x80:
                    # Rest: cmd (00-7F) is marker, length in next byte
                    if offset < len(data):
                        length = data[offset]
                        offset += 1
                        if length > 0:
                            tick += length
                elif cmd >= 0xC0:
                    # Commands
                    if cmd == 0xF9:  # [ loop start - points to F8's counter
                        if offset + 1 < len(data):
                            # Just skip - F8 will handle the actual looping
                            offset += 2
                    elif cmd == 0xF8:  # ] loop end
                        if offset + 3 < len(data):
                            loop_count = data[offset]
                            counter_offset = offset + 1
                            # Jump target: stored offset + 1 (PMD addressing) + 2 (skip F9 params)
                            jump_target = (data[offset + 2] | (data[offset + 3] << 8)) + 1 + 2
                            
                            # Initialize or increment counter
                            if counter_offset not in loop_counters:
                                loop_counters[counter_offset] = 0
                            loop_counters[counter_offset] += 1
                            
                            # Check if we should loop
                            if loop_count == 0:
                                # Infinite loop - just do it twice for conversion
                                if loop_counters[counter_offset] < 2:
                                    offset = jump_target
                                else:
                                    offset += 4
                            elif loop_counters[counter_offset] < loop_count:
                                offset = jump_target
                            else:
                                offset += 4
                                # Reset counter for next time
                                loop_counters[counter_offset] = 0
                        else:
                            break
                    elif cmd == 0xF7:  # : loop exit
                        if offset + 1 < len(data):
                            target_offset = (data[offset] | (data[offset + 1] << 8)) + 1
                            offset += 2
                            # Check if this is the last iteration
                            counter_offset = target_offset + 1  # counter is at target + 1
                            loop_count_at_target = data[target_offset] if target_offset < len(data) else 1
                            current_count = loop_counters.get(counter_offset, 0)
                            if current_count == loop_count_at_target - 1:
                                # Exit - jump past the loop end
                                offset = target_offset + 4
                    elif cmd == 0xE8:  # \V rhythm master volume
                        offset += 1 if offset < len(data) else 0
                    elif cmd == 0xEA:  # \v rhythm channel volume
                        offset += 1 if offset < len(data) else 0
                    elif cmd == 0xE9:  # \<pan> rhythm pan
                        offset += 1 if offset < len(data) else 0
                    elif cmd == 0xFD:  # v volume
                        offset += 1 if offset < len(data) else 0
                    elif cmd == 0xDF:  # C zenlen
                        offset += 1 if offset < len(data) else 0
                    elif cmd == 0xFC:  # t tempo
                        if offset < len(data):
                            sub = data[offset]
                            offset += 1
                            if sub >= 0xFB and offset < len(data):
                                offset += 1
                    # else: unknown command, skip
            
            patterns.append((pattern_events, tick))
        
        return patterns
    
    def _rhythm_to_patterns(self, ssg_drum_map: dict):
        """Convert K/R rhythm channel to Furnace SSG-I channel (8)
        
        PMD K/R system:
        - K channel contains R pattern references (R0, R1, etc.)
        - R patterns contain SSG drum definitions with @<value> instruments
        - Drums play on SSG channel 3 (SSG-I in Furnace = channel 8)
        
        SSG Drum instrument values:
        - @128 = effect 0 (Bass Drum)
        - @129 = effect 1 (Snare Drum)
        - @130 = effect 2 (Low Tom)
        - etc. (value - 128 = effect index)
        
        Values >= 128 are SSG effect indices, values < 128 are hardware rhythm flags
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
        # K channel uses bytes 0x00-0x7F as R pattern indices (single byte, no length!)
        # Need to handle loop commands (F9/F8/F7) properly
        all_events = []
        tick = 0
        max_tick = 100000
        
        # Loop handling similar to _channel_to_patterns
        loop_stack = []
        event_index = 0
        events = rhythm_ch.events
        
        while event_index < len(events) and tick < max_tick:
            event = events[event_index]
            
            if isinstance(event, PMDNote):
                if event.is_rest:
                    tick += event.length
                else:
                    # K channel: raw byte value is stored as (octave << 4) | note
                    raw_byte = (event.octave << 4) | event.note
                    pattern_idx = raw_byte  # Direct R pattern index (0-127)
                    
                    if 0 <= pattern_idx < len(r_patterns):
                        r_pattern_events, r_duration = r_patterns[pattern_idx]
                        
                        # Add each drum event from the R pattern
                        for pat_tick, drum_val, drum_len in r_pattern_events:
                            all_events.append((tick + pat_tick, drum_val, drum_len))
                        
                        # Advance tick by the R pattern's total duration
                        if r_duration > 0:
                            tick += r_duration
                event_index += 1
                
            elif isinstance(event, PMDCommand):
                if event.cmd == 0xF9:  # Loop start [
                    # Find matching ] and get loop count
                    depth = 1
                    scan_idx = event_index + 1
                    loop_count = 2
                    while scan_idx < len(events) and depth > 0:
                        scan_event = events[scan_idx]
                        if isinstance(scan_event, PMDCommand):
                            if scan_event.cmd == 0xF9:
                                depth += 1
                            elif scan_event.cmd == 0xF8:
                                depth -= 1
                                if depth == 0:
                                    loop_count = scan_event.params[0] if scan_event.params else 2
                        scan_idx += 1
                    
                    loop_stack.append({
                        'start': event_index + 1,
                        'count': loop_count,
                        'iteration': 0,
                    })
                    event_index += 1
                    
                elif event.cmd == 0xF8:  # Loop end ]
                    if loop_stack:
                        loop_info = loop_stack[-1]
                        loop_info['iteration'] += 1
                        
                        if loop_info['count'] == 0:
                            # Infinite loop - do 2 iterations
                            if loop_info['iteration'] < 2:
                                event_index = loop_info['start']
                            else:
                                loop_stack.pop()
                                event_index += 1
                        elif loop_info['iteration'] < loop_info['count']:
                            event_index = loop_info['start']
                        else:
                            loop_stack.pop()
                            event_index += 1
                    else:
                        event_index += 1
                        
                elif event.cmd == 0xF7:  # Loop escape :
                    if loop_stack:
                        loop_info = loop_stack[-1]
                        if loop_info['iteration'] == loop_info['count'] - 1:
                            # Skip to end of loop
                            depth = 1
                            skip_idx = event_index + 1
                            while skip_idx < len(events) and depth > 0:
                                cmd = events[skip_idx]
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
                    event_index += 1
            else:
                event_index += 1
        
        if not all_events:
            return
        
        # Limit events
        if len(all_events) > 5000:
            all_events = all_events[:5000]
        
        # Map drum values to notes and instruments
        # PMD drum values are BIT FLAGS where bit position = drum index:
        #   Bit 0 (@1)    = Bass Drum (SSG drum 0)
        #   Bit 1 (@2)    = Snare 1 (SSG drum 1)
        #   Bit 2 (@4)    = Low Tom (SSG drum 2)
        #   Bit 3 (@8)    = Mid Tom (SSG drum 3)
        #   Bit 4 (@16)   = High Tom (SSG drum 4)
        #   Bit 5 (@32)   = Rim Shot (SSG drum 5)
        #   Bit 6 (@64)   = Snare 2 (SSG drum 6)
        #   Bit 7 (@128)  = HH Closed (SSG drum 7)
        #   Bit 8 (@256)  = HH Open (SSG drum 8)
        #   Bit 9 (@512)  = Crash (SSG drum 9)
        #   Bit 10 (@1024)= Ride (SSG drum 10)
        # Lowest set bit takes priority for SSG channel
        def get_drum_note_and_ins(drum_val):
            # Find lowest set bit to determine which SSG drum plays
            # Different drums need different notes based on PMDWin frequencies
            # Furnace note numbering: C-0 = 60
            
            # Note mapping by bit index (from PMDWin table.cpp frequencies)
            DRUM_NOTES = {
                0: 36,   # Bass Drum - C_2 (low trigger)
                1: 36,   # Snare 1 - C_2
                2: 88,   # Low Tom - E-3 (~160 Hz)
                3: 93,   # Mid Tom - A-3 (~224 Hz)
                4: 102,  # High Tom - F#4 (~373 Hz)
                5: 36,   # Rim Shot - C_2
                6: 36,   # Snare 2 - C_2
                7: 151,  # HH Closed - G-7
                8: 151,  # HH Open - G-7
                9: 151,  # Crash - G-7
                10: 151, # Ride - G-7
            }
            
            for bit_idx in range(11):  # Bits 0-10
                bit_mask = 1 << bit_idx
                if drum_val & bit_mask:
                    # Map bit index to SSG drum instrument
                    ins = ssg_drum_map.get(bit_idx, ssg_drum_map.get(0, None))
                    note = DRUM_NOTES.get(bit_idx, 36)
                    return note, ins
            # Fallback to bass drum
            return 36, ssg_drum_map.get(0, None)
        
        # Convert to SSG-I channel (8)
        fur_channel = 8
        TICKS_PER_ROW = 3  # Match the main channel conversion
        max_patterns = 200
        
        current_pattern_data = bytearray()
        pattern_index = 0
        current_row_in_pattern = 0
        last_ins = None
        
        for tick, drum_val, length in all_events:
            row = tick // TICKS_PER_ROW
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
            
            # Get note and instrument for this drum
            note, ins = get_drum_note_and_ins(drum_val)
            
            # Only write instrument if changed
            ins_to_write = ins if ins != last_ins else None
            if ins is not None:
                last_ins = ins
            
            # Write drum note to SSG-I
            entry = self._make_entry(note=note, ins=ins_to_write)
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
        # Gate time: qdata = ticks before note end to keyoff (0 = full length, higher = more staccato)
        current_qdata = 0  # Direct q value (ticks to cut from end)
        current_qdatb = 0  # Q percentage value (0-8, where gate = length * Q / 8)
        
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
                
                # Stop pitch slide from previous portamento
                if pitch_slide_active and not event.is_rest:
                    note_effects.append((0xE1, 0x00))  # E100 = stop slide
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
                
                # Calculate actual gate time (qdat) for this note
                # PMD gate time: qdat = ticks before note end to keyoff
                # q command: direct ticks value
                # Q command: percentage (length * Q / 8)
                qdat = current_qdata
                if current_qdatb > 0:
                    qdat += (event.length * current_qdatb) // 8
                
                # Calculate release timing for FCxx effect (FM channels only)
                # qdat = ticks before note end to trigger keyoff
                cut_tick_offset = None  # Tick offset within row for FCxx effect
                
                if not event.is_rest and channel.channel_type == 'fm' and qdat > 0 and qdat < event.length:
                    # Calculate when cut happens relative to note start
                    ticks_until_cut = event.length - qdat
                    note_row = tick_pos // TICKS_PER_ROW
                    cut_tick = tick_pos + ticks_until_cut
                    cut_row = cut_tick // TICKS_PER_ROW
                    cut_tick_in_row = cut_tick % TICKS_PER_ROW
                    
                    if cut_row == note_row:
                        # Release is in the same row as note - use FCxx on the note
                        cut_tick_offset = cut_tick_in_row
                    else:
                        # Cut is on a different row - add NOTE_OFF event with tick offset
                        events_with_ticks.append((cut_tick, 'NOTE_OFF', 0, None, [], None, 0, cut_tick_in_row))
                
                events_with_ticks.append((tick_pos, event, current_transpose + current_master, current_volume, note_effects, ssg_env_for_note, qdat, cut_tick_offset))
                
                # SSG: always use NOTE_RELEASE for macro release (unchanged)
                if not event.is_rest and channel.channel_type == 'ssg':
                    if qdat > 0 and qdat < event.length:
                        release_tick = tick_pos + event.length - qdat
                    else:
                        release_tick = tick_pos + event.length
                    if release_tick > tick_pos:
                        events_with_ticks.append((release_tick, 'NOTE_RELEASE', 0, None, [], None, 0, None))
                
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
                    # Note: Tempo (0xFC) is now handled globally in _collect_tempo_changes
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
                    elif event.cmd == 0xFE and event.params:  # Gate time q
                        # q command: direct ticks value (ticks to cut from end)
                        # Higher value = more staccato, 0 = full length
                        current_qdata = event.params[0]
                    elif event.cmd == 0xC4 and event.params:  # Gate time Q
                        # Q command: percentage-based (0-8 range, gate = length * Q / 8)
                        current_qdatb = event.params[0]
                    elif event.cmd == 0xDA and len(event.params) >= 3:  # Portamento { }
                        # Params: [start_note, end_note, duration]
                        # PMD portamento: smooth pitch slide from START to END over duration
                        # 
                        # Furnace approach: Use E1xy (slide up) or E2xy (slide down)
                        # - Play the START note (transpose applied separately)
                        # - Add E1xy or E2xy to slide toward END
                        # - x = speed (1-F), y = semitones to slide (1-F)
                        #
                        start_byte = event.params[0]
                        end_byte = event.params[1]
                        duration = event.params[2]
                        
                        start_octave = (start_byte >> 4) & 0x0F
                        start_note_val = start_byte & 0x0F
                        end_octave = (end_byte >> 4) & 0x0F
                        end_note_val = end_byte & 0x0F
                        
                        # Calculate semitone difference for slide direction
                        # Transpose doesn't affect the difference since it's applied equally
                        start_semitones = start_octave * 12 + start_note_val
                        end_semitones = end_octave * 12 + end_note_val
                        semitone_diff = end_semitones - start_semitones
                        
                        porta_effects = list(pending_effects)
                        
                        if semitone_diff != 0:
                            semitones = min(15, abs(semitone_diff))  # Max 15 for y nibble
                            
                            # Calculate speed: need to cover 'semitones' in 'duration' ticks
                            # User testing: E227 (speed 2) works for 7 semitones in 3 ticks
                            # Formula: speed = semitones / duration (integer division)
                            speed = max(1, min(15, semitones // max(1, duration)))
                            
                            if semitone_diff > 0:
                                # Slide UP - use E1xy
                                porta_effects.append((0xE1, (speed << 4) | semitones))
                            else:
                                # Slide DOWN - use E2xy
                                porta_effects.append((0xE2, (speed << 4) | semitones))
                        
                        # Always play the START note (where the slide begins)
                        porta_note = PMDNote(note=start_note_val, octave=start_octave, length=duration)
                        
                        if tie_active:
                            # Tied portamento - slide continues from previous pitch
                            events_with_ticks.append((tick_pos, 'SLIDE_EFFECT', 0, None, porta_effects))
                            tie_active = False
                        else:
                            events_with_ticks.append((tick_pos, porta_note, current_transpose + current_master, current_volume, porta_effects))
                        
                        pitch_slide_active = True  # Mark to stop slide on next note
                        tick_pos += duration
                        pending_effects = []
                        event_index += 1
                        continue  # Skip adding the command itself
                    events_with_ticks.append((tick_pos, event, 0, current_volume, []))
                    event_index += 1
            else:
                event_index += 1
        
        # Sort events by tick position with proper priority:
        # - Commands (instrument/volume changes) must come BEFORE notes at the same tick
        # - Notes take priority over releases (so releases don't overwrite notes)
        def event_sort_key(item):
            tick = item[0]
            event = item[1]
            # Priority: 0 = commands (processed first), 1 = notes, 2 = slides, 3 = releases/offs
            if isinstance(event, PMDCommand):
                priority = 0  # Commands must be processed before notes at same tick
            elif isinstance(event, PMDNote):
                priority = 1
            elif event == 'SLIDE_EFFECT':
                priority = 2
            elif event in ('NOTE_RELEASE', 'NOTE_OFF'):
                priority = 3
            else:
                priority = 2  # Other events
            return (tick, priority)
        
        events_with_ticks.sort(key=event_sort_key)
        
        # Second pass: place notes at correct row positions
        last_row = -1
        
        # For FM channels, add envelope hard reset effect (30xx) to the first note
        fm_hard_reset_added = False
        for item in events_with_ticks:
            # Unpack with cut_tick_offset for FCxx effects
            cut_tick_offset = None
            if len(item) == 8:
                tick_pos, event, note_transpose, note_volume, note_effects, note_ssg_env, note_qdat, cut_tick_offset = item
            elif len(item) == 7:
                tick_pos, event, note_transpose, note_volume, note_effects, note_ssg_env, note_qdat = item
            elif len(item) == 6:
                tick_pos, event, note_transpose, note_volume, note_effects, note_ssg_env = item
                note_qdat = 0
            elif len(item) == 5:
                tick_pos, event, note_transpose, note_volume, note_effects = item
                note_ssg_env = None
                note_qdat = 0
            elif len(item) == 4:
                tick_pos, event, note_transpose, note_volume = item
                note_effects = []
                note_ssg_env = None
                note_qdat = 0
            elif len(item) == 3:
                tick_pos, event, note_transpose = item
                note_volume = None
                note_effects = []
                note_ssg_env = None
                note_qdat = 0
            else:
                tick_pos, event = item
                note_transpose = 0
                note_volume = None
                note_effects = []
                note_qdat = 0
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
                
                # Build effects list
                fx_list = list(note_effects) if note_effects else []
                
                # Add 3001 (envelope hard reset) to first FM note to fix fade artifacts
                if channel.channel_type == 'fm' and not fm_hard_reset_added:
                    fx_list.insert(0, (0x30, 0x01))
                    fm_hard_reset_added = True
                
                # Add FCxx (note release) effect for FM channels with same-row gate time
                if cut_tick_offset is not None and channel.channel_type == 'fm':
                    fx_list.append((0xFC, cut_tick_offset))
                
                # Add effects if any
                fx_to_set = fx_list if fx_list else None
                
                # Update effects column count if needed (max 8 columns)
                if fx_to_set and len(fx_to_set) > 0:
                    self.effects_count[fur_channel] = min(8, max(self.effects_count[fur_channel], len(fx_to_set)))
                
                entry = self._make_entry(note=fur_note, ins=ins_to_set, vol=vol_to_set, fx=fx_to_set)
                current_pattern_data += entry
                current_row_in_pattern += 1
                pending_ins = None
                pending_vol = None
                last_row = row
                # Track last content tick for loop jump placement (end of note, not start)
                note_end_tick = tick_pos + (event.length if hasattr(event, 'length') else 0)
                self.last_content_tick = max(self.last_content_tick, note_end_tick)
            
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
                
                # Update effects column count (max 8 columns)
                if note_effects and len(note_effects) > 0:
                    self.effects_count[fur_channel] = min(8, max(self.effects_count[fur_channel], len(note_effects)))
                
                # Add entry with just the effect (no note, no instrument, no volume)
                entry = self._make_entry(fx=note_effects)
                current_pattern_data += entry
                current_row_in_pattern += 1
                last_row = row
            
            elif event == 'NOTE_RELEASE':
                # Skip if there's already a note on this row (notes take priority over releases)
                if row == last_row:
                    continue
                
                # Write a REL note (182) to trigger macro release phase (SSG)
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
                
                # Write REL note (182) - triggers macro release only
                entry = self._make_entry(note=FUR_NOTE_REL)
                current_pattern_data += entry
                current_row_in_pattern += 1
                last_row = row
            
            elif event == 'NOTE_OFF':
                # Skip if there's already a note on this row (notes take priority over offs)
                if row == last_row:
                    continue
                
                # Use FCxx effect for precise note release timing
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
                
                # Use FCxx effect with tick offset for precise release, otherwise use FC00
                tick_offset = cut_tick_offset if cut_tick_offset is not None else 0
                entry = self._make_entry(fx=[(0xFC, tick_offset)])
                current_pattern_data += entry
                current_row_in_pattern += 1
                last_row = row
                self.effects_count[fur_channel] = max(self.effects_count[fur_channel], 1)
                
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
        
        # Create ADPCM-A drum kit for hardware rhythm (0xEB commands)
        self.adpcma_drum_map = self._create_adpcma_drum_kit()
        
        # Create SSG drum kit instruments for K/R channel
        self.ssg_drum_map = self._create_ssg_drum_kit()
        
        # Map PMD channels to Furnace channels
        # YM2608: FM1-6 (0-5), SSG1-3 (6-8), ADPCM-B (9), ADPCM-A Rhythm (10-15)
        # SSG-I (channel 8) is used for SSG drums from K/R channel
        # ADPCM-A channels 10-15 are used for hardware rhythm (0xEB commands)
        channel_map = {
            'FM-A': 0, 'FM-B': 1, 'FM-C': 2, 'FM-D': 3, 'FM-E': 4, 'FM-F': 5,
            'SSG-G': 6, 'SSG-H': 7,
            # 'SSG-I': 8,  # Reserved for SSG drums from K/R
            'ADPCM-J': 9
        }
        
        # Pre-collect ALL tempo changes from all channels (tempo is global)
        self._collect_tempo_changes()
        
        # Convert each channel
        for ch in self.pmd.channels:
            if ch.name in channel_map:
                fur_ch = channel_map[ch.name]
                # Skip channels with no actual notes
                note_count = sum(1 for e in ch.events if isinstance(e, PMDNote) and not e.is_rest)
                if note_count > 0:
                    self._channel_to_patterns(ch, fur_ch)
        
        # Convert K/R rhythm channel:
        # 1. SSG-I (channel 8) plays lowest bit only (SSG limitation)
        self._rhythm_to_patterns(self.ssg_drum_map)
        
        # 2. ADPCM-A (channels 9-14) plays ALL triggered drums (RSS hardware)
        # Create mapping: SSG drum bit -> (ADPCM-A channel, instrument)
        ssg_to_adpcma_map = {
            0: (9, self.adpcma_drum_map[9]),     # @1 Bass Drum -> BD
            1: (10, self.adpcma_drum_map[10]),   # @2 Snare 1 -> SD
            2: (13, self.adpcma_drum_map[13]),   # @4 Low Tom -> TOM
            3: (13, self.adpcma_drum_map[13]),   # @8 Mid Tom -> TOM
            4: (13, self.adpcma_drum_map[13]),   # @16 High Tom -> TOM
            5: (14, self.adpcma_drum_map[14]),   # @32 Rim Shot -> RIM
            6: (10, self.adpcma_drum_map[10]),   # @64 Snare 2 -> SD
            7: (12, self.adpcma_drum_map[12]),   # @128 HH Closed -> HH
            8: (12, self.adpcma_drum_map[12]),   # @256 HH Open -> HH
            9: (11, self.adpcma_drum_map[11]),   # @512 Crash -> TOP
            10: (11, self.adpcma_drum_map[11]),  # @1024 Ride -> TOP
        }
        self._rhythm_to_adpcma(ssg_to_adpcma_map)
        
        # 3. Also handle 0xEB commands from other channels (direct OPNA rhythm triggers)
        self._opna_rhythm_to_adpcma()
        
        # 4. Output tempo changes on ADPCM-B channel (15)
        self._tempo_to_adpcmb()
        
        # Determine order count
        self.order_count = max(len(pats) for pats in self.patterns) if any(self.patterns) else 1
        
        # Fill empty channels with empty patterns
        for ch_idx in range(self.channel_count):
            while len(self.patterns[ch_idx]) < self.order_count:
                idx = len(self.patterns[ch_idx])
                empty_pat = self._make_pattern(ch_idx, idx, b'\xFF')
                self.patterns[ch_idx].append(empty_pat)
        
        # Add loop jump (0Bxx) where the song content actually ends
        # Use ADPCM-B channel (15) which is usually empty, to avoid overwriting notes
        if self.loop_point_order is not None and self.order_count > 0:
            loop_channel = 15  # ADPCM-B channel - usually empty
            
            # Calculate the row where content ends
            TICKS_PER_ROW = 3
            last_content_row = self.last_content_tick // TICKS_PER_ROW
            target_pattern = last_content_row // self.pattern_length
            row_in_pattern = last_content_row % self.pattern_length
            
            # Make sure we don't exceed order count
            if target_pattern >= self.order_count:
                target_pattern = self.order_count - 1
                row_in_pattern = self.pattern_length - 1
            
            # Create a pattern with the loop jump at the correct row
            loop_jump_fx = [(0x0B, self.loop_point_order)]
            loop_data = bytearray()
            # Skip to the target row
            if row_in_pattern > 0:
                self._write_skip(loop_data, row_in_pattern)
            # Add an empty entry with the loop jump effect
            loop_data += self._make_entry(fx=loop_jump_fx)
            # Fill remaining rows
            remaining = self.pattern_length - row_in_pattern - 1
            if remaining > 0:
                self._write_skip(loop_data, remaining)
            loop_data += b'\xFF'
            
            # Replace or add the pattern on the loop channel
            while len(self.patterns[loop_channel]) <= target_pattern:
                idx = len(self.patterns[loop_channel])
                empty_pat = self._make_pattern(loop_channel, idx, b'\xFF')
                self.patterns[loop_channel].append(empty_pat)
            
            self.patterns[loop_channel][target_pattern] = self._make_pattern(loop_channel, target_pattern, bytes(loop_data))
            # Make sure we have enough effect columns
            self.effects_count[loop_channel] = max(self.effects_count[loop_channel], 1)
        
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
            pack_byte(3),   # speed 1 (3 ticks per row)
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
            pack_string('NEC PC-98'),  # system name
            pack_string(''),  # category
            pack_string(' / '.join(self.pmd.header.memo) if self.pmd.header.memo else ''),  # album (PMD memo)
            pack_string(''),  # artist
            pack_string('NEC PC-98'),  # system name 2
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
    import io
    
    # Set stdout to UTF-8 to handle Japanese characters
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    
    if len(sys.argv) < 2:
        print("PMD to Furnace Converter")
        print("Usage: python pmd2furnace.py <file.M> [output.fur]")
        print("\nConverts compiled PMD .M files to Furnace .fur modules.")
        sys.exit(1)
    
    input_file = sys.argv[1].strip('"')  # Remove quotes if present
    if len(sys.argv) >= 3:
        output_file = sys.argv[2].strip('"')  # Remove quotes if present
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
