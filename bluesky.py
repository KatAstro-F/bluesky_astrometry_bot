import os
from atproto import Client
import json
from datetime import datetime
import requests

class bluesky():

    def __init__(self,logger,botname,username,password,PROCESSED_NOTIFICATIONS_FILE):
        # Initialize the bluesky class with the given parameters
        # logger: logger object for logging info and errors
        # botname: the bot's username mention (e.g. '@kat-astro-bot')
        # username, password: credentials for logging into Bluesky
        # PROCESSED_NOTIFICATIONS_FILE: file to track processed notifications
        self.client = Client()  # Create an instance of the Bluesky client
        self.botname=botname    # Store the bot name to check mentions
        self.client.login(username, password)  # Log in to the Bluesky client
        self.PROCESSED_NOTIFICATIONS_FILE = 'processed_notifications.json'  # Set the notifications file
        self.processed_notifications = self.load_processed_notifications()  # Load processed notifications
        self.logger=logger  # Store the logger

    def load_processed_notifications(self):
        # Load the set of processed notifications from the JSON file
        if os.path.exists(self.PROCESSED_NOTIFICATIONS_FILE):
            with open(self.PROCESSED_NOTIFICATIONS_FILE, 'r') as f:
                return set(json.load(f))
        return set()

    def save_processed_notifications(self):
        # Save the set of processed notifications to the JSON file
        with open(self.PROCESSED_NOTIFICATIONS_FILE, 'w') as f:
            json.dump(list(self.processed_notifications), f)


    def reply_with_text_only(self, post_id, text):
        # Reply to a post with text only (no images)
        record = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.utcnow().isoformat() + 'Z',
            "reply": {
                "root": {
                    "uri": post_id["root_uri"],
                    "cid": post_id["root_cid"]
                },
                "parent": {
                    "uri": post_id["parent_uri"],
                    "cid": post_id["parent_uri"]
                }
            }
        }

        # Attempt to create a text-only reply post
        try:
            self.client.com.atproto.repo.create_record({
                'repo': self.client.me.did,
                'collection': 'app.bsky.feed.post',
                'record': record
            })
            self.logger.info("Replied with astrometry failed message.")
        except Exception as e:
            # Log errors if the post could not be created
            self.logger.error("Error creating text-only post: %s", e)
            self.logger.error("Record being sent: %s", record)

    def upload_and_create_image_blob(self,image_path):
        # Upload an image to Bluesky and create a blob reference
        with open(image_path, 'rb') as f:
            image_data = f.read()
        image_blob = self.client.upload_blob(image_data)
        self.logger.info("Uploaded image blob: %s", image_blob)

        # Construct the blob reference dictionary for embedding
        image_blob_ref = {
            '$type': 'blob',
            'ref': {
                '$link': image_blob.blob.ref.link
            },
            'mimeType': image_blob.blob.mime_type,
            'size': image_blob.blob.size
        }
        return image_blob_ref

    def download_image(self, author_did, cid, save_path='results/downloaded_image.jpg'):
        # Download an image from Bluesky CDN using the author's DID and CID of the image
        try:
            image_url = f"https://cdn.bsky.app/img/feed_fullsize/plain/{author_did}/{cid}"
            headers = {'User-Agent': 'YourBotName/1.0'}
            response = requests.get(image_url, headers=headers)
            if response.status_code == 200:
                # Save the downloaded image locally
                with open(save_path, 'wb') as file:
                    file.write(response.content)
                self.logger.info(f"Image downloaded from {image_url}: {save_path}")
                return save_path
            else:
                # Log error if the download fails (non-200 status code)
                self.logger.error(f"Failed to download image. Status code: {response.status_code} URL: {image_url}")
                return None
        except Exception as e:
            # Log exception if something goes wrong during download
            self.logger.error(f"Error downloading image: {e}")
            return None


    def Check_valid_notifications(self):
        # Check notifications and return valid mentions with images
        notifications = self.client.app.bsky.notification.list_notifications()['notifications']
        for notification in notifications:
            # Skip if notification already processed
            if notification['uri'] in self.processed_notifications:
                continue


            # Mark notification as processed
            self.processed_notifications.add(notification['uri'])
            self.save_processed_notifications()

            # Check if the notification is a mention
            if notification['reason'] == 'mention':
                # Get the full post thread of the mention
                post_thread = self.client.app.bsky.feed.get_post_thread({'uri': notification['uri']})
                post = post_thread['thread']['post']
                post_content = post['record']
                post_text = post_content.text

                # Check if the bot is mentioned in the post text
                if self.botname in post_text.lower():
                    self.logger.info(f"Bot was tagged in a post: {post_text}")

                    if not (hasattr(post_content, 'embed') and post_content.embed):
                        try:
                            #check if the post is a comment from a parent post
                            if post_thread['thread']['parent'] is None :
                                return None
                            #check if the comment author is the  original post author to avoid spam
                            if post["author"]["handle"]==post_thread['thread']['parent']['post']["author"]["handle"]:
                                post = post_thread['thread']['parent']['post']
                                post_content = post['record']
                            else:
                                return None
                        except Exception as e:
                            # Log errors if unable to create the post
                            self.logger.error("Error finding parent post: %s", e)
                            return None

                    # Check if there is an embed with images
                    if hasattr(post_content, 'embed') and post_content.embed:
                        
                        embed = post_content.embed
                        #if not image in the post try to get the image in the quoted post
                        if not(hasattr(embed, 'images') and embed.images):
                            try:
                                quoted_thread=self.client.app.bsky.feed.get_post_thread({'uri':embed["record"]["uri"] })
                                embed=quoted_thread['thread']['post']['record'].embed
                            except Exception as e:
                                # Log errors if unable to create the post
                                self.logger.error("Error finding image in quoted post: %s", e)
                                return None

                        if hasattr(embed, 'images') and embed.images:
                            images = embed.images
                            if images:
                                # Get the CID of the first image
                                image_cid = images[0].image.ref.link
                                # Get the author's DID
                                author_did = post['author']['did']
                                # Download the image
                                downloaded_image_path = self.download_image(author_did, image_cid)

                                # Get root and parent URIs and CIDs for reply
                                root_uri = post['uri']
                                root_cid = post['cid']

                                if 'parent' in post_thread['thread'] and post_thread['thread']['parent']:
                                    parent_post = post_thread['thread']['parent']['post']
                                    parent_uri = parent_post['uri']
                                    parent_cid = parent_post['cid']
                                else:
                                    parent_uri = root_uri
                                    parent_cid = root_cid

                                # Construct a dictionary with post IDs for replying
                                post_id = { "root_uri" : root_uri, "root_cid" : root_cid, "parent_uri":parent_uri,"parent_cid":parent_cid}
                                return post_id, downloaded_image_path
        return None


    def post_reply(self,images_list,post_text,post_id):
        # Post a reply with given images and text
        # images_list should be a list of tuples (image_path, alt_text)
        image_embeds = []
        for image in images_list:
            if image[0] and os.path.exists(image[0]):
                image_blob_ref = self.upload_and_create_image_blob(image[0])
                image_embeds.append({
                    "image": image_blob_ref,
                    "alt": image[1]
                })

        # Construct the record for embedding images if available
        if image_embeds:
            record = {
                "$type": "app.bsky.feed.post",
                "text": post_text,
                "createdAt": datetime.utcnow().isoformat() + 'Z',
                "embed": {
                    "$type": "app.bsky.embed.images",
                    "images": image_embeds
                },
                "reply": {
                    "root": {
                        "uri": post_id["root_uri"],
                        "cid": post_id["root_cid"]
                    },
                    "parent": {
                        "uri": post_id["parent_uri"],
                        "cid": post_id["parent_cid"]
                    }
                }
            }
        else:
            # If no images, just post text
            record = {
                "$type": "app.bsky.feed.post",
                "text": post_text,
                "createdAt": datetime.utcnow().isoformat() + 'Z',
                "reply": {
                    "root": {
                        "uri": post_id["root_uri"],
                        "cid": post_id["root_cid"]
                    },
                    "parent": {
                        "uri": post_id["parent_uri"],
                        "cid": post_id["parent_cid"]
                    }
                }
            }

        # Try to create the reply post
        try:
            self.client.com.atproto.repo.create_record({
                'repo': self.client.me.did,
                'collection': 'app.bsky.feed.post',
                'record': record
            })
            self.logger.info("Replied to the post with astrometry data, annotated images, and table image")
        except Exception as e:
            # Log errors if unable to create the post
            self.logger.error("Error creating post: %s", e)
            self.logger.error("Record being sent: %s", record)
