# Hosting the randomizer

## Fix the Render port error

In your Render Web Service settings, use:
- **Build Command:** `pip install -r web/requirements.txt`
- **Start Command:** `python web/start.py`
- **Root Directory:** leave empty (repository root)

The launcher uses Render's `PORT` environment variable, defaulting to 10000 when missing. `render.yaml` provides the same configuration if deploying with a Render Blueprint.

## Card images

The website now **downloads each small card image to `web/image_cache/` once** and serves it from disk on later requests, with a 0.5-second minimum interval between new downloads. The cache directory can be changed using the `IMAGE_CACHE_DIR` environment variable. Images are deliberately *not* bundled into the source zip.

**Important for public hosting:** Render free services do **not** preserve local files across deployments/restarts. An on-disk cache on a free instance is therefore *not* permanent and may download the same images again after a restart. Before opening this to substantial public traffic, use persistent disk storage (if your plan supports it) with `IMAGE_CACHE_DIR` set to its mount path, or a durable object-storage/image-hosting solution, so the image provider is not repeatedly queried. YGOPRODeck asks apps to download and re-host images instead of repeatedly hotlinking them: https://api.ygoprodeck.com/api-guide/

## Local development

From the extracted project directory, use `python -m pip install -r web/requirements.txt` and `python web/app.py`, then visit http://127.0.0.1:8000.
