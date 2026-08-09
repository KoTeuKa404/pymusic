from os.path import join

from pythonforandroid.recipe import PyProjectRecipe, Recipe


class FFPyPlayerRecipe(PyProjectRecipe):
    # ffpyplayer 4.5.3 switched its build to Cython 3, which is required for
    # current CPython. Its stock Cython 3.0.x pin predates Python 3.14, so the
    # local pyproject patch below raises that to a Python-3.14-compatible Cython.
    version = "v4.5.3"
    url = "https://github.com/matham/ffpyplayer/archive/{version}.zip"
    depends = ["python3", "sdl2", "ffmpeg"]
    patches = ["setup.py.patch", "pyproject-cython.patch"]
    opt_depends = ["openssl", "ffpyplayer_codecs"]

    def get_recipe_env(self, arch, with_flags_in_cc=True):
        env = super().get_recipe_env(arch)

        build_dir = Recipe.get_recipe("ffmpeg", self.ctx).get_build_dir(arch.arch)
        env["FFMPEG_INCLUDE_DIR"] = join(build_dir, "include")
        env["FFMPEG_LIB_DIR"] = join(build_dir, "lib")

        env["SDL_INCLUDE_DIR"] = join(
            self.ctx.bootstrap.build_dir, "jni", "SDL", "include"
        )
        env["SDL_LIB_DIR"] = join(
            self.ctx.bootstrap.build_dir, "libs", arch.arch
        )

        env["USE_SDL2_MIXER"] = "1"

        sdl2_mixer_recipe = self.get_recipe("sdl2_mixer", self.ctx)
        env["SDL2_MIXER_INCLUDE_DIR"] = sdl2_mixer_recipe.get_include_dirs(arch)[0]

        # NDKPLATFORM and LIBLINK are ffpyplayer's Android detection switches.
        env["NDKPLATFORM"] = "NOTNONE"
        env["LIBLINK"] = "NOTNONE"

        # Keep postproc disabled in the ffpyplayer wrapper. This avoids relying
        # on that legacy API and remains compatible with both the local FFmpeg
        # 6.1.2 recipe and newer p4a FFmpeg recipes.
        env["CONFIG_POSTPROC"] = "0"

        return env


recipe = FFPyPlayerRecipe()
