#!/usr/bin/env bash
# Run from isolated nonproduction checkout on Pi after owner authorizes access.
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/pi_preflight.sh
python3 -m pytest -q
python3 - <<'PY'
from fastapi.testclient import TestClient
from receptionist.server import create_app, Settings
import tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
    app=create_app(Settings(db_path=str(Path(d)/'test.sqlite'),dev_allow_unsigned=True))
    res=TestClient(app).get('/healthz')
    assert res.status_code == 200, res.text
    assert res.json()['status'] == 'ok'
    print('PI4_PRIVATE_HEALTH_OK (in-process, not remotely witnessed)')
PY
echo "PI4_SMOKE_COMPLETE: manually verify SSH tunnel from BOTH laptops"
