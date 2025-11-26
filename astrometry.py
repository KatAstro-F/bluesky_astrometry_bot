import os
import time
import json
import requests
import logging
import tools
from credentials import credentials
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://nova.astrometry.net/api"   # HTTPS

class astrometry():
    def __init__(self, logger, API_KEY):
        self.logger = logger
        self.API_KEY = API_KEY
        self.session = None  # API session token
        # Persistent HTTP session for all calls (API + images)
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": "GIMP-Astrometry-Plugin/1.0 (+https://nova.astrometry.net)",
        })
        # --- NEW: make the session resilient to transient network issues ---
        retry = Retry(
            total=5,                 # overall retry budget
            connect=5,               # connection errors
            read=5,                  # read errors (incl. RemoteDisconnected)
            status=5,                # HTTP 5xx/429
            backoff_factor=0.5,      # small exponential backoff (0.5, 1, 2, ...)
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.http.mount("https://", adapter)
        self.http.mount("http://", adapter)
        # ------------------------------------------------------------------

    def login_astrometry(self):
        url = f"{BASE_URL}/login"
        payload = {"apikey": self.API_KEY}

        try:
            response = self.http.post(url, data={'request-json': json.dumps(payload)}, timeout=30)
            response.raise_for_status()
        except Exception as e:
            self.logger.error("Login to astrometry.net failed. No response from server:")
            raise Exception("Login to astrometry.net failed, server unreachable.") from e

        response_data = response.json()

        if response_data.get("status") == "success":
            self.session = response_data["session"]
            self.logger.info("Astrometry.net login successful.")
            # Tie subsequent fetches to this session as a cookie (harmless if ignored).
            try:
                self.http.cookies.set("sessionid", self.session, domain="nova.astrometry.net", path="/")
            except Exception:
                pass
        else:
            self.logger.error("Login to astrometry.net failed. Response: %s", response_data)
            raise Exception("Login to astrometry.net failed.", response_data)

    def upload_astrometry_file(self, file_path):
        url = f"{BASE_URL}/upload"
        request_payload = {
            "session": self.session,
            "publicly_visible": "y",
            "allow_modifications": "d",
            "allow_commercial_use": "d",
        }

        with open(file_path, 'rb') as file:
            files = {
                'file': file,
                'request-json': (None, json.dumps(request_payload), 'text/plain')
            }
            response = self.http.post(url, files=files, timeout=120)
        response_data = response.json()

        if response_data.get("status") == "success":
            self.logger.info(f"File uploaded to astrometry. Submission ID: {response_data['subid']}")
            return response_data['subid']
        else:
            self.logger.error("File upload to astrometry failed. Response: %s", response_data)
            raise Exception("File upload to astrometry failed.", response_data)

    def check_submission_status(self, subid):
        """Return (jobs, calibrations). On transient network errors, return ([], [])."""
        url = f"{BASE_URL}/submissions/{subid}"
        try:
            response = self.http.get(url, timeout=30)
            response.raise_for_status()
            response_data = response.json()
            jobs = response_data.get("jobs", [])
            calibrations = response_data.get("job_calibrations", [])
            return jobs, calibrations
        except requests.exceptions.RequestException as e:
            # Transient issue: let the outer loop keep waiting using existing sleeps/timeouts
            self.logger.warning(f"Transient error getting submission status for {subid}: {e}")
            return [], []

    def is_job_ready(self, job_id):
        """Return True (success), False (failure), or None (not ready or transient error)."""
        url = f"{BASE_URL}/jobs/{job_id}"
        try:
            response = self.http.get(url, timeout=30)
            if response.status_code == 200:
                job_status = response.json().get("status")
                if job_status == "success":
                    return True
                elif job_status == "failure":
                    return False
                else:
                    return None
            else:
                self.logger.error(f"Failed to fetch job status for {job_id}. Response: {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            # Treat network hiccups as "not ready yet" so the loop continues
            self.logger.warning(f"Transient error checking job {job_id} status: {e}")
            return None

    def get_job_result(self, field, results, job_id):
        for i in range(6):
            try:
                url = f"{BASE_URL}/jobs/{job_id}/{field}/"
                response = self.http.get(url, timeout=30)
                response.raise_for_status()
                results[field] = response.json()
                return results
            except Exception:
                time.sleep(10)
        return results

    def get_job_results(self, job_id):
        results = {}
        fields = ["calibration","tags","machine_tags","objects_in_field","annotations","info"]
        for field in fields:
            results = self.get_job_result(field, results, job_id)
        return results

    def _download_result_image(self, url, outfile_png):
        """Download an image-like results file with robust checks."""
        os.makedirs(os.path.dirname(outfile_png), exist_ok=True)
        headers = {"Accept": "image/*"}
        try:
            r = self.http.get(url, headers=headers, stream=True, timeout=120)
            ct = r.headers.get("Content-Type", "")
            if r.status_code == 200 and ct.startswith("image/"):
                with open(outfile_png, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                return outfile_png
            # If we were served HTML (eg, a human-check page), surface a clear message.
            text_snippet = ""
            try:
                if "text/html" in ct or "application/xhtml" in ct:
                    text_snippet = (r.text or "")[:300]
            except Exception:
                pass
            self.logger.error(f"Expected image from {url} but got {r.status_code} ({ct}). {text_snippet}")
            return None
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Transient error downloading image from {url}: {e}")
            return None  # let caller decide; your existing logic will move on

    def download_annotated_image_generic(self, job_or_cal_id, url_suffix, suffix_name):
        """
        Fetch images from official 'results files' endpoints over HTTPS.

        url_suffix:
          - 'annotated_full' or 'annotated_display'          -> JOBID
          - 'sky_plot/zoom1' or 'sky_plot/zoom2'             -> CALIBRATION ID
          - also available: 'red_green_image_display', 'extraction_image_display' (JOBID)
        """
        url = f"https://nova.astrometry.net/{url_suffix}/{job_or_cal_id}"
        outfile = f"results/{job_or_cal_id}_annotated_{suffix_name}.png"
        return self._download_result_image(url, outfile)

    def prepare_image_for_upload(self, job_id, url_suffix, suffix_name):
        png_path = self.download_annotated_image_generic(job_id, url_suffix, suffix_name)
        if not png_path:
            return None
        jpg_path = tools.convert_image_to_jpg(self.logger, png_path)
        if not jpg_path:
            return None
        jpg_path = tools.ensure_image_size_under_limit(self.logger, jpg_path)
        return jpg_path

    def perform_astrometry_and_get_results(self, image_path):
        subid = self.upload_astrometry_file(image_path)
        time.sleep(5)
        self.logger.info("Checking astrometry submission status...")
        start_time = time.time()

        while True:
            jobs, calibrations = self.check_submission_status(subid)
            if jobs and jobs[0] is not None and calibrations:
                self.logger.info(f"Astrometry Jobs found: {jobs}")
                break
            if time.time() - start_time > 1200:
                raise Exception("Astrometry job took too long.")
            time.sleep(5)

        job_id = jobs[0]
        calibration_id = calibrations[0][1]
        self.logger.info(f"Waiting for Astrometry Job ID: {job_id} to complete...")

        while True:
            status = self.is_job_ready(job_id)
            if status is True:
                break
            elif status is False:
                raise Exception("Astrometry job failed.")
            else:
                if time.time() - start_time > 1200:
                    raise Exception("Astrometry job took too long.")
                self.logger.info("Job is still processing. Retrying in 10 seconds...")
                time.sleep(10)

        self.logger.info(f"Fetching astrometry results for Job ID: {job_id}")
        results = self.get_job_results(job_id)
        self.logger.info("Astrometry Results collected: %s", json.dumps(results, indent=2))

        # Download and prepare various annotated images for upload (unchanged call sites)
        annotated_full_path    = self.prepare_image_for_upload(job_id,         "annotated_full",    "full")
        annotated_display_path = self.prepare_image_for_upload(job_id,         "annotated_display", "display")
        skymap1_path           = self.prepare_image_for_upload(calibration_id, "sky_plot/zoom1",    "zoom1")
        skymap2_path           = self.prepare_image_for_upload(calibration_id, "sky_plot/zoom2",    "zoom2")

        return results, annotated_full_path, annotated_display_path, skymap1_path, skymap2_path


if __name__ == "__main__":
    LOG_FILENAME = 'bot.log'
    logging.basicConfig(filename=LOG_FILENAME,
                        level=logging.INFO,
                        format='%(asctime)s %(levelname)s: %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')
    logger = logging.getLogger(__name__)

    astro = astrometry(logger, credentials["API_KEY"])
    astro.login_astrometry()
    astro.perform_astrometry_and_get_results("test-image.jpg")
