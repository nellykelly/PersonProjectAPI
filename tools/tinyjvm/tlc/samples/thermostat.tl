# reads a simulated sensor on pin 34, lights pin 2 when it's hot
pinMode(2, OUTPUT);
while (1) {
    let c = digitalRead(34);
    digitalWrite(2, c > 28);
}
