pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // 카카오 로그인 SDK 는 카카오 저장소에만 있다. 다른 라이브러리는 여기서 찾지 않게 묶는다
        maven("https://devrepo.kakao.com/nexus/content/groups/public/") {
            content { includeGroup("com.kakao.sdk") }
        }
    }
}

// CANDiY HC SDK — 압축을 푼 SDK 폴더를 연결합니다 (README 참고)
includeBuild("../candiyHC")

rootProject.name = "candiyHC-watch"
include(":mobile")
// 워치 직접 수집 실험 — Health Services 로 기록해 Data Layer 로 폰 앱에 넘긴다
include(":wear")
