#!/usr/bin/python
#
# Copyright 2010 Dan Smith <dsmith@danplanet.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from chirp import chirp_common, yaesu_clone, util
from chirp import bitwise

mem_format = """
#seekto 0x0611;
u8 checksum1;

#seekto 0x0691;
u8 checksum2;

#seekto 0x3F52;
u8 checksum3;

#seekto 0x1202;
struct {
  u8 even_pskip:1,
     even_skip:1,
     even_valid:1,
     even_masked:1,
     odd_pskip:1,
     odd_skip:1,
     odd_valid:1,
     odd_masked:1;
} flags[225];

#seekto 0x1322;
struct {
  u8   unknown1;
  u8   power:2,
       duplex:2,
       tune_step:4;
  bbcd freq[3];
  u8   zeros1:2,
       ones:2,
       zeros2:2,
       mode:2;
  u8   name[8];
  u8 zero;
  bbcd offset[3];
  u8   zeros3:2,
       tone:6;
  u8   zeros4:1,
       dcs:7;
  u8   zeros5:6,
       tmode:2;
  u8   charset;
} memory[450];
"""

DUPLEX = ["", "-", "+", "split"]
MODES  = ["FM", "AM", "WFM", "FM"] # last is auto
TMODES = ["", "Tone", "TSQL", "DTCS"]
STEPS = list(chirp_common.TUNING_STEPS)
STEPS.remove(6.25)
STEPS.remove(30.0)
STEPS.append(100.0)
STEPS.append(9.0)

CHARSET = ["%i" % int(x) for x in range(0, 10)] + \
    [" "] + \
    [chr(x) for x in range(ord("A"), ord("Z")+1)] + \
    [chr(x) for x in range(ord("a"), ord("z")+1)] + \
    list(".,:;!\"#$%&'()*+-.=<>?@[?]^_\\{|}") + \
    list("?" * 100)

class VX7Radio(yaesu_clone.YaesuCloneModeRadio):
    BAUD_RATE = 19200
    VENDOR = "Yaesu"
    MODEL = "VX-7"

    _model = "\x0A\x01\x02\x06\x09" #0x0a01020609 isn't really ascii
    _memsize = 16211
    _block_lengths = [ 10, 8, 16193 ]
    _block_size = 8

    def _checksums(self):
        return [ yaesu_clone.YaesuChecksum(0x0592, 0x0610),
                 yaesu_clone.YaesuChecksum(0x0612, 0x0690),
                 yaesu_clone.YaesuChecksum(0x0000, 0x3F51),
                 ]

    def process_mmap(self):
        self._memobj = bitwise.parse(mem_format, self._mmap)

    def get_features(self):
        rf = chirp_common.RadioFeatures()
        rf.has_bank = False
        rf.has_dtcs_polarity = False
        rf.valid_modes = list(set(MODES))
        rf.valid_tmodes = list(TMODES)
        rf.valid_duplexes = list(DUPLEX)
        rf.valid_tuning_steps = list(STEPS)
        rf.valid_bands = [(0.5, 999.0)]
        rf.valid_skips = ["", "S", "P"]
        rf.memory_bounds = (1, 450)
        rf.can_odd_split = True
        rf.has_ctone = False
        return rf

    def get_raw_memory(self, number):
        return self._memobj.memory[number].get_raw()

    def get_memory(self, number):
        _mem = self._memobj.memory[number-1]
        _flag = self._memobj.flags[(number-1)/2]

        nibble = ((number-1) % 2) and "even" or "odd"
        used = _flag["%s_masked" % nibble] and _flag["%s_valid" % nibble]
        pskip = _flag["%s_pskip" % nibble]
        skip = _flag["%s_skip" % nibble]

        mem = chirp_common.Memory()
        mem.number = number
        if not used:
            mem.empty = True
            return mem

        mem.freq = int(_mem.freq) / 1000.0
        mem.offset = int(_mem.offset) / 1000.0
        mem.rtone = mem.ctone = chirp_common.TONES[_mem.tone]
        mem.tmode = TMODES[_mem.tmode]
        mem.duplex = DUPLEX[_mem.duplex]
        mem.mode = MODES[_mem.mode]
        mem.dtcs = chirp_common.DTCS_CODES[_mem.dcs]
        mem.tuning_step = STEPS[_mem.tune_step]
        mem.skip = pskip and "P" or skip and "S" or ""

        for i in _mem.name:
            if i == "\xFF":
                break
            mem.name += CHARSET[i]

        return mem

    def _wipe_memory(self, mem):
        mem.set_raw("\x00" * (mem.size() / 8))
        mem.unknown1 = 0x05
        mem.ones = 0x03
        mem.power = 0b11

    def set_memory(self, mem):
        _mem = self._memobj.memory[mem.number-1]
        _flag = self._memobj.flags[(mem.number-1)/2]

        nibble = ((mem.number-1) % 2) and "even" or "odd"
        
        was_valid = int(_flag["%s_valid" % nibble])

        _flag["%s_masked" % nibble] = not mem.empty
        _flag["%s_valid" % nibble] = not mem.empty
        if mem.empty:
            return

        if not was_valid:
            self._wipe_memory(_mem)

        _mem.freq = int(mem.freq * 1000)
        _mem.offset = int(mem.offset * 1000)
        _mem.tone = chirp_common.TONES.index(mem.rtone)
        _mem.tmode = TMODES.index(mem.tmode)
        _mem.duplex = DUPLEX.index(mem.duplex)
        _mem.mode = MODES.index(mem.mode)
        _mem.dcs = chirp_common.DTCS_CODES.index(mem.dtcs)
        _mem.tune_step = STEPS.index(mem.tuning_step)

        _flag["%s_pskip" % nibble] = mem.skip == "P"
        _flag["%s_skip" % nibble] = mem.skip == "S"

        for i in range(0, 8):
            _mem.name[i] = CHARSET.index(mem.name.ljust(8)[i])
        
    def get_banks(self):
        return []

    def filter_name(self, name):
        return chirp_common.name8(name)
