from flask import Flask, render_template

app = Flask(__name__)

@app.route('/<name>')
def test(name):
	return '<h1> Hello World {}!</h1>'.format(name)

@app.route('/')
def index():
	return render_template('index.html')