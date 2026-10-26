"""Render entrypoint. Start Command: python web/start.py"""
import logging                                   # so errors from app.py show in Render's log
import os                                        # reads Render's PORT setting
import uvicorn                                   # the web server program
from app import app                              # our FastAPI app (web/app.py)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)      # print INFO-level messages and above
    # 0.0.0.0 = accept connections from outside the machine (needed on a host). Render sets PORT; 10000 is its default.
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT') or '10000'))
