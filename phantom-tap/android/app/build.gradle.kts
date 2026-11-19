plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.phantomtap"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.phantomtap"
        // java.time (LocalTime, ZonedDateTime) without desugaring needs 26.
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

// No runtime dependencies on purpose: plain platform APIs only, so there is
// nothing to keep updated and nothing between the alarm and the tap.
dependencies {
    testImplementation("junit:junit:4.13.2")
}
