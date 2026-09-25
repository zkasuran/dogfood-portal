"""Judging and reproducible-signing core, wired into the Django app.

The modules in this package (engine, signing, canonical, bundle, pairwise) are the
isolated, tested core. They use flat top-level imports (`import engine`), because
they are also meant to run standalone from `verify.py`. That is the core's own
interface and we do not rewrite it. To let those imports resolve when the package is
imported as `core.judging.*`, this puts the package directory on sys.path once, then
loads the modules as top-level so every caller shares one instance.

The ORM bridge is `adapter.py`. Key handling is `keys.py`. Neither belongs to the
pure core, so both may import Django.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import engine  # noqa: E402
import signing  # noqa: E402
import canonical  # noqa: E402
import bundle  # noqa: E402
import pairwise  # noqa: E402

__all__ = ["engine", "signing", "canonical", "bundle", "pairwise"]
