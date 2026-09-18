#!/usr/bin/env python3
"""Builds the three hand-assembled .tvm programs used to prove vm.c works
before the Java compiler exists (build spec section 11, step 1). Each
program exercises a different slice of the instruction set:

  add_print   -- PUSH/ADD/PRINT/HALT only (expect: 7)
  sum_loop    -- adds LOAD/STORE/JZ/JMP: sums 5+4+3+2+1 (expect: 15)
  factorial   -- adds CALL/RET: computes fact(6) recursively (expect: 720)

factorial's calling convention (not dictated by the opcode table, so
fixed here and documented because the real compiler will need to agree
with it later): the caller pushes each argument in order, then CALLs; the
callee's first instructions STORE each argument off the shared value
stack into its own local slots (last pushed = first stored); the callee
leaves exactly one value on the shared stack before RET, which is its
return value. CALL/RET never touch the value stack themselves -- only
frames/locals/ip -- so this convention falls entirely out of how the
functions are written, matching vm.c as implemented.
"""
from pathlib import Path

from assemble import Assembler, write_tvm

HERE = Path(__file__).parent


def build_add_print() -> bytes:
    a = Assembler()
    a.emit("PUSH", 3)
    a.emit("PUSH", 4)
    a.emit("ADD")
    a.emit("PRINT")
    a.emit("HALT")
    return a.assemble()


def build_sum_loop() -> bytes:
    # i = 5; sum = 0; while (i != 0) { sum += i; i -= 1; } print sum;
    # slot 0 = i, slot 1 = sum
    a = Assembler()
    a.emit("PUSH", 5)
    a.emit("STORE", 0)
    a.emit("PUSH", 0)
    a.emit("STORE", 1)
    a.label("loop_start")
    a.emit("LOAD", 0)
    a.emit("JZ", "loop_end")
    a.emit("LOAD", 1)
    a.emit("LOAD", 0)
    a.emit("ADD")
    a.emit("STORE", 1)
    a.emit("LOAD", 0)
    a.emit("PUSH", 1)
    a.emit("SUB")
    a.emit("STORE", 0)
    a.emit("JMP", "loop_start")
    a.label("loop_end")
    a.emit("LOAD", 1)
    a.emit("PRINT")
    a.emit("HALT")
    return a.assemble()


def build_factorial() -> bytes:
    a = Assembler()
    a.emit("JMP", "top")
    a.label("fact")
    a.emit("STORE", 0)          # slot 0 = n (popped off the shared stack)
    a.emit("LOAD", 0)
    a.emit("JZ", "base_case")
    a.emit("LOAD", 0)           # save n for the multiply after the recursive call returns
    a.emit("LOAD", 0)
    a.emit("PUSH", 1)
    a.emit("SUB")               # n - 1
    a.emit("CALL", "fact")      # leaves fact(n-1) on the shared stack
    a.emit("MUL")               # n * fact(n-1)
    a.emit("RET")
    a.label("base_case")
    a.emit("PUSH", 1)
    a.emit("RET")
    a.label("top")
    a.emit("PUSH", 6)
    a.emit("CALL", "fact")
    a.emit("PRINT")
    a.emit("HALT")
    return a.assemble(entry="top")


def build_lt() -> bytes:
    # prints 1,0,0 for (3<5), (5<3), (3<3)
    a = Assembler()
    a.emit("PUSH", 3)
    a.emit("PUSH", 5)
    a.emit("LT")
    a.emit("PRINT")
    a.emit("PUSH", 5)
    a.emit("PUSH", 3)
    a.emit("LT")
    a.emit("PRINT")
    a.emit("PUSH", 3)
    a.emit("PUSH", 3)
    a.emit("LT")
    a.emit("PRINT")
    a.emit("HALT")
    return a.assemble()


def build_trap_divzero() -> bytes:
    a = Assembler()
    a.emit("PUSH", 10)
    a.emit("PUSH", 0)
    a.emit("DIV")
    a.emit("PRINT")
    a.emit("HALT")
    return a.assemble()


def build_trap_div_overflow() -> bytes:
    # INT32_MIN / -1: the one arithmetic case where C's plain `a / b`
    # is undefined behaviour (a real hardware trap on most platforms,
    # not a graceful failure) -- found during a crosscheck pass, not
    # part of the original build. vm.c must catch this before dividing.
    a = Assembler()
    a.emit("PUSH", -2147483648)
    a.emit("PUSH", -1)
    a.emit("DIV")
    a.emit("PRINT")
    a.emit("HALT")
    return a.assemble()


def build_trap_overflow() -> bytes:
    # Pushes forever with no pop -- must trip VM_TRAP_STACK_OVERFLOW
    # rather than actually overflowing the C array.
    a = Assembler()
    a.label("loop")
    a.emit("PUSH", 1)
    a.emit("JMP", "loop")
    return a.assemble()


def build_trap_badmagic() -> bytes:
    return b"XXXX" + bytes([1, 0, 0, 0, 0])


def build_trap_truncated() -> bytes:
    # Header claims 0xFFFF bytes of code; the file has none.
    return b"TVM\x00" + bytes([1]) + b"\x00\x00" + b"\xff\xff"


# name -> (builder, expected stdout or None, expected exit code)
SAMPLES = {
    "add_print.tvm": (build_add_print, "7", 0),
    "sum_loop.tvm": (build_sum_loop, "15", 0),
    "factorial.tvm": (build_factorial, "720", 0),
    "lt.tvm": (build_lt, "1\n0\n0", 0),
}

# Adversarial cases: prove the VM traps cleanly instead of crashing or
# reading/writing out of bounds. Not "samples" in the demo-page sense --
# these never appear on the project page -- but they live alongside the
# valid ones because build_samples.py is the one place that knows how to
# hand-assemble a .tvm file.
TRAP_CASES = {
    "trap_divzero.tvm": (build_trap_divzero, None, 1),
    "trap_div_overflow.tvm": (build_trap_div_overflow, None, 1),
    "trap_overflow.tvm": (build_trap_overflow, None, 1),
    "trap_badmagic.tvm": (build_trap_badmagic, None, 65),
    "trap_truncated.tvm": (build_trap_truncated, None, 65),
}

if __name__ == "__main__":
    for filename, (builder, expected, _exit_code) in {**SAMPLES, **TRAP_CASES}.items():
        data = builder()
        out_path = HERE / filename
        write_tvm(str(out_path), data)
        note = f"expect output: {expected}" if expected is not None else "expect a clean trap, no crash"
        print(f"wrote {out_path} ({len(data)} bytes, {note})")
