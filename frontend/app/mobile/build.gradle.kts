import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

// 서버 주소와 API 키는 local.properties 에서 읽는다 — 저장소에 키가 올라가지 않게
val localProps = Properties().apply {
    rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}
fun localProp(key: String, default: String = "") = localProps.getProperty(key, default)

android {
    namespace = "io.candiy.healthconnect.quickstart"
    compileSdk = 36

    defaultConfig {
        applicationId = "io.candiy.healthconnect.quickstart"
        minSdk = 28
        targetSdk = 35
        versionCode = 11
        versionName = "1.10"

        buildConfigField("String", "CANDIY_BASE_URL", "\"${localProp("candiy.baseUrl", "https://api.example.invalid")}\"")
        buildConfigField("String", "CANDIY_CLIENT_ID", "\"${localProp("candiy.clientId")}\"")
        buildConfigField("String", "CANDIY_CLIENT_SECRET", "\"${localProp("candiy.clientSecret")}\"")
        // 수집 전용 모드에서 폰에 저장된 ID 가 없을 때만 쓴다. 기본값 user-123 은 "설정 안 함"으로 본다
        buildConfigField("String", "CANDIY_END_USER_ID", "\"${localProp("candiy.endUserId", "user-123")}\"")
        // 수집 전용 모드(개발용) — 로그인 없이 기존 사용자 ID 로 수집·전송만 한다. 디버그 빌드에서만 켜진다
        buildConfigField("boolean", "COLLECTOR_MODE", localProp("candiy.collectorMode", "false"))
        // 연동 테스트 모드 — 목업 데이터를 Health Connect 에 쓰고 네 지표를 동기화한다. 디버그 빌드에서만 켜진다
        buildConfigField("boolean", "CANDIY_MOCK_DATA", localProp("candiy.mockData", "false"))
        // 우리 백엔드(로그인·회원 API). debug 는 개발 서버, release 는 아래 buildTypes 에서 운영 서버로 바꾼다
        buildConfigField("String", "BACKEND_BASE_URL", "\"${localProp("backend.baseUrl", "https://api.example.invalid")}\"")
        // AI 코치 근거 화면 미리보기(coaching/EvidencePreview.kt)를 release 빌드에서도 열 수 있게 하는 스위치(S15P31A403-440). 기본은 꺼짐.
        // 실기기에는 release 만 덮어 설치할 수 있어(debug 와 앱 ID 는 같고 도장이 다르다), 실기기에서 지어낸 값으로 화면 모양을 볼 때만 켠다:
        //   ./gradlew :mobile:assembleRelease -PevidencePreview=true
        // 확인이 끝나면 옵션 없이 다시 빌드해 덮어 설치한다
        buildConfigField("boolean", "EVIDENCE_PREVIEW", "${project.findProperty("evidencePreview") == "true"}")
        // 소셜 로그인 공개값. 비어 있으면 그 제공자 버튼은 "설정이 없어요"만 안내한다
        //  - google.serverClientId: 서버 GOOGLE_CLIENT_ID 와 같은 값(웹 클라이언트 ID). Android 클라이언트 ID 가 아니다
        //  - kakao.nativeAppKey: 서버 KAKAO_NATIVE_APP_KEY 와 같은 값. REST 키가 아니다
        val kakaoKey = localProp("kakao.nativeAppKey")
        buildConfigField("String", "GOOGLE_SERVER_CLIENT_ID", "\"${localProp("google.serverClientId")}\"")
        buildConfigField("String", "KAKAO_NATIVE_APP_KEY", "\"$kakaoKey\"")
        // 카카오 계정 로그인(웹) 뒤 앱으로 돌아오는 주소 kakao{앱 키}://oauth
        manifestPlaceholders["kakaoScheme"] = "kakao$kakaoKey"
    }

    // release 도장(제출·운영용). 도장 파일 위치와 비밀번호는 local.properties 에만 둔다 — 저장소·깃랩에 올리지 않는다.
    // 워치 앱(wear/build.gradle.kts)도 같은 도장을 써야 워치 기록이 폰 앱으로 전달된다
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
            // release 는 운영 서버로 보낸다(debug 는 위 defaultConfig 의 개발 서버)
            buildConfigField("String", "BACKEND_BASE_URL", "\"${localProp("backend.releaseBaseUrl", "https://api.example.invalid")}\"")
            // 개발용 모드는 release 에서 항상 끈다. 운영 서버는 이 앱 패키지가 쓴 기록을 실제 기록으로 적재하므로
            // 목업 기록이 들어가면 실제 회원 데이터와 섞인다 (S15P31A403-231)
            buildConfigField("boolean", "CANDIY_MOCK_DATA", "false")
            buildConfigField("boolean", "COLLECTOR_MODE", "false")
            // 도장 설정이 없으면 서명 없는 APK 가 나온다(설치 불가) — 빌드는 막지 않아 다른 팀원도 컴파일은 확인할 수 있다
            if (releaseStore.isNotBlank()) signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    kotlinOptions { jvmTarget = "11" }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    packaging {
        resources.excludes += setOf(
            "META-INF/INDEX.LIST",
            "META-INF/DEPENDENCIES",
            "META-INF/io.netty.versions.properties",
        )
    }
}

// local.properties 에서 목업을 켠 채 release 를 만들면 조용히 꺼 버리지 않고 빌드를 멈춘다.
// "켰는데 왜 목업이 안 나오지"와 "운영용 APK 에 목업이 켜졌나" 둘 다 헷갈리지 않게 한다
tasks.matching { it.name == "preReleaseBuild" }.configureEach {
    doFirst {
        if (localProp("candiy.mockData", "false").toBoolean()) {
            throw GradleException("release 빌드에서는 candiy.mockData 를 켤 수 없습니다. local.properties 에서 candiy.mockData=false 로 바꾸세요")
        }
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.lifecycle.runtime.ktx)

    // CANDiY HC SDK — settings.gradle.kts 의 includeBuild 로 연결한 SDK 폴더가 쓰입니다
    implementation("com.candiy:candiyHC:0.1.9")

    // 권한 요청 코드에서 직접 쓰므로 앱에서도 선언합니다
    implementation("androidx.health.connect:connect-client:1.1.0")

    // 소셜 로그인 — 외부 라이브러리는 "제공자에게서 ID 토큰을 받는 한 단계"에만 쓴다(auth/ProviderSignIn.kt).
    // HTTP 호출·토큰 저장·갱신·화면은 직접 만들었다. 직접 만들 수 없는 이유는 ProviderSignIn.kt 머리 주석에 있다
    implementation("androidx.credentials:credentials:1.3.0")
    implementation("androidx.credentials:credentials-play-services-auth:1.3.0")
    implementation("com.google.android.libraries.identity.googleid:googleid:1.1.1")
    implementation("com.kakao.sdk:v2-user:2.20.6")

    // AI 코치 탭의 WebSocket(명세 7절)에만 쓴다. TLS 위 WebSocket(핸드셰이크·마스킹·프레임·핑)을 직접 짜면 보안 실수 위험이 크고,
    // CANDiY SDK 가 이미 같은 버전을 앱에 넣고 있어 용량이 늘지 않는다. SDK 에 기대지 않게 명시한다. 코칭 REST 는 직접 부른다
    implementation("com.squareup.okhttp3:okhttp:4.9.2")

    // 워치 직접 수집 실험 — 워치 앱이 보낸 기록을 Data Layer 로 받는다
    implementation(libs.play.services.wearable)
    implementation(libs.kotlinx.coroutines.play.services)

    // 로그 가리기 규칙 확인용 (기기 없이 도는 단위 테스트)
    testImplementation("junit:junit:4.13.2")
    // 단위 테스트의 android.jar 에는 org.json 이 빈 껍데기라 실제 구현을 넣는다 (로그인 응답 해석 테스트)
    testImplementation("org.json:json:20240303")
}
