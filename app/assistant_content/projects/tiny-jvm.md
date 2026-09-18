---
title: "Project: Tiny JVM"
kind: project
---

# What it is

A small stack-based virtual machine written in portable C that runs the same
compiled bytecode in two very different places: the browser, via WebAssembly, and a
microcontroller, in the Wokwi simulator. The bytecode is produced by a real compiler
toolchain -- lexer, parser, resolver, code generator -- written in Java for a small
custom language called TL. "Tiny JVM" is aspirational shorthand for a JVM-*style*
stack architecture, not a literal claim of JVM bytecode compatibility; the page and
the build spec are both explicit about that.

# Why it's built this way

Java work and embedded/firmware work normally live in separate portfolio projects.
This one makes them a single system where each half needs the other: the Java
toolchain is useless without something to run its output, and the C VM is useless
without a program to execute. The bytecode format both halves agree on is the
connective tissue. Three ways to feed the VM were considered -- a hand-written
assembler, a real subset of actual Java `.class` files, and a small custom language
compiled by a real front end -- and the custom-language option won because it is a
genuine, complete compiler front-to-back while keeping the instruction set small
enough to fit a microcontroller's memory.

# The Java toolchain

A Gradle project (`tlc`, the TL compiler) with a hand-written lexer, a recursive-descent
parser producing a typed AST, a resolver pass that assigns local-variable slot indices
and catches use-before-declaration and duplicate-declaration errors, and a code
generator that back-patches control-flow jumps and emits a `.tvm` bytecode file (or a
human-readable disassembly via `--dump`). 57/57 JUnit tests pass across the lexer,
parser, resolver, and code generator, including golden tests that lock the exact
bytecode a given snippet must compile to.

# The C virtual machine

One C11 file, under 500 lines, with no dependencies and no dynamic allocation
anywhere -- every stack, call frame, and locals array is a fixed-size, statically
allocated buffer, a deliberate firmware-correctness choice: known memory footprint at
build time, nothing that can fragment or fail at runtime. The dispatch loop is a
`switch` over one-byte opcodes with operands inline in the bytecode stream. GPIO
access (`pinMode`/`digitalWrite`/`digitalRead`) is exposed through a small function-pointer
struct the host installs at startup, so the identical VM core runs against a
JavaScript-backed host in the browser and an Arduino-core-backed host on real
hardware -- only that one struct's contents differ. 9/9 hand-assembled VM test cases
pass, including divide-by-zero, stack overflow, a bad magic number, a truncated file,
and an `INT32_MIN / -1` divide-overflow case (undefined behaviour in C) found during a
crosscheck pass and fixed after it crashed the process outright.

# What's live on the site today

The project page (`/projects/tiny-jvm`) has a genuinely interactive demo, not just a
concept write-up: pick a sample program, then Step, Run, or Reset it while watching
the value stack, locals, console output, and a simulated GPIO pin update live. Two
samples are compiled by the real toolchain and run end-to-end in the browser via the
WASM build: a recursive Fibonacci/factorial program, and an over-temp-alarm program
that reads a simulated sensor slider and correctly drives a simulated GPIO pin high
when the threshold is crossed.

# What's not built yet

The Wokwi firmware view -- an ESP32 simulation running the identical bytecode on
real-ish embedded hardware, with the over-temp-alarm sample wired to a real thermistor
part and LED -- and a standalone GitHub repo + README for the VM and toolchain on
their own. Both are scoped, not abandoned; the C VM and Java toolchain underneath them
are already built and independently tested, so what's missing is the embedded target
and packaging, not the hard engineering.

# What it demonstrates

A complete compiler front end (lexing, recursive-descent parsing, AST design, symbol
resolution, code generation with back-patched control flow), a hand-managed,
allocation-free memory model suited to constrained hardware, and designing a binary
format that two independent implementations -- a C VM and a Java-side reference VM
used only in tests -- stay in agreement on, cross-compiled to a hosted target (WASM)
and, once the remaining step lands, a bare-metal one.
