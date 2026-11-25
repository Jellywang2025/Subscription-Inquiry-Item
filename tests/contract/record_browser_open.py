"""Browser substitute for recording the URL received from the Windows launcher."""

import os
import sys
from pathlib import Path


if __name__ == "__main__":
    Path(os.environ["QUOTA_CONTRACT_BROWSER_LOG"]).write_text(
        sys.argv[1] + "\n", encoding="utf-8"
    )
