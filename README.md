# Family backend deploy mirror for Render (no secrets).

## Local tests (no real database)
```
pip install -r requirements.txt mongomock-motor pytest requests
python tests/run_mock_server.py . 8099 &
EXPO_BACKEND_URL=http://127.0.0.1:8099 pytest -q tests
```
