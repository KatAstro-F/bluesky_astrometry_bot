import os
import re
from atproto import Client
import json
from datetime import datetime
import requests

from astrobin_fetch_main_image import extract_hash, download_file, get_main_image_url

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

    def _get_field(self, obj, key, default=None):
        """
        Best-effort accessor that works across:
        - plain dicts
        - atproto_client DotDict
        - pydantic models from atproto_client
        """
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        try:
            value = obj[key]
            if value is not None:
                return value
        except Exception:
            pass
        return getattr(obj, key, default)

    def _get_first_image_fullsize(self, post_view):
        """Extract a CDN fullsize URL from the *view* embed if present (images or recordWithMedia)."""
        view_embed = self._get_field(post_view, 'embed')
        if not view_embed:
            return None

        images = self._get_field(view_embed, 'images')
        if images:
            return self._get_field(images[0], 'fullsize')

        media = self._get_field(view_embed, 'media')
        if media:
            images = self._get_field(media, 'images')
            if images:
                return self._get_field(images[0], 'fullsize')

        return None

    def _get_first_image_cid(self, images_embed):
        """
        Extract the blob ref CID/link from an *images record embed*.
        Returns "" if not available (caller can still download using alt_link).
        """
        images = getattr(images_embed, 'images', None)
        if not images:
            return ""

        first = images[0]
        blob = getattr(first, 'image', None) or self._get_field(first, 'image')
        ref = getattr(blob, 'ref', None) or self._get_field(blob, 'ref')
        link = getattr(ref, 'link', None) or self._get_field(ref, 'link') or self._get_field(ref, '$link')
        return link or ""

    def _get_quoted_uri(self, embed):
        """
        Extract the quoted record URI from embeds that support quoting:
        - app.bsky.embed.record (embed.record.uri)
        - app.bsky.embed.recordWithMedia (embed.record.record.uri)
        """
        record_part = getattr(embed, 'record', None) or self._get_field(embed, 'record')
        if not record_part:
            return None

        uri = getattr(record_part, 'uri', None) or self._get_field(record_part, 'uri')
        if uri:
            return uri

        inner = getattr(record_part, 'record', None) or self._get_field(record_part, 'record')
        return getattr(inner, 'uri', None) or self._get_field(inner, 'uri')

    def _normalize_url(self, url):
        if not url:
            return None
        url = str(url).strip()
        # Strip common punctuation around URLs in text.
        url = url.strip("()[]{}<>,.?!\"'")
        if not url:
            return None
        if url.startswith("www."):
            url = "https://" + url
        if url.startswith("app.astrobin.com/") or url.startswith("astrobin.com/"):
            url = "https://" + url
        return url

    def _extract_astrobin_urls(self, post_view, record, record_embed):
        urls = []

        text = self._get_field(record, 'text', '') or ''
        for match in re.finditer(r"(https?://[^\s]+|(?:www\.)?[^\s]+\.[^\s/]+/[^\s]+)", text, flags=re.IGNORECASE):
            candidate = self._normalize_url(match.group(0))
            if candidate:
                urls.append(candidate)

        # External card embeds often carry the URL even if the text doesn't.
        if record_embed and hasattr(record_embed, 'external') and record_embed.external:
            candidate = self._normalize_url(getattr(record_embed.external, 'uri', None))
            if candidate:
                urls.append(candidate)

        view_embed = self._get_field(post_view, 'embed')
        external_view = self._get_field(view_embed, 'external')
        if external_view:
            candidate = self._normalize_url(self._get_field(external_view, 'uri'))
            if candidate:
                urls.append(candidate)

        # Keep only AstroBin links.
        astrobin_urls = []
        for url in urls:
            if re.search(r"://(?:app\.)?astrobin\.com/", url, flags=re.IGNORECASE):
                astrobin_urls.append(url)
        return astrobin_urls

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

    def download_image(self, author_did, cid, alt_link,save_path='results/downloaded_image.jpg'):
        # Download an image from Bluesky CDN using the author's DID and CID of the image
        try:
            headers = {'User-Agent': 'YourBotName/1.0'}
            # Prefer the fully-qualified CDN URL from the AppView when available.
            # This is more reliable for quoted images (different author DID) and for various embed shapes.
            response = None
            if alt_link:
                response = requests.get(alt_link, headers=headers, timeout=30)

            if (response is None or response.status_code != 200) and author_did and cid:
                image_url = f"https://cdn.bsky.app/img/feed_fullsize/plain/{author_did}/{cid}"
                response = requests.get(image_url, headers=headers, timeout=30)

            if response is not None and response.status_code == 200:
                # Save the downloaded image locally
                with open(save_path, 'wb') as file:
                    file.write(response.content)
                self.logger.info(f"Image downloaded: {save_path}")
                return save_path
            else:
                # Log error if the download fails (non-200 status code)
                status = getattr(response, "status_code", None)
                self.logger.error(f"Failed to download image. Status code: {status} alt_link: {alt_link}")
                return None
        except Exception as e:
            # Log exception if something goes wrong during download
            self.logger.error(f"Error downloading image: {e}")
            return None

    def _download_first_image_from_post(self, post_view, *, allow_quote=True):
        """
        Try to find an image in a post (including quoted posts) and download it.
        Returns a local filepath or None.
        """
        record = self._get_field(post_view, 'record')
        if not record:
            return None

        embed = getattr(record, 'embed', None)
        if not embed:
            return None

        author = self._get_field(post_view, 'author')
        author_did = self._get_field(author, 'did')

        # Case A: post contains images directly.
        if hasattr(embed, 'images') and embed.images:
            alt_link = self._get_first_image_fullsize(post_view)
            cid = self._get_first_image_cid(embed)
            return self.download_image(author_did, cid, alt_link)

        # Case B: recordWithMedia where media is images.
        if hasattr(embed, 'media') and embed.media and hasattr(embed.media, 'images') and embed.media.images:
            alt_link = self._get_first_image_fullsize(post_view)
            cid = self._get_first_image_cid(embed.media)
            return self.download_image(author_did, cid, alt_link)

        # Case C: quoted post (with or without media).
        if allow_quote:
            quoted_uri = self._get_quoted_uri(embed)
            if quoted_uri:
                try:
                    quoted_thread = self.client.app.bsky.feed.get_post_thread({'uri': quoted_uri})
                    quoted_post = quoted_thread['thread']['post']
                    return self._download_first_image_from_post(quoted_post, allow_quote=False)
                except Exception as e:
                    self.logger.error("Error finding image in quoted post: %s", e)
                    return None

        # Case D: AstroBin link in the post (text or external-card embed).
        for astrobin_url in self._extract_astrobin_urls(post_view, record, embed):
            try:
                main_url = get_main_image_url(astrobin_url)
                if not main_url:
                    continue
                image_hash = extract_hash(astrobin_url) or "astrobin"
                out_path = os.path.join("results", f"astrobin_{image_hash}.jpg")
                download_file(main_url, out_path)
                return out_path
            except Exception as e:
                self.logger.error("Error downloading AstroBin image for %s: %s", astrobin_url, e)
                continue

        # No images found (external embeds, videos, etc.)
        return None


    def Check_valid_notifications(self):
        # Check notifications and return valid mentions with images
        notifications = self.client.app.bsky.notification.list_notifications()['notifications']
        #image_cid=None
        for notification in notifications:
            # Skip if notification already processed
            if notification['uri'] in self.processed_notifications:
                continue
                #pass

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
                mention_record = post_content  # MODIFIED (B fix): keep the original mention's record

                # Compute the reply threading references.
                #
                # Bluesky threading is determined by `reply.root` and `reply.parent`:
                # - `parent` is the post you reply to
                # - `root` is the top of the thread
                #
                # Fix: when the bot is mentioned in a *comment*, we want the bot to reply to the
                # original message (the thread root), not to the comment. Previously `parent` was
                # always set to the mention post, and `root` was only corrected in a later branch,
                # so replies could be created with the wrong `root`/`parent` and appear "lost".
                mention_uri = post["uri"]
                mention_cid = post["cid"]

                root_uri = mention_uri
                root_cid = mention_cid
                if hasattr(mention_record, "reply") and mention_record.reply:
                    reply_ref = mention_record.reply
                    if hasattr(reply_ref, "root") and reply_ref.root:
                        root_uri = reply_ref.root.uri
                        root_cid = reply_ref.root.cid

                # Check if the bot is mentioned in the post text
                if self.botname in post_text.lower():
                    self.logger.info(f"Bot was tagged in a post: {post_text}")

                    # Reply target selection:
                    # - If the mention post itself provides the image (direct/quote/AstroBin), reply to the mention post.
                    # - If the mention post has no image and is a comment, fall back to the parent post's image
                    #   and reply to the original message (thread root).

                    downloaded_image_path = self._download_first_image_from_post(post, allow_quote=True)
                    if downloaded_image_path:
                        post_id = {"root_uri": root_uri, "root_cid": root_cid, "parent_uri": mention_uri, "parent_cid": mention_cid}
                        return post_id, downloaded_image_path

                    # No image found in mention post: if this is a comment, try parent post.
                    try:
                        parent = post_thread['thread']['parent']
                        parent_post = self._get_field(parent, 'post')
                    except Exception:
                        parent_post = None

                    if parent_post:
                        downloaded_image_path = self._download_first_image_from_post(parent_post, allow_quote=True)
                        post_id = {"root_uri": root_uri, "root_cid": root_cid, "parent_uri": root_uri, "parent_cid": root_cid}
                        return post_id, downloaded_image_path

                    # Otherwise: no image anywhere; reply to the mention post (so the user sees the failure reply).
                    post_id = {"root_uri": root_uri, "root_cid": root_cid, "parent_uri": mention_uri, "parent_cid": mention_cid}
                    return post_id, None
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

        facets = self.add_mention_facets(post_text)

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

        if facets:
            record["facets"] = facets

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


    def add_mention_facets(self,post_text,mention_str="@quantumkat.bsky.social",mention_did="did:plc:bqvcty4gfx5s2b4gvlff6ikp"):
        """
        Returns a 'facets' list if the mention_str is found in post_text.
        mention_str should include '@' (e.g. '@quantumkat.bsky.social').
        """
        start_index = post_text.find(mention_str)
        if start_index == -1:
            return None  # Not found, so no facets to return

        end_index = start_index + len(mention_str)

        # Build the facets structure for a mention
        facets = [{
            "index": {
                "byteStart": start_index,
                "byteEnd": end_index
            },
            "features": [{
                "$type": "app.bsky.richtext.facet#mention",
                "did": mention_did
            }]
        }]

        return facets


    def repost_original_post(self, uri, cid):
        """
        Repost the original post on the bot feed.
        - uri: The unique at:// URI of the post to repost
        - cid: The content ID (CID) of the post
        """
        # Construct a record of type 'app.bsky.feed.repost'
        repost_record = {
            "$type": "app.bsky.feed.repost",
            "subject": {
                "uri": uri,
                "cid": cid
            },
            # 'createdAt' is required for repost
            "createdAt": datetime.utcnow().isoformat() + 'Z'
        }

        try:
            # Use the create_record API to create a repost
            self.client.com.atproto.repo.create_record({
                'repo': self.client.me.did,
                'collection': 'app.bsky.feed.repost',
                'record': repost_record
            })
            self.logger.info(f"Reposted the post with URI: {uri}")
        except Exception as e:
            self.logger.error("Error creating repost: %s", e)
            self.logger.error("Record being sent: %s", repost_record)
