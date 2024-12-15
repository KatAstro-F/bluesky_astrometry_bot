import time
import json
import requests
import logging
import tools
from credentials import credentials

BASE_URL = "http://nova.astrometry.net/api"

class astrometry():
    def __init__(self, logger, API_KEY):
        # Store the logger and the API key for later use
        self.logger = logger
        self.API_KEY = API_KEY
        self.session = None

    def login_astrometry(self):
        # Attempt to log in to the astrometry.net API using the provided API key
        url = f"{BASE_URL}/login"
        payload = {"apikey": self.API_KEY}

        try:
            # Make a POST request to the login endpoint with the API key
            response = requests.post(url, data={'request-json': json.dumps(payload)})
        except:
            # If no response from the server, log an error and raise an exception
            self.logger.error("Login to astrometry.net failed. No response from server:")
            raise Exception("Login to astrometry.net failed, server unreachable.")        

        response_data = response.json()

        # Check if the login was successful
        if response_data["status"] == "success":
            self.logger.info("Astrometry.net login successful.")
            # Store the session token for future requests
            self.session = response_data["session"]
        else:
            # If login failed, log the response and raise an exception
            self.logger.error("Login to astrometry.net failed. Response: %s", response_data)
            raise Exception("Login to astrometry.net failed.", response_data)

    def upload_astrometry_file(self, file_path):
        # Upload an image file to astrometry.net for processing
        url = f"{BASE_URL}/upload"
        # Construct the payload to indicate how the image should be handled
        request_payload = {
            "session": self.session,
            "publicly_visible": "y",
            "allow_modifications": "d",
            "allow_commercial_use": "d",
        }

        # Open the file and send it via multipart/form-data
        with open(file_path, 'rb') as file:
            files = {
                'file': file,
                'request-json': (None, json.dumps(request_payload), 'text/plain')
            }
            response = requests.post(url, files=files)
        response_data = response.json()

        # Check the upload response
        if response_data["status"] == "success":
            # If successful, log the submission ID
            self.logger.info(f"File uploaded to astrometry. Submission ID: {response_data['subid']}")
            return response_data['subid']
        else:
            # If upload failed, log the response and raise an exception
            self.logger.error("File upload to astrometry failed. Response: %s", response_data)
            raise Exception("File upload to astrometry failed.", response_data)

    def check_submission_status(self, subid):
        # Check the status of a previously submitted image by its submission ID
        url = f"{BASE_URL}/submissions/{subid}"
        response = requests.get(url)
        response_data = response.json()
        # Extract jobs and calibrations from the response
        jobs = response_data.get("jobs", [])
        calibrations = response_data.get("job_calibrations", [])
        return jobs, calibrations

    def is_job_ready(self, job_id):
        # Check if a given job (by job_id) is completed or not
        url = f"{BASE_URL}/jobs/{job_id}"
        response = requests.get(url)
        if response.status_code == 200:
            job_status = response.json().get("status")
            if job_status == "success":
                # The job is completed successfully
                return True
            elif job_status == "failure":
                # The job failed
                return False
            else:
                # The job is still in progress
                return None
        else:
            # If we can't fetch job status, log an error
            self.logger.error(f"Failed to fetch job status for {job_id}. Response: {response.text}")
            return None

    def get_job_result(self, field, results, job_id):
        # Attempt multiple times to get a particular field of the job result,
        # because sometimes results are not immediately available
        for i in range(6):
            try:
                url = f"{BASE_URL}/jobs/{job_id}/{field}/"
                response = requests.get(url)
                response.raise_for_status()
                # Store the retrieved field data in the results dictionary
                results[field] = response.json()
                return results
            except:
                # Wait for 10 seconds before retrying
                time.sleep(10)
        return results

    def get_job_results(self, job_id):
        # Retrieve all relevant fields (calibration, tags, etc.) for the completed job
        results = {}
        fields = ["calibration","tags","machine_tags","objects_in_field","annotations","info"]
        for field in fields:
            results = self.get_job_result(field, results, job_id)
        return results

    def download_annotated_image_generic(self, job_id, url_suffix, suffix_name):
        # Download a generic annotated image from astrometry.net using the given suffix and job ID
        url = f"http://nova.astrometry.net/{url_suffix}/{job_id}"
        response = requests.get(url)
        if response.status_code == 200:
            annotated_name = f"{job_id}_annotated_{suffix_name}.png"
            # Save the downloaded image locally
            with open(annotated_name, 'wb') as f:
                f.write(response.content)
            self.logger.info(f"Downloaded annotated image ({suffix_name}): {annotated_name}")
            return annotated_name
        else:
            self.logger.error(f"Failed to download annotated image ({suffix_name}). URL: {url}")
            return None

    def perform_astrometry_and_get_results(self, image_path):
        # The main process:
        # 1. Upload the image to astrometry.net
        # 2. Wait for the submission to be processed
        # 3. Retrieve results and download annotated images

        subid = self.upload_astrometry_file(image_path)
        time.sleep(5)
        self.logger.info("Checking astrometry submission status...")
        start_time = time.time()

        # Wait for jobs and calibrations to become available
        while True:
            jobs, calibrations = self.check_submission_status(subid)
            if jobs and jobs[0] is not None and calibrations is not None and calibrations:
                self.logger.info(f"Astrometry Jobs found: {jobs}")
                break
            # Timeout after 10 minutes
            if time.time() - start_time > 600:
                raise Exception("Astrometry job took too long.")
            time.sleep(5)

        job_id = jobs[0]
        calibration_id = calibrations[0][1]
        self.logger.info(f"Waiting for Astrometry Job ID: {job_id} to complete...")

        # Wait until the job is marked as success or failure
        while True:
            status = self.is_job_ready(job_id)
            if status is True:
                break
            elif status is False:
                raise Exception("Astrometry job failed.")
            else:
                # Timeout after ~13 minutes total
                if time.time() - start_time > 800:
                    raise Exception("Astrometry job took too long.")
                self.logger.info("Job is still processing. Retrying in 10 seconds...")
                time.sleep(10)

        self.logger.info(f"Fetching astrometry results for Job ID: {job_id}")
        results = self.get_job_results(job_id)
        self.logger.info("Astrometry Results collected: %s", json.dumps(results, indent=2))

        # Download and prepare various annotated images for upload
        annotated_full_path = self.prepare_image_for_upload(job_id, "annotated_full", "full")
        annotated_display_path = self.prepare_image_for_upload(job_id, "annotated_display", "display")
        # Download sky map images as well
        skymap1_path = self.prepare_image_for_upload(calibration_id, "sky_plot/zoom1", "zoom1")
        skymap2_path = self.prepare_image_for_upload(calibration_id, "sky_plot/zoom2", "zoom2")

        return results, annotated_full_path, annotated_display_path, skymap1_path, skymap2_path

    def prepare_image_for_upload(self, job_id, url_suffix, suffix_name):
        # Download and convert the annotated image to a suitable format (JPG)
        # and ensure it's under the size limit before returning the final path
        png_path = self.download_annotated_image_generic(job_id, url_suffix, suffix_name)
        if not png_path:
            return None
        # Convert the image to JPEG and ensure size constraints are met
        jpg_path = tools.convert_image_to_jpg(self.logger, png_path)
        if not jpg_path:
            return None
        jpg_path = tools.ensure_image_size_under_limit(self.logger, jpg_path)
        return jpg_path

    def download_annotated_image_generic(self, job_id, url_suffix, suffix_name):
        # Overriding the previous method to store results in a "results/" directory
        url = f"http://nova.astrometry.net/{url_suffix}/{job_id}"
        response = requests.get(url)
        if response.status_code == 200:
            annotated_name = f"results/{job_id}_annotated_{suffix_name}.png"
            with open(annotated_name, 'wb') as f:
                f.write(response.content)
            self.logger.info(f"Downloaded annotated image ({suffix_name}): {annotated_name}")
            return annotated_name
        else:
            self.logger.error(f"Failed to download annotated image ({suffix_name}). URL: {url}")
            return None


if __name__ == "__main__":
    # Configure logger
    LOG_FILENAME = 'bot.log'
    logging.basicConfig(filename=LOG_FILENAME,
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
    logger = logging.getLogger(__name__)

    # Start the astrometry process on a test image
    astro = astrometry(logger, credentials["API_KEY"])

    # Log into astrometry.net
    astro.login_astrometry()
    # Perform astrometry on the specified image and get results
    astro.perform_astrometry_and_get_results("test-image.jpg")
