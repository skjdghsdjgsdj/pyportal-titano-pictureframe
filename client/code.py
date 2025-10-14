import os
import re

try:
	# noinspection PyUnusedImports
	from typing import Iterable, Any, List, Final
except ImportError:
	pass

import adafruit_connection_manager
import adafruit_requests
import board
import digitalio
import displayio
import storage
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.label import Label
from adafruit_esp32spi import adafruit_esp32spi
from adafruit_esp32spi.adafruit_esp32spi import ESP_SPIcontrol
from digitalio import DigitalInOut
import adafruit_sdcard

root = displayio.Group()

class UI:
	def __init__(self, display):
		self.display = display
		self.display.auto_refresh = False

		self._init_components()

		display.root_group = self.root_group

	def _init_components(self):
		self.root_group = displayio.Group()
		self.font = bitmap_font.load_font("/bdf/sf-compact-display.bdf")

		self.status_label = Label(
			font = self.font,
			text = "",
			anchor_point = (0.0, 0.0),
			anchored_position = (10, self.display.height - 10 - 16),
			color = (255, 255, 255),
		)

		self.status_label_shadow = Label(
			font = self.font,
			text = "",
			anchor_point = (0.0, 0.0),
			anchored_position = (10 + 1, self.display.height - 10 - 16 + 1),
			color = (0, 0, 0)
		)

		self.root_group.append(self.status_label_shadow)
		self.root_group.append(self.status_label)

	def set_status(self, status: str | None):
		if status is None:
			self.status_label.hidden = True
			self.status_label_shadow.hidden = True
		else:
			self.status_label.hidden = False
			self.status_label_shadow.hidden = False

			self.status_label.text = status
			self.status_label_shadow.text = status

		return self

	def render(self) -> None:
		self.display.refresh()

class App:
	def __init__(self, asset_path: str = "/sd/assets"):
		self.ui = UI(board.DISPLAY)
		self.asset_path: Final[str] = asset_path

		self.requests: adafruit_requests.Session | None = None
		self.spi = None
		self.sd = None

	def start(self):
		self._mount_sd()
		self._connect()
		self._sync()

	def _walk_fs_assets(self) -> Iterable[tuple[str, str]]:
		for asset_dir in os.listdir(self.asset_path):
			# FIXME this match isn't great but CircuitPython doesn't support "counted repetitions" (braces)
			if not re.match(r"^[a-f0-9]+-?[a-f0-9]+-?4[a-f0-9]+-?[89ab][a-f0-9]+-?[a-f0-9]+$", asset_dir):
				print(f"Ignoring {asset_dir} because it's not a UUID")
				continue

			full_asset_dir = self.asset_path + "/" + asset_dir
			stat = os.stat(full_asset_dir)
			if not (stat[0] & 0x4000):
				print(f"Ignoring {full_asset_dir} because it's not a directory")

			for asset_file in os.listdir(full_asset_dir):
				full_asset_path = full_asset_dir + "/" + asset_file

				match = re.match("^([a-f0-9]+)\.jpg$", asset_file)
				if not match:
					print(f"Ignoring {full_asset_path} because it doesn't match filename format (regex)")
					continue

				md5 = match.group(1)
				if len(md5) != 32:
					print(f"Ignoring {full_asset_path} because it doesn't match filename format (char count)")
					continue

				yield asset_dir, md5

	def _build_asset_path(self, uuid: str, md5: str) -> str:
		return f"{self.asset_path}/{uuid}/{md5}.jpg"

	def _sync(self):
		self.ui.set_status("Syncing images...").render()

		# ask the server for all available assets, even if they're already stored on the SD card
		response = self.requests.get(os.getenv("ENDPOINT_URL") + "/assets")
		if response.status_code != 200:
			print(f"Got HTTP {response.status_code} when syncing assets")
			return

		assets_on_server: dict[str, str] = response.json()

		# first build an index of what's on the SD card
		assets_on_sd_card = {}
		for uuid_on_sd_card, md5_on_sd_card in self._walk_fs_assets():
			assets_on_sd_card[uuid_on_sd_card] = md5_on_sd_card

		print("On server:")
		print(assets_on_server)

		print("On SD card:")
		print(assets_on_sd_card)

		# then build a list of what needs downloading (new UUID or same UUID with new MD5)
		assets_to_download: dict[str, str] = {}
		for uuid_on_server, md5_on_server in assets_on_server.items():
			if uuid_on_server in assets_on_sd_card:
				md5_on_sd_card = assets_on_sd_card[uuid_on_server]
				if md5_on_server == md5_on_sd_card: # same image, no need to download
					continue
				else: # same UUID, different hash, so delete the old one too
					os.unlink(self._build_asset_path(uuid_on_server, md5_on_sd_card))

			assets_to_download[uuid_on_server] = md5_on_server

		# download assets that need downloading
		i = 0
		for uuid, md5 in assets_to_download.items():
			self.ui.set_status(f"Syncing images ({i + 1}/{len(assets_to_download)})").render()

			url = os.getenv("ENDPOINT_URL") + "/image/" + uuid
			print(f"Downloading {url}")
			response = self.requests.get(
				url = url,
				stream = True
			)

			if response.status_code != 200:
				raise RuntimeError(f"Got HTTP {response.status_code} when downloading {url}")

			# free up space if needed before downloading
			self._free_up_space(assets_on_server)

			total_bytes = 0
			filename = self._build_asset_path(uuid, md5)
			self._mkdir_if_needed(self.asset_path + "/" + uuid)
			with open(filename, "wb") as f:
				for chunk in response.iter_content(1024):
					# noinspection PyTypeChecker
					total_bytes += f.write(chunk)

				print(f"Downloaded {filename} ({total_bytes} bytes)")

			i += 1

	def _delete_asset(self, min_free_bytes: int, uuid: str, md5: str, available_assets: dict[str, str] | None = None) -> tuple[bool, bool | None]:
		if available_assets is not None and (uuid in available_assets or available_assets[uuid] == md5):
			return False, None # skip this one; it's still in the rotation and this is the first pass

		delete_path = self._build_asset_path(uuid, md5)
		os.unlink(delete_path)
		os.sync()

		free_bytes = self._get_free_bytes()
		print(f"Deleted {delete_path}; need {min_free_bytes} bytes free, {free_bytes} now free")
		return True, free_bytes >= min_free_bytes

	def _free_up_space(self, available_assets: dict[str, str]):
		min_free_bytes = int(os.getenv("MIN_FREE_BYTES", 1048576))
		if min_free_bytes <= 0:
			return # free space cleanup is disabled

		starting_free_bytes = self._get_free_bytes()
		if starting_free_bytes >= min_free_bytes:
			return # don't need to free up any space

		# first delete orphans
		for uuid, md5 in self._walk_fs_assets():
			_, enough_free_space_now = self._delete_asset(
			 	min_free_bytes = min_free_bytes,
				uuid = uuid,
				md5 = md5,
				available_assets = available_assets
			)

			if enough_free_space_now:
				return

		# still not enough space free (or no orphans to delete), so start culling actual assets
		for uuid, md5 in self._walk_fs_assets():
			_, enough_free_space_now = self._delete_asset(
				min_free_bytes = min_free_bytes,
				uuid = uuid,
				md5 = md5
			)

			if enough_free_space_now:
				return

		raise RuntimeError(f"Need {min_free_bytes} free bytes but couldn't find anything else to delete")

	def _get_free_bytes(self):
		# this mess: https://docs.circuitpython.org/en/latest/shared-bindings/os/#os.statvfs
		f_bsize, f_frsize, f_blocks, f_bfree, f_bavail, f_files, f_ffree, f_favail, f_flag, f_namemax = os.statvfs(self.asset_path)
		return f_frsize * f_bfree

	def _connect(self):
		wifi_ssid = os.getenv("CIRCUITPY_WIFI_SSID")
		wifi_password = os.getenv("CIRCUITPY_WIFI_PASSWORD")

		self.ui.set_status("Starting...").render()

		esp, requests = self._get_esp32()

		mac_id = ':'.join('%02X' % byte for byte in esp.MAC_address)
		print(f"ESP32 found; firmware version {esp.firmware_version}, MAC ID {mac_id}")

		attempt_count = 1
		while not esp.is_connected:
			try:
				status = f"Connecting to {wifi_ssid}..."
				if attempt_count > 1:
					status += f" (attempt #{attempt_count})"

				self.ui.set_status(status).render()

				esp.connect_AP(wifi_ssid, wifi_password)
			except ConnectionError as e:
				print(f"Failed to connect to {wifi_ssid}, retrying: {e}")
				esp._debug = True
				attempt_count += 1

		esp._debug = False

		self.requests = requests

	@staticmethod
	def _mkdir_if_needed(path: str) -> None:
		try:
			stat = os.stat(path)
			if not (stat[0] & 0x4000):
				raise RuntimeError(f"Path {path} exists but isn't a directory")
		except OSError:
			os.mkdir(path)

	def _mount_sd(self):
		if self.sd is not None:
			return self.sd

		self.ui.set_status("Mounting SD...").render()

		spi = board.SPI()
		cs = digitalio.DigitalInOut(board.SD_CS)

		self.sd = adafruit_sdcard.SDCard(spi, cs)
		# noinspection PyTypeChecker
		vfs = storage.VfsFat(self.sd)
		storage.mount(vfs, "/sd")

		# create the asset directory if needed
		self._mkdir_if_needed(self.asset_path)

		return self.sd

	def _get_esp32(self) -> tuple[ESP_SPIcontrol, adafruit_requests.Session]:
		esp32_cs = DigitalInOut(board.ESP_CS)
		esp32_ready = DigitalInOut(board.ESP_BUSY)
		esp32_reset = DigitalInOut(board.ESP_RESET)

		spi = board.SPI()
		esp = adafruit_esp32spi.ESP_SPIcontrol(spi, esp32_cs, esp32_ready, esp32_reset)

		pool = adafruit_connection_manager.get_radio_socketpool(esp)
		ssl_context = adafruit_connection_manager.get_radio_ssl_context(esp)
		requests = adafruit_requests.Session(pool, ssl_context)

		if esp.status == adafruit_esp32spi.WL_IDLE_STATUS:
			raise RuntimeError(f"ESP32 status is {esp.status} but expected {adafruit_esp32spi.WL_IDLE_STATUS} (idle)")

		return esp, requests

app = App()
app.start()

while True:
	pass