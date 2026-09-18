package com.nelsonkoskela.tinyjvm;

/** A growable byte buffer for the code section being generated, plus the
 * back-patching primitives control flow and function calls both need
 * (build spec section 5.4): emit a placeholder 16-bit operand, remember
 * its position, then overwrite it once the real value (a jump distance,
 * a function's address) is known. All multi-byte values are little-
 * endian, matching the .tvm format (section 6.2/6.3) and vm.c's fetch
 * helpers exactly. */
public final class Chunk {
    private byte[] buffer = new byte[64];
    private int size = 0;

    public int size() {
        return size;
    }

    private void ensureCapacity(int extra) {
        if (size + extra > buffer.length) {
            int newCap = Math.max(buffer.length * 2, size + extra);
            byte[] bigger = new byte[newCap];
            System.arraycopy(buffer, 0, bigger, 0, size);
            buffer = bigger;
        }
    }

    public void writeByte(int b) {
        ensureCapacity(1);
        buffer[size++] = (byte) b;
    }

    public void writeU8(int v) {
        writeByte(v & 0xFF);
    }

    public void writeI32(int v) {
        ensureCapacity(4);
        buffer[size++] = (byte) (v & 0xFF);
        buffer[size++] = (byte) ((v >> 8) & 0xFF);
        buffer[size++] = (byte) ((v >> 16) & 0xFF);
        buffer[size++] = (byte) ((v >> 24) & 0xFF);
    }

    public void writeU16(int v) {
        ensureCapacity(2);
        buffer[size++] = (byte) (v & 0xFF);
        buffer[size++] = (byte) ((v >> 8) & 0xFF);
    }

    /** Reserves 2 bytes at the current position for a later back-patch
     * and returns the position of the first of those two bytes. */
    public int reserveU16() {
        int pos = size;
        writeU16(0);
        return pos;
    }

    public void patchU16(int pos, int value) {
        buffer[pos] = (byte) (value & 0xFF);
        buffer[pos + 1] = (byte) ((value >> 8) & 0xFF);
    }

    public byte[] toByteArray() {
        byte[] out = new byte[size];
        System.arraycopy(buffer, 0, out, 0, size);
        return out;
    }
}
