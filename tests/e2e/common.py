"""Works that the e2e tests open. The defaults come from tests/fixtures/make_demo.py."""
import os

WORK = os.environ.get("SP_WORK", "Demo-Symphony")            # strings only, 4 movements
SOLO_WORK = os.environ.get("SP_SOLO_WORK", "Demo-Concerto")  # piano and strings, with long string rests
