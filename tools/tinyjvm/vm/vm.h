/* Tiny JVM -- the C interpreter's public interface.
 *
 * A stack machine that executes the .tvm bytecode format documented in
 * docs/build-spec-tiny-jvm.md section 6. Every limit below is a
 * compile-time constant and every buffer is statically sized -- there is
 * no malloc anywhere in this VM, on purpose: a fixed memory footprint,
 * known at build time, is a firmware-correctness requirement, not a
 * style preference (see the build spec section 6.4).
 *
 * The same vm.c compiles unmodified for three targets: a native host
 * (this repo's smoke tests), WebAssembly (the in-page demo), and an
 * ESP32/Arduino build (the Wokwi demo). Only the VMHost callbacks below
 * differ per target -- see section 7.2.
 */
#ifndef TINYJVM_VM_H
#define TINYJVM_VM_H

#include <stddef.h>
#include <stdint.h>

/* ---- Runtime limits (build spec section 6.4) ---- */
#define TVM_STACK_MAX  256   /* value stack depth: 256 * 4 B = 1 KB       */
#define TVM_FRAMES_MAX 64    /* call stack depth: bounds recursion        */
#define TVM_LOCALS_MAX 256   /* locals per frame: matches the uint8 slot  */
#define TVM_CODE_MAX   65536 /* code size: matches the 16-bit offsets     */

/* ---- Bytecode file format (build spec section 6.3) ---- */
#define TVM_MAGIC        "TVM\0"
#define TVM_MAGIC_LEN    4
#define TVM_VERSION      1
#define TVM_HEADER_LEN   9 /* magic(4) + version(1) + entry(2) + len(2)  */

/* ---- Opcodes (build spec section 6.2 --
 *      kept byte-for-byte identical to app/blueprints/tiny_jvm/routes.py's
 *      OPCODES table, which is what the project page renders). */
typedef enum {
    OP_PUSH    = 0x01, /* int32 (LE) operand -- push a constant           */
    OP_POP     = 0x02,
    OP_DUP     = 0x03,
    OP_ADD     = 0x10,
    OP_SUB     = 0x11,
    OP_MUL     = 0x12,
    OP_DIV     = 0x13, /* traps on divide-by-zero                        */
    /* Added during codegen (build spec section 6.2 predicted this: "a
     * later revision can add EQ/LT/GT -- the format has room"). == and !=
     * synthesize from SUB + a zero test; but with only JZ/JNZ available,
     * <, <=, >, >= genuinely cannot be synthesized from SUB alone -- a
     * zero test can't recover the *sign* of a difference. One signed
     * less-than opcode is enough: a>b is b<a, a<=b is !(b<a), a>=b is
     * !(a<b), all via LT plus negation, so this is the only addition
     * needed. */
    OP_LT      = 0x14, /* a, b -> (a < b) ? 1 : 0, signed                */
    OP_LOAD    = 0x20, /* uint8 slot                                     */
    OP_STORE   = 0x21, /* uint8 slot                                     */
    OP_JMP     = 0x30, /* int16 (LE) relative offset                     */
    OP_JZ      = 0x31,
    OP_JNZ     = 0x32,
    OP_CALL    = 0x40, /* uint16 (LE) absolute bytecode address          */
    OP_RET     = 0x41,
    OP_PRINT   = 0x50,
    OP_PINMODE = 0x51, /* pin, mode -> configure a GPIO pin              */
    OP_DWRITE  = 0x52, /* pin, value -> drive a GPIO pin                 */
    OP_DREAD   = 0x53, /* pin -> push the pin's current level            */
    OP_HALT    = 0xFF,
} VMOpcode;

/* Every way a run can end. The VM never calls exit()/abort() -- it sets
 * this and returns control to the host, because on the MCU target there
 * is nowhere to exit *to*. */
typedef enum {
    VM_RUNNING = 0,     /* only used transiently inside vm_step/vm_run    */
    VM_OK_HALTED,       /* HALT opcode reached -- a normal, successful end */
    VM_ERR_BAD_MAGIC,
    VM_ERR_BAD_VERSION,
    VM_ERR_TRUNCATED,   /* the file is shorter than its own header says   */
    VM_TRAP_STACK_OVERFLOW,
    VM_TRAP_STACK_UNDERFLOW,
    VM_TRAP_FRAME_OVERFLOW,  /* call depth exceeded                       */
    VM_TRAP_FRAME_UNDERFLOW, /* RET with no caller (e.g. at top level)    */
    VM_TRAP_BAD_SLOT,        /* LOAD/STORE slot out of range              */
    VM_TRAP_DIV_ZERO,
    /* INT32_MIN / -1: the mathematical result (2147483648) doesn't fit in
     * int32_t. In C this is undefined behaviour, not just "a weird
     * answer" -- on real hardware the idiv instruction itself faults,
     * killing the whole process before vm->status could even be set.
     * Checked explicitly for the same reason divide-by-zero is: this VM's
     * one job is turning every failure into a named status, never a
     * crash (build spec section 7.1) -- and unlike zero, this one won't
     * show up until a program actually computes it. */
    VM_TRAP_DIV_OVERFLOW,
    VM_TRAP_BAD_OPCODE,
    VM_TRAP_BAD_JUMP,        /* a jump/call target lands outside code[]   */
    VM_TRAP_TRUNCATED_OPERAND, /* ip ran off the end mid-operand          */
} VMStatus;

/* The seam between VM core and hardware (build spec section 7.2). The
 * host installs one of these before running; only its contents differ
 * between the native/WASM/Wokwi builds, never the VM core. */
typedef struct {
    void (*pin_mode)(int pin, int mode);
    void (*digital_write)(int pin, int value);
    int  (*digital_read)(int pin);
    void (*print_int)(int32_t value);
} VMHost;

typedef struct {
    uint16_t ret_ip;
    int32_t  locals[TVM_LOCALS_MAX];
} VMFrame;

typedef struct {
    const uint8_t *code;      /* points at the loaded .tvm code section   */
    uint16_t       code_len;
    uint16_t       ip;        /* instruction pointer                     */

    int32_t        stack[TVM_STACK_MAX];
    int32_t        sp;        /* number of live values (next free slot)  */

    VMFrame        frames[TVM_FRAMES_MAX];
    int32_t        fp;        /* number of live frames (next free frame) */
                               /* fp is always >= 1 once loaded: frame 0  */
                               /* is the implicit top-level scope.        */

    VMStatus       status;
    const VMHost  *host;
} VM;

/* Parses a .tvm buffer's 9-byte header (magic, version, entry point, code
 * length), points `vm->code` directly at the buffer's code section (the
 * caller owns tvm_data's lifetime -- the VM never copies or frees it),
 * and leaves the VM ready to run from the file's declared entry point.
 * On success, sets vm->status to VM_RUNNING and returns it. On a
 * malformed file, sets and returns the specific VM_ERR_* reason (never a
 * VM_TRAP_*, since nothing has executed yet). */
VMStatus vm_load(VM *vm, const uint8_t *tvm_data, size_t tvm_len, const VMHost *host);

/* Runs from the current ip until HALT or a trap. Returns the terminal
 * status (VM_OK_HALTED or a VM_TRAP_*). */
VMStatus vm_run(VM *vm);

/* Executes exactly one instruction -- the primitive a future in-page
 * stepper UI drives one click at a time. Returns the status after the
 * step; once it stops returning VM_RUNNING, further calls are no-ops
 * that just return the same terminal status again. */
VMStatus vm_step(VM *vm);

/* A short, stable, human-readable name for a status -- used by the
 * native host's error output and (later) the in-page console. */
const char *vm_status_string(VMStatus status);

#endif /* TINYJVM_VM_H */
