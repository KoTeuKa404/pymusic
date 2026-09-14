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


recipe = KivyMDRecipe()
