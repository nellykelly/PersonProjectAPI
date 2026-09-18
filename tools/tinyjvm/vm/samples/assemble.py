#!/usr/bin/env python3
"""A tiny two-pass assembler for the .tvm bytecode format, used to
hand-assemble test programs before the real Java compiler (tlc) exists
(build spec section 11, step 1's deliverable: "a hand-assembled .tvm
runs and prints"). Opcode values and the file format both match
docs/build-spec-tiny-jvm.md sections 6.2/6.3 and app/blueprints/tiny_jvm/
routes.py's OPCODES table exactly -- this is not an independent encoding,
it targets the one spec everything else targets.

Every instruction's byte length is fixed by its opcode alone (no
variable-length encoding), so labels resolve in exactly two passes: the
first walks the instruction list to assign each label a byte address,
the second emits bytes with jump/call operands resolved against those
addresses. No fixed-point iteration needed.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"TVM\0"
VERSION = 1

OPCODES = {
    "PUSH": 0x01, "POP": 0x02, "DUP": 0x03,
    "ADD": 0x10, "SUB": 0x11, "MUL": 0x12, "DIV": 0x13, "LT": 0x14,
    "LOAD": 0x20, "STORE": 0x21,
    "JMP": 0x30, "JZ": 0x31, "JNZ": 0x32,
    "CALL": 0x40, "RET": 0x41,
    "PRINT": 0x50, "PINMODE": 0x51, "DWRITE": 0x52, "DREAD": 0x53,
    "HALT": 0xFF,
}

# Operand encoding per mnemonic: byte length of the operand (0 if none)
# and whether it's a label reference that needs resolving.
_OPERAND_SIZE = {
    "PUSH": 4, "LOAD": 1, "STORE": 1,
    "JMP": 2, "JZ": 2, "JNZ": 2, "CALL": 2,
}
_LABEL_OPS = {"JMP", "JZ", "JNZ", "CALL"}
_RELATIVE_OPS = {"JMP", "JZ", "JNZ"}  # CALL's operand is an absolute address


@dataclass
class _Instr:
    mnemonic: str
    operand: int | str | None  # int for PUSH/LOAD/STORE, str (label) for jumps/calls
    addr: int = 0


class Assembler:
    """Usage:
        a = Assembler()
        a.label("start")
        a.emit("PUSH", 3)
        a.emit("JZ", "end")
        a.label("end")
        a.emit("HALT")
        data = a.assemble(entry="start")
    """

    def __init__(self) -> None:
        self._instrs: list[_Instr] = []
        self._pending_labels: list[str] = []
        self._label_positions: dict[str, int] = {}  # label -> index into _instrs

    def label(self, name: str) -> None:
        if name in self._label_positions:
            raise ValueError(f"duplicate label {name!r}")
        self._label_positions[name] = len(self._instrs)

    def emit(self, mnemonic: str, operand: int | str | None = None) -> None:
        if mnemonic not in OPCODES:
            raise ValueError(f"unknown mnemonic {mnemonic!r}")
        expected = _OPERAND_SIZE.get(mnemonic, 0)
        if expected == 0 and operand is not None:
            raise ValueError(f"{mnemonic} takes no operand")
        if expected != 0 and operand is None:
            raise ValueError(f"{mnemonic} requires an operand")
        self._instrs.append(_Instr(mnemonic, operand))

    def assemble(self, entry: str = "__start__") -> bytes:
        # A label at the very end (after the last emit()) is valid --
        # e.g. an "end:" label used only as a jump target past the last
        # real instruction. entry defaults to address 0 unless "entry"
        # was itself declared as a label.
        addr = 0
        addrs: list[int] = []
        for instr in self._instrs:
            addrs.append(addr)
            addr += 1 + _OPERAND_SIZE.get(instr.mnemonic, 0)
        code_len = addr
        end_of_code_index = len(self._instrs)  # a label() call after the last emit()

        def label_addr(name: str) -> int:
            if name not in self._label_positions:
                raise ValueError(f"undefined label {name!r}")
            idx = self._label_positions[name]
            return addrs[idx] if idx < len(addrs) else code_len

        entry_addr = label_addr(entry) if entry in self._label_positions else 0
        if entry not in self._label_positions and entry != "__start__":
            raise ValueError(f"undefined entry label {entry!r}")

        code = bytearray()
        for i, instr in enumerate(self._instrs):
            code.append(OPCODES[instr.mnemonic])
            size = _OPERAND_SIZE.get(instr.mnemonic, 0)
            if size == 0:
                continue
            if instr.mnemonic in _LABEL_OPS:
                target = label_addr(instr.operand)  # type: ignore[arg-type]
                if instr.mnemonic in _RELATIVE_OPS:
                    this_instr_end = addrs[i] + 1 + size
                    value = target - this_instr_end
                    code += struct.pack("<h", value)
                else:  # CALL -- absolute address
                    code += struct.pack("<H", target)
            elif instr.mnemonic == "PUSH":
                code += struct.pack("<i", instr.operand)
            else:  # LOAD/STORE -- uint8 slot
                code += struct.pack("<B", instr.operand)

        assert len(code) == code_len, "internal error: size pass and emit pass disagree"

        header = MAGIC + struct.pack("<B", VERSION) + struct.pack("<H", entry_addr) + struct.pack("<H", code_len)
        return bytes(header) + bytes(code)


def write_tvm(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
