import base64
import hashlib
import io
import os
import re
from typing import Iterable, Any

import PIL
import dotenv
import requests
from PIL import Image, ImageOps
from flask import Flask, jsonify, make_response

app = Flask(__name__)

class Immich:
	def __init__(self, base_url: str, api_key: str):
		self.api_key = api_key
		self.base_url = base_url

	def _do_search(self, page: Any | None = None) -> dict:
		payload = {
			"isFavorite": True,
			"isMotion": False,
			"isOffline": False,
			"type": "IMAGE"
		}

		if page is not None:
			payload["page"] = page

		response = requests.post(
			url = self.base_url + "/api/search/metadata",
			headers = {
				"x-api-key": self.api_key
			},
			json = payload
		)
		response.raise_for_status()

		return response.json()

	def get_assets(self) -> Iterable[tuple[str, str]]:
		page = None
		while True:
			response = self._do_search(page)

			for asset in response["assets"]["items"]:
				if asset["visibility"] != "archive" and asset["visibility"] != "timeline":
					continue # to skip locked/hidden or future unknown visibility types to be safe

				uuid = asset["id"]
				md5 = hashlib.md5(base64.b64decode(asset["checksum"])).hexdigest()

				yield uuid, md5

			page = response["assets"]["nextPage"]
			if page is None:
				break # stop once Immich's pagination says nothing is left

	def get_image(self, uuid: str) -> PIL.Image.Image:
		response = requests.get(
			url = f"{self.base_url}/api/assets/{uuid}/thumbnail",
			params = {
				"size": "preview"
			},
			headers = {
				"x-api-key": self.api_key
			}
		)
		response.raise_for_status()

		return Image.open(io.BytesIO(response.content))

dotenv.load_dotenv()

immich = Immich(
	base_url = os.getenv("IMMICH_BASE_URL"),
	api_key = os.getenv("IMMICH_API_KEY")
)

@app.route('/assets', methods=['GET'])
def get_available_images():
	to_download = {}
	for uuid, md5 in immich.get_assets():
		to_download[uuid] = md5

	return jsonify(to_download), 200

@app.route('/image/<immichUUID>', methods=['GET'])
def get_image(immichUUID: str):
	if not re.match("^[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}$", immichUUID):
		return jsonify({"error": "Not a valid UUID"}), 400

	image = immich.get_image(immichUUID)
	resized = ImageOps.pad(image, (os.getenv("IMAGE_WIDTH", 480), os.getenv("IMAGE_HEIGHT", 320)))
	output_image = resized.convert(mode = "P", colors = 256, dither = Image.Dither.FLOYDSTEINBERG)

	buffer = io.BytesIO()
	output_image.save(buffer, format="BMP")
	buffer.seek(0)

	response = make_response(buffer)
	response.headers["Content-Type"] = "image/bmp"

	return response

if __name__ == '__main__':
	app.run()
