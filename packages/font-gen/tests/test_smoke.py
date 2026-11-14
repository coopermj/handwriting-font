import importlib.metadata
from pathlib import Path

import font_gen


def test_version_present():
    assert font_gen.__version__ == "0.1.0"


def test_distribution_maps_to_import_package():
    assert importlib.metadata.distribution("font-gen").version == "0.1.0"
    # editable hatchling installs list no top-level modules in RECORD, so packages_distributions() omits them;
    # the wheel `packages` entry is pinned by where the import package lives instead
    assert Path(font_gen.__file__).resolve().parts[-4:-1] == ("font-gen", "src", "font_gen")
