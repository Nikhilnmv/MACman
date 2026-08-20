"""MACman — call or text your Mac, tell it what you need, watch it work.

The version lives here and nowhere else.

It has to be readable by three things that cannot share a parser: setuptools
(via `dynamic = ["version"]` in pyproject.toml), the build script (a shell
`grep`), and the daemon itself at runtime. A plain module constant is the only
form all three can read, which is why it is not in pyproject.toml — and why
`importlib.metadata` is not used: MACman ships as copied source inside the app
bundle, not as an installed distribution, so there is no metadata to read.

Two files claimed this number independently before, with nothing keeping them
in step. `app/build.sh` now stamps `Info.plist` from here at build time.
"""

__version__ = "0.1.0"
