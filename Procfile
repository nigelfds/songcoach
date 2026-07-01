web: gunicorn songcoach.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT --workers 2 --timeout 120
worker: python worker.py
release: python -m songcoach.db upgrade
