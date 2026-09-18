plugins {
    java
    application
}

group = "com.nelsonkoskela.tinyjvm"
version = "0.1.0"

java {
    sourceCompatibility = JavaVersion.VERSION_21
    targetCompatibility = JavaVersion.VERSION_21
}

repositories {
    mavenCentral()
}

dependencies {
    testImplementation(platform("org.junit:junit-bom:5.11.0"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

application {
    // tlc program.tl -o program.tvm   (compile)
    // tlc program.tl --dump           (human-readable disassembly to stdout)
    mainClass.set("com.nelsonkoskela.tinyjvm.Main")
}

tasks.test {
    useJUnitPlatform()
}

tasks.jar {
    manifest {
        attributes["Main-Class"] = "com.nelsonkoskela.tinyjvm.Main"
    }
    // No runtime dependencies (JUnit is test-only), so a plain jar is
    // already runnable with `java -jar` -- no shadow/fat-jar plugin needed.
}
