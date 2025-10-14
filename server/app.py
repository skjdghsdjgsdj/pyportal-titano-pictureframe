from flask import Flask

app = Flask(__name__)


@app.route('/sync', methods=['POST'])
def get_image_candidates():
	pass

@app.route('/image<immichUUID>', methods=['GET'])
def get_images(immichUUID: str):
	pass

if __name__ == '__main__':
	app.run()
