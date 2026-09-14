import os
import shutil

from pythonforandroid.logger import info
from pythonforandroid.recipe import PythonRecipe


class KivyMDRecipe(PythonRecipe):
    """Build KivyMD 1.2.0 from its source distribution.

    KivyMD 1.2.0 is published on PyPI as an sdist only. Current p4a's
    Android dependency preflight asks pip for binary-only distributions,
    so the normal ``kivymd==1.2.0`` requirement is rejected before p4a can
    install it. Keeping a local recipe makes p4a fetch and install the exact
    source release directly instead of trying to resolve an Android wheel.

    Kivy and Pillow are p4a recipe dependencies and are already built for
    the Android target. KivyMD also imports MaterialYouColor at runtime and
    requires Asynckivy. Declare both explicitly because pip dependency
    resolution is disabled below: MaterialYouColor must use p4a's Android
    recipe, while Asynckivy is a pure-Python dependency.

    KivyMD loads many KV files directly from disk at import time. p4a's custom
    PythonRecipe installation does not reliably preserve the package-data files
    from this old sdist, so copy the exact data-file types declared by KivyMD
    1.2.0's setup.py into the target site-packages tree after installation.
    """

    name = "kivymd"
    version = "1.2.0"
    url = (
        "https://files.pythonhosted.org/packages/20/81/"
        "0b1154f5e581d5910702d9fadb3217f56cb186f72c8b36de0271e7ff9b5c/"
        "kivymd-{version}.tar.gz"
    )
    sha256sum = "2d33e2c59259998e93aee55acde647a4a20e5a0f962469db24ee4c9ec586962e"
    depends = ["kivy", "pillow", "materialyoucolor"]
    python_depends = ["asynckivy>=0.6,<0.7"]
    site_packages_name = "kivymd"
    setup_extra_args = ["--no-deps"]

    _package_data_suffixes = (".kv", ".ttf", ".png", ".pot", ".po")

    def build_arch(self, arch):
        super().build_arch(arch)
        self._install_package_data(arch)

    def _install_package_data(self, arch):
        source_root = os.path.join(self.get_build_dir(arch.arch), "kivymd")
        target_root = os.path.join(
            self.ctx.get_python_install_dir(arch.arch),
            "kivymd",
        )

        if not os.path.isdir(source_root):
            raise RuntimeError(
                "KivyMD source package directory was not found: " + source_root
            )

        copied = 0
        for root, _dirs, files in os.walk(source_root):
            relative_root = os.path.relpath(root, source_root)
            destination_root = (
                target_root
                if relative_root == "."
                else os.path.join(target_root, relative_root)
            )

            for filename in files:
                if not filename.lower().endswith(self._package_data_suffixes):
                    continue

                os.makedirs(destination_root, exist_ok=True)
                shutil.copy2(
                    os.path.join(root, filename),
                    os.path.join(destination_root, filename),
                )
                copied += 1

        required_label_kv = os.path.join(
            target_root,
            "uix",
            "label",
            "label.kv",
        )
        if not os.path.isfile(required_label_kv):
            raise RuntimeError(
                "KivyMD package data copy failed; missing " + required_label_kv
            )

        info("KivyMD package data installed: {} files".format(copied))


recipe = KivyMDRecipe()
