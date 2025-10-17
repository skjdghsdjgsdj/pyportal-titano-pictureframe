import os
import random
import re
import time

# CircuitPython doesn't have the typing library, so this is for the IDE's sake and gets ignored on the board
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
from adafruit_display_text.bitmap_label import Label
from adafruit_esp32spi import adafruit_esp32spi
from adafruit_esp32spi.adafruit_esp32spi import ESP_SPIcontrol
from digitalio import DigitalInOut
import adafruit_sdcard

# no REPL on the built-in screen
board.DISPLAY.root_group = None

# reset the display rotation in case it was messed with
board.DISPLAY.rotation = 0

# turn off the speaker to save a token amount of power
speaker = digitalio.DigitalInOut(board.SPEAKER_ENABLE)
speaker.switch_to_output(True)
speaker.value = False

class UI:
	"""
	The UI.
	"""

	def __init__(self, display):
		"""
		Sets up the UI.

		:param display: Board's display, which is probably board.DISPLAY
		"""

		self.display = display
		self.display.auto_refresh = False

		self.image: displayio.TileGrid | None = None

		self._init_components()

		display.root_group = self.root_group

	def _init_components(self) -> None:
		"""
		Sets up all the UI components in their default states.
		"""

		self.root_group = displayio.Group()
		self.font = bitmap_font.load_font("/bdf/sf-compact-display.bdf")

		offline_bitmap = displayio.OnDiskBitmap("/img/offline.bmp")
		self.offline_icon = displayio.TileGrid(
			offline_bitmap,
			pixel_shader = offline_bitmap.pixel_shader,
			x = self.display.width - 35,
			y = self.display.height - 35
		)
		self.offline_icon.hidden = True # just so the UI is less cluttered at first
		self.root_group.append(self.offline_icon)

		self.status_label = Label(
			font = self.font,
			text = "",
			anchor_point = (0.0, 1.0),
			anchored_position = (10, self.display.height - 20),
			color = (255, 255, 255),
		)

		self.status_label_shadow = Label(
			font = self.font,
			text = "",
			anchor_point = (0.0, 1.0),
			anchored_position = (10 + 1, self.display.height - 20 + 1),
			color = (0, 0, 0)
		)

		self.root_group.append(self.status_label_shadow)
		self.root_group.append(self.status_label)

	def show_image(self, path: str | None):
		"""
		Shows a slideshow image or placeholder text if one isn't provided.

		:param path: Path to the image to show, or None to show placeholder text
		:return: self
		"""

		if self.image is not None:
			self.root_group.remove(self.image)

		if path is not None:
			print(f"Showing image: {path}")

			bitmap = displayio.OnDiskBitmap(open(path, "rb"))
			self.image = displayio.TileGrid(bitmap, pixel_shader = bitmap.pixel_shader)

			self.root_group.insert(0, self.image)
		else:
			print("Showing empty image")

		return self

	def set_status(self, status: str | None):
		"""
		Sets the text of the status label at the bottom-left of the screen or hides it if None.

		:param status: Status text to show or hides label if None
		:return: self
		"""

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
	"""
	The main app flow.
	"""

	def __init__(self, asset_path: str = "/sd/assets"):
		"""
		Sets up the main app.

		:param asset_path: Path to where assets are stored on the SD card
		"""

		self.ui = UI(board.DISPLAY)
		self.asset_path: Final[str] = asset_path

		# the underlying ESP32 hardware; should only get initialized once at startup
		self.esp = None

		# is not None once there's a successful Wi-Fi connection
		self.requests: adafruit_requests.Session | None = None

	def start(self) -> None:
		"""
		Entry point for the app; loops forever.
		"""

		self._mount_sd()

		has_assets = False
		if not any(self._walk_fs_assets()): # if the SD card is empty, then download assets first
			print("SD card has no assets; attempting to download them before starting slideshow")
			if self._auto_connect(): # only attempt a sync if online
				self._sync()
		else:
			has_assets = True
			print("SD card has at least one asset; starting slideshow first")

		self._loop(sync_immediately = has_assets)

	def _loop(self, sync_immediately: bool = False) -> None:
		"""
		Main loop that runs once all the hardware is setup and initial sync completed. Loops forever.

		:param: sync_immediately: If True, run a sync immediately after rendering the first image. If false, wait until
		the usual timeout before running a sync. Only affects the first image render.
		"""

		last_image_path = None
		last_sync = None if sync_immediately else time.monotonic()
		while True:
			# pick a random asset and show it
			path = self._get_random_sd_asset_path(avoid = last_image_path)
			if path is None: # nothing on the SD card
				self.ui.show_image(None)
				self.ui.set_status("No images available").render()
			elif last_image_path != path: # show a new image
				last_image_path = path
				self.ui.set_status(None).render()
				self.ui.show_image(path).render() # a separate render to get rid of the first before slowly drawing image

			# see if a sync is needed or was forced
			now = time.monotonic()
			if last_sync is None or now - last_sync > os.getenv("SYNC_INTERVAL_SECONDS", 3600):
				last_sync = now
				print("Sync timeout reached or sync forced")
				if self._auto_connect():
					print("Starting sync")
					self._sync()
				else:
					print("Skipping sync, offline")

			time.sleep(os.getenv("REFRESH_INTERVAL_SECONDS", 300))

	@staticmethod
	def _is_uuid(string: str) -> bool:
		"""
		Validates that the given string is a valid lowercase UUID.
		:param string: Candidate string to validate
		:return: True if the string is a valid UUID, False otherwise
		"""

		# The ACTUAL pattern should be:
		# ^[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}$
		# ...but the CircuitPython re engine doesn't support the brace syntax, so have to count group sizes manually.
		match = re.match(r"^([a-f0-9]+)-?([a-f0-9]+)-?(4[a-f0-9]+)-?([89ab][a-f0-9]+)-?([a-f0-9]+)$", string)
		if not match:
			return False

		group_sizes = [8, 4, 4, 4, 12]
		for i, size in enumerate(group_sizes):
			if len(match.group(i + 1)) != size:
				return False

		return True

	@staticmethod
	def _is_dir(path: str) -> bool:
		"""
		Checks if the given path is a directory. IOErrors are (unfortunately) assumed to indicate a file doesn't exist
		due to CircuitPython limitations.

		:param path: Path to test
		:return: True if the given path is a directory, False otherwise (like a file, doesn't exist at all, etc.)
		"""

		try:
			stat = os.stat(path)
			return stat[0] & 0x4000
		except OSError:
			return False

	def _get_random_sd_asset_path(self, avoid: str | None = None) -> str | None:
		"""
		Gets a random asset from the SD card or None if there aren't any available.

		:param avoid: Try not to pick this asset; no effect if fewer than two assets are available.
		:return: A random asset path or None if there aren't any available.
		"""

		all_assets = list(self._walk_fs_assets())
		if not all_assets:
			return None

		path = None
		# keep picking a random path that differs from the avoid path, unless there's only one file anyway or there's
		# no need to avoid
		while path is None or (avoid is not None and len(all_assets) > 1 and path == avoid):
			uuid, md5 = random.choice(all_assets)
			path = self._build_asset_path(uuid, md5)

		return path

	def _walk_fs_assets(self, delete_orphans: bool = False) -> Iterable[tuple[str, str]]:
		"""
		Iterates all the assets on the SD card, optionally deleting orphans found on the way.

		:param delete_orphans: True to delete files that were found that don't match the asset naming pattern, or false
		to just ignore them.
		:return: Tuple of asset UUIDs and their MD5 hashes
		"""

		for asset_dir in os.listdir(self.asset_path):
			full_asset_dir = self.asset_path + "/" + asset_dir

			if not self._is_uuid(asset_dir):
				print(f"Ignoring {full_asset_dir} because it's not a UUID")
				continue

			if not self._is_dir(full_asset_dir):
				print(f"Ignoring {full_asset_dir} because it's not a directory")
				if delete_orphans:
					print(f"Deleting {full_asset_dir}")
					os.unlink(full_asset_dir)

			for asset_file in os.listdir(full_asset_dir):
				full_asset_path = full_asset_dir + "/" + asset_file

				# pattern should be {32}, not +, but CircuitPython re doesn't support braces
				match = re.match("^([a-f0-9]+)\.bmp$", asset_file)
				md5 = None if not match or len(match.group(1)) != 32 else match.group(1)
				if not md5:
					print(f"Ignoring {full_asset_path} because it doesn't match filename format")
					if delete_orphans:
						print(f"Deleting {full_asset_path}")
						os.unlink(full_asset_path)
					continue

				yield asset_dir, md5

	def _build_asset_path(self, uuid: str, md5: str) -> str:
		"""
		Builds a path to the asset for the given UUID and MD5 hash. This doesn't necessarily mean the asset actually
		exists.

		:param uuid: Asset's UUID
		:param md5: Asset's MD5 hash
		:return: Path to the asset on the SD card
		"""

		return f"{self.asset_path}/{uuid}/{md5}.bmp"

	def _download_asset(self, uuid: str, md5: str) -> None:
		"""
		Downloads the .bmp for the asset with the given UUID and MD5 hash.

		:param uuid: Asset's UUID
		:param md5: Asset's MD5 hash
		"""

		url = os.getenv("ENDPOINT_URL") + "/image/" + uuid
		print(f"Downloading {url}...", end = "")
		response = self.requests.get(
			url = url,
			stream = True
		)

		if response.status_code != 200:
			raise RuntimeError(f"Got HTTP {response.status_code} when downloading {url}")

		total_bytes = 0
		filename = self._build_asset_path(uuid, md5)
		self._mkdir_if_needed(self.asset_path + "/" + uuid)
		with open(filename, "wb") as f:
			for chunk in response.iter_content(1024):
				# noinspection PyTypeChecker
				total_bytes += f.write(chunk)

			print(f"done ({total_bytes} bytes)")

	def _sync(self) -> None:
		"""
		Syncs assets from the server to the SD card. Along with relevant UI updates, this will:

		1. Ask the server for all available assets (UUIDs and their MD5s)
		2. Enumerate what's actually on the SD card, deleting orphans if that setting is turned on, and deleting found
		   assets that are no longer on the server or that mismatch hashes.
		3. Finding out what assets need downloading (SD card lacks the UUID or the existing UUID has a different hash)
		4. Downloads each asset to the SD card, freeing up space if needed along the way

		If there are server-related problems along the way, like non-200 responses or exceptions, warnings are printed
		and the sync is skipped (if the JSON couldn't be downloaded) or that specific asset being downloaded is skipped.
		"""

		self.ui.set_status("Syncing images...").render()

		# ask the server for all available assets, even if they're already stored on the SD card
		url = os.getenv("ENDPOINT_URL") + "/assets"
		try:
			response = self.requests.get(url)
			if response.status_code != 200:
				raise RuntimeError(f"Got HTTP {response.status_code} when syncing assets from {url}")
		except Exception as e:
			print(f"Failed to request assets JSON: {e}")
			return

		assets_on_server: dict[str, str] = response.json()
		print(f"Server has {len(assets_on_server)} assets")

		# first build an index of what's on the SD card, and delete ones that aren't on the server anymore
		assets_on_sd_card = {}
		for uuid_on_sd_card, md5_on_sd_card in self._walk_fs_assets(delete_orphans = os.getenv("DELETE_ORPHANS", False)):
			if uuid_on_sd_card not in assets_on_server or assets_on_server[uuid_on_sd_card] != md5_on_sd_card:
				self._delete_asset(uuid = uuid_on_sd_card, md5 = md5_on_sd_card)
			else:
				assets_on_sd_card[uuid_on_sd_card] = md5_on_sd_card

		print(f"SD card has {len(assets_on_sd_card)} assets")

		# then build a list of what needs downloading (new UUID or same UUID with new MD5)
		assets_to_download: dict[str, str] = {}
		for uuid_on_server, md5_on_server in assets_on_server.items():
			if uuid_on_server in assets_on_sd_card:
				md5_on_sd_card = assets_on_sd_card[uuid_on_server]
				if md5_on_server == md5_on_sd_card: # same image, no need to download
					continue
				else: # same UUID, different hash, so delete the old one too
					os.unlink(self._build_asset_path(uuid_on_server, md5_on_sd_card))
					os.sync()

			assets_to_download[uuid_on_server] = md5_on_server

		print(f"{len(assets_to_download)} assets to download")

		# download assets that need downloading
		i = 0
		for uuid, md5 in assets_to_download.items():
			print(f"Downloading asset {uuid} ({i + 1} of {len(assets_to_download)})")

			self.ui.set_status(f"Syncing images ({i + 1}/{len(assets_to_download)})").render()
			self._free_up_space(assets_on_server) # if it'll be needed
			try:
				self._download_asset(uuid, md5)
				i += 1
			except Exception as e:
				print(f"Failed to download asset with UUID {uuid} and MD5 {md5}: {e}")

		print(f"Downloaded {i + 1 if i == 1 else 'no'} assets, sync done")

		self.ui.set_status(None).render()

	def _delete_asset(self, uuid: str, md5: str, min_free_bytes: int | None = None, available_assets: dict[str, str] | None = None) -> tuple[bool, bool | None]:
		"""
		Deletes an asset from the SD card.

		:param uuid: Asset's UUID
		:param md5: Asset's MD5 hash
		:param min_free_bytes: Check and report on free space available on the SD card, or ignored if 0 or None
		:param available_assets: If not None, and the existing asset on the SD card matches this UUID and MD5 hash,
		skips the deletion.
		:return: Tuple of whether this asset was actually deleted or not, and whether or not there's now enough free
		space on the SD card if min_free_bytes is not None and > 0, or None if no free space check was run
		"""

		if available_assets is not None and (uuid in available_assets and available_assets[uuid] == md5):
			return False, None # skip this one; it's still in the rotation and this is the first pass

		delete_path = self._build_asset_path(uuid, md5)
		os.unlink(delete_path)
		os.sync()

		if min_free_bytes is None or min_free_bytes <= 0:
			return True, None
		else:
			free_bytes = self._get_free_bytes()
			print(f"Deleted {delete_path}; need {min_free_bytes} bytes free, {free_bytes} now free")
			return True, free_bytes >= min_free_bytes

	def _free_up_space(self, available_assets: dict[str, str]) -> None:
		"""
		Deletes files repeatedly on the SD card until there's enough free space available as defined in the environment
		variable MIN_FREE_BYTES. The order of deletion is undefined. Orphaned assets are deleted first, and there's
		still not enough free space, then in-use assets are deleted too.

		:param available_assets: Avoid deleting these assets first (UUIDs and their MD5 hashes)
		"""

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

	def _get_free_bytes(self) -> int:
		"""
		Gets the amount of free space on the SD card in bytes.
		:return: Amount of free space on the SD card in bytes
		"""

		# Indices for the tuple: https://docs.circuitpython.org/en/latest/shared-bindings/os/#os.statvfs
		statvfs = os.statvfs(self.asset_path)
		return statvfs[1] * statvfs[3]

	def _auto_connect(self) -> bool:
		"""
		Connects to Wi-Fi. If already connected, does nothing.
		:return: True if connected, False if not.
		"""

		if self.requests is None:
			self._connect()

		was_offline_icon_visible = not self.ui.offline_icon.hidden
		is_offline_now = self.requests is None

		if is_offline_now != was_offline_icon_visible:
			print(f"Wi-Fi is now {'disconnected' if is_offline_now else 'connected'}")
			self.ui.offline_icon.hidden = not is_offline_now
			self.ui.render()

		return not is_offline_now

	def _connect(self, attempt_timeout: int = 5, total_timeout = 30) -> bool:
		"""
		Connects to Wi-Fi. This will:

		* Initialize the ESP32 hardware if not already done so
		* Repeatedly attempt to connect to Wi-Fi until one of the following happens:
		  * The connection is successful, in which case True is returned
		  * Repeated attempts to establish the connection are unsuccessful within the timeout window, in which case
		    False is returned

		:param attempt_timeout: Maximum timeout for a single connection attempt
		:param total_timeout: Maximum timeout for the entire series of connection attempts
		"""

		wifi_ssid = os.getenv("CIRCUITPY_WIFI_SSID")
		wifi_password = os.getenv("CIRCUITPY_WIFI_PASSWORD")

		self.requests = None

		if self.esp is None:
			self.ui.set_status("Initializing Wi-Fi hardware...").render()

			while True:
				try:
					self.esp, requests = self._get_esp32()
					break
				except Exception as e:
					print(f"Failed to init ESP32, retrying: {e}")

			mac_id = ':'.join('%02X' % byte for byte in self.esp.MAC_address)
			print(f"ESP32 init'ed: firmware {self.esp.firmware_version}, MAC ID {mac_id}")
		else:
			requests = self.requests

		attempt_count = 1
		start = time.monotonic()
		while not self.esp.is_connected and time.monotonic() - start < total_timeout:
			try:
				status = "Connecting to \"" + (wifi_ssid[:18] + "..." if len(wifi_ssid) > 20 else wifi_ssid) + "\""
				if attempt_count > 1:
					status += f" (attempt #{attempt_count})"

				self.ui.set_status(status).render()

				self.esp.connect_AP(wifi_ssid, wifi_password, attempt_timeout)

				self.requests = requests
				self.esp._debug = False
				return True
			except ConnectionError as e:
				print(f"Failed to connect to {wifi_ssid}, retrying: {e}")
				self.esp._debug = True # be noisy if there are connection errors
				attempt_count += 1

		print(f"Wi-Fi connection timed out after {attempt_count} attempts")
		self.ui.set_status(None).render()
		return False

	@staticmethod
	def _mkdir_if_needed(path: str) -> None:
		"""
		Creates a directory at the given path if one doesn't already exist there.

		:param path: Directory to create
		"""

		try:
			stat = os.stat(path)
			if not (stat[0] & 0x4000):
				raise RuntimeError(f"Path {path} exists but isn't a directory")
		except OSError:
			os.mkdir(path)
			os.sync()

	def _mount_sd(self) -> None:
		"""
		Mounts the SD card to /sd.
		"""

		self.ui.set_status("Mounting SD...").render()

		spi = board.SPI()
		cs = digitalio.DigitalInOut(board.SD_CS)

		while True:
			try:
				sd = adafruit_sdcard.SDCard(spi, cs)
				# noinspection PyTypeChecker
				vfs = storage.VfsFat(sd)
				storage.mount(vfs, "/sd")
				break
			except OSError as e:
				print(f"Failed to mount SD card, retrying: {e}")

		# create the asset directory if needed
		self._mkdir_if_needed(self.asset_path)

	@staticmethod
	def _get_esp32() -> tuple[ESP_SPIcontrol, adafruit_requests.Session]:
		"""
		Gets the ESP32 device and a requests object to be used for making HTTP requests.

		:return: The actual ESP32 device and a requests object to be used for making HTTP requests.
		"""

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