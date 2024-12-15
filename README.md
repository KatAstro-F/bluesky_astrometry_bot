Bluesky Astrometry Bot

This repository hosts a Bluesky bot that performs astrometry on images mentioned in posts. When the bot is mentioned in a post containing an image, it analyzes the image using nova.astrometry.net and replies with:

    An annotated image highlighting detected celestial objects
    A list of objects found in the field
    Two sky maps at different scales

Installation

    Clone this repository.
    Install the required Python packages. Based on the previously provided source code, the following should suffice:

    pip install requests pillow atproto

    (Ensure you have pip and Python 3.7+ installed.)

Configuration

You need to create a credentials.py file at the root of the project. This file should contain a dictionary with your bot’s credentials and API key. For example:

credentials = {
    "botname": "your-bot-name",
    "BLUESKY_USERNAME": "your-bot-username.bsky.social",
    "BLUESKY_PASSWORD": "your-bluesky-password",
    "API_KEY": "your-astrometry-api-key"
}

Important: Do not commit this credentials.py file to the repository, as it contains sensitive information. The .gitignore is configured to exclude it by default.
Usage

    Run the bot:

    python main.py

    The bot will listen for mentions on Bluesky, download attached images, run astrometry via nova.astrometry.net, and post a reply with the results.

Notes

    Ensure that credentials.py and .gitignore are properly set up before running the bot.
    The results directory will store any intermediate or downloaded images. If you want to ensure this directory is tracked even when empty, you may place a .gitkeep file inside it.
