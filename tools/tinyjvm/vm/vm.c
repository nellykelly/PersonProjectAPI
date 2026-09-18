/* Tiny JVM -- the C interpreter. See vm.h for the public interface and
 * docs/build-spec-tiny-jvm.md sections 6-7 for the full design rationale.
 *
 * Every array access here is bounds-checked before it happens, and every
 * failure mode is a named VMStatus, never undefined behaviour -- that
 * discipline is itself part of what this project demonstrates (build
 * spec section 7.3). The dispatch loop is a plain switch, not threaded
 * dispatch: clearer to read, and fast enough for programs this small.
 */
#include "vm.h"

#include <string.h>

/* ---- Fetch helpers: every one bounds-checks ip against code_len before
 *      touching memory, and sets a specific trap status on failure. Each
 *      returns 1 on success, 0 on failure (vm->status is already set). */

static int fetch_u8(VM *vm, uint8_t *out) {
    if (vm->ip >= vm->code_len) {
        vm->status = VM_TRAP_BAD_JUMP;
        return 0;
    }
    *out = vm->code[vm->ip++];
    return 1;
}

static int fetch_i16(VM *vm, int16_t *out) {
    if ((uint32_t)vm->ip + 2u > vm->code_len) {
        vm->status = VM_TRAP_TRUNCATED_OPERAND;
        return 0;
    }
    uint16_t raw = (uint16_t)(vm->code[vm->ip] | (vm->code[vm->ip + 1] << 8));
    vm->ip = (uint16_t)(vm->ip + 2);
    *out = (int16_t)raw;
    return 1;
}

static int fetch_u16(VM *vm, uint16_t *out) {
    if ((uint32_t)vm->ip + 2u > vm->code_len) {
        vm->status = VM_TRAP_TRUNCATED_OPERAND;
        return 0;
    }
    *out = (uint16_t)(vm->code[vm->ip] | (vm->code[vm->ip + 1] << 8));
    vm->ip = (uint16_t)(vm->ip + 2);
    return 1;
}

static int fetch_i32(VM *vm, int32_t *out) {
    if ((uint32_t)vm->ip + 4u > vm->code_len) {
        vm->status = VM_TRAP_TRUNCATED_OPERAND;
        return 0;
    }
    uint32_t raw = (uint32_t)vm->code[vm->ip]
                 | ((uint32_t)vm->code[vm->ip + 1] << 8)
                 | ((uint32_t)vm->code[vm->ip + 2] << 16)
                 | ((uint32_t)vm->code[vm->ip + 3] << 24);
    vm->ip = (uint16_t)(vm->ip + 4);
    *out = (int32_t)raw;
    return 1;
}

/* ---- Stack helpers ---- */

static int vm_push(VM *vm, int32_t v) {
    if (vm->sp >= TVM_STACK_MAX) {
        vm->status = VM_TRAP_STACK_OVERFLOW;
        return 0;
    }
    vm->stack[vm->sp++] = v;
    return 1;
}

static int vm_pop(VM *vm, int32_t *out) {
    if (vm->sp <= 0) {
        vm->status = VM_TRAP_STACK_UNDERFLOW;
        return 0;
    }
    *out = vm->stack[--vm->sp];
    return 1;
}

/* Current frame's locals. Always valid once loaded: fp is >= 1 for the
 * lifetime of a loaded VM (frame 0 is the implicit top-level scope), and
 * `slot` is a uint8_t so it can never exceed TVM_LOCALS_MAX-1 -- there is
 * no separate bounds check to write because there is no way to violate
 * it. */
static int32_t *current_locals(VM *vm) {
    return vm->frames[vm->fp - 1].locals;
}

/* Relative branch: JMP/JZ/JNZ's offset is measured from the instruction
 * *following* the 2-byte operand (i.e. from vm->ip as it stands right
 * after fetch_i16 already consumed the operand), matching how the code
 * generator back-patches a placeholder at that same position (build spec
 * section 5.4). Landing outside the code section is a trap, checked here
 * rather than left to the next fetch, so a negative offset that
 * underflows past 0 is also caught (uint16_t would otherwise wrap). */
static int branch_to(VM *vm, int16_t offset) {
    int32_t target = (int32_t)vm->ip + (int32_t)offset;
    if (target < 0 || target > (int32_t)vm->code_len) {
        vm->status = VM_TRAP_BAD_JUMP;
        return 0;
    }
    vm->ip = (uint16_t)target;
    return 1;
}

VMStatus vm_load(VM *vm, const uint8_t *tvm_data, size_t tvm_len, const VMHost *host) {
    memset(vm, 0, sizeof(*vm));
    vm->host = host;

    if (tvm_len < TVM_HEADER_LEN) {
        vm->status = VM_ERR_TRUNCATED;
        return vm->status;
    }
    if (memcmp(tvm_data, TVM_MAGIC, TVM_MAGIC_LEN) != 0) {
        vm->status = VM_ERR_BAD_MAGIC;
        return vm->status;
    }
    uint8_t version = tvm_data[4];
    if (version != TVM_VERSION) {
        vm->status = VM_ERR_BAD_VERSION;
        return vm->status;
    }

    uint16_t entry = (uint16_t)(tvm_data[5] | (tvm_data[6] << 8));
    /* code_len is a uint16_t read straight from the file, so it can never
     * exceed TVM_CODE_MAX (65536) -- that limit is enforced by the format
     * itself (a 16-bit field), not by a runtime check here. */
    uint16_t code_len = (uint16_t)(tvm_data[7] | (tvm_data[8] << 8));

    if (tvm_len < (size_t)TVM_HEADER_LEN + code_len) {
        vm->status = VM_ERR_TRUNCATED;
        return vm->status;
    }

    vm->code = tvm_data + TVM_HEADER_LEN;
    vm->code_len = code_len;
    vm->ip = entry;
    vm->sp = 0;
    vm->fp = 1; /* frame 0: the implicit top-level scope */
    vm->status = VM_RUNNING;
    /* frames[0].locals is already zeroed by the memset above; ret_ip is
     * never read for frame 0 (RET at fp==1 is VM_TRAP_FRAME_UNDERFLOW,
     * not a jump to garbage). */
    return vm->status;
}

VMStatus vm_step(VM *vm) {
    if (vm->status != VM_RUNNING) {
        return vm->status; /* already terminal -- idempotent */
    }

    uint8_t opcode;
    if (!fetch_u8(vm, &opcode)) {
        return vm->status;
    }

    int32_t a, b;
    int16_t rel;
    uint16_t addr;
    uint8_t slot;

    switch ((VMOpcode)opcode) {
    case OP_PUSH:
        if (!fetch_i32(vm, &a)) return vm->status;
        if (!vm_push(vm, a)) return vm->status;
        break;

    case OP_POP:
        if (!vm_pop(vm, &a)) return vm->status;
        break;

    case OP_DUP:
        if (vm->sp <= 0) { vm->status = VM_TRAP_STACK_UNDERFLOW; return vm->status; }
        if (!vm_push(vm, vm->stack[vm->sp - 1])) return vm->status;
        break;

    case OP_ADD:
        if (!vm_pop(vm, &b) || !vm_pop(vm, &a)) return vm->status;
        if (!vm_push(vm, a + b)) return vm->status;
        break;

    case OP_SUB:
        if (!vm_pop(vm, &b) || !vm_pop(vm, &a)) return vm->status;
        if (!vm_push(vm, a - b)) return vm->status;
        break;

    case OP_MUL:
        if (!vm_pop(vm, &b) || !vm_pop(vm, &a)) return vm->status;
        if (!vm_push(vm, a * b)) return vm->status;
        break;

    case OP_DIV:
        if (!vm_pop(vm, &b) || !vm_pop(vm, &a)) return vm->status;
        if (b == 0) { vm->status = VM_TRAP_DIV_ZERO; return vm->status; }
        /* INT32_MIN / -1 overflows int32_t -- checked before the actual
         * division, which would otherwise be undefined behaviour (a real
         * hardware trap on most platforms, not a graceful VMStatus). */
        if (a == INT32_MIN && b == -1) { vm->status = VM_TRAP_DIV_OVERFLOW; return vm->status; }
        if (!vm_push(vm, a / b)) return vm->status;
        break;

    case OP_LT:
        if (!vm_pop(vm, &b) || !vm_pop(vm, &a)) return vm->status;
        if (!vm_push(vm, (a < b) ? 1 : 0)) return vm->status;
        break;

    case OP_LOAD:
        if (!fetch_u8(vm, &slot)) return vm->status;
        if (!vm_push(vm, current_locals(vm)[slot])) return vm->status;
        break;

    case OP_STORE:
        if (!fetch_u8(vm, &slot)) return vm->status;
        if (!vm_pop(vm, &a)) return vm->status;
        current_locals(vm)[slot] = a;
        break;

    case OP_JMP:
        if (!fetch_i16(vm, &rel)) return vm->status;
        if (!branch_to(vm, rel)) return vm->status;
        break;

    case OP_JZ:
        if (!fetch_i16(vm, &rel)) return vm->status;
        if (!vm_pop(vm, &a)) return vm->status;
        if (a == 0) { if (!branch_to(vm, rel)) return vm->status; }
        break;

    case OP_JNZ:
        if (!fetch_i16(vm, &rel)) return vm->status;
        if (!vm_pop(vm, &a)) return vm->status;
        if (a != 0) { if (!branch_to(vm, rel)) return vm->status; }
        break;

    case OP_CALL:
        if (!fetch_u16(vm, &addr)) return vm->status;
        if (vm->fp >= TVM_FRAMES_MAX) { vm->status = VM_TRAP_FRAME_OVERFLOW; return vm->status; }
        if (addr > vm->code_len) { vm->status = VM_TRAP_BAD_JUMP; return vm->status; }
        vm->frames[vm->fp].ret_ip = vm->ip;
        memset(vm->frames[vm->fp].locals, 0, sizeof(vm->frames[vm->fp].locals));
        vm->fp++;
        vm->ip = addr;
        break;

    case OP_RET:
        if (vm->fp <= 1) { vm->status = VM_TRAP_FRAME_UNDERFLOW; return vm->status; }
        vm->fp--;
        vm->ip = vm->frames[vm->fp].ret_ip;
        break;

    case OP_PRINT:
        if (!vm_pop(vm, &a)) return vm->status;
        vm->host->print_int(a);
        break;

    case OP_PINMODE:
        /* CodeGen pushes call arguments left-to-right (pinMode(pin, mode)),
         * so pin was pushed first and sits deeper; mode was pushed last and
         * is on top. Pop top first: b = mode, a = pin. */
        if (!vm_pop(vm, &b) || !vm_pop(vm, &a)) return vm->status;
        vm->host->pin_mode(/* pin */ a, /* mode */ b);
        break;

    case OP_DWRITE:
        /* Same left-to-right push order for digitalWrite(pin, value):
         * b (top) = value, a (below) = pin. */
        if (!vm_pop(vm, &b) || !vm_pop(vm, &a)) return vm->status;
        vm->host->digital_write(/* pin */ a, /* value */ b);
        break;

    case OP_DREAD:
        if (!vm_pop(vm, &a)) return vm->status;
        if (!vm_push(vm, vm->host->digital_read(/* pin */ a))) return vm->status;
        break;

    case OP_HALT:
        vm->status = VM_OK_HALTED;
        break;

    default:
        vm->status = VM_TRAP_BAD_OPCODE;
        break;
    }

    return vm->status;
}

VMStatus vm_run(VM *vm) {
    while (vm->status == VM_RUNNING) {
        vm_step(vm);
    }
    return vm->status;
}

const char *vm_status_string(VMStatus status) {
    switch (status) {
    case VM_RUNNING:               return "running";
    case VM_OK_HALTED:             return "halted (ok)";
    case VM_ERR_BAD_MAGIC:         return "bad magic (not a .tvm file)";
    case VM_ERR_BAD_VERSION:       return "unsupported .tvm version";
    case VM_ERR_TRUNCATED:         return "truncated .tvm file";
    case VM_TRAP_STACK_OVERFLOW:   return "trap: stack overflow";
    case VM_TRAP_STACK_UNDERFLOW:  return "trap: stack underflow";
    case VM_TRAP_FRAME_OVERFLOW:   return "trap: call depth exceeded";
    case VM_TRAP_FRAME_UNDERFLOW:  return "trap: return with no caller";
    case VM_TRAP_BAD_SLOT:         return "trap: bad local slot";
    case VM_TRAP_DIV_ZERO:         return "trap: divide by zero";
    case VM_TRAP_DIV_OVERFLOW:     return "trap: divide overflow (INT32_MIN / -1)";
    case VM_TRAP_BAD_OPCODE:       return "trap: bad opcode";
    case VM_TRAP_BAD_JUMP:         return "trap: jump/call outside code";
    case VM_TRAP_TRUNCATED_OPERAND: return "trap: truncated operand";
    }
    return "unknown status";
}
