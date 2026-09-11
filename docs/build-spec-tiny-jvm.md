# Build Spec: Tiny JVM

A portfolio project that demonstrates **Java** and **embedded engineering**
in one artifact: a small stack-based virtual machine written in portable C
that runs the *same bytecode* in the browser (compiled to WebAssembly) and
on a microcontroller (in the Wokwi simulator). The bytecode is produced by
a compiler written in Java for a small custom language.

This document explains every part of the project and the reasoning behind
each decision, so the whole thing can be understood before implementation
starts. It is written to sit alongside the other `docs/build-spec-*.md`
files.

---

## Status: DEFERRED (2026-09-10)

**The scaffold is done and stays on the site; implementation is paused.**

What exists and remains live:

- `/projects/tiny-jvm` page (concept, architecture, instruction-set table,
  sample programs), the `PROJECTS` card, the icon, the blueprint, and
  this spec.
- `scripts/smoke_tiny_jvm.py` and the `tests/test_projects_landing.py`
  inventory row.

Why it is paused — the remaining work in §11 needs three toolchains that
were **not available in the development environment** when the build was
attempted:

| Needed for | Toolchain | Present? |
|------------|-----------|----------|
| `vm.c` compile + unit tests + ASan/fuzz | a C compiler (`gcc` / `clang` / MSVC), `make` | no |
| `tlc` build + JUnit + `RefVM` | JDK 21, Gradle | no |
| the in-page demo | `emscripten` (`emcc`) | no |
| — | Python, Node 24 | yes |

Three ways forward were identified and recorded here so no analysis is
lost:

1. **Install the toolchains, then build for real.** Install JDK 21, a C
   compiler (MSVC or mingw-w64), and emscripten, then build `vm.c` and
   `tlc` layer by layer, compiling and testing each. Highest-quality
   result, matches this spec exactly. Requires environment setup first.
   *This is the intended resumption path.*
2. **Ship a JavaScript VM first, port to C/Java later.** Implement the
   VM in JS (the `RefVM` §5.6 already calls for), add a small assembler,
   and wire the in-page stepper — all testable with Node. The page
   becomes interactive without WASM; the C VM and Java toolchain are
   then written against a working, tested reference. Trades the "Java +
   embedded" story landing now for visible progress on the site now.
3. **Write the C + Java source unverified this pass.** Produce `vm.c`,
   `vm.h`, the `tlc` Gradle project, a `Makefile`, and hand-assembled
   sample bytecode without compiling or running any of it; fix build
   errors once the toolchains exist. Fastest to "code exists", but
   untested compiler/VM code carries real risk.

**Decision:** none of the above right now. The toolchain setup is
substantial, so Tiny JVM is shelved as a *later* modelling exercise and
work moves to a new project: a yfinance → dbt → Snowflake dimensional
warehouse, in its own self-contained directory `market-data-warehouse/`
(see `market-data-warehouse/PROMPT.md` and `market-data-warehouse/README.md`).
When Tiny JVM resumes, take path 1 and pick up at §11 step 1.

Everything below this line is the original spec, unchanged, for when the
project resumes.

---

## 1. Why this project exists

The site needed a project that shows Java work and embedded/firmware work.
Those two normally live in separate projects. A telemetry pipeline (an
MCU publishing sensor data to a Spring service) would show both, but as
two loosely coupled halves. This project instead makes them **one system
where each half needs the other**:

- The **Java half** is a compiler toolchain — lexer, parser, code
  generator, CLI. It is useless without something to run its output.
- The **embedded half** is a VM small enough to fit a microcontroller's
  RAM and run without an operating system. It is useless without a
  program to execute.

The connective tissue is a bytecode format that both halves agree on. If
you understand the bytecode, you understand the project.

### What each half proves

| Half | Skills it demonstrates |
|------|------------------------|
| Java toolchain | Lexing, recursive-descent parsing, AST design, code generation, symbol tables, a clean CLI, unit tests, build tooling (Gradle) |
| C VM | A tight interpreter loop, a hand-managed memory model, fixed-size buffers, no dynamic allocation, portability across a hosted target (WASM) and a bare-metal target (MCU), peripheral (GPIO) access |
| The seam | Designing a binary format, versioning it, keeping two independent implementations in agreement, cross-compilation |

---

## 2. Architecture: five stages

```
  ┌─────────────┐   ┌──────────────────┐   ┌────────────┐   ┌──────────────┐
  │ 1. Source   │──▶│ 2. Java toolchain│──▶│ 3. Bytecode│──▶│ 4. C VM      │
  │  program.tl │   │  lex → parse →   │   │  program.  │   │  fetch/decode│
  │  (text)     │   │  codegen         │   │  tvm (bin) │   │  /execute    │
  └─────────────┘   └──────────────────┘   └────────────┘   └──────┬───────┘
                                                                   │
                                              ┌────────────────────┴────────────────────┐
                                              ▼                                         ▼
                                   ┌─────────────────────┐                 ┌─────────────────────┐
                                   │ 5a. WebAssembly     │                 │ 5b. Firmware        │
                                   │  emscripten build,  │                 │  ESP32 / Arduino,   │
                                   │  runs in the        │                 │  runs in the Wokwi  │
                                   │  project page       │                 │  simulator          │
                                   └─────────────────────┘                 └─────────────────────┘
```

The bytecode file at stage 3 is byte-for-byte identical whichever target
consumes it. That is the whole point: one program, one compiler, two very
different machines.

---

## 3. The core decision: "Option B"

Three ways to feed the VM were considered. The source language the Java
toolchain compiles is the axis they differ on.

| Option | Source the toolchain accepts | Java work | Verdict |
|--------|------------------------------|-----------|---------|
| **A. Assembler** | Hand-written mnemonics: `PUSH 3` / `PUSH 4` / `ADD` / `PRINT` | Small — tokenise lines, map mnemonic → opcode byte | Rejected: barely a compiler, and demo programs are painful to write by hand |
| **B. Tiny custom language** *(chosen)* | A small C/BASIC-like language: `let x = fib(10); print x;` | A real front end: lexer, recursive-descent parser, AST, code generator, symbol table | **Chosen** |
| **C. Real Java subset** | Actual `.class` files from `javac` | Parse the class-file format, constant pool, a bytecode verifier, translate a JVM subset to our bytecode | Rejected: most of the effort is spec-reading for `.class` parsing; the language subset a from-scratch VM can support is so small the demo programs end up trivial anyway |

### Why B wins

- It is a genuine, complete compiler front-to-back — the strongest single
  thing the Java half can show.
- We own the instruction set, so we can keep it tiny enough to fit an
  MCU. Option C would force us toward JVM semantics (objects, a
  constant pool, exceptions) that don't fit.
- Demo programs are written in a normal-feeling language, so producing a
  dozen of them is quick and they read well on the project page.
- The name "Tiny JVM" is aspirational shorthand, not a literal claim of
  JVM compatibility. The page and this doc are explicit about that.

### The cost of B

We must design a language, however small. Section 4 keeps that in check
by fixing a deliberately minimal grammar.

---

## 4. The source language

Working name: **TL** (`.tl` files). One file, no imports, no modules.

### Design rules

1. **Only one type: 32-bit signed integer.** No floats, no strings as a
   first-class type, no arrays in v1. This keeps the VM's value stack a
   plain `int32_t[]` and removes every question about boxing, layout, and
   garbage collection. A microcontroller VM should not have a heap.
2. **C-like syntax** so it needs no explanation on the page: `let`,
   `if`/`else`, `while`, `fn`, `return`, `{ }` blocks, `//` and `#`
   comments, the usual operators.
3. **Functions, but no closures or recursion limits beyond the VM's call
   stack depth.** Recursion works (the factorial sample depends on it);
   it just bottoms out at a fixed call-stack size.
4. **A handful of built-ins, not a standard library:** `print(x)`,
   `pinMode(pin, mode)`, `digitalWrite(pin, value)`, `digitalRead(pin)`.
   These compile to dedicated opcodes (`PRINT`, `PINMODE`, `DWRITE`,
   `DREAD`), not to `CALL`. That is what lets the same program do
   something visible on both targets.
5. **Booleans are integers.** `true` is `1`, `false` is `0`, comparisons
   yield `0`/`1`, `if`/`while` treat non-zero as true. One less type.

### Grammar (EBNF, informal)

```
program     = { declaration } ;
declaration = funcDecl | statement ;
funcDecl    = "fn" IDENT "(" [ params ] ")" block ;
params      = IDENT { "," IDENT } ;
statement   = letStmt | ifStmt | whileStmt | returnStmt
            | block | exprStmt ;
letStmt     = "let" IDENT "=" expression ";" ;
ifStmt      = "if" "(" expression ")" statement [ "else" statement ] ;
whileStmt   = "while" "(" expression ")" statement ;
returnStmt  = "return" [ expression ] ";" ;
block       = "{" { statement } "}" ;
exprStmt    = expression ";" ;
expression  = assignment ;
assignment  = IDENT "=" assignment | equality ;
equality    = comparison { ( "==" | "!=" ) comparison } ;
comparison  = term { ( "<" | "<=" | ">" | ">=" ) term } ;
term        = factor { ( "+" | "-" ) factor } ;
factor      = unary { ( "*" | "/" ) unary } ;
unary       = ( "-" | "!" ) unary | call ;
call        = primary { "(" [ args ] ")" } ;
primary     = INT | IDENT | "(" expression ")" | "true" | "false" ;
```

Small enough to hand-write a parser for in an afternoon; expressive
enough for the sample programs on the project page.

---

## 5. The Java toolchain

A Gradle project. Package `com.nelsonkoskela.tinyjvm`. One executable
JAR: `tlc` (TL compiler).

```
  tlc program.tl -o program.tvm        # compile
  tlc program.tl --dump                # human-readable disassembly to stdout
```

### 5.1 Lexer  (`Lexer.java`, `Token.java`, `TokenType.java`)

- Hand-written scanner, single pass, one character of lookahead.
- **Why hand-written, not a generator (ANTLR/JFlex):** the token set is
  ~25 entries; a generator would add a build dependency and a `.g4` file
  to explain, for no benefit at this size. Hand-writing it is also more
  demonstrative of understanding.
- Emits a `List<Token>` with `type`, `lexeme`, `line`. A trailing `EOF`
  token so the parser never checks bounds.
- Errors (unterminated block comment, stray character) carry a line
  number and abort compilation with a non-zero exit code.

### 5.2 Parser  (`Parser.java`, `Expr.java`, `Stmt.java`)

- **Recursive descent**, one method per grammar rule, precedence encoded
  by the call chain (`equality` calls `comparison` calls `term`…). This
  is the standard teaching structure (Crafting Interpreters) and the
  clearest to read.
- Produces an **AST** of `Stmt` and `Expr` nodes (sealed interfaces +
  records, Java 17+). No visitor framework — a `sealed` hierarchy plus
  `switch` pattern matching in the code generator is enough and keeps the
  node classes to one line each.
- **Panic-mode error recovery:** on a syntax error, report it, then skip
  tokens to the next `;` or `}` and keep parsing, so one run can report
  several errors. Compilation still fails overall.

### 5.3 Resolver / symbol table  (`Resolver.java`)

A small pass between parse and codegen:

- Assigns every local variable and parameter a **slot index** (0–255)
  within its function. The VM addresses locals by slot number, not name.
- Rejects use-before-declaration and duplicate declarations in the same
  scope.
- Records each function's address-to-be so calls can be emitted before
  the callee is generated (a two-pass fixup, see 5.4).
- **Why a separate pass:** doing scope resolution inside codegen tangles
  two concerns. A dedicated pass is easy to unit-test on its own.

### 5.4 Code generator  (`CodeGen.java`, `Chunk.java`)

- Walks the AST and appends bytes to a `Chunk` (a growable
  `byte[]` plus a parallel line table for diagnostics).
- **Control flow uses back-patching:** emit `JZ` with a placeholder
  16-bit offset, remember the position, generate the branch body, then
  overwrite the placeholder with the now-known distance. Standard
  single-pass compiler technique; worth showing.
- **Functions:** the generator emits a jump over all function bodies to
  the top-level code, lays each function out, and keeps a map of
  `name → address`. A second pass fills every `CALL` operand with the
  real address.
- Output file format — see section 6.3.
- `--dump` prints a disassembly (offset, opcode name, operand, decoded
  target) so the bytecode is inspectable without the VM.

### 5.5 CLI  (`Main.java`)

- Argument parsing by hand (three flags); no `picocli` dependency for
  this surface.
- Exit codes: `0` success, `65` compile error (matches `sysexits.h`
  `EX_DATAERR`), `64` bad usage.

### 5.6 Tests  (`src/test/java/...`, JUnit 5)

- **Lexer:** token stream for representative snippets.
- **Parser:** AST shape for each grammar construct; error cases produce
  the expected diagnostics.
- **Resolver:** slot assignment, scope errors.
- **CodeGen golden tests:** compile a `.tl` snippet, assert the exact
  disassembly. These lock the bytecode contract.
- **End-to-end:** compile a snippet, run it through a JVM-side reference
  implementation of the VM (a ~150-line `RefVM.java` used only in
  tests), assert the printed output. This lets the Java project be
  verified without the C build in the loop.

---

## 6. The bytecode

### 6.1 Why a stack machine, not a register machine

- The compiler for a stack machine is dramatically simpler: an
  expression compiles to "emit the sub-expressions, then emit the
  operator." No register allocation.
- The VM's dispatch loop is smaller, which matters for the MCU target.
- The downside (more instructions executed per program than a register
  VM) is irrelevant here — the programs are tiny and nothing is
  performance-critical.
- It also matches the mental model of "the JVM" the project name evokes;
  the real JVM is a stack machine too.

### 6.2 Instruction set

One-byte opcodes. Operands, where present, follow the opcode inline in
the bytecode stream (little-endian). This is the canonical list — the C
VM and `CodeGen.java` both target it, and it is duplicated in
`app/blueprints/tiny_jvm/routes.py` (`OPCODES`) so the project page can
render it.

| Hex | Name | Operand | Stack effect / meaning |
|-----|------|---------|------------------------|
| `0x01` | `PUSH` | int32 (LE) | push a constant |
| `0x02` | `POP` | — | discard top |
| `0x03` | `DUP` | — | duplicate top |
| `0x10` | `ADD` | — | `a b → a+b` |
| `0x11` | `SUB` | — | `a b → a-b` |
| `0x12` | `MUL` | — | `a b → a*b` |
| `0x13` | `DIV` | — | `a b → a/b`, trap on divide-by-zero |
| `0x20` | `LOAD` | uint8 slot | push local `#slot` |
| `0x21` | `STORE` | uint8 slot | pop into local `#slot` |
| `0x30` | `JMP` | int16 (LE) offset | unconditional relative branch |
| `0x31` | `JZ` | int16 (LE) offset | branch if `pop() == 0` |
| `0x32` | `JNZ` | int16 (LE) offset | branch if `pop() != 0` |
| `0x40` | `CALL` | uint16 (LE) addr | call function at bytecode address |
| `0x41` | `RET` | — | return (top of stack is the return value) |
| `0x50` | `PRINT` | — | pop, emit to the output console |
| `0x51` | `PINMODE` | — | `mode pin →` configure a GPIO pin |
| `0x52` | `DWRITE` | — | `value pin →` drive a GPIO pin |
| `0x53` | `DREAD` | — | `pin → level` read a GPIO pin |
| `0xFF` | `HALT` | — | stop the VM |

Comparisons (`==`, `<`, …) compile to `SUB` + a conditional jump, or to
small opcode sequences; they deliberately have no dedicated opcodes to
keep the set short. (If the disassembly gets too noisy in practice, a
later revision can add `EQ`/`LT`/`GT` — the format has room.)

### 6.3 File format (`.tvm`)

```
  offset  size  field
  0       4     magic  "TVM\0"
  4       1     version (currently 1)
  5       2     entry point: bytecode offset where execution starts (LE)
  7       2     code length in bytes (LE)
  9       N     code
```

- **Magic + version** so the VM can reject a stale or foreign file with
  a clear error rather than executing garbage.
- **Explicit entry point** because function bodies are laid out first;
  execution starts at the top-level code that follows them.
- No symbol table, no debug section in v1 — `--dump` at compile time
  covers inspection.

### 6.4 Runtime limits (fixed, compile-time constants in the VM)

| Limit | Value | Reason |
|-------|-------|--------|
| Value stack depth | 256 | Plenty for these programs; `256 × 4 B = 1 KB` |
| Call stack depth | 64 frames | Bounds recursion; each frame is small |
| Locals per frame | 256 | Matches the `uint8` slot operand |
| Code size | 64 KB | Matches the 16-bit offsets in the format |

All storage is statically allocated. **No `malloc` anywhere in the VM.**
That is a firmware-correctness choice: a fixed memory footprint that is
known at build time and cannot fragment or fail at runtime.

---

## 7. The C VM

One file, `vm.c`, plus `vm.h`. C11, no dependencies, no libc beyond
`stdint.h` / `string.h`. `< 500` lines target.

### 7.1 Structure

```c
typedef struct {
    const uint8_t *code;      // points at the loaded .tvm code section
    uint16_t       code_len;
    uint16_t       ip;        // instruction pointer

    int32_t        stack[256];
    int32_t        sp;        // stack pointer (next free slot)

    struct { uint16_t ret_ip; int32_t locals[256]; } frames[64];
    int32_t        fp;        // frame pointer

    VMStatus       status;    // OK, HALTED, TRAP_*
} VM;
```

- **The dispatch loop** is a `for (;;)` around `switch (code[ip++])`.
  A computed-goto ("threaded") dispatch is a possible later
  optimisation; the plain `switch` is clearer and fast enough.
- **Traps** (divide by zero, stack overflow, bad opcode, call-depth
  exceeded) set `status` and break the loop. The VM never calls
  `exit()` or `abort()` — the host decides what to do. On the MCU there
  is nowhere to exit *to*.

### 7.2 The GPIO seam

`PINMODE` / `DWRITE` / `DREAD` don't touch hardware directly. They call
three function pointers the host installs at startup:

```c
typedef struct {
    void    (*pin_mode)(int pin, int mode);
    void    (*digital_write)(int pin, int value);
    int     (*digital_read)(int pin);
    void    (*print_int)(int32_t value);
} VMHost;
```

- **On WASM:** these are implemented in JS (via emscripten), and route to
  the page — pin writes update an on-screen pin diagram, `print` appends
  to a console element, `digital_read` returns whatever a slider/toggle
  in the UI is set to.
- **On the ESP32/Wokwi:** these are one-line wrappers over the Arduino
  core (`pinMode`, `digitalWrite`, `digitalRead`, `Serial.print`).

The VM core is identical in both builds. Only this struct's contents
differ. That is the cleanest possible demonstration of "portable
firmware logic."

### 7.3 Why C, not Rust or C++

- C is the lingua franca of embedded and the Wokwi / Arduino toolchain
  path is frictionless.
- A no-allocation, single-file interpreter is exactly where C's
  simplicity is an asset and its footguns (no bounds checking) are
  contained — every array access in the loop is guarded explicitly, and
  that guarding is itself part of what the project shows.
- WASM via emscripten is a well-trodden path for C.

---

## 8. Target 5a: WebAssembly (the in-page demo)

### 8.1 Build

- `emcc vm.c wasm_host.c -o tinyjvm.js -s MODULARIZE -s EXPORT_ES6 \
   -s EXPORTED_RUNTIME_METHODS=cwrap -O2`
- Exposes `load_program(ptr, len)` and `step()` / `run()` to JS via
  `cwrap`.
- Output: `tinyjvm.wasm` + a small `tinyjvm.js` loader, both served as
  static assets from `app/static/js/tiny_jvm/`.

### 8.2 In-page behaviour

The project page (`app/templates/tiny_jvm/index.html`) will gain:

- A **program picker** (the samples from `SAMPLE_PROGRAMS`) and an
  editable text area.
- A **"Compile" button.** The compile step needs the Java toolchain,
  which cannot run in the browser. Two options, decided at build time:
  1. **Precompiled** — ship the `.tvm` for each sample as a static asset;
     the editor is read-only-ish (samples only). Simplest, no server.
  2. **Server compile** — a `POST /projects/tiny-jvm/api/compile`
     endpoint runs `tlc` in a subprocess and returns the bytecode.
     Needs the JAR on the server, input limits, a timeout, and an
     `X-CSRFToken` header (like the assistant/leetcode endpoints).
  The page will start with option 1 and add option 2 only if free-form
  editing is worth the operational surface.
- A **stepper**: run one `step()` at a time, drawing the value stack,
  the locals, the instruction pointer against the disassembly, and the
  console / pin panel.

### 8.3 CSP note

`app/__init__.py` sends a **report-only** CSP today, and its `script-src`
does not list `'wasm-unsafe-eval'`. Report-only means nothing breaks now.
Before this demo goes live the CSP should get `'wasm-unsafe-eval'` added
to `script-src` (and, if it is ever switched to enforcing, verified
against the emscripten loader). Tracked here so it isn't discovered late.

---

## 9. Target 5b: Wokwi (the embedded view)

- A separate tiny Arduino/ESP-IDF project: `main.c` includes `vm.c`,
  installs the Arduino-backed `VMHost`, embeds one sample's `.tvm` as a
  `const uint8_t[]` (via `xxd -i`), and runs it in `loop()`.
- The **over-temp-alarm** sample is the showcase: a Wokwi NTC/thermistor
  part on an analog pin, an LED on a digital pin. The *same bytecode*
  that runs in the browser lights a real LED in the simulation when the
  simulated temperature crosses the threshold.
- Wokwi projects have shareable URLs and embed as an `<iframe>`. The page
  will show it beside the WASM demo with a line making the point: same
  bytes, different machine.
- The firmware repo also carries a short writeup of the memory footprint
  (`.bss` size with the VM's static arrays, flash used by the
  interpreter) — concrete embedded numbers.

---

## 10. Flask integration — what this pass built

This first pass is scaffolding only. No VM, no toolchain, no WASM. The
goal was a real home on the site and this specification.

### 10.1 Files added

| File | Purpose |
|------|---------|
| `app/blueprints/tiny_jvm/__init__.py` | Blueprint object, 4 lines, mirrors every other blueprint |
| `app/blueprints/tiny_jvm/routes.py` | One route, `GET ""` → `tiny_jvm.index`. Holds `OPCODES` and `SAMPLE_PROGRAMS` as module constants that feed the template |
| `app/templates/tiny_jvm/index.html` | Placeholder page: concept, architecture, the instruction-set table, the sample programs, and a "what's built" list. Extends `base.html` |
| `app/static/assets/img/icons/tiny-jvm.svg` | Card icon: a microcontroller outline with a two-frame stack and a "push" arrow, in the existing icon style (64×64, `#3aa0ff` primary) |
| `docs/build-spec-tiny-jvm.md` | This document |

### 10.2 Files changed

| File | Change | Why |
|------|--------|-----|
| `app/__init__.py` | Import `tiny_jvm_bp`; `register_blueprint(tiny_jvm_bp, url_prefix="/projects/tiny-jvm")`, placed next to the other `/projects/*` demos | Same registration pattern as `timed_squares`, `sre_infra`, etc. |
| `app/blueprints/projects/routes.py` | New entry in the `PROJECTS` list (slug `tiny-jvm`, endpoint `tiny_jvm.index`, tags `Java` / `Embedded / C` / `WebAssembly` / `Compilers`) | Puts the card on `/projects` with the right tag story |

### 10.3 Deliberately NOT done this pass

- **No `/api/*` routes.** The page is static and read-only, so it
  touches neither CSRF, the database, nor auth. When
  `POST /projects/tiny-jvm/api/compile` (or `/run`) is added it will
  need an `X-CSRFToken` header and input/time limits — noted in the
  route docstring and in §8.2.
- **No config keys, no models, no migration.** Nothing here has server
  state.
- **No CSP change yet.** Needed before the WASM demo ships (§8.3), not
  before a static page.
- The project is **listed immediately** (not `on_hold`). The page is
  honest that the demo is pending; hiding it until the VM is done would
  mean nothing to show for the design work.

### 10.4 Verification run (this pass)

Against `.venv/Scripts/python.exe`:

- `create_app('development')` imports cleanly.
- `tiny_jvm.index` resolves to `/projects/tiny-jvm`; `GET` → 200.
- `GET /projects` → 200, contains "Tiny JVM", references `tiny-jvm.svg`.
- `GET /static/assets/img/icons/tiny-jvm.svg` → 200.
- No existing route touched.

---

## 11. Build order (remaining work)

Each step is independently demonstrable, so the project is never in a
"nothing works yet" state on the site.

1. **C VM + JVM reference VM.** `vm.c` / `vm.h`, and `RefVM.java` for
   tests. Deliverable: a hand-assembled `.tvm` runs and prints.
2. **Java toolchain.** Lexer → parser → resolver → codegen → CLI, with
   the JUnit suite. Deliverable: `tlc fib.tl -o fib.tvm`, and `--dump`.
3. **Wire them together.** `tlc` output runs on `vm.c`. Golden tests
   compare `RefVM` and the C VM on the same bytecode. Deliverable: all
   samples run end to end from the command line.
4. **WASM build + in-page stepper.** emscripten build, static assets,
   the picker/stepper/console UI on `index.html`. CSP gets
   `'wasm-unsafe-eval'`. Deliverable: the demo runs in the browser.
5. **Wokwi project.** Arduino host, embedded bytecode, thermistor + LED,
   the iframe on the page, the footprint writeup. Deliverable: the
   over-temp alarm runs on the simulated board.
6. **GitHub repo + README** for the VM and the toolchain, linked from
   the page (the flagship projects each carry their own README).

---

## 12. Risks and open questions

| Risk / question | Current thinking |
|-----------------|------------------|
| Free-form editing needs a server compile endpoint | Ship samples-only (precompiled `.tvm`) first; add `/api/compile` only if warranted (§8.2) |
| CSP enforcement later breaks the WASM loader | Add `'wasm-unsafe-eval'` when step 4 lands; re-check if CSP is ever switched from report-only to enforcing |
| "Tiny JVM" reads as a literal JVM-compatibility claim | Page and this doc both state plainly that it is a custom VM with a JVM-*style* stack architecture, not JVM bytecode |
| Scope creep into a "real" language (arrays, strings, floats) | v1 grammar in §4 is frozen; extensions are a later, separate revision |
| Running `tlc` in a subprocess on the server (if §8.2 option 2) | Hard input size cap, wall-clock timeout, no filesystem writes outside a temp dir, rate-limited like other public endpoints |

---

## 13. File manifest (whole project, when complete)

```
docs/build-spec-tiny-jvm.md                     this document

app/blueprints/tiny_jvm/__init__.py             blueprint  [done]
app/blueprints/tiny_jvm/routes.py               route + OPCODES/SAMPLES  [done]
app/templates/tiny_jvm/index.html               page  [placeholder done]
app/static/assets/img/icons/tiny-jvm.svg        card icon  [done]
app/static/js/tiny_jvm/tinyjvm.js               emscripten loader  [later]
app/static/js/tiny_jvm/tinyjvm.wasm             compiled VM  [later]
app/static/js/tiny_jvm/stepper.js               in-page UI  [later]
app/static/assets/tiny_jvm/*.tvm                precompiled samples  [later]

tools/tinyjvm/                                   (new top-level, or a sibling repo)
  vm/vm.h  vm/vm.c                               the C VM  [later]
  vm/wasm_host.c                                 WASM host bindings  [later]
  wokwi/main.c  wokwi/diagram.json               embedded target  [later]
  tlc/ (Gradle project)                          the Java toolchain  [later]
    build.gradle.kts
    src/main/java/com/nelsonkoskela/tinyjvm/
      Main.java Lexer.java Token.java TokenType.java
      Parser.java Expr.java Stmt.java Resolver.java
      CodeGen.java Chunk.java Disassembler.java
    src/test/java/com/nelsonkoskela/tinyjvm/
      LexerTest.java ParserTest.java ResolverTest.java
      CodeGenGoldenTest.java EndToEndTest.java RefVM.java
```

`[done]` items are this pass. Everything else is specified above and
built in the order of §11.
