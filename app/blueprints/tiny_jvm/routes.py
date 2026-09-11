from flask import render_template

from app.blueprints.tiny_jvm import bp

# The instruction set the placeholder page renders as a reference table.
# This is the *specification* the real VM and the Java assembler will both
# target -- kept here (not just in docs/) so the page and the eventual
# implementation read from one list. Opcodes are 1 byte; operands, where
# present, are noted per row. See docs/build-spec-tiny-jvm.md for the full
# rationale behind the stack-machine design.
OPCODES = [
    {"hex": "0x01", "name": "PUSH", "operand": "int32 (LE)", "effect": "push a constant onto the stack"},
    {"hex": "0x02", "name": "POP", "operand": "--", "effect": "discard the top of stack"},
    {"hex": "0x03", "name": "DUP", "operand": "--", "effect": "duplicate the top of stack"},
    {"hex": "0x10", "name": "ADD", "operand": "--", "effect": "a, b -> a + b"},
    {"hex": "0x11", "name": "SUB", "operand": "--", "effect": "a, b -> a - b"},
    {"hex": "0x12", "name": "MUL", "operand": "--", "effect": "a, b -> a * b"},
    {"hex": "0x13", "name": "DIV", "operand": "--", "effect": "a, b -> a / b (trap on 0)"},
    {"hex": "0x20", "name": "LOAD", "operand": "uint8 slot", "effect": "push local variable #slot"},
    {"hex": "0x21", "name": "STORE", "operand": "uint8 slot", "effect": "pop into local variable #slot"},
    {"hex": "0x30", "name": "JMP", "operand": "int16 offset", "effect": "unconditional branch"},
    {"hex": "0x31", "name": "JZ", "operand": "int16 offset", "effect": "branch if top of stack == 0"},
    {"hex": "0x32", "name": "JNZ", "operand": "int16 offset", "effect": "branch if top of stack != 0"},
    {"hex": "0x40", "name": "CALL", "operand": "uint16 addr", "effect": "call function at bytecode addr"},
    {"hex": "0x41", "name": "RET", "operand": "--", "effect": "return from function"},
    {"hex": "0x50", "name": "PRINT", "operand": "--", "effect": "pop and emit to the output console"},
    {"hex": "0x51", "name": "PINMODE", "operand": "--", "effect": "mode, pin -> configure a GPIO pin"},
    {"hex": "0x52", "name": "DWRITE", "operand": "--", "effect": "value, pin -> drive a GPIO pin"},
    {"hex": "0x53", "name": "DREAD", "operand": "--", "effect": "pin -> push the pin's current level"},
    {"hex": "0xFF", "name": "HALT", "operand": "--", "effect": "stop the VM"},
]

# Programs the demo page will let a visitor pick from once the WASM VM is
# wired up. Written in the Option-B source language (see the build spec).
# Shipped now as static text so the placeholder page has real content and
# the eventual `ctx`/assembler work has fixed targets to compile.
SAMPLE_PROGRAMS = [
    {
        "slug": "fib",
        "title": "Fibonacci",
        "shows": "loops, locals, arithmetic",
        "source": (
            "let a = 0;\n"
            "let b = 1;\n"
            "let i = 0;\n"
            "while (i < 15) {\n"
            "    let t = a + b;\n"
            "    a = b;\n"
            "    b = t;\n"
            "    i = i + 1;\n"
            "}\n"
            "print a;\n"
        ),
    },
    {
        "slug": "factorial",
        "title": "Factorial (recursive)",
        "shows": "function calls, the call stack, RET",
        "source": (
            "fn fact(n) {\n"
            "    if (n == 0) { return 1; }\n"
            "    return n * fact(n - 1);\n"
            "}\n"
            "print fact(6);\n"
        ),
    },
    {
        "slug": "thermostat",
        "title": "Over-temp alarm",
        "shows": "the same bytecode driving a GPIO on the Wokwi board",
        "source": (
            "# reads a simulated sensor on pin 34, lights pin 2 when it's hot\n"
            "pinMode(2, OUTPUT);\n"
            "while (1) {\n"
            "    let c = digitalRead(34);\n"
            "    digitalWrite(2, c > 28);\n"
            "}\n"
        ),
    },
]


@bp.route("")
def index():
    """Placeholder landing page for the Tiny JVM project.

    The interactive demo (a WebAssembly build of the C interpreter running
    bytecode produced by the Java toolchain) is not wired up yet -- this
    page currently explains the concept and the architecture, and shows
    the instruction set and the sample programs the demo will run. The
    full plan lives in docs/build-spec-tiny-jvm.md.

    Static and read-only: no form posts, so nothing here touches CSRF, the
    database, or auth. When the `/api/run` endpoint is added (compile +
    execute server-side as a fallback for no-WASM browsers) it will need
    an X-CSRFToken header like the assistant and leetcode blueprints.
    """
    return render_template(
        "tiny_jvm/index.html",
        opcodes=OPCODES,
        sample_programs=SAMPLE_PROGRAMS,
    )
