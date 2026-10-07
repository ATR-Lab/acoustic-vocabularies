"""``python -m av_analysis`` runs the command line (``av_analysis.cli``)."""

import sys

from .cli import main

sys.exit(main())
