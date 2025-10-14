import base64
import hashlib
import io
import os
import re
from typing import Iterable

import PIL
import dotenv
import requests
from PIL import Image, ImageOps
from flask import Flask, request, jsonify, make_response
from marshmallow import Schema, fields, validate

app = Flask(__name__)

class SyncStateSchema(Schema):
	assets = fields.Dict(keys=fields.UUID(), values=fields.String(validate = validate.Regexp('^[0-9a-f]{32}$')))

class Immich:
	def __init__(self, base_url: str, api_key: str):
		self.api_key = api_key
		self.base_url = base_url

	def get_assets_to_download(self, already_have_assets: dict[str, str]) -> Iterable[str]:
		response = requests.post(
			url = self.base_url + "/api/search/random",
			headers = {
				"x-api-key": self.api_key
			},
			json = {
				"isFavorite": True,
				"isMotion": False,
				"isOffline": False,
				"type": "IMAGE",
				"visibility": "timeline"
			}
		)
		response.raise_for_status()

		for row in response.json():
			uuid = row["id"]
			checksum = row["checksum"]

			if uuid not in already_have_assets:
				yield uuid # entirely new UUID
			else:
				have_checksum = already_have_assets[uuid]
				current_checksum = hashlib.md5(base64.b64decode(checksum)).hexdigest()

				if have_checksum != current_checksum:
					# asset changed
					yield uuid

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

@app.route('/sync', methods=['POST'])
def get_image_candidates():
	json = request.get_json()
	errors = SyncStateSchema().validate(json)

	if errors:
		return jsonify(errors), 400

	uuids = list(immich.get_assets_to_download(json["assets"]))
	return jsonify(uuids), 200

@app.route('/image/<immichUUID>', methods=['GET'])
def get_image(immichUUID: str):
	if not re.match("^[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}$", immichUUID):
		return jsonify({"error": "Not a valid UUID"}), 400

	image = immich.get_image(immichUUID)
	resized = ImageOps.pad(image, (320, 240))

	buffer = io.BytesIO()
	resized.save(buffer, format="JPEG", quality=90)
	buffer.seek(0)

	response = make_response(buffer)
	response.headers["Content-Type"] = "image/jpeg"

	return response

if __name__ == '__main__':
	app.run()
