let a = 0;
let b = 1;
let i = 0;
while (i < 15) {
    let t = a + b;
    a = b;
    b = t;
    i = i + 1;
}
print a;
