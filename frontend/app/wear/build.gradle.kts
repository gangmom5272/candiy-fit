import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

// release 도장 위치·비밀번호는 폰 앱과 같은 local.properties 에서 읽는다
val localProps = Properties().apply {
    rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}
fun localProp(key: String, default: String = "") = localProps.getProperty(key, default)

android {
    namespace = "io.candiy.healthconnect.quickstart.wear"
    compileSdk = 36

    defaultConfig {
        // Data Layer 는 폰 앱과 applicationId·서명이 같아야 서로를 찾는다
        applicationId = "io.candiy.healthconnect.quickstart"
        minSdk = 30
        targetSdk = 35
        versionCode = 4
        versionName = "1.3-wear"
    }

    // Data Layer 는 서명도 폰 앱과 같아야 해서 폰 앱(mobile)과 같은 release 도장을 쓴다
    val releaseStore = localProp("release.storeFile")
    signingConfigs {
        create("release") {
            if (releaseStore.isNotBlank()) {
                storeFile = file(releaseStore)
                storePassword = localProp("release.storePassword")
                keyAlias = localProp("release.keyAlias", "link")
                keyPassword = localProp("release.keyPassword", localProp("release.storePassword"))
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (releaseStore.isNotBlank()) signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    kotlinOptions { jvmTarget = "11" }
}

dependencies {
    implementation(libs.androidx.health.services)
    // 15분마다 모아 둔 자동 수집분을 폰으로 보낸다
    implementation(libs.androidx.work.runtime.ktx)
    implementation(libs.androidx.concurrent.futures.ktx)
    implementation(libs.play.services.wearable)
    implementation(libs.kotlinx.coroutines.play.services)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.activity.compose)
    // play-services-wearable 이 Fragment 1.1 을 끌어온다. registerForActivityResult(권한 요청)는 Fragment 1.3 이상이어야
    // 요청 결과가 올바르게 돌아온다(release 빌드의 lint 치명 오류 InvalidFragmentVersionForActivityResult)
    implementation("androidx.fragment:fragment:1.8.5")
}
