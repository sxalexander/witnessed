"""Fixtures shared by the test suite.

`pytester` is the fixture pytest ships for running a plugin in an isolated
session and asserting on its outcomes, and it is available only to a suite that
asks for it by name.
"""

pytest_plugins = ["pytester"]
