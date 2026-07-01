# Crayfish Detection on Raspberry Pi

This project captures video from the Pi camera, sends frames to Roboflow Inference, and draws bounding boxes for the `mraj-amoso/crayfish-detection-ae3qo` Universe model.

## What was set up

- Local Python 3.12 virtual environment at `.venv312`
- Installed dependencies for camera capture, inference, visualization, and serial output
- A ready-to-run detection app in `app.py`

## Run it

1. Copy `.env.example` to `.env` and set `ROBOFLOW_API_KEY`.
2. Activate the environment:

```bash
source .venv312/bin/activate
```

3. Start the app:

```bash
python app.py
```

Or run the helper script:

```bash
./run.sh
```

## Notes

- The Roboflow client package is `inference`, which provides `inference_sdk`.
- The default model id is `mraj-amoso/crayfish-detection-ae3qo/1`. If the Universe page uses a different version number, update `ROBOFLOW_MODEL_ID`.
- Press `q` or `Esc` to exit.