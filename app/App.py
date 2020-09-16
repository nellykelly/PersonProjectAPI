from flask import Flask, render_template

app = Flask(__name__)

@app.route('/name')
def test(name):
	# return '<h1> Hello World {}!</h1>'.format(name)
	return '<h1>	Hello Nelson</h1>'

@app.route('/')
def index():
	return render_template('index.html')

@app.route('/courses')
def course():
	return render_template('courses.html')

@app.route('/googlecalender')
def GoogleCalender():
	return render_template('GoogleCalander.html')