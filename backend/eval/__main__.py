"""Allow `python -m backend.eval ...` invocation."""
import sys
from .cli import main

sys.exit(main())
