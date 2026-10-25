# Offline version — same interface as the public website

The offline app now opens the **same web interface** locally, with an additional
**Check for New Cards** updater. No online hosting or account is required.

## Start on Windows

1. Install Python 3.12+ (with pip).
2. In the extracted project directory run:
   `py -3.12 -m pip install -r web/requirements.txt`
3. Double-click `Yu-Gi-Oh Randomizer.pyw`, or run `python web/offline.py`.
4. Your browser opens a temporary `http://127.0.0.1:...` address. You can close
   the server by closing its terminal, or ending the Python process if launched via .pyw.

The generator works without internet **as long as local card data is present**.
Images already saved in `web/image_cache` work offline; uncached images and the
card updater need internet. Updating cards and banlists requires internet.

## Update local database

Click **Check for New Cards** under the generator controls. If a database or
parser update is available, click **Update Card Database** and confirm.
Otherwise you can use **Refresh Banlists**. Follow progress in Updater log.
After an update, reload the page to refresh categories and banlists.
Keep the local browser and updater open while updating.

## Differences

The public website has no updater. The local version has the exact same
generation controls, sliders, category search, preview, and PNG/YDK/TXT exports.
The original Tkinter app is retained as `Yu-Gi-Oh Randomizer (Legacy Tkinter).pyw`
for compatibility, but the shared browser UI is now the recommended offline app.
