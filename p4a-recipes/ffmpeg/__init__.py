from multiprocessing import cpu_count
from os.path import exists, join, realpath

import sh

from pythonforandroid.toolchain import Recipe, current_directory, shprint


class FFMpegRecipe(Recipe):
    """FFmpeg recipe compatible with ffpyplayer 4.5.1.

    ffpyplayer 4.5.1 still uses the legacy libavcodec/avfft.h API. FFmpeg 8
    removed that API, so keep FFmpeg pinned to the last p4a-supported line
    known to work with ffpyplayer while retaining the current Android build
    flags.
    """

    version = "6.1.2"
    url = "https://www.ffmpeg.org/releases/ffmpeg-{version}.tar.xz"
    depends = ["sdl2"]
    opts_depends = ["openssl", "ffpyplayer_codecs", "av_codecs"]

    _libs = [
        "libavcodec.so",
        "libavfilter.so",
        "libavutil.so",
        "libswscale.so",
        "libavdevice.so",
        "libavformat.so",
        "libswresample.so",
        "libpostproc.so",
        "libffmpegbin.so",
    ]
    built_libraries = dict.fromkeys(_libs, "./lib")

    def should_build(self, arch):
        build_dir = self.get_build_dir(arch.arch)
        return not exists(join(build_dir, "lib", "libavcodec.so"))

    def get_recipe_env(self, arch):
        env = super().get_recipe_env(arch)
        env["NDK"] = self.ctx.ndk_dir
        return env

    @staticmethod
    def _patch_configure(build_dir):
        """Apply the two p4a cross-compilation fixes used for FFmpeg 6.1.2.

        Validate every replacement so a changed source archive fails loudly
        instead of silently producing a misconfigured FFmpeg build.
        """
        configure_path = join(build_dir, "configure")
        with open(configure_path, "r", encoding="utf-8") as handle:
            text = handle.read()

        replacements = (
            (
                "enabled libshine          && require_pkg_config libshine shine shine/layer3.h shine_encode_buffer",
                "enabled libshine          && require \"shine\" shine/layer3.h shine_encode_buffer -lshine -lm",
            ),
            (
                "enabled libx264           && require_pkg_config libx264 x264 \"stdint.h x264.h\" x264_encoder_encode &&",
                "enabled libx264           && require \"x264\" \"stdint.h x264.h\" x264_encoder_encode &&",
            ),
        )

        changed = False
        for old, new in replacements:
            if new in text:
                continue
            if old not in text:
                raise RuntimeError(
                    "FFmpeg 6.1.2 configure layout changed; refusing to apply "
                    "an unverified cross-compilation patch."
                )
            text = text.replace(old, new, 1)
            changed = True

        if changed:
            with open(configure_path, "w", encoding="utf-8") as handle:
                handle.write(text)

    def build_arch(self, arch):
        build_dir = self.get_build_dir(arch.arch)
        self._patch_configure(build_dir)

        with current_directory(build_dir):
            env = arch.get_env()
            flags = ["--enable-jni", "--enable-mediacodec"]
            cflags = []
            ldflags = []

            if "openssl" in self.ctx.recipe_build_order:
                flags += [
                    "--enable-version3",
                    "--enable-openssl",
                    "--enable-nonfree",
                    "--enable-protocol=https,tls_openssl",
                ]
                openssl_dir = Recipe.get_recipe("openssl", self.ctx).get_build_dir(
                    arch.arch
                )
                cflags += ["-I" + openssl_dir + "/include/"]
                ldflags += ["-L" + openssl_dir, "-lssl", "-lcrypto"]

            codecs_opts = {"ffpyplayer_codecs", "av_codecs"}
            if codecs_opts.intersection(self.ctx.recipe_build_order):
                flags += ["--enable-gpl"]

                x264_dir = Recipe.get_recipe("libx264", self.ctx).get_build_dir(
                    arch.arch
                )
                flags += ["--enable-libx264"]
                cflags += ["-I" + x264_dir + "/include/"]
                ldflags += [x264_dir + "/lib/libx264.a"]

                shine_dir = Recipe.get_recipe("libshine", self.ctx).get_build_dir(
                    arch.arch
                )
                flags += ["--enable-libshine"]
                cflags += ["-I" + shine_dir + "/include/"]
                ldflags += ["-lshine", "-L" + shine_dir + "/lib/", "-lm"]

                vpx_dir = Recipe.get_recipe("libvpx", self.ctx).get_build_dir(arch.arch)
                flags += ["--enable-libvpx"]
                cflags += ["-I" + vpx_dir + "/include/"]
                ldflags += ["-lvpx", "-L" + vpx_dir + "/lib/"]

                flags += [
                    "--enable-parsers",
                    "--enable-decoders",
                    "--enable-encoders",
                    "--enable-muxers",
                    "--enable-demuxers",
                ]
            else:
                flags += [
                    "--enable-parser=aac,ac3,h261,h264,mpegaudio,mpeg4video,mpegvideo,vc1",
                    "--enable-decoder=aac,h264,mpeg4,mpegvideo",
                    "--enable-muxer=h264,mov,mp4,mpeg2video",
                    "--enable-demuxer=aac,h264,m4v,mov,mpegvideo,vc1,rtsp",
                ]

            flags += [
                "--disable-symver",
                "--disable-doc",
                "--enable-filter=aresample,resample,crop,adelay,volume,scale",
                "--enable-protocol=file,http,hls,udp,tcp",
                "--enable-small",
                "--enable-hwaccels",
                "--enable-pic",
                "--disable-static",
                "--disable-debug",
                "--enable-shared",
            ]

            if "arm64" in arch.arch:
                arch_flag = "aarch64"
            elif "x86" in arch.arch:
                arch_flag = "x86"
                flags += ["--disable-asm"]
            else:
                arch_flag = "arm"

            flags += [
                "--target-os=android",
                "--enable-cross-compile",
                "--cross-prefix={}-".format(arch.target),
                "--arch={}".format(arch_flag),
                "--strip={}".format(self.ctx.ndk.llvm_strip),
                "--nm={}".format(self.ctx.ndk.llvm_nm),
                "--sysroot={}".format(self.ctx.ndk.sysroot),
                "--enable-neon",
                "--prefix={}".format(realpath(".")),
            ]

            if arch_flag == "arm":
                cflags += [
                    "-Wno-error=incompatible-pointer-types",
                    "-mfpu=vfpv3-d16",
                    "-mfloat-abi=softfp",
                    "-fPIC",
                ]

            env["CFLAGS"] += " " + " ".join(cflags)
            env["LDFLAGS"] += " " + " ".join(ldflags)

            configure = sh.Command("./configure")
            shprint(configure, *flags, _env=env)
            shprint(sh.make, "-j", str(cpu_count()), _env=env)
            shprint(sh.make, "install", _env=env)
            shprint(sh.cp, "ffmpeg", "./lib/libffmpegbin.so")


recipe = FFMpegRecipe()
