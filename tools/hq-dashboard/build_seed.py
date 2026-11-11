# Hosted seed CLI compatibility entry point.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from profile_builder import *

# Retain the operational importer's static version interlock.
RUBRIC_VERSION = 'v3.2'
if RUBRIC_VERSION != policy.RUBRIC_VERSION:
    raise ValueError('Rubric version mismatch: seed entry point')

if __name__ == "__main__":
    main()
