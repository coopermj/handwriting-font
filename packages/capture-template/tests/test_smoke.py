def test_package_imports_and_has_version():
    import capture_template

    assert capture_template.__version__ == "0.1.0"
