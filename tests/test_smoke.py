import itclens


def test_version_is_set() -> None:
    assert isinstance(itclens.__version__, str)
    assert itclens.__version__
