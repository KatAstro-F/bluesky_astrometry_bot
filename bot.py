import logging
import tools
from credentials import credentials
from astrometry import astrometry
from bluesky import bluesky
import time

if __name__ == "__main__":
    # Configure logger to log messages to 'bot.log'
    # Set the logging level to INFO and specify a format and date format for the logs
    LOG_FILENAME = 'bot.log'
    logging.basicConfig(filename=LOG_FILENAME,
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
    logger = logging.getLogger(__name__)

    # Log a message indicating that the bot is listening for mentions
    logger.info("🤖 Bot is listening for mentions...")

    # Create an instance of the bluesky class to handle Bluesky operations
    # Provide logger, bot name, username/password for Bluesky, and the processed notifications file
    bs = bluesky(logger, credentials["botname"], credentials["BLUESKY_USERNAME"], credentials["BLUESKY_PASSWORD"], 'processed_notifications.json')

    # Create an instance of the astrometry class for handling astrometry.net operations
    astro = astrometry(logger, credentials["API_KEY"])

    # Enter an infinite loop to continuously check for notifications
    while True:
        # Sleep for 1 second before checking again
        time.sleep(10)
        # Check for valid notifications (mentions with images)
        results = bs.Check_valid_notifications()
        # If no valid mentions, continue looping
        if results is None:
            continue
        # Extract the post_id and the image_path from the results
        post_id, image_path = results

        # If no image was downloaded, continue looping
        if not image_path:
            continue

        # Log into astrometry.net before performing astrometry on the image
        #added retry on fail, if astrometry server is down
        while (True):
            try:
                astro.login_astrometry()
                break
            except:
                #astrometry server is down, just wait
                time.sleep(120)




        try:
            # Perform astrometry on the downloaded image and get results and annotated images
            # If the astrometry server is down or times out, an exception will be raised
            results, annotated_full_path, annotated_display_path, skymap1_path, skymap2_path = astro.perform_astrometry_and_get_results(image_path)
        except Exception as e:
            # If an error occurs during astrometry, log it
            logger.error("Error performing astrometry: %s", e)
            # Reply to the user indicating that astrometry failed
            fail_message = "Astrometry failed. Please try another image."
            bs.reply_with_text_only(post_id, fail_message)
            # Continue to the next iteration of the loop
            continue

        # Generate a reply text and alt text for the images from the astrometry results
        reply_text, reply_alt_text = tools.generate_text(results)

        # Create a table image summarizing the objects and other info, and get its path
        table_image_path = tools.create_table_image(logger, results)

        # Prepare a list of images to post in the reply: annotated full, table, and two sky maps
        images_list = [
            (annotated_full_path, reply_alt_text),
            (table_image_path, "Objects and Information Table"),
            (skymap1_path, "Sky map - Zoom level 1"),
            (skymap2_path, "Sky map - Zoom level 2"),
        ]

        # Post a reply with the images and the generated text
        bs.post_reply(images_list, reply_text, post_id)
        bs.repost_original_post(post_id["parent_uri"],post_id["parent_cid"])
