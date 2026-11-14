"""`python -m font_gen ...`: the same CLI as the `font-gen` script."""
from __future__ import annotations

from font_gen.run import main

if __name__ == "__main__":
    raise SystemExit(main())
