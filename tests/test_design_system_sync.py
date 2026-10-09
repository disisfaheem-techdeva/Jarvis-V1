"""Every TechDeva surface carries the current design system.

design-system/*.css is the source of truth; sync.py copies it into the Next.js
app and inlines it into the single-file pages. This fails when someone edits a
token without re-running the sync, or edits a generated copy by hand.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_design_system_is_in_sync():
    result = subprocess.run(
        [sys.executable, str(ROOT / "design-system" / "sync.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
