/* Native host for the Tiny JVM C interpreter -- the first proof that
 * vm.c actually works, before the WASM or Wokwi targets exist (build
 * spec section 11, step 1). Reads a .tvm file, runs it to completion,
 * and implements the VMHost seam with plain stdio: PRINT goes to
 * stdout, PINMODE/DWRITE log what they'd do, DREAD returns a fixed
 * simulated value. No hardware here -- this is the "hosted" target, not
 * an embedded one; see wasm_host.c / the Wokwi firmware for the other
 * two VMHost implementations the same vm.c will pair with later.
 */
#include <stdio.h>
#include <stdlib.h>

#include "vm.h"

static void host_print_int(int32_t value) {
    printf("%d\n", value);
}

static void host_pin_mode(int pin, int mode) {
    printf("[gpio] pinMode(pin=%d, mode=%d)\n", pin, mode);
}

static void host_digital_write(int pin, int value) {
    printf("[gpio] digitalWrite(pin=%d, value=%d)\n", pin, value);
}

/* No real or simulated sensor is wired up for the native host -- always
 * reads low. The WASM host will route this to a page control (a slider
 * or toggle); the Wokwi host will route it to the real Arduino core. */
static int host_digital_read(int pin) {
    (void)pin;
    return 0;
}

static const VMHost NATIVE_HOST = {
    .pin_mode = host_pin_mode,
    .digital_write = host_digital_write,
    .digital_read = host_digital_read,
    .print_int = host_print_int,
};

static uint8_t *read_whole_file(const char *path, size_t *out_len) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "error: could not open %s\n", path);
        return NULL;
    }
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return NULL; }
    long size = ftell(f);
    if (size < 0) { fclose(f); return NULL; }
    if (fseek(f, 0, SEEK_SET) != 0) { fclose(f); return NULL; }

    uint8_t *buf = malloc((size_t)size > 0 ? (size_t)size : 1);
    if (!buf) { fclose(f); return NULL; }

    size_t read = fread(buf, 1, (size_t)size, f);
    fclose(f);
    if (read != (size_t)size) {
        free(buf);
        fprintf(stderr, "error: short read on %s\n", path);
        return NULL;
    }
    *out_len = (size_t)size;
    return buf;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s program.tvm\n", argv[0]);
        return 64; /* EX_USAGE */
    }

    size_t len = 0;
    uint8_t *data = read_whole_file(argv[1], &len);
    if (!data) {
        return 66; /* EX_NOINPUT */
    }

    VM vm;
    VMStatus status = vm_load(&vm, data, len, &NATIVE_HOST);
    if (status != VM_RUNNING) {
        fprintf(stderr, "error loading %s: %s\n", argv[1], vm_status_string(status));
        free(data);
        return 65; /* EX_DATAERR */
    }

    status = vm_run(&vm);
    free(data);

    if (status != VM_OK_HALTED) {
        fprintf(stderr, "%s\n", vm_status_string(status));
        return 1;
    }
    return 0;
}
