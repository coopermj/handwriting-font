def test_package_imports_and_has_version():
    import hwfont_schema

    assert hwfont_schema.__version__ == "0.1.0"
