[app]

title = Py Music
package.name = pymusic
package.domain = org.koteuka404

source.dir = android_src
source.include_exts = py,kv,png,jpg,jpeg,svg,ttf,json,txt
source.include_patterns = images/*.png, ico/*.png, ico/*.svg, *.kv, *.py, *.json
source.exclude_patterns = *.so, *.pyd, *.pyc
source.exclude_dirs = __pycache__, .git, .idea, .vscode, venv, venv311, .venv, build, bin

version = 1.0

# python-for-android builds requirements with dependencies disabled, so KivyMD's
# runtime dependencies must be listed explicitly. KivyMD 2.x imports these at
# startup; without them Android exits with ModuleNotFoundError before the app UI.
requirements = python3, kivy, kivymd, pillow, materialyoucolor==3.0.3, materialshapes==0.3, asynckivy==0.6.4, ffpyplayer, yt_dlp>=2025.10.0, pycryptodome, httpx, beautifulsoup4, urllib3, charset-normalizer, certifi, idna, httpcore, cryptography, h11, requests, typing_extensions, pyjnius, ffpyplayer_codecs, filetype


orientation = portrait
fullscreen = 0

icon.filename = android_src/ico/icopymusic.png

android.permissions = INTERNET,WAKE_LOCK,FOREGROUND_SERVICE,MODIFY_AUDIO_SETTINGS,RECORD_AUDIO,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,POST_NOTIFICATIONS,READ_MEDIA_VIDEO,READ_MEDIA_IMAGES

android.api = 33
android.minapi = 21
android.ndk = 25b
android.ndk_api = 21
android.kivy_version = 2.3.1

android.private_storage = True
android.copy_libs = 1
android.allow_backup = True

android.entrypoint = org.koteuka404.pymusic.MediaKeyActivity
android.apptheme = @android:style/Theme.NoTitleBar
android.add_src = java_src

android.archs = arm64-v8a
p4a.bootstrap = sdl2
# materialyoucolor 3.x Android support is provided by the current p4a recipe.
p4a.branch = develop
android.gradle_options = -Xmx6144m -XX:MaxMetaspaceSize=2048m -Dfile.encoding=UTF-8 -XX:+UseG1GC

log_level = 2
warn_on_root = 1

android.env =
    SDL_AAUDIO_ENABLED=0
    SDL_AUDIODRIVER=opensl_es

android.services = audio_service:audio_service.py
