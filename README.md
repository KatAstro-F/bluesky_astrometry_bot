# Bluesky Astrometry Bot

This repository hosts a Bluesky bot that performs astrometry on images mentioned in posts. When the bot is mentioned in a post containing an image, it analyzes the image using nova.astrometry.net and replies with:

## Features

1. **Annotated Image**: The bot generates an annotated image highlighting detected celestial objects.
    
    <img src="https://github.com/KatAstro-F/bluesky_astrometry_bot/blob/main/ressources/test-image.jpg" alt="test image Bubble Nebula" width="25%">

    Example reply:

    <img src="https://github.com/KatAstro-F/bluesky_astrometry_bot/blob/main/ressources/12213519_annotated_full.png" alt="Annotated image" width="25%">

2. **List of Objects Found**: Provides a list of objects detected in the field.

    <img src="https://github.com/KatAstro-F/bluesky_astrometry_bot/blob/main/ressources/objects.jpg" alt="objects in field image" width="25%">

3. **Sky Maps**: Generates two sky maps at different scales.

    <img src="https://github.com/KatAstro-F/bluesky_astrometry_bot/blob/main/ressources/9532908_annotated_zoom1.jpg" alt="Sky map 1" width="25%">
    <img src="https://github.com/KatAstro-F/bluesky_astrometry_bot/blob/main/ressources/9532908_annotated_zoom2.jpg" alt="Sky map 2" width="25%">

---

## Installation

1. Clone this repository:
    ```bash
    git clone https://github.com/KatAstro-F/bluesky_astrometry_bot.git
    cd bluesky_astrometry_bot
    ```

2. Install the required Python packages:
    ```bash
    pip install -r requirements.txt
    ```
    Ensure you have Python 3.7+ and pip installed.

---

## Configuration

1. Create a `credentials.py` file at the root of the project.

2. Add the following structure to your `credentials.py` file:
    ```python
    credentials = {
        "botname": "your-bot-name",
        "BLUESKY_USERNAME": "your-bot-username.bsky.social",
        "BLUESKY_PASSWORD": "your-bluesky-password",
        "API_KEY": "your-astrometry-api-key"
    }
    ```

---

## Running the Bot

### One-Time Execution
Run the bot manually with:
```bash
python bot.py
```

### Scheduled Execution (via cron)
Use the provided `run_astrometry.sh` script to ensure the bot runs every 5 minutes and auto-restarts on crashes:
```bash
chmod +x run_astrometry.sh
./run_astrometry.sh
```
Set it up in your crontab for periodic execution:
```bash
*/5 * * * * /path/to/bluesky_astrometry_bot/run_astrometry.sh
```

---

The bot listens for mentions on Bluesky, downloads attached images, performs astrometry via nova.astrometry.net, and posts a reply with the analysis results.
