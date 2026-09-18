# How to write and run a Tiny JVM program

You don't write a `.tvm` file directly, almost ever. `.tvm` is the
*compiled bytecode format* (see `docs/build-spec-tiny-jvm.md` section
6.3) -- a binary file, not something meant to be typed by hand. The
normal path is:

```
you write:      program.tl     (a text file, in the TL language)
tlc compiles:   program.tl  →  program.tvm
a VM runs it:   program.tvm →  program's output
```

Everything below points at files that exist on disk right now, in this
repo, that you can actually open.

## 1. Write a `.tl` file

Open one of the three real, working example programs and use it as a
template:

- `tools/tinyjvm/tlc/samples/fib.tl` -- loops, locals, arithmetic
- `tools/tinyjvm/tlc/samples/factorial.tl` -- a function, recursion
- `tools/tinyjvm/tlc/samples/thermostat.tl` -- the GPIO built-ins

These are the exact same three programs shown on the live site's
`/projects/tiny-jvm` page (`app/blueprints/tiny_jvm/routes.py`'s
`SAMPLE_PROGRAMS` list) -- editing one of these files and recompiling it
is the fastest way to see a change take effect.

### The language, quickly (full detail: `docs/build-spec-tiny-jvm.md` section 4)

- **One type**: 32-bit integers. `true`/`false` are just `1`/`0`.
- **Declare**: `let x = 5;`
- **Reassign**: `x = 6;` (assignment is itself an expression, so `print x = 6;` is legal and prints `6`)
- **Control flow**: `if (cond) { ... } else { ... }`, `while (cond) { ... }`
- **Functions**: `fn add(a, b) { return a + b; }` -- recursion works
- **Print**: `print someExpression;` (no parentheses -- it's its own statement, not a function call)
- **Operators**: `+ - * / == != < <= > >= ! -` (unary minus/not)
- **Comments**: `// like this` or `# like this`
- **The three GPIO built-ins** (compile to dedicated opcodes, not real function calls): `pinMode(pin, mode)`, `digitalWrite(pin, value)`, `digitalRead(pin)`
- **Four built-in constants**, matching the Arduino convention: `OUTPUT` = 1, `INPUT` = 0, `HIGH` = 1, `LOW` = 0

There is no array/string type, no closures, and no standard library beyond the four GPIO built-ins above -- see the design rules in section 4 of the spec for why.

## 2. Compile it with `tlc`

`tlc` is the Java compiler at `tools/tinyjvm/tlc/`. It's already built.
First, load the toolchain paths into your shell (only needed once per
terminal session):

```bash
source tools/tinyjvm/env.local.sh
```

Then compile:

```bash
cd tools/tinyjvm/tlc
java -jar build/libs/tlc-0.1.0.jar samples/fib.tl -o samples/fib.tvm
```

Add `--dump` to also print a human-readable disassembly instead of (or
alongside) writing the file:

```bash
java -jar build/libs/tlc-0.1.0.jar samples/fib.tl --dump
```

If your program has a mistake, `tlc` prints a line number and a message
and exits without writing a `.tvm` file -- nothing silently produces a
broken file.

## 3. Run the compiled `.tvm`

The native C VM host is at `tools/tinyjvm/vm/tinyjvm_host.exe`, already
built. It takes one argument -- the path to a `.tvm` file, from wherever
you run it:

```bash
# from tools/tinyjvm/tlc/, right after compiling fib.tvm there:
../vm/tinyjvm_host.exe samples/fib.tvm
```

It prints whatever the program's `print` statements produce, and exits
with a non-zero code and a clear message (`trap: divide by zero`, etc.)
if the program hits a runtime error.

## The other, rarer path: hand-assembling bytecode directly

Before `tlc` existed, test `.tvm` files were built by hand, one opcode
at a time, using a small assembler at
`tools/tinyjvm/vm/samples/assemble.py` (labels, no manual byte-offset
math) and `tools/tinyjvm/vm/samples/build_samples.py` (the actual
programs it builds, including a few deliberately-broken ones that prove
the VM traps cleanly instead of crashing). This is for testing the VM
*itself* in isolation from the compiler -- not something you'd use to
write a real program. You almost certainly want section 1-2 above
instead.

## Try it yourself right now

```bash
source tools/tinyjvm/env.local.sh
cd tools/tinyjvm/tlc

# Copy a sample and change something -- e.g. open samples/fib.tl and
# change "while (i < 15)" to "while (i < 10)".

java -jar build/libs/tlc-0.1.0.jar samples/fib.tl -o samples/fib.tvm
../vm/tinyjvm_host.exe samples/fib.tvm
# should print a different number now
```
