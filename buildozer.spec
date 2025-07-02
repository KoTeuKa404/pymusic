[app]

title = Py Music 
package.name = pymusic
package.domain = org.koteuka404
source.dir = .
source.include_exts = py,kv,png,jpg,ttf,mp3,mp4,wav
version = 1.0
requirements = python3, kivy==2.2.1, kivymd, ffpyplayer, yt-dlp, pycryptodome, httpx, beautifulsoup4, urllib3, charset-normalizer, certifi,yt_dlp, idna, httpcore, cryptography, h11, requests,typing_extensions,pyjnius
orientation = portrait
osx.python_version = 3
fullscreen = 1

# Android configurations
android.permissions = INTERNET, WAKE_LOCK, FOREGROUND_SERVICE,MODIFY_AUDIO_SETTINGS,RECORD_AUDIO,READ_EXTERNAL_STORAGE, WRITE_EXTERNAL_STORAGE
android.minapi = 21
android.sdk = 33
android.ndk = 25c
android.ndk_api = 21
android.private_storage = True
android.copy_libs = 1
android.allow_backup = True
android.entrypoint = org.kivy.android.PythonActivity
android.apptheme = @android:style/Theme.NoTitleBar
android.archs = armeabi-v7a, arm64-v8a
android.foreground = False
android.services = audio_service:service.main


# Manual NDK & SDK paths
android.ndk_path = /home/home/.buildozer/android/platform/android-ndk-r25c
android.sdk_path = /home/home/.buildozer/android/platform/android-sdk

# SDL2 bootstrap (for GUI)
android.bootstrap = sdl2

# Logging and debugging
log_level = 2
warn_on_root = 1
android.env =
    SDL_AAUDIO_ENABLED=0
    SDL_AUDIODRIVER=opensl_es