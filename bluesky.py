import os
import re
import time
from urllib.parse import urljoin, urlparse
from io import BytesIO
from PIL import Image
from atproto import Client
import json
from datetime import datetime
import requests

try:
    # Optional: used only for high-res AstroBin downloads.
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
except ImportError:  # pragma: no cover - optional dependency
    webdriver = None
    ChromeOptions = None

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
            image_url = f"https://cdn.bsky.app/img/feed_fullsize/plain/{author_did}/{cid}"
            headers = {'User-Agent': 'YourBotName/1.0'}
            response = requests.get(image_url, headers=headers)
            if not response.status_code == 200:
                #some link are indirect try alt link in case of failure
                response = requests.get(alt_link, headers=headers)
            if  response.status_code == 200:
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

    def _extract_astrobin_url(self, text):
        """
        Extract the first AstroBin URL from the given text.
        Supports URLs with or without an explicit scheme.
        """
        if not text:
            return None

        # Match e.g. "https://www.astrobin.com/abcd12/0/",
        # "astrobin.com/abcd12", "https://app.astrobin.com/i/edt08c", etc.
        pattern = r'((?:https?://)?(?:[\w.-]+\.)?astrobin\.com/[^\s]+)'
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None

        url = match.group(1)
        # Strip common trailing punctuation
        url = url.rstrip(').,;\'"')
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def _extract_astrobin_url_from_embed(self, embed):
        """
        Try to extract an AstroBin URL from a Bluesky external-card embed
        (app.bsky.embed.external), where the AstroBin link lives in
        something like embed.external.uri.
        """
        if not embed:
            return None

        external = None
        try:
            if hasattr(embed, "external"):
                external = embed.external
        except Exception:
            external = None

        if external is None:
            try:
                if isinstance(embed, dict):
                    external = embed.get("external")
            except Exception:
                external = None

        if not external:
            return None

        uri = None
        try:
            if hasattr(external, "uri"):
                uri = external.uri
        except Exception:
            uri = None

        if uri is None:
            try:
                if isinstance(external, dict):
                    uri = external.get("uri")
            except Exception:
                uri = None

        if not uri:
            return None

        return self._extract_astrobin_url(str(uri))

    def _find_astrobin_in_mention_and_quoted(self, post, post_content):
        """
        Given a PostView (`post`) and its record (`post_content`), attempt to
        find an AstroBin URL in:
          1) The mention post's text.
          2) Any external-card embed on the mention.
          3) The quoted post's text.
          4) Any external-card embed on the quoted post.
        Returns a local image path if a download succeeds, otherwise None.
        """
        # 1) Mention text
        post_text = getattr(post_content, "text", "") or ""
        astrobin_url = self._extract_astrobin_url(post_text)

        # 2) Mention external-card embeds (record-level and view-level)
        if not astrobin_url:
            embed_obj = getattr(post_content, "embed", None)
            astrobin_url = self._extract_astrobin_url_from_embed(embed_obj)

        if not astrobin_url and hasattr(post, "embed") and post.embed:
            astrobin_url = self._extract_astrobin_url_from_embed(post.embed)

        # 3) Quoted post: try to get the quoted record from the PostView's
        #    embed view first (already-resolved), then fall back to using the
        #    strongRef in the record embed and get_post_thread.
        quoted_record = None

        # 3a) Prefer the view-level embed (app.bsky.embed.record#view).
        try:
            if hasattr(post, "embed") and post.embed and hasattr(post.embed, "record"):
                view_record = post.embed.record
                if hasattr(view_record, "value"):
                    quoted_record = view_record.value
        except Exception:
            quoted_record = None

        # 3b) Fallback via strongRef (record-level embed).
        if quoted_record is None:
            embed_ref = getattr(post_content, "embed", None)
            quoted_uri = None
            try:
                if hasattr(embed_ref, "record") and hasattr(embed_ref.record, "uri"):
                    quoted_uri = embed_ref.record.uri
                elif isinstance(embed_ref, dict) and "record" in embed_ref and isinstance(embed_ref["record"], dict):
                    quoted_uri = embed_ref["record"].get("uri")
            except Exception:
                quoted_uri = None

            if quoted_uri:
                try:
                    quoted_thread = self.client.app.bsky.feed.get_post_thread({'uri': quoted_uri})
                    quoted_post = quoted_thread['thread']['post']
                    quoted_record = quoted_post['record']
                except Exception as e:
                    self.logger.error("Error fetching quoted post for AstroBin link: %s", e)
                    quoted_record = None

        # 3c) Search the quoted Record, if any.
        if not astrobin_url and quoted_record is not None:
            quoted_text = getattr(quoted_record, "text", "") or ""
            astrobin_url = self._extract_astrobin_url(quoted_text)

            if not astrobin_url and hasattr(quoted_record, "embed") and quoted_record.embed:
                astrobin_url = self._extract_astrobin_url_from_embed(quoted_record.embed)

        if not astrobin_url:
            return None

        # Try to download from AstroBin.
        downloaded_image_path = self.download_astrobin_image(astrobin_url)
        return downloaded_image_path

    def _extract_astrobin_hash(self, astrobin_url):
        """
        Extract the AstroBin image hash from a URL such as:
          - https://app.astrobin.com/i/edt08c
          - https://www.astrobin.com/edt08c/
          - https://astrobin.com/edt08c/0/
        """
        try:
            parsed = urlparse(astrobin_url)
            parts = [p for p in parsed.path.split("/") if p]
            if not parts:
                return None
            # Handle app-style URLs: /i/<hash>
            if parts[0] == "i" and len(parts) >= 2:
                return parts[1]
            # Canonical web URLs: /<hash>/ or /<hash>/<version>/
            return parts[0]
        except Exception:
            return None

    def _download_astrobin_original(self, astrobin_url, headers, save_path):
        """
        Try AstroBin's public download mechanism to retrieve the original image
        without using the API. This uses the canonical URL:
          https://www.astrobin.com/<hash>/?download=1
        and follows redirects until it reaches an image response.
        """
        astro_hash = self._extract_astrobin_hash(astrobin_url)
        if not astro_hash:
            return None

        download_url = f"https://www.astrobin.com/{astro_hash}/?download=1"
        try:
            resp = requests.get(download_url, headers=headers, timeout=60, stream=True, allow_redirects=True)
            ct = resp.headers.get("Content-Type", "")
            if resp.status_code == 200 and ct.startswith("image/"):
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                try:
                    img = Image.open(BytesIO(resp.content))
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    img.save(save_path, "JPEG", quality=90)
                except Exception as e:
                    self.logger.error(f"Failed to convert AstroBin original image to JPEG: {e}")
                    return None

                self.logger.info(f"Downloaded AstroBin original image from {resp.url} to {save_path}")
                return save_path
            return None
        except Exception as e:
            self.logger.warning(f"Error using AstroBin download URL {download_url}: {e}")
            return None

    def _download_astrobin_via_selenium(self, astrobin_url, headers, save_path):
        """
        Use a real browser (Selenium + Chrome) to load the AstroBin page,
        inspect all network resources, and pick the largest AstroBin image.

        This mimics what your browser does when it loads the viewer, even if
        the image itself is exposed via a blob: URL in the DOM.
        """
        if webdriver is None or ChromeOptions is None:
            self.logger.warning("Selenium is not installed; cannot use browser-based AstroBin download.")
            return None

        try:
            options = ChromeOptions()
            # Headless to avoid opening a visible window.
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            ua = headers.get("User-Agent", "Mozilla/5.0")
            options.add_argument(f"--user-agent={ua}")

            driver = webdriver.Chrome(options=options)
        except Exception as e:  # pragma: no cover - depends on local driver setup
            self.logger.warning(f"Could not start Selenium Chrome driver: {e}")
            return None

        try:
            driver.set_window_size(1920, 1080)
            driver.get(astrobin_url)

            # Give the page some time to load JS and images.
            time.sleep(8)

            try:
                urls = driver.execute_script(
                    "return (window.performance && performance.getEntriesByType) ? "
                    "performance.getEntriesByType('resource').map(e => e.name) : [];"
                )
            except Exception as e:
                self.logger.warning(f"Could not read performance entries via Selenium: {e}")
                urls = []

            if not urls:
                return None

            # Filter to candidate image URLs from AstroBin/CDN.
            candidates = []
            for url in urls:
                if not isinstance(url, str):
                    continue
                lower = url.lower()
                if "astrobin.com" not in lower:
                    continue
                if not any(ext in lower for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                    continue
                if any(bad in lower for bad in ["avatar", "favicon", "logo", "apple-touch-icon"]):
                    continue
                candidates.append(url)

            if not candidates:
                self.logger.info("Selenium did not observe any suitable AstroBin image URLs.")
                return None

            best_img = None
            best_pixels = -1

            for url in candidates:
                try:
                    resp = requests.get(url, headers=headers, timeout=60)
                    if resp.status_code != 200:
                        continue
                    img = Image.open(BytesIO(resp.content))
                    w, h = img.size
                    pixels = w * h
                    if pixels > best_pixels:
                        best_pixels = pixels
                        best_img = img
                except Exception:
                    continue

            if best_img is None:
                return None

            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            if best_img.mode != "RGB":
                best_img = best_img.convert("RGB")
            best_img.save(save_path, "JPEG", quality=90)
            self.logger.info(
                f"Downloaded AstroBin image via Selenium from {astrobin_url} to {save_path} "
                f"({best_pixels} pixels)"
            )
            return save_path
        finally:
            driver.quit()

    def download_astrobin_image(self, astrobin_url, save_path='results/downloaded_astrobin.jpg'):
        """
        Resolve an AstroBin page URL to its underlying image and download it.
        Prefer the highest-resolution image available:
          1) Try AstroBin's public download URL (?download=1) to get the
             original image if publicly accessible.
          2) Try Selenium (real browser) to discover the largest AstroBin
             image requested by the page.
          3) Otherwise, parse the HTML (srcset/OpenGraph/Twitter) as a fallback.
        """
        try:
            headers = {'User-Agent': 'YourBotName/1.0'}

            # --- 1) Try direct download of original image (no API needed) ---
            original_path = self._download_astrobin_original(astrobin_url, headers, save_path)
            if original_path:
                return original_path

            # --- 2) Try using Selenium to mimic a real browser and
            #         discover the largest image used by the viewer.
            selenium_path = self._download_astrobin_via_selenium(astrobin_url, headers, save_path)
            if selenium_path:
                return selenium_path

            img_url = None

            # --- 3) Fallback to HTML scraping if higher-resolution sources are not available ---
            if not img_url:
                page_resp = requests.get(astrobin_url, headers=headers, timeout=30)
                if page_resp.status_code != 200:
                    self.logger.error(f"Failed to load AstroBin page. Status code: {page_resp.status_code} URL: {astrobin_url}")
                    return None

                html = page_resp.text or ""

                # 3a) Try to find ALL srcset/data-srcset entries and pick the
                #     largest AstroBin CDN URL across them.
                best_width = -1
                best_url = None
                for srcset_match in re.finditer(
                    r'(?:srcset|data-srcset)\s*=\s*["\']([^"\']+)["\']',
                    html,
                    re.IGNORECASE,
                ):
                    srcset_value = srcset_match.group(1)
                    for part in srcset_value.split(','):
                        part = part.strip()
                        if not part:
                            continue
                        tokens = part.split()
                        if not tokens:
                            continue
                        candidate_url = tokens[0]
                        width = 0
                        if len(tokens) > 1 and tokens[1].endswith('w'):
                            try:
                                width = int(tokens[1][:-1])
                            except ValueError:
                                width = 0
                        # Prefer non-avatar AstroBin CDN URLs.
                        if "astrobin.com" in candidate_url and "avatars" not in candidate_url:
                            if width >= best_width:
                                best_width = width
                                best_url = candidate_url
                if best_url:
                    img_url = best_url

                # 3b) Fallback to OpenGraph image, then Twitter image.
                if not img_url:
                    og_match = re.search(
                        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                        html,
                        re.IGNORECASE,
                    )
                    if og_match:
                        img_url = og_match.group(1).strip()
                    else:
                        tw_match = re.search(
                            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
                            html,
                            re.IGNORECASE,
                        )
                        if tw_match:
                            img_url = tw_match.group(1).strip()

            if not img_url:
                self.logger.error(f"Could not find image URL on AstroBin page: {astrobin_url}")
                return None

            if img_url.startswith("//"):
                img_url = "https:" + img_url
            elif img_url.startswith("/"):
                img_url = urljoin(astrobin_url, img_url)

            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            img_resp = requests.get(img_url, headers=headers, timeout=60)
            if img_resp.status_code != 200:
                self.logger.error(f"Failed to download AstroBin image. Status code: {img_resp.status_code} URL: {img_url}")
                return None

            try:
                img = Image.open(BytesIO(img_resp.content))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(save_path, "JPEG", quality=90)
            except Exception as e:
                self.logger.error(f"Failed to convert AstroBin image to JPEG: {e}")
                return None

            self.logger.info(f"Downloaded AstroBin image from {img_url} to {save_path}")
            return save_path
        except Exception as e:
            self.logger.error(f"Error downloading AstroBin image from {astrobin_url}: {e}")
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

                # Get root and parent URIs and CIDs for reply 
                root_uri = post["uri"]
                root_cid = post["cid"]
                # The key: always attach to *this* post (child) so your reply is not orphaned
                parent_uri = post["uri"]
                parent_cid = post["cid"]
                
                # Construct a dictionary with post IDs for replying
                post_id = { "root_uri" : root_uri, "root_cid" : root_cid, "parent_uri":parent_uri,"parent_cid":parent_cid}

                # Check if the bot is mentioned in the post text
                if self.botname in post_text.lower():
                    self.logger.info(f"Bot was tagged in a post: {post_text}")
                    alt_link=None
                    if not (hasattr(post_content, 'embed') and post_content.embed):
                        try:
                            #check if the post is a comment from a parent post
                            if post_thread['thread']['parent'] is None :
                                return post_id,None
                            #check if the comment author is the  original post author to avoid spam
                            if post["author"]["handle"]==post_thread['thread']['parent']['post']["author"]["handle"]:
                                post = post_thread['thread']['parent']['post']
                                post_content = post['record']
                            else:
                                return post_id,None
                        except Exception as e:
                            # Log errors if unable to create the post
                            self.logger.error("Error finding parent post: %s", e)
                            return post_id,None

                    # Check if there is an embed with images
                    if hasattr(post_content, 'embed') and post_content.embed:
                        
                        embed = post_content.embed
                        if not(hasattr(embed, 'images') and embed.images):
                            #handle case where images is embedded together with a quoted post (pffff)
                            if (hasattr(embed, 'media') and hasattr(embed.media, 'images')) and embed.media.images:   
                                #image_cid=embed.media.images[0].image.ref.link 
                                embed=embed.media
                                alt_link=None
                            else:     
                                try:
                                    #if not image in the post try to get the image in the quoted post
                                    quoted_thread=self.client.app.bsky.feed.get_post_thread({'uri':embed["record"]["uri"] })
                                    embed=quoted_thread['thread']['post']['record'].embed
                                    if (hasattr(quoted_thread['thread']['post']['embed'], 'images') and quoted_thread['thread']['post']['embed'].images):
                                        alt_link=quoted_thread['thread']['post']['embed']['images'][0]["fullsize"]
                                    else:
                                        embed=quoted_thread['thread']['post']['embed'].media
                                        alt_link=embed.images[0].fullsize
                                        
                                except Exception as e:
                                    # Log errors if unable to create the post
                                    self.logger.error("Error finding image in quoted post: %s", e)
                                    # Do not return here; fall through so AstroBin
                                    # link detection can still run later.
                        else:
                            try:
                                alt_link=post['embed']['images'][0]["fullsize"]
                            except Exception as e:
                                # Log errors if unable to create the post
                                self.logger.error("Error finding image in quoted post: %s", e)
                                # Keep going without alt_link; we might still
                                # download via the primary CDN URL.
                            
                        if hasattr(embed, 'images') and embed.images:
                            images = embed.images
                            if images: 
                                # Get the CID of the first image 
                                #if image_cid is None:
                                if hasattr(embed.images[0],"image"):
                                    image_cid = images[0].image.ref.link
                                else:
                                    image_cid=""
                                # Get the author's DID
                                author_did = post['author']['did']
                                # Download the image
                                downloaded_image_path = self.download_image(author_did, image_cid,alt_link)

                                # Correct the problem of orphan post when replying to a comment of a root post
                                # MODIFIED (B fix): compute root from the ORIGINAL mention's record,
                                # not from `post` which may have been switched to the parent to fetch the image.
                                if hasattr(mention_record, "reply") and mention_record.reply:
                                    reply_ref = mention_record.reply
                                    if hasattr(reply_ref, "root") and reply_ref.root:
                                        root_uri = reply_ref.root.uri
                                        root_cid = reply_ref.root.cid

                                # Construct a dictionary with post IDs for replying
                                post_id = { "root_uri" : root_uri, "root_cid" : root_cid, "parent_uri":parent_uri,"parent_cid":parent_cid}
                                return post_id, downloaded_image_path

                    # If no image was found, look for AstroBin links in the
                    # mention post and, if present, in the quoted post.
                    downloaded_image_path = self._find_astrobin_in_mention_and_quoted(post, post_content)
                    if downloaded_image_path:
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
